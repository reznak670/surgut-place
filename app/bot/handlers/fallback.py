"""Последние роутеры: отказ в доступе к ролевым экранам, неизвестный ввод, error handler."""

from __future__ import annotations

import contextlib
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, ExceptionTypeFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ErrorEvent, Message

from app.bot import keyboards as kb
from app.bot.callbacks import AdmCb, AttCb, PbCb, PmCb, ScfCb
from app.bot.common import safe_answer
from app.bot.texts import T, pick
from app.db.models import User

log = logging.getLogger(__name__)


async def deny_message(message: Message) -> None:
    await message.answer(T["error.no_access"])


async def deny_callback(callback: CallbackQuery) -> None:
    await safe_answer(callback, T["error.no_access"])


def setup_denied() -> Router:
    """Ролевые команды/колбэки, не прошедшие RoleFilter в partner/admin-роутерах."""
    r = Router(name="denied")
    r.message.filter(F.chat.type == "private")
    r.message.register(deny_message, Command("admin", "partner"))
    r.message.register(deny_message, F.text.in_({T["menu.btn_admin"], T["menu.btn_partner"]}))
    for cb in (AdmCb, ScfCb, PbCb, AttCb, PmCb):
        r.callback_query.register(deny_callback, cb.filter())
    return r


async def unknown_message(message: Message, state: FSMContext, user: User | None = None) -> None:
    if await state.get_state() is not None:
        await message.answer(T["error.blocked_fsm"])
        return
    await message.answer(pick("rot.error_unknown"), reply_markup=kb.menu_for(user))


async def unknown_callback(callback: CallbackQuery) -> None:
    await safe_answer(callback, T["error.expired"])


async def on_error(event: ErrorEvent) -> bool:
    log.exception("unhandled error in handler", exc_info=event.exception)
    update = event.update
    text = pick("rot.error_generic")
    try:
        if update.callback_query is not None:
            cq = update.callback_query
            with contextlib.suppress(TelegramAPIError):
                await cq.answer()
            if cq.message is not None and cq.message.chat.type == "private":
                await cq.bot.send_message(cq.message.chat.id, text)  # type: ignore[union-attr]
        elif update.message is not None and update.message.chat.type == "private":
            await update.message.answer(text)
    except Exception:
        log.warning("error handler could not notify user")
    return True


def setup_fallback() -> Router:
    r = Router(name="fallback")
    r.message.register(unknown_message, F.chat.type == "private")
    r.callback_query.register(unknown_callback)
    r.errors.register(on_error, ExceptionTypeFilter(Exception))
    return r
