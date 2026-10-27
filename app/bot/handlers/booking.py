"""Бронь (§6): FSM слот → имя → контакт → подтверждение → заявка партнёру."""

from __future__ import annotations

import contextlib
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards as kb
from app.bot.callbacks import BookCb
from app.bot.common import NO_PREVIEW, cb_message, safe_answer, safe_edit_markup, send_safe
from app.bot.formatting import (
    booking_new_text,
    esc,
    fmt_price,
    fmt_slot,
    normalize_contact,
    valid_name,
)
from app.bot.states import BookingFlow
from app.bot.texts import T, pick
from app.db.models import Booking, User
from app.services import bookings, scenarios, slots
from app.services.errors import (
    InvalidState,
    NotFound,
    ServiceError,
    SlotFull,
    SlotInPast,
    ValidationError,
)

log = logging.getLogger(__name__)


def _chat_id(callback: CallbackQuery) -> int:
    msg = cb_message(callback)
    return msg.chat.id if msg else callback.from_user.id


async def abort(message_or_chat: Message | int, bot: Bot, state: FSMContext, user: User) -> None:
    await state.clear()
    chat_id = message_or_chat if isinstance(message_or_chat, int) else message_or_chat.chat.id
    await bot.send_message(chat_id, T["booking.aborted"], reply_markup=kb.menu_for(user))


async def show_slots(
    bot: Bot, chat_id: int, session: AsyncSession, state: FSMContext, scenario_id: int
) -> None:
    try:
        sc = await scenarios.get(session, scenario_id)
    except NotFound:
        await bot.send_message(chat_id, T["card.not_found"], link_preview_options=NO_PREVIEW)
        return
    available = await slots.available(session, sc.id) if sc.need_booking else []
    if not available:
        await bot.send_message(chat_id, T["booking.no_slots"], reply_markup=kb.no_slots_kb())
        return
    await state.clear()
    await state.set_state(BookingFlow.slot)
    await state.update_data(scenario_id=sc.id)
    await bot.send_message(
        chat_id,
        T["booking.slots"].format(title=esc(sc.title)),
        reply_markup=kb.slots_kb(available),
    )


async def ask_name(bot: Bot, chat_id: int, state: FSMContext, user: User) -> None:
    data = await state.get_data()
    await state.set_state(BookingFlow.name)
    await bot.send_message(
        chat_id,
        T["booking.ask_name"].format(slot=data.get("slot_label", "")),
        reply_markup=kb.ask_name_kb(user.first_name),
    )


async def notify_partner(bot: Bot, session: AsyncSession, booking: Booking) -> None:
    """Заявка с контактом — только в чат партнёра этой брони (или в админ-чат)."""
    slot = await slots.get(session, booking.slot_id)  # свежий booked_count
    text = booking_new_text(booking, slot.free, with_prefix=True)
    ok = await send_safe(
        bot,
        bookings.partner_chat_id(booking),
        text,
        reply_markup=kb.partner_booking_kb(booking.id),
    )
    if not ok:
        log.warning("booking request not delivered to partner", extra={"booking_id": booking.id})


# ---------------------------------------------------------------- колбэки


async def on_book(
    callback: CallbackQuery,
    callback_data: BookCb,
    session: AsyncSession,
    user: User,
    state: FSMContext,
    bot: Bot,
) -> None:
    chat_id = _chat_id(callback)
    msg = cb_message(callback)
    match callback_data.a:
        case "start":
            await safe_answer(callback)
            await show_slots(bot, chat_id, session, state, callback_data.id)
        case "slot":
            try:
                slot = await slots.get(session, callback_data.id)
            except NotFound:
                await safe_answer(callback, T["error.expired"])
                return
            await safe_answer(callback)
            if msg is not None:
                await safe_edit_markup(msg, None)
            label = fmt_slot(slot.starts_at)
            await state.set_state(BookingFlow.name)
            await state.update_data(
                scenario_id=slot.scenario_id,
                title=slot.scenario.title,
                location_name=slot.scenario.location_name,
                price=fmt_price(slot.scenario),
                slot_id=slot.id,
                slot_label=label,
                sending=False,
            )
            await ask_name(bot, chat_id, state, user)
        case "edit":
            await safe_answer(callback)
            if msg is not None:
                await safe_edit_markup(msg, None)
            if not (await state.get_data()).get("slot_id"):
                await abort(chat_id, bot, state, user)
                return
            await ask_name(bot, chat_id, state, user)
        case "send":
            await send_booking(callback, session, user, state, bot)
        case _:  # abort
            await safe_answer(callback)
            if msg is not None:
                await safe_edit_markup(msg, None)
            await abort(chat_id, bot, state, user)


