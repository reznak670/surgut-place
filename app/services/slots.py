from __future__ import annotations

from datetime import datetime

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Booking, BookingStatus, Scenario, Slot, User
from app.services.errors import InvalidState, NotFound, ValidationError
from app.services.timeutil import now_utc

ACTIVE_STATUSES = (BookingStatus.pending, BookingStatus.confirmed)


async def get(session: AsyncSession, slot_id: int) -> Slot:
    slot = await session.get(Slot, slot_id, populate_existing=True)
    if slot is None:
        raise NotFound(f"slot {slot_id}")
    return slot


async def available(
    session: AsyncSession, scenario_id: int, *, now: datetime | None = None
) -> list[Slot]:
    """Будущие слоты со свободными местами, по возрастанию времени."""
    now = now or now_utc()
    stmt = (
        select(Slot)
        .where(
            Slot.scenario_id == scenario_id,
            Slot.starts_at > now,
            Slot.booked_count < Slot.capacity,
        )
        .order_by(Slot.starts_at)
    )
    return list(await session.scalars(stmt))


async def create(
    session: AsyncSession,
    scenario_id: int,
    starts_at: datetime,
    capacity: int,
    *,
    now: datetime | None = None,
) -> Slot:
    if starts_at.tzinfo is None:
        raise ValidationError("Время слота должно быть с таймзоной")
    if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
        raise ValidationError("Вместимость — целое число от 1")
    if starts_at <= (now or now_utc()):
        raise ValidationError("Нельзя создать слот в прошлом")
    if await session.get(Scenario, scenario_id) is None:
        raise NotFound(f"scenario {scenario_id}")
    dup = await session.scalar(
        select(Slot.id).where(Slot.scenario_id == scenario_id, Slot.starts_at == starts_at)
    )
    if dup is not None:
        raise ValidationError("Такой слот уже есть")
    slot = Slot(scenario_id=scenario_id, starts_at=starts_at, capacity=capacity, booked_count=0)
    try:
        async with session.begin_nested():  # savepoint: гонка двух админов
            session.add(slot)
            await session.flush()
    except IntegrityError as e:
        raise ValidationError("Такой слот уже есть") from e
    await session.refresh(slot, attribute_names=["scenario"])
    return slot


async def delete(session: AsyncSession, slot_id: int) -> None:
    slot = await get(session, slot_id)
    has_active = await session.scalar(
        select(exists().where(Booking.slot_id == slot_id, Booking.status.in_(ACTIVE_STATUSES)))
    )
    if has_active:
        raise InvalidState("На слот есть активные брони")
    await session.delete(slot)
    await session.flush()


async def list_for_scenario(session: AsyncSession, scenario_id: int) -> list[Slot]:
    stmt = select(Slot).where(Slot.scenario_id == scenario_id).order_by(Slot.starts_at)
    return list(await session.scalars(stmt))


async def list_for_partner(
    session: AsyncSession, partner: User, *, now: datetime | None = None
) -> list[Slot]:
    now = now or now_utc()
    stmt = (
        select(Slot)
        .join(Scenario, Scenario.id == Slot.scenario_id)
        .where(Scenario.partner_id == partner.id, Slot.starts_at > now)
        .order_by(Slot.starts_at)
    )
    return list(await session.scalars(stmt))
