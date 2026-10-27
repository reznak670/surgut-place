"""/start: онбординг (§2), deep-link `start=sc_<id>`, возвращение."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards as kb
from app.bot.callbacks import OnbCb
from app.bot.common import NO_PREVIEW, cb_message, safe_answer, safe_edit_markup
from app.bot.handlers.card import open_card
from app.bot.handlers.catalog import send_weekend
from app.bot.media import send_media
from app.bot.texts import T, seasonal, with_privacy
from app.config import settings
from app.db.models import Budget, User, Who
from app.services import users
from app.services.timeutil import now_utc

log = logging.getLogger(__name__)


def welcome_caption() -> str:
    tail = seasonal("season.welcome_tail", now_utc().astimezone(settings.tz).date())
    template = T["onboarding.welcome_body"]
    if tail:
        template += "\n\n" + tail
    template += "\n\n" + T["onboarding.welcome_privacy"]
    return with_privacy(template, settings.PRIVACY_POLICY_URL)


async def cmd_start(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    user: User,
    state: FSMContext,
    bot: Bot,
) -> None:
    await state.clear()
    arg = (command.args or "").strip()
    if arg.startswith("sc_") and arg[3:].isdigit():
        if not user.onboarded:
            # пришли по «Поделиться»: вопросы не задаём, согласие — фактом использования
            await users.accept_privacy(session, user)
            await users.mark_onboarded(session, user)
            await message.answer(
                with_privacy(T["onboarding.deeplink_welcome"], settings.PRIVACY_POLICY_URL),
                reply_markup=kb.menu_for(user),
                link_preview_options=NO_PREVIEW,
            )
        await open_card(bot, message.chat.id, session, user, int(arg[3:]))
        return
    if user.onboarded:
        await message.answer(T["menu.welcome_back"], reply_markup=kb.menu_for(user))
        return
    await send_media(
        bot, message.chat.id, "welcome", caption=welcome_caption(), reply_markup=kb.onb_start_kb()
    )


async def _edit(callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    msg = cb_message(callback)
    if msg is None:
        await callback.bot.send_message(callback.from_user.id, text, reply_markup=markup)  # type: ignore[union-attr]
        return
    try:
        if msg.photo:
            await msg.edit_caption(caption=text, reply_markup=markup)
        else:
            await msg.edit_text(text, reply_markup=markup, link_preview_options=NO_PREVIEW)
    except TelegramBadRequest as e:
        if "not modified" not in str(e):
            await msg.answer(text, reply_markup=markup)


async def on_onboarding(
    callback: CallbackQuery,
    callback_data: OnbCb,
    session: AsyncSession,
    user: User,
    bot: Bot,
) -> None:
    await safe_answer(callback)
    value = callback_data.value
    match callback_data.step:
        case "go":
            await users.accept_privacy(session, user)
            await _edit(callback, T["onboarding.who"], kb.onb_who_kb())
        case "who":
            if value in Who.__members__:
                await users.set_profile(session, user, who=Who(value))
            await _edit(callback, T["onboarding.budget"], kb.onb_budget_kb())
        case "bud":
            if value in Budget.__members__:
                await users.set_profile(session, user, budget=Budget(value))
            first_time = not user.onboarded
            await users.accept_privacy(session, user)
            await users.mark_onboarded(session, user)
            msg = cb_message(callback)
            if msg is not None:
                await safe_edit_markup(msg, None)
            if not first_time:
                return
            chat_id = msg.chat.id if msg else callback.from_user.id
            await bot.send_message(chat_id, T["onboarding.done"], reply_markup=kb.menu_for(user))
            await send_weekend(bot, chat_id, session, user)


def setup() -> Router:
    r = Router(name="start")
    # онбординг и каталог — только в личке (в админ-чате бот на /start не отвечает)
    r.message.filter(F.chat.type == "private")
    r.message.register(cmd_start, CommandStart())
    r.callback_query.register(on_onboarding, OnbCb.filter())
    return r
