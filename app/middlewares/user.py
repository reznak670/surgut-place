from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from datetime import timedelta
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from aiogram.types import User as TgUser
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.db.models import User
from app.services import users


class UserMiddleware(BaseMiddleware):
    """Upsert пользователя по event_from_user → data["user"] (app.db.models.User).
    Должен стоять ПОСЛЕ DbSessionMiddleware.

    С sessionmaker запись (новый юзер / смена имени / last_active_at раз в 10 мин)
    коммитится отдельной короткой сессией: на SQLite write-lock не висит всё время
    работы хендлера. Без sessionmaker — пишет в сессию хендлера (как раньше).
    """

    def __init__(
        self,
        admin_ids: Iterable[int] | None = None,
        sessionmaker: async_sessionmaker[AsyncSession] | None = None,
        min_interval: timedelta = users.TOUCH_INTERVAL,
    ) -> None:
        self.admin_ids = set(settings.ADMIN_IDS if admin_ids is None else admin_ids)
        self.sessionmaker = sessionmaker
        self.min_interval = min_interval

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user: TgUser | None = data.get("event_from_user")
        session: AsyncSession | None = data.get("session")
        if tg_user is not None and session is not None and not tg_user.is_bot:
            data["user"] = await self._load_user(session, tg_user)
        return await handler(event, data)

    async def _load_user(self, session: AsyncSession, tg_user: TgUser) -> User:
        kwargs: dict[str, Any] = {"admin_ids": self.admin_ids, "min_interval": self.min_interval}
        if self.sessionmaker is None:
            return await users.touch(
                session, tg_user.id, tg_user.username, tg_user.first_name, **kwargs
            )
        async with self.sessionmaker() as short:
            touched = await users.touch(
                short, tg_user.id, tg_user.username, tg_user.first_name, **kwargs
            )
            await short.commit()  # без изменений — пустой коммит, записи нет
        user = await session.get(User, touched.id, populate_existing=True)
        assert user is not None
        return user
