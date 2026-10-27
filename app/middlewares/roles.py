from __future__ import annotations

from typing import Any

from aiogram.filters import Filter
from aiogram.types import TelegramObject

from app.db.models import Role, User


class RoleFilter(Filter):
    """`@router.message(RoleFilter(Role.admin))` — пропускает, если user.role среди ролей."""

    def __init__(self, *roles: Role | str) -> None:
        self.roles = {Role(r) for r in roles}

    async def __call__(self, event: TelegramObject, user: User | None = None, **_: Any) -> bool:
        return user is not None and user.role in self.roles
