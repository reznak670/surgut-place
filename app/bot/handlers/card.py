"""Карточка сценария: открыть, сохранить, ещё фото, подробнее, назад."""

from __future__ import annotations

import logging

from aiogram import Bot, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    ReplyParameters,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.callbacks import CardCb
from app.bot.common import NO_PREVIEW, cb_message, safe_answer, safe_edit_markup, send_card
from app.bot.formatting import card_details
from app.bot.texts import T
from app.db.models import User
from app.services import saved, scenarios
from app.services.errors import NotFound

log = logging.getLogger(__name__)


async def open_card(
    bot: Bot, chat_id: int, session: AsyncSession, user: User | None, scenario_id: int
) -> bool:
    """Отправляет карточку и пишет просмотр. False — сценария нет/скрыт."""
    try:
        sc = await scenarios.get(session, scenario_id)
    except NotFound:
        await bot.send_message(chat_id, T["card.not_found"], link_preview_options=NO_PREVIEW)
        return False
    await scenarios.register_view(session, user, sc.id)
    # коммитим сразу: не держим write-lock SQLite, пока ждём ответов Telegram
    await session.commit()
    await send_card(bot, chat_id, session, user, sc)
    return True


def _with_save_state(
    markup: InlineKeyboardMarkup | None, scenario_id: int, is_saved: bool
) -> InlineKeyboardMarkup | None:
    """Меняем только кнопку «Сохранить» — остальная клавиатура карточки как была."""
    if markup is None:
        return None
    target = CardCb(a="save", id=scenario_id).pack()
    text = T["card.btn_saved" if is_saved else "card.btn_save"]
    rows = [
        [
            InlineKeyboardButton(text=text, callback_data=target)
            if b.callback_data == target
            else b
            for b in row
        ]
        for row in markup.inline_keyboard
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def on_card(
    callback: CallbackQuery,
    callback_data: CardCb,
    session: AsyncSession,
    user: User,
    bot: Bot,
) -> None:
    msg = cb_message(callback)
    chat_id = msg.chat.id if msg else callback.from_user.id
    sc_id = callback_data.id
    match callback_data.a:
        case "open":
            await safe_answer(callback)
            await open_card(bot, chat_id, session, user, sc_id)
        case "save":
            try:
                now_saved = await saved.toggle(session, user, sc_id)
            except NotFound:
                await safe_answer(callback, T["error.expired"])
                return
            await safe_answer(
                callback, T["card.saved_toast" if now_saved else "card.unsaved_toast"]
            )
            if msg is not None:
                await safe_edit_markup(msg, _with_save_state(msg.reply_markup, sc_id, now_saved))
        case "ph":
            await safe_answer(callback)
            try:
                sc = await scenarios.get(session, sc_id)
            except NotFound:
                await bot.send_message(chat_id, T["card.not_found"])
                return
            extra = (sc.photos or [])[1:]
            reply = ReplyParameters(message_id=msg.message_id) if msg else None
            try:
                if len(extra) == 1:
                    await bot.send_photo(chat_id, photo=extra[0], reply_parameters=reply)
                elif extra:
                    await bot.send_media_group(
                        chat_id,
                        media=[InputMediaPhoto(media=p) for p in extra[:10]],
                        reply_parameters=reply,
                    )
            except TelegramAPIError as e:
                log.warning("album failed: %s", e, extra={"scenario_id": sc_id})
        case "det":
            await safe_answer(callback)
            try:
                sc = await scenarios.get(session, sc_id)
            except NotFound:
                await bot.send_message(chat_id, T["card.not_found"])
                return
            await bot.send_message(chat_id, card_details(sc), link_preview_options=NO_PREVIEW)
        case "back":
            await safe_answer(callback)
            if msg is not None:
                try:
                    await msg.delete()
                except TelegramAPIError:
                    await safe_edit_markup(msg, None)
        case _:
            await safe_answer(callback, T["error.expired"])


def setup() -> Router:
    r = Router(name="card")
    r.callback_query.register(on_card, CardCb.filter())
    return r
