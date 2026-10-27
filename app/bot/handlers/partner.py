"""Кабинет партнёра (§12) и обработка заявок (подтвердить/отклонить) — для партнёра и админа."""

from __future__ import annotations

import logging
from datetime import timedelta

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards as kb
from app.bot.callbacks import AttCb, PbCb, PmCb
from app.bot.common import (
    NO_PREVIEW,
    cb_message,
    safe_answer,
    safe_edit_text,
    send_safe,
)
from app.bot.formatting import (
    booking_new_text,
    esc,
    fmt_slot,
    fmt_time,
    join_within,
    place_link,
)
from app.bot.media import send_media
from app.bot.texts import T
from app.db.models import Booking, BookingStatus, Role, User
from app.middlewares import RoleFilter
from app.services import bookings, slots
from app.services.errors import Forbidden, InvalidState, NotFound
from app.services.timeutil import now_utc

log = logging.getLogger(__name__)

PARTNER_ROLES = (Role.partner, Role.admin)


async def send_partner_menu(bot: Bot, chat_id: int, session: AsyncSession, user: User) -> None:
    pending = await bookings.list_for_partner(session, user, statuses=[BookingStatus.pending])
    await bot.send_message(
        chat_id,
        T["partner.menu"].format(pending_count=len(pending)),
        reply_markup=kb.partner_menu_kb(),
    )


async def cmd_partner(
    message: Message, session: AsyncSession, user: User, state: FSMContext, bot: Bot
) -> None:
    await state.clear()
    await send_partner_menu(bot, message.chat.id, session, user)


async def send_booking_card(
    bot: Bot, chat_id: int, booking: Booking, prefix: str = "", *, mask_contact: bool = False
) -> None:
    """Заявка в формате partner.booking_new с кнопками (как пуш).
    mask_contact=True — для админа: контакт скрыт, если у сценария есть партнёр."""
    text = prefix + booking_new_text(
        booking, booking.slot.free, with_prefix=False, mask_contact=mask_contact
    )
    await bot.send_message(
        chat_id,
        text,
        reply_markup=kb.partner_booking_kb(booking.id),
        link_preview_options=NO_PREVIEW,
    )


async def on_partner_menu(
    callback: CallbackQuery,
    callback_data: PmCb,
    session: AsyncSession,
    user: User,
    bot: Bot,
) -> None:
    await safe_answer(callback)
    msg = cb_message(callback)
    chat_id = msg.chat.id if msg else callback.from_user.id
    now = now_utc()
    match callback_data.a:
        case "bookings":
            pending = await bookings.list_for_partner(
                session, user, statuses=[BookingStatus.pending]
            )
            pending = [b for b in pending if b.slot.starts_at > now]
            confirmed = [
                b
                for b in await bookings.list_for_partner(
                    session, user, statuses=[BookingStatus.confirmed]
                )
                if now < b.slot.starts_at <= now + timedelta(days=7)
            ]
            if not pending and not confirmed:
                await bot.send_message(chat_id, T["partner.bookings_empty"])
                return
            if pending:
                await bot.send_message(chat_id, T["partner.bookings"].format(n=len(pending)))
                for b in pending:
                    await send_booking_card(bot, chat_id, b)
            if confirmed:
                lines = [T["partner.upcoming"]] + [
                    T["partner.upcoming_item"].format(
                        slot=fmt_slot(b.slot.starts_at),
                        title=esc(b.slot.scenario.title),
                        name=esc(b.name),
                        contact=esc(b.contact),
                    )
                    for b in confirmed
                ]
                await bot.send_message(chat_id, join_within(lines))
        case "slots":
            items = await slots.list_for_partner(session, user)
            if not items:
                await bot.send_message(chat_id, T["partner.slots_empty"])
                return
            parts = [T["partner.slots"]]
            current: int | None = None
            for s in items:
                if s.scenario_id != current:
                    current = s.scenario_id
                    parts.append(f"\n<b>{esc(s.scenario.title)}</b>")
                parts.append(
                    T["partner.slots_item"].format(
                        slot=fmt_slot(s.starts_at), booked=s.booked_count, capacity=s.capacity
                    )
                )
            parts.append("\n" + T["partner.slots_footer"])
            await bot.send_message(chat_id, join_within(parts))
        case "attend":
            items = [
                b
                for b in await bookings.list_for_partner(
                    session, user, statuses=[BookingStatus.confirmed]
                )
                if now - timedelta(days=7) <= b.slot.starts_at <= now and b.attended is None
            ]
            if not items:
                await bot.send_message(chat_id, T["partner.attendance_empty"])
                return
            for b in items:
                await bot.send_message(
                    chat_id,
                    T["partner.attendance"].format(
                        slot=fmt_slot(b.slot.starts_at),
                        title=esc(b.slot.scenario.title),
                        name=esc(b.name),
                        contact=esc(b.contact),
                    ),
                    reply_markup=kb.attendance_kb(b.id),
                )


