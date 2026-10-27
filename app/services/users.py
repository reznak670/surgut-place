from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from types import EllipsisType

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Budget, Role, Scenario, User, Who
from app.services.errors import NotFound
from app.services.timeutil import now_utc


async def get_by_tg_id(session: AsyncSession, tg_id: int) -> User | None:
    return await session.scalar(select(User).where(User.tg_id == tg_id))


TOUCH_INTERVAL = timedelta(minutes=10)


def _expected_role(user: User, tg_id: int, admin_ids: set[int]) -> Role:
    if tg_id in admin_ids:
        return Role.admin
    if user.role == Role.admin:  # источник правды для админов — ADMIN_IDS
        return Role.user
    return user.role


def needs_touch(
    user: User | None,
    username: str | None,
    first_name: str | None,
    *,
    admin_ids: Iterable[int] = (),
    now: datetime | None = None,
    min_interval: timedelta = TOUCH_INTERVAL,
) -> bool:
    """Нужна ли запись в БД: новый юзер, сменились имя/роль, висит флаг реактивации
    или last_active_at старше min_interval."""
    if user is None:
        return True
    now = now or now_utc()
    return (
        user.username != username
        or user.first_name != first_name
        or user.reactivation_sent_at is not None
        or user.role != _expected_role(user, user.tg_id, set(admin_ids))
        or user.last_active_at < now - min_interval
    )


async def touch(
    session: AsyncSession,
    tg_id: int,
    username: str | None,
    first_name: str | None,
    *,
    admin_ids: Iterable[int] = (),
    now: datetime | None = None,
    min_interval: timedelta | None = None,
) -> User:
    """Upsert пользователя при апдейте (используется UserMiddleware).

    min_interval задан и ничего не поменялось → БД не трогаем (никакого flush/записи).
    """
    now = now or now_utc()
    admin_ids = set(admin_ids)
    user = await get_by_tg_id(session, tg_id)
    if (
        user is not None
        and min_interval is not None
        and not needs_touch(
            user, username, first_name, admin_ids=admin_ids, now=now, min_interval=min_interval
        )
    ):
        return user
    if user is None:
        user = User(tg_id=tg_id, created_at=now, role=Role.user, onboarded=False)
        session.add(user)
    user.username = username
    user.first_name = first_name
    user.last_active_at = now
    user.reactivation_sent_at = None
    user.role = _expected_role(user, tg_id, admin_ids)
    await session.flush()
    return user


async def set_profile(
    session: AsyncSession,
    user: User,
    who: Who | EllipsisType | None = ...,
    budget: Budget | EllipsisType | None = ...,
) -> User:
    """`...` = не менять поле, `None` = сбросить."""
    if who is not ...:
        user.who = Who(who) if who is not None else None
    if budget is not ...:
        user.budget = Budget(budget) if budget is not None else None
    session.add(user)
    await session.flush()
    return user


async def mark_onboarded(session: AsyncSession, user: User) -> User:
    user.onboarded = True
    session.add(user)
    await session.flush()
    return user


async def set_role(session: AsyncSession, tg_id: int, role: Role) -> User:
    role = Role(role)
    user = await get_by_tg_id(session, tg_id)
    if user is None:
        user = User(tg_id=tg_id, role=role, onboarded=False)
        session.add(user)
    else:
        user.role = role
    await session.flush()
    return user


async def list_partners(session: AsyncSession) -> list[User]:
    res = await session.scalars(select(User).where(User.role == Role.partner).order_by(User.id))
    return list(res)


async def accept_privacy(session: AsyncSession, user: User, *, now: datetime | None = None) -> User:
    """Фиксирует согласие на обработку ПД (152-ФЗ). Повторный вызов не меняет дату."""
    if user.privacy_accepted_at is None:
        user.privacy_accepted_at = now or now_utc()
    session.add(user)
    await session.flush()
    return user


async def revoke_partner(session: AsyncSession, tg_id: int) -> User:
    """Снимает роль партнёра: role=user (админа не понижает — его роль из ADMIN_IDS)
    и отвязывает все его сценарии (partner_id=NULL → заявки уйдут в ADMIN_CHAT_ID)."""
    user = await get_by_tg_id(session, tg_id)
    if user is None:
        raise NotFound(f"user tg_id={tg_id}")
    if user.role == Role.partner:
        user.role = Role.user
    await session.execute(
        update(Scenario)
        .where(Scenario.partner_id == user.id)
        .values(partner_id=None)
        .execution_options(synchronize_session=False)
    )
    await session.flush()
    # сценарии из identity map могли закешировать старого партнёра
    for obj in list(session.identity_map.values()):
        if isinstance(obj, Scenario) and obj.partner_id == user.id:
            await session.refresh(obj)
    return user
