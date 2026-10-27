"""О проекте, /help и «Предложить место» (§3, §11)."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards as kb
from app.bot.callbacks import CancelCb
from app.bot.common import NO_PREVIEW, cb_message, safe_answer, safe_edit_markup, send_safe
from app.bot.formatting import esc_trunc, user_link
from app.bot.states import SuggestFlow
from app.bot.texts import T
from app.config import settings
from app.db.models import User

log = logging.getLogger(__name__)

SUGGEST_MIN = 10


async def send_about(bot: Bot, chat_id: int) -> None:
    await bot.send_message(
        chat_id,
        T["about.text"],
        reply_markup=kb.about_kb(settings.PRIVACY_POLICY_URL),
        link_preview_options=NO_PREVIEW,
    )


async def send_help(bot: Bot, chat_id: int) -> None:
    await bot.send_message(
        chat_id, T["help.text"], reply_markup=kb.weekend_btn_kb(), link_preview_options=NO_PREVIEW
    )


async def start_suggest(bot: Bot, chat_id: int, state: FSMContext) -> None:
    await state.set_state(SuggestFlow.text)
    await bot.send_message(chat_id, T["suggest.prompt"], reply_markup=kb.cancel_kb("suggest"))


async def on_suggest(message: Message, state: FSMContext, user: User, bot: Bot) -> None:
    text = (message.text or message.caption or "").strip()
    if len(text) < SUGGEST_MIN:
        await message.answer(T["suggest.too_short"], reply_markup=kb.cancel_kb("suggest"))
        return
    await state.clear()
    tg = message.from_user
    forward = T["admin.suggest_forward"].format(
        user_link=user_link(tg.id, tg.first_name, tg.username) if tg else "—",
        text=esc_trunc(text, 3500),  # режем исходный текст, не HTML
    )
    if message.photo and settings.ADMIN_CHAT_ID is not None:
        try:
            await bot.send_photo(settings.ADMIN_CHAT_ID, photo=message.photo[-1].file_id)
        except TelegramAPIError as e:
            log.warning("suggest forward (photo) failed: %s", type(e).__name__)
    await send_safe(bot, settings.ADMIN_CHAT_ID, forward)
    await message.answer(T["suggest.thanks"], reply_markup=kb.menu_for(user))


async def on_suggest_other(message: Message) -> None:
    await message.answer(T["error.blocked_fsm"], reply_markup=kb.cancel_kb("suggest"))


async def on_cancel_suggest(callback: CallbackQuery, state: FSMContext, user: User) -> None:
    await safe_answer(callback)
    await state.clear()
    msg = cb_message(callback)
    if msg is not None:
        await safe_edit_markup(msg, None)
        await msg.answer(T["menu.show"], reply_markup=kb.menu_for(user))


def setup() -> Router:
    r = Router(name="about")
    r.callback_query.register(on_cancel_suggest, CancelCb.filter(F.w == "suggest"))
    r.message.register(on_suggest, SuggestFlow.text, F.text | F.photo)
    r.message.register(on_suggest_other, SuggestFlow.text)
    return r
