"""Общие хелперы хендлеров: безопасное редактирование, отправка карточки, служебные отправки."""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards as kb
from app.bot.formatting import card_caption, esc_trunc
from app.bot.media import send_media
from app.db.models import Scenario, Slot, User
from app.services import saved, slots

log = logging.getLogger(__name__)

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


def cb_message(callback: CallbackQuery) -> Message | None:
    """Сообщение колбэка, если оно ещё доступно (не InaccessibleMessage)."""
    msg = callback.message
    return msg if isinstance(msg, Message) else None


async def safe_answer(callback: CallbackQuery, text: str | None = None, **kw: Any) -> None:
    try:
        await callback.answer(text, **kw)
    except TelegramAPIError:
        log.debug("callback answer failed (too old?)")


async def safe_edit_text(
    message: Message, text: str, reply_markup: InlineKeyboardMarkup | None = None
) -> bool:
    """edit_text; «message is not modified» и прочие BadRequest не валят хендлер."""
    try:
        await message.edit_text(text, reply_markup=reply_markup, link_preview_options=NO_PREVIEW)
        return True
    except TelegramBadRequest as e:
        if "not modified" in str(e):
            return True
        log.info("edit_text failed: %s", e)
        return False


async def safe_edit_markup(message: Message, reply_markup: InlineKeyboardMarkup | None) -> None:
    try:
        await message.edit_reply_markup(reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "not modified" not in str(e):
            log.info("edit_reply_markup failed: %s", e)


async def show_text(
    callback: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup | None = None
) -> None:
    """Текстовый экран: редактируем сообщение колбэка, если оно текстовое, иначе шлём новое."""
    msg = cb_message(callback)
    if msg is not None and msg.text is not None and await safe_edit_text(msg, text, reply_markup):
        return
    chat_id = msg.chat.id if msg else callback.from_user.id
    await callback.bot.send_message(  # type: ignore[union-attr]
        chat_id, text, reply_markup=reply_markup, link_preview_options=NO_PREVIEW
    )


async def send_safe(bot: Bot, chat_id: int | None, text: str, **kw: Any) -> bool:
    """Отправка в чужой чат (партнёр/админ-чат/пользователь): не падаем при недоставке."""
    if chat_id is None:
        log.warning("send_safe: no chat to deliver")
        return False
    kw.setdefault("link_preview_options", NO_PREVIEW)
    try:
        await bot.send_message(chat_id, text, **kw)
        return True
    except TelegramAPIError as e:
        log.warning("send_safe failed: %s", type(e).__name__, extra={"chat_id": chat_id})
        return False


async def bot_username(bot: Bot) -> str | None:
    try:
        return (await bot.me()).username
    except TelegramAPIError:
        return None


async def next_slot(session: AsyncSession, sc: Scenario) -> Slot | None:
    if not sc.need_booking or sc.id is None:
        return None
    available = await slots.available(session, sc.id)
    return available[0] if available else None


async def next_slots(session: AsyncSession, items: list[Scenario]) -> dict[int, Slot | None]:
    return {sc.id: await next_slot(session, sc) for sc in items}


async def send_scenario_photo(
    bot: Bot,
    chat_id: int,
    sc: Scenario,
    caption: str,
    make_kb: Any,
) -> Message:
    """Фото-карточка: photos[0], при ошибке — плейсхолдер. make_kb(has_photo: bool) -> markup.

    Если Telegram отверг именно подпись (длина/разметка), плейсхолдер уходит с короткой
    подписью (только название), а при повторной ошибке — без подписи: до error handler
    дело не доходит."""
    photos = sc.photos or []
    caption_bad = False
    if photos:
        try:
            return await bot.send_photo(
                chat_id, photo=photos[0], caption=caption, reply_markup=make_kb(True)
            )
        except TelegramBadRequest as e:
            text = str(e).lower()
            caption_bad = "caption" in text or "entities" in text or "too long" in text
            log.warning("scenario photo failed: %s", e, extra={"scenario_id": sc.id})
    captions: list[str | None] = [caption] if not caption_bad else []
    captions += [f"<b>{esc_trunc(sc.title or '', 900)}</b>", None]
    last_error: TelegramBadRequest | None = None
    for cap in captions:
        try:
            return await send_media(
                bot, chat_id, "scenario_placeholder", caption=cap, reply_markup=make_kb(False)
            )
        except TelegramBadRequest as e:
            last_error = e
            log.warning("placeholder send failed: %s", e, extra={"scenario_id": sc.id})
    assert last_error is not None
    raise last_error


async def send_card(
    bot: Bot,
    chat_id: int,
    session: AsyncSession,
    user: User | None,
    sc: Scenario,
) -> Message:
    """Пользовательская карточка сценария (§5)."""
    caption = card_caption(sc, await next_slot(session, sc))
    is_saved = await saved.is_saved(session, user, sc.id) if user else False
    username = await bot_username(bot)

    def make_kb(has_photo: bool) -> InlineKeyboardMarkup:
        return kb.card_kb(
            sc,
            saved=is_saved,
            bot_username=username,
            truncated=caption.truncated,
            show_more_photos=has_photo,
        )

    return await send_scenario_photo(bot, chat_id, sc, caption.text, make_kb)
