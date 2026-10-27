"""Мои брони (§7) и отмена в один тап (+ уведомление партнёру)."""

from __future__ import annotations

import contextlib
import logging

from aiogram import Bot, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards as kb
from app.bot.callbacks import MyCb
from app.bot.common import NO_PREVIEW, cb_message, safe_answer, send_safe, show_text
from app.bot.formatting import (
    booking_cancelled_partner_text,
    esc,
    fmt_slot,
    join_within,
    place_link,
    status_parts,
)
from app.bot.media import send_media
from app.bot.texts import T, pick
from app.db.models import BookingStatus, User
from app.services import bookings
from app.services.errors import Forbidden, InvalidState, NotFound
from app.services.timeutil import now_utc

log = logging.getLogger(__name__)

PAST_LIMIT = 5


async def render_my(
    session: AsyncSession, user: User
) -> tuple[str, InlineKeyboardMarkup | None] | None:
    items = await bookings.list_for_user(session, user)
    if not items:
        return None
    now = now_utc()
    future = [b for b in items if b.slot.starts_at > now]
    past = [b for b in items if b.slot.starts_at <= now][:PAST_LIMIT]
    parts = [T["my_bookings.list"]]
    for b in future:
        emoji, label = status_parts(b.status)
        parts.append(
            T["my_bookings.item"].format(
                status_emoji=emoji,
                slot=fmt_slot(b.slot.starts_at),
                title=esc(b.slot.scenario.title),
                status_label=label,
                place=place_link(b.slot.scenario),
            )
        )
    if past:
        parts.append(T["my_bookings.past_header"])
        for b in past:
            emoji, label = status_parts(b.status)
            parts.append(
                T["my_bookings.item_past"].format(
                    status_emoji=emoji,
                    slot=fmt_slot(b.slot.starts_at),
                    title=esc(b.slot.scenario.title),
                    status_label=label,
                    attended=T["my_bookings.attended"] if b.attended else "",
                )
            )
    cancellable = [
        b for b in future if b.status in (BookingStatus.pending, BookingStatus.confirmed)
    ]
    return join_within(parts, "\n\n"), kb.my_bookings_kb(cancellable, now)


async def send_my(bot: Bot, chat_id: int, session: AsyncSession, user: User) -> None:
    page = await render_my(session, user)
    if page is None:
        await send_media(
            bot,
            chat_id,
            "empty_bookings",
            caption=pick("rot.empty_bookings"),
            reply_markup=kb.weekend_btn_kb(),
        )
        return
    await bot.send_message(chat_id, page[0], reply_markup=page[1], link_preview_options=NO_PREVIEW)


async def on_my(
    callback: CallbackQuery,
    callback_data: MyCb,
    session: AsyncSession,
    user: User,
    bot: Bot,
) -> None:
    msg = cb_message(callback)
    chat_id = msg.chat.id if msg else callback.from_user.id
    try:
        booking = await bookings.get(session, callback_data.id)
    except NotFound:
        await safe_answer(callback, T["error.expired"])
        return
    if booking.user_id != user.id:
        await safe_answer(callback, T["error.no_access"])
        return
    sc = booking.slot.scenario
    slot_label = fmt_slot(booking.slot.starts_at)

    match callback_data.a:
        case "ask" | "askn":
            await safe_answer(callback)
            text = T["booking.cancel_confirm"].format(title=esc(sc.title), slot=slot_label)
            markup = kb.cancel_confirm_kb(booking.id, new=callback_data.a == "askn")
            if callback_data.a == "ask":
                await show_text(callback, text, markup)
            else:
                await bot.send_message(chat_id, text, reply_markup=markup)
        case "yes":
            try:
                booking = await bookings.cancel(session, booking.id, user)
            except Forbidden:
                await safe_answer(callback, T["error.no_access"])
                return
            except InvalidState:
                await safe_answer(callback)
                key = (
                    "booking.cancel_started"
                    if booking.slot.starts_at <= now_utc()
                    else "booking.cancel_not_allowed"
                )
                await show_text(callback, T[key].format(location_name=esc(sc.location_name)))
                return
            await session.commit()
            await safe_answer(callback)
            await show_text(
                callback, T["booking.cancelled"].format(title=esc(sc.title), slot=slot_label)
            )
            await send_safe(
                bot, bookings.partner_chat_id(booking), booking_cancelled_partner_text(booking)
            )
            log.info("booking cancelled by user", extra={"booking_id": booking.id})
        case "no":
            await safe_answer(callback)
            page = await render_my(session, user)
            if page is None:
                await show_text(callback, T["my_bookings.empty"])
            else:
                await show_text(callback, page[0], page[1])
        case _:  # "non" — подтверждение пришло отдельным сообщением: просто убираем его
            await safe_answer(callback)
            if msg is not None:
                with contextlib.suppress(TelegramAPIError):
                    await msg.delete()


def setup() -> Router:
    r = Router(name="my_bookings")
    r.callback_query.register(on_my, MyCb.filter())
    return r
