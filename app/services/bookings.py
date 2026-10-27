"""Брони. Место в слоте занимается атомарным UPDATE ... WHERE booked_count < capacity,
поэтому две одновременные брони последнего места не пройдут обе."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Booking, BookingStatus, Role, Scenario, Slot, User
from app.services.errors import (
    Forbidden,
    InvalidState,
    NotFound,
    SlotFull,
    SlotInPast,
    ValidationError,
)
from app.services.timeutil import now_utc

log = logging.getLogger(__name__)

ACTIVE_STATUSES = (BookingStatus.pending, BookingStatus.confirmed)


async def get(session: AsyncSession, booking_id: int) -> Booking:
    """Бронь с подгруженными user, slot.scenario.partner, feedback (lazy=selectin)."""
    booking = await session.get(Booking, booking_id, populate_existing=True)
    if booking is None:
        raise NotFound(f"booking {booking_id}")
    return booking


def has_active_partner(booking: Booking) -> bool:
    """У сценария брони есть партнёр с role == partner (роль не снята)."""
    partner = booking.slot.scenario.partner
    return partner is not None and partner.role == Role.partner


def partner_chat_id(booking: Booking) -> int | None:
    """Куда слать заявку с контактом: tg_id активного партнёра сценария, иначе ADMIN_CHAT_ID."""
    if has_active_partner(booking):
        return booking.slot.scenario.partner.tg_id  # type: ignore[union-attr]
    return settings.ADMIN_CHAT_ID


def _can_manage(actor: User, booking: Booking) -> bool:
    if actor.role == Role.admin:
        return True
    return actor.role == Role.partner and booking.slot.scenario.partner_id == actor.id


async def _release_seat(session: AsyncSession, slot_id: int) -> None:
    await session.execute(
        update(Slot)
        .where(Slot.id == slot_id, Slot.booked_count > 0)
        .values(booked_count=Slot.booked_count - 1)
        .execution_options(synchronize_session=False)
    )


async def create(
    session: AsyncSession,
    user: User,
    slot_id: int,
    name: str,
    contact: str,
    *,
    now: datetime | None = None,
) -> Booking:
    now = now or now_utc()
    name = (name or "").strip()
    contact = (contact or "").strip()
    if not 1 <= len(name) <= 100:
        raise ValidationError("Имя — от 1 до 100 символов")
    if not 3 <= len(contact) <= 100:
        raise ValidationError("Контакт — от 3 до 100 символов")

    slot = await session.get(Slot, slot_id)
    if slot is None or not slot.scenario.is_active:
        raise NotFound(f"slot {slot_id}")
    if slot.starts_at <= now:
        raise SlotInPast()

    result = await session.execute(
        update(Slot)
        .where(Slot.id == slot_id, Slot.booked_count < Slot.capacity, Slot.starts_at > now)
        .values(booked_count=Slot.booked_count + 1)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        raise SlotFull()

    booking = Booking(
        user_id=user.id,
        slot_id=slot_id,
        status=BookingStatus.pending,
        name=name,
        contact=contact,
        created_at=now,
    )
    try:
        # savepoint: дубль (двойное нажатие) упрётся в частичный уникальный индекс
        # uq_bookings_active_user_slot, откатим только вставку
        async with session.begin_nested():
            session.add(booking)
            await session.flush()
    except IntegrityError as e:
        await _release_seat(session, slot_id)  # место заняли выше — возвращаем
        raise InvalidState("У вас уже есть заявка на это время") from e
    log.info("booking created", extra={"booking_id": booking.id, "slot_id": slot_id})
    return await get(session, booking.id)


async def _transition(
    session: AsyncSession,
    booking_id: int,
    from_statuses: tuple[BookingStatus, ...],
    values: dict[str, Any],
    error: str,
) -> None:
    """Условный UPDATE ... WHERE status IN (...): из двух одновременных переходов
    пройдёт только один, второй получит InvalidState."""
    result = await session.execute(
        update(Booking)
        .where(Booking.id == booking_id, Booking.status.in_(from_statuses))
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        raise InvalidState(error)


async def confirm(
    session: AsyncSession, booking_id: int, actor: User, *, now: datetime | None = None
) -> Booking:
    booking = await get(session, booking_id)
    if not _can_manage(actor, booking):
        raise Forbidden()
    await _transition(
        session,
        booking_id,
        (BookingStatus.pending,),
        {"status": BookingStatus.confirmed, "confirmed_at": now or now_utc()},
        "Заявка уже обработана",
    )
    return await get(session, booking_id)


async def decline(
    session: AsyncSession, booking_id: int, actor: User, *, now: datetime | None = None
) -> Booking:
    booking = await get(session, booking_id)
    if not _can_manage(actor, booking):
        raise Forbidden()
    await _transition(
        session,
        booking_id,
        (BookingStatus.pending,),
        {"status": BookingStatus.declined, "declined_at": now or now_utc()},
        "Заявка уже обработана",
    )
    await _release_seat(session, booking.slot_id)  # только после успешного перехода
    return await get(session, booking_id)


async def cancel(
    session: AsyncSession, booking_id: int, user: User, *, now: datetime | None = None
) -> Booking:
    now = now or now_utc()
    booking = await get(session, booking_id)
    if booking.user_id != user.id:
        raise Forbidden()
    if booking.slot.starts_at <= now:
        raise InvalidState("Слот уже начался")
    await _transition(
        session,
        booking_id,
        ACTIVE_STATUSES,
        {"status": BookingStatus.cancelled, "cancelled_at": now},
        "Бронь уже отменена или отклонена",
    )
    await _release_seat(session, booking.slot_id)
    return await get(session, booking_id)


async def mark_attended(
    session: AsyncSession, booking_id: int, actor: User, attended: bool
) -> Booking:
    booking = await get(session, booking_id)
    if not _can_manage(actor, booking):
        raise Forbidden()
    await _transition(
        session,
        booking_id,
        (BookingStatus.confirmed,),
        {"attended": bool(attended)},
        "Явку можно отметить только у подтверждённой брони",
    )
    return await get(session, booking_id)


async def list_for_user(
    session: AsyncSession, user: User, *, now: datetime | None = None
) -> list[Booking]:
    """Сначала будущие (ближайшие первыми), затем прошедшие (свежие первыми)."""
    now = now or now_utc()
    rows = list(await session.scalars(select(Booking).where(Booking.user_id == user.id)))
    future = sorted((b for b in rows if b.slot.starts_at > now), key=lambda b: b.slot.starts_at)
    past = sorted(
        (b for b in rows if b.slot.starts_at <= now),
        key=lambda b: b.slot.starts_at,
        reverse=True,
    )
    return future + past


async def list_for_partner(
    session: AsyncSession,
    partner: User,
    statuses: Iterable[BookingStatus] | None = None,
) -> list[Booking]:
    stmt = (
        select(Booking)
        .join(Slot, Slot.id == Booking.slot_id)
        .join(Scenario, Scenario.id == Slot.scenario_id)
        .where(Scenario.partner_id == partner.id)
        .order_by(Slot.starts_at, Booking.id)
    )
    if statuses is not None:
        stmt = stmt.where(Booking.status.in_([BookingStatus(s) for s in statuses]))
    return list(await session.scalars(stmt))


async def list_pending(session: AsyncSession) -> list[Booking]:
    stmt = (
        select(Booking)
        .join(Slot, Slot.id == Booking.slot_id)
        .where(Booking.status == BookingStatus.pending)
        .order_by(Slot.starts_at, Booking.id)
    )
    return list(await session.scalars(stmt))
