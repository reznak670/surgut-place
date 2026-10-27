"""Меню команд (docs/botfather.md): общие — всем, /admin и /partner — своим ролям через
BotCommandScopeChat. Вызывается на старте (router.startup) и при назначении/снятии партнёра."""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.texts import T
from app.config import settings
from app.services import users

log = logging.getLogger(__name__)

USER_COMMAND_NAMES = ("start", "menu", "weekend", "filters", "bookings", "saved", "about", "help")


def user_commands() -> list[BotCommand]:
    return [BotCommand(command=c, description=T[f"commands.{c}"]) for c in USER_COMMAND_NAMES]


def admin_commands() -> list[BotCommand]:
    return [*user_commands(), BotCommand(command="admin", description=T["commands.admin"])]


def partner_commands() -> list[BotCommand]:
    return [*user_commands(), BotCommand(command="partner", description=T["commands.partner"])]


async def set_partner_commands(bot: Bot, tg_id: int) -> None:
    try:
        await bot.set_my_commands(partner_commands(), scope=BotCommandScopeChat(chat_id=tg_id))
    except TelegramAPIError as e:
        log.info("set partner commands failed: %s", type(e).__name__)


async def remove_partner_commands(bot: Bot, tg_id: int) -> None:
    try:
        await bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=tg_id))
    except TelegramAPIError as e:
        log.info("delete partner commands failed: %s", type(e).__name__)


async def setup_commands(
    bot: Bot, sessionmaker: async_sessionmaker[AsyncSession] | None = None
) -> None:
    try:
        await bot.set_my_commands(user_commands(), scope=BotCommandScopeDefault())
    except TelegramAPIError as e:
        log.warning("set default commands failed: %s", type(e).__name__)
        return
    for admin_id in settings.ADMIN_IDS:
        try:
            await bot.set_my_commands(admin_commands(), scope=BotCommandScopeChat(chat_id=admin_id))
        except TelegramAPIError as e:  # админ ещё не писал боту → chat not found
            log.info("set admin commands failed: %s", type(e).__name__, extra={"tg_id": admin_id})
    if sessionmaker is None:
        return
    async with sessionmaker() as session:
        partners = await users.list_partners(session)
    for p in partners:
        await set_partner_commands(bot, p.tg_id)
    log.info(
        "bot commands set", extra={"admins": len(settings.ADMIN_IDS), "partners": len(partners)}
    )


async def on_startup(bot: Bot, **data: Any) -> None:
    """Регистрируется как router.startup — получает workflow data диспетчера."""
    try:
        await setup_commands(bot, data.get("sessionmaker"))
    except Exception:  # не мешаем старту бота
        log.exception("setup_commands failed")