async def _notify_user_confirmed(bot: Bot, booking: Booking) -> None:
    sc = booking.slot.scenario
    caption = T["booking.confirmed_push"].format(
        title=esc(sc.title),
        slot=fmt_slot(booking.slot.starts_at),
        place=place_link(sc),
        address=esc(sc.address),
    )
    try:
        await send_media(
            bot,
            booking.user.tg_id,
            "booking_confirmed",
            caption=caption,
            reply_markup=kb.confirmed_push_kb(sc),
        )
    except TelegramAPIError as e:
        log.info("confirmed push failed: %s", type(e).__name__, extra={"booking_id": booking.id})


async def _notify_user_declined(bot: Bot, booking: Booking) -> None:
    sc = booking.slot.scenario
    text = T["booking.declined_push"].format(
        title=esc(sc.title), slot=fmt_slot(booking.slot.starts_at)
    )
    await send_safe(bot, booking.user.tg_id, text, reply_markup=kb.declined_push_kb(sc.id))


async def on_partner_booking(
    callback: CallbackQuery,
    callback_data: PbCb,
    session: AsyncSession,
    user: User,
    bot: Bot,
) -> None:
    msg = cb_message(callback)
    confirm = callback_data.a == "ok"
    try:
        if confirm:
            booking = await bookings.confirm(session, callback_data.id, user)
        else:
            booking = await bookings.decline(session, callback_data.id, user)
    except Forbidden:
        await safe_answer(callback, T["partner.booking_forbidden"])
        return
    except InvalidState:
        await safe_answer(callback, T["partner.booking_already"])
        if msg is not None and msg.text is not None:
            await safe_edit_text(msg, msg.html_text, None)
        return
    except NotFound:
        await safe_answer(callback, T["error.expired"])
        return
    await session.commit()
    await safe_answer(callback)
    mark_key = "partner.booking_confirmed_mark" if confirm else "partner.booking_declined_mark"
    if msg is not None and msg.text is not None:
        await safe_edit_text(
            msg, msg.html_text + "\n\n" + T[mark_key].format(time=fmt_time(now_utc())), None
        )
    log.info("booking processed", extra={"booking_id": booking.id, "status": str(booking.status)})
    if confirm:
        await _notify_user_confirmed(bot, booking)
    else:
        await _notify_user_declined(bot, booking)


async def on_attendance(
    callback: CallbackQuery, callback_data: AttCb, session: AsyncSession, user: User
) -> None:
    msg = cb_message(callback)
    try:
        await bookings.mark_attended(session, callback_data.id, user, bool(callback_data.v))
    except Forbidden:
        await safe_answer(callback, T["partner.booking_forbidden"])
        return
    except (InvalidState, NotFound):
        await safe_answer(callback, T["error.expired"])
        return
    await safe_answer(callback)
    mark = T["partner.attended_mark" if callback_data.v else "partner.no_show_mark"]
    if msg is not None and msg.text is not None:
        await safe_edit_text(msg, msg.html_text + "\n\n" + mark, None)


def setup() -> Router:
    r = Router(name="partner")
    role = RoleFilter(*PARTNER_ROLES)
    r.message.register(cmd_partner, Command("partner"), role)
    r.message.register(cmd_partner, F.text == T["menu.btn_partner"], role)
    r.callback_query.register(on_partner_menu, PmCb.filter(), role)
    r.callback_query.register(on_partner_booking, PbCb.filter(), role)
    r.callback_query.register(on_attendance, AttCb.filter(), role)
    return r
