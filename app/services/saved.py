from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SavedScenario, Scenario, User
from app.services.errors import NotFound


async def toggle(session: AsyncSession, user: User, scenario_id: int) -> bool:
    """True — сценарий теперь в сохранённых, False — убран."""
    existing = await session.get(SavedScenario, (user.id, scenario_id))
    if existing is not None:
        await session.delete(existing)
        await session.flush()
        return False
    if await session.get(Scenario, scenario_id) is None:
        raise NotFound(f"scenario {scenario_id}")
    session.add(SavedScenario(user_id=user.id, scenario_id=scenario_id))
    await session.flush()
    return True


async def is_saved(session: AsyncSession, user: User, scenario_id: int) -> bool:
    return await session.get(SavedScenario, (user.id, scenario_id)) is not None


async def list_saved(
    session: AsyncSession, user: User, *, include_hidden: bool = False
) -> list[Scenario]:
    """Сохранённые сценарии, свежие первыми. По умолчанию только активные."""
    stmt = (
        select(Scenario)
        .join(SavedScenario, SavedScenario.scenario_id == Scenario.id)
        .where(SavedScenario.user_id == user.id)
        .order_by(SavedScenario.created_at.desc())
    )
    if not include_hidden:
        stmt = stmt.where(Scenario.is_active.is_(True))
    return list(await session.scalars(stmt))
