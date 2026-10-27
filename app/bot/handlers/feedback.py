"""Фидбэк после вечера (§10): оценка 1–5 → ≥4 «2 идеи», ≤3 «что не так» + алерт админам."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards as kb
from app.bot.callbacks import CardCb, FbCb
from app.bot.common import cb_message, safe_answer, safe_edit_markup, safe_edit_text, send_safe
from app.bot.formatting import esc, esc_trunc, fmt_slot, guest_contact, title_short
from app.bot.media import send_media
from app.bot.states import FeedbackFlow
from app.bot.texts import T
from app.config import settings
from app.db.models import Booking, User
from app.services import bookings, feedback, scenarios
from app.services.errors import ServiceError, ValidationError

log = logging.getLogger(__name__)

LOW_RATING_MAX = 3


def low_rating_alert(booking: Booking, rating: int, comment: str | None = None) -> str:
    return T["admin.alert_low_rating"].format(
        rating=rating,
        title=esc(booking.slot.scenario.title),
        slot=fmt_slot(booking.slot.starts_at),
        name=esc(booking.name),
        contact=guest_contact(booking, for_admin=True),
        comment_or_dash=esc_trunc(comment, 3000) if comment else "—",
    )


async def on_rating(
    callback: CallbackQuery,
    callback_data: FbCb,
    session: AsyncSession,
    user: User,
    state: FSMContext,
    bot: Bot,
) -> None:
    msg = cb_message(callback)
    chat_id = msg.chat.id if msg else callback.from_user.id
    if callback_data.r == 0:  # «Пропустить» комментарий
        await safe_answer(callback)
        await state.clear()
        if msg is not None:
            await safe_edit_markup(msg, None)
        await bot.send_message(chat_id, T["feedback.skipped"], reply_markup=kb.menu_for(user))
        return
    rating = callback_data.r
    try:
        booking = await bookings.get(session, callback_data.id)
        previous = booking.feedback.rating if booking.feedback is not None else None
        await feedback.save_rating(session, callback_data.id, user, rating)
        booking = await bookings.get(session, callback_data.id)
    except ServiceError:
        await safe_answer(callback, T["error.expired"])
        return
    await safe_answer(callback)
    if msg is not None and msg.text is not None:
        await safe_edit_text(
            msg, msg.html_text + "\n\n" + T["feedback.rated_mark"].format(rating=rating)
        )
    sc = booking.slot.scenario
    if rating > LOW_RATING_MAX:
        ideas = await scenarios.recommend(session, user, [sc.id])
        if ideas:
            markup = kb.inline(
                [[kb.btn(title_short(s.title), CardCb(a="open", id=s.id))] for s in ideas]
            )
            await send_media(
                bot,
                chat_id,
                "feedback_thanks",
                caption=T["feedback.thanks_good"],
                reply_markup=markup,
            )
        else:
            await send_media(
                bot, chat_id, "feedback_thanks", caption=T["feedback.thanks_good_short"]
            )
        return
    await state.set_state(FeedbackFlow.comment)
    await state.update_data(booking_id=booking.id, rating=rating)
    await bot.send_message(
        chat_id, T["feedback.ask_comment"], reply_markup=kb.feedback_skip_kb(booking.id)
    )
    # алерт — только при первой оценке или переходе из «хорошо» в «плохо», не на каждый тап
    if previous is None or previous > LOW_RATING_MAX:
        await send_safe(bot, settings.ADMIN_CHAT_ID, low_rating_alert(booking, rating))


async def on_comment(
    message: Message, session: AsyncSession, user: User, state: FSMContext, bot: Bot
) -> None:
    data = await state.get_data()
    booking_id = data.get("booking_id")
    text = (message.text or "").strip()
    if not booking_id:
        await state.clear()
        return
    try:
        fb = await feedback.save_comment(session, booking_id, user, text)
        booking = await bookings.get(session, booking_id)
    except ValidationError as e:
        await message.answer(esc(e.args[0] if e.args else ""))
        return
    except ServiceError:
        await state.clear()
        await message.answer(T["error.expired"], reply_markup=kb.menu_for(user))
        return
    await state.clear()
    await send_media(
        bot,
        message.chat.id,
        "feedback_thanks",
        caption=T["feedback.thanks_comment"],
        reply_markup=kb.menu_for(user),
    )
    alert = T["admin.alert_comment"].format(
        rating=fb.rating,
        title=esc(booking.slot.scenario.title),
        comment=esc_trunc(text, 3500),
    )
    await send_safe(bot, settings.ADMIN_CHAT_ID, alert)


async def on_comment_other(message: Message) -> None:
    await message.answer(T["error.blocked_fsm"])


def setup() -> Router:
    r = Router(name="feedback")
    r.callback_query.register(on_rating, FbCb.filter())
    r.message.register(on_comment, FeedbackFlow.comment, F.text)
    r.message.register(on_comment_other, FeedbackFlow.comment)
    return r
