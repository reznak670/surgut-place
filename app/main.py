"""Точка входа: `uv run python -m app`."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot import get_routers
from app.bot.notify import TelegramNotifier
from app.config import settings
from app.db import make_engine, make_sessionmaker
from app.logging_setup import setup_logging
from app.middlewares import DbSessionMiddleware, UserMiddleware
from app.scheduler.jobs import setup_scheduler

log = logging.getLogger("app")


def make_storage() -> BaseStorage:
    if settings.REDIS_URL:
        from aiogram.fsm.storage.redis import RedisStorage

        return RedisStorage.from_url(settings.REDIS_URL)
    return MemoryStorage()


async def main() -> None:
    setup_logging(settings.LOG_LEVEL)
    if not settings.BOT_TOKEN:
        raise SystemExit("BOT_TOKEN не задан — заполните .env")

    engine = make_engine(settings.DATABASE_URL)
    sessionmaker = make_sessionmaker(engine)
    storage = make_storage()
    bot = Bot(settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    # workflow data: доступны в хендлерах аргументами sessionmaker / settings / notifier
    dp = Dispatcher(storage=storage, sessionmaker=sessionmaker, settings=settings)
    # outer-middleware на update: срабатывают до фильтров, поэтому RoleFilter видит `user`
    dp.update.outer_middleware(DbSessionMiddleware(sessionmaker))
    dp.update.outer_middleware(UserMiddleware(sessionmaker=sessionmaker))
    dp.include_routers(*get_routers())

    notifier = TelegramNotifier(bot)
    dp["notifier"] = notifier
    scheduler = setup_scheduler(sessionmaker, notifier)
    scheduler.start()

    log.info(
        "bot starting",
        extra={"storage": type(storage).__name__, "db": engine.dialect.name},
    )
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()
        await storage.close()
        await engine.dispose()
        log.info("bot stopped")


def run() -> None:
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
