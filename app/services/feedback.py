from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Booking, BookingStatus, Feedback, Slot, User
from app.services.errors import Forbidden, InvalidState, NotFound, ValidationError
from app.services.timeutil import now_utc


async def _own_booking(
    session: AsyncSession, booking_id: int, user: User, now: datetime
) -> Booking:
    booking = await session.get(Booking, booking_id)
    if booking is None:
        raise NotFound(f"booking {booking_id}")
    if booking.user_id != user.id:
        raise Forbidden()
    if booking.status != BookingStatus.confirmed:
        raise InvalidState("Отзыв можно оставить только по подтверждённой брони")
    slot = await session.get(Slot, booking.slot_id)
    if slot is not None and slot.starts_at > now:
        raise InvalidState("Отзыв можно оставить после начала встречи")
    return booking


async def _get_feedback(session: AsyncSession, booking_id: int) -> Feedback | None:
    return await session.scalar(
        select(Feedback)
        .where(Feedback.booking_id == booking_id)
        .execution_options(populate_existing=True)
    )


async def save_rating(
    session: AsyncSession,
    booking_id: int,
    user: User,
    rating: int,
    *,
    now: datetime | None = None,
) -> Feedback:
    """Upsert оценки 1–5 (повторное нажатие меняет оценку)."""
    if isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5:
        raise ValidationError("Оценка — от 1 до 5")
    now = now or now_utc()
    await _own_booking(session, booking_id, user, now)
    fb = await _get_feedback(session, booking_id)
    if fb is None:
        fb = Feedback(booking_id=booking_id, rating=rating, created_at=now, updated_at=now)
        session.add(fb)
    else:
        fb.rating = rating
        fb.updated_at = now
    await session.flush()
    return await _get_feedback(session, booking_id)  # type: ignore[return-value]


async def save_comment(
    session: AsyncSession,
    booking_id: int,
    user: User,
    comment: str,
    *,
    now: datetime | None = None,
) -> Feedback:
    """Комментарий к уже поставленной оценке (без оценки → InvalidState)."""
    comment = (comment or "").strip()
    if not comment:
        raise ValidationError("Комментарий пустой")
    if len(comment) > 2000:
        raise ValidationError("Комментарий длиннее 2000 символов")
    now = now or now_utc()
    await _own_booking(session, booking_id, user, now)
    fb = await _get_feedback(session, booking_id)
    if fb is None:
        raise InvalidState("Сначала поставьте оценку")
    fb.comment = comment
    fb.updated_at = now
    await session.flush()
    return fb


async def list_recent(
    session: AsyncSession, limit: int = 20, only_low: bool = False
) -> list[Feedback]:
    """Свежие отзывы (only_low → оценки ≤3); booking.slot.scenario и booking.user загружены."""
    stmt = select(Feedback).order_by(Feedback.updated_at.desc(), Feedback.id.desc()).limit(limit)
    if only_low:
        stmt = stmt.where(Feedback.rating <= 3)
    return list(await session.scalars(stmt))