async def send_booking(
    callback: CallbackQuery, session: AsyncSession, user: User, state: FSMContext, bot: Bot
) -> None:
    chat_id = _chat_id(callback)
    msg = cb_message(callback)
    data = await state.get_data()
    if await state.get_state() != BookingFlow.confirm.state or not data.get("slot_id"):
        await safe_answer(callback, T["error.expired"])
        if msg is not None:
            await safe_edit_markup(msg, None)
        return
    if data.get("sending"):
        await safe_answer(callback, T["error.too_fast"])
        return
    await state.update_data(sending=True)
    await safe_answer(callback)
    if msg is not None:
        await safe_edit_markup(msg, None)

    scenario_id = data["scenario_id"]
    try:
        booking = await bookings.create(
            session, user, data["slot_id"], data.get("name", ""), data.get("contact", "")
        )
    except (SlotFull, SlotInPast) as e:
        await state.clear()
        key = "booking.slot_taken" if isinstance(e, SlotFull) else "booking.slot_past"
        await bot.send_message(chat_id, T[key], reply_markup=kb.other_time_kb(scenario_id))
        await bot.send_message(chat_id, T["menu.show"], reply_markup=kb.menu_for(user))
        return
    except NotFound:
        await state.clear()
        await bot.send_message(chat_id, T["card.not_found"], reply_markup=kb.menu_for(user))
        return
    except (InvalidState, ValidationError) as e:
        await state.clear()
        await bot.send_message(
            chat_id, esc(e.args[0] if e.args else ""), reply_markup=kb.menu_for(user)
        )
        return
    except ServiceError:
        await state.clear()
        await bot.send_message(chat_id, pick("rot.error_generic"), reply_markup=kb.menu_for(user))
        return

    await state.clear()
    # коммитим до отправки партнёру, чтобы его «Подтвердить» увидело бронь
    await session.commit()
    sent = T["booking.sent"].format(
        title=esc(booking.slot.scenario.title), slot=fmt_slot(booking.slot.starts_at)
    )
    await bot.send_message(
        chat_id, sent + "\n" + pick("rot.booking_wait"), reply_markup=kb.menu_for(user)
    )
    await notify_partner(bot, session, booking)


# ---------------------------------------------------------------- сообщения


async def on_cancel_text(message: Message, state: FSMContext, user: User, bot: Bot) -> None:
    await abort(message, bot, state, user)


async def on_name(message: Message, state: FSMContext, user: User) -> None:
    text = (message.text or "").strip()
    if not valid_name(text):
        await message.answer(T["booking.name_invalid"])
        return
    await state.update_data(name=text)
    await state.set_state(BookingFlow.contact)
    await message.answer(T["booking.ask_contact"], reply_markup=kb.ask_contact_kb(user.username))


async def on_contact_shared(message: Message, state: FSMContext, user: User, bot: Bot) -> None:
    contact = message.contact
    if contact is None or message.from_user is None or contact.user_id != message.from_user.id:
        await message.answer(T["booking.contact_foreign"])
        return
    phone = contact.phone_number.strip()
    if not phone.startswith("+"):
        phone = "+" + phone
    await _to_confirm(message, state, phone, bot)


async def on_contact_text(message: Message, state: FSMContext, user: User, bot: Bot) -> None:
    text = (message.text or "").strip()
    if user.username and text == T["booking.btn_use_username"].format(username=user.username):
        contact: str | None = "@" + user.username
    else:
        contact = normalize_contact(text)
    if contact is None:
        await message.answer(T["booking.contact_invalid"])
        return
    await _to_confirm(message, state, contact, bot)


async def _to_confirm(message: Message, state: FSMContext, contact: str, bot: Bot) -> None:
    await state.update_data(contact=contact, sending=False)
    await state.set_state(BookingFlow.confirm)
    data = await state.get_data()
    checking = await message.answer(pick("rot.checking"), reply_markup=ReplyKeyboardRemove())
    text = T["booking.confirm"].format(
        title=esc(data.get("title", "")),
        slot=data.get("slot_label", ""),
        location_name=esc(data.get("location_name", "")),
        price=esc(data.get("price", "")),
        name=esc(data.get("name", "")),
        contact=esc(contact),
    )
    await message.answer(text, reply_markup=kb.booking_confirm_kb())
    with contextlib.suppress(TelegramAPIError):
        await checking.delete()


async def on_wrong_input(message: Message) -> None:
    await message.answer(T["error.blocked_fsm"])


async def on_contact_any(message: Message, state: FSMContext, user: User, bot: Bot) -> None:
    if message.contact is not None:
        await on_contact_shared(message, state, user, bot)
    elif message.text is not None:
        await on_contact_text(message, state, user, bot)
    else:
        await on_wrong_input(message)


def setup() -> Router:
    r = Router(name="booking")
    r.callback_query.register(on_book, BookCb.filter())
    r.message.register(on_cancel_text, StateFilter(BookingFlow), F.text == T["booking.btn_cancel"])
    r.message.register(on_name, BookingFlow.name, F.text)
    r.message.register(on_wrong_input, BookingFlow.name)
    r.message.register(on_contact_any, BookingFlow.contact)
    return r
