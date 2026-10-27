from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Booking, BookingStatus, Event, EventType, Feedback, Scenario, Slot
from app.services.timeutil import now_utc

TOP_VIEW_ALERT_SHARE = 0.30
MIN_INDOOR_ACTIVE = 3


@dataclass
class Stats:
    days: int
    views: int
    unique_viewers: int
    bookings: int
    conversion: float | None  # bookings / unique_viewers
    attendance_rate: float | None  # attended=True / прошедшие confirmed с отмеченной явкой
    repeat_rate: float | None  # юзеры с ≥2 confirmed / юзеры с ≥1 confirmed
    avg_rating: float | None
    active_scenarios_week: int  # активные сценарии со слотами на ближайшие 7 дней
    top_view_share: list[tuple[str, float]] = field(default_factory=list)  # топ-5, доля 0..1
    indoor_active_count: int = 0

    @property
    def top_view_alert(self) -> bool:
        """Один сценарий собирает >30% просмотров."""
        return any(share > TOP_VIEW_ALERT_SHARE for _, share in self.top_view_share)

    @property
    def indoor_warning(self) -> bool:
        """Меньше 3 активных indoor-сценариев (на случай непогоды)."""
        return self.indoor_active_count < MIN_INDOOR_ACTIVE


def _ratio(a: int, b: int) -> float | None:
    return a / b if b else None


async def collect(session: AsyncSession, days: int = 56, *, now: datetime | None = None) -> Stats:
    now = now or now_utc()
    since = now - timedelta(days=days)
    view_filter = (Event.type == EventType.card_view.value, Event.created_at >= since)

    views = await session.scalar(select(func.count(Event.id)).where(*view_filter)) or 0
    unique_viewers = (
        await session.scalar(select(func.count(distinct(Event.user_id))).where(*view_filter)) or 0
    )
    bookings = (
        await session.scalar(select(func.count(Booking.id)).where(Booking.created_at >= since)) or 0
    )

    # явка: прошедшие подтверждённые брони, у которых партнёр отметил attended
    att_rows = (
        await session.execute(
            select(Booking.attended)
            .join(Slot, Slot.id == Booking.slot_id)
            .where(
                Booking.status == BookingStatus.confirmed,
                Booking.created_at >= since,
                Slot.starts_at <= now,
                Booking.attended.is_not(None),
            )
        )
    ).scalars()
    att = list(att_rows)
    attendance_rate = _ratio(sum(1 for a in att if a), len(att))

    per_user = (
        await session.execute(
            select(Booking.user_id, func.count(Booking.id))
            .where(Booking.status == BookingStatus.confirmed, Booking.created_at >= since)
            .group_by(Booking.user_id)
        )
    ).all()
    repeat_rate = _ratio(sum(1 for _, c in per_user if c >= 2), len(per_user))

    avg = await session.scalar(
        select(func.avg(Feedback.rating)).where(Feedback.created_at >= since)
    )
    avg_rating = round(float(avg), 2) if avg is not None else None

    active_week = (
        await session.scalar(
            select(func.count(distinct(Scenario.id)))
            .join(Slot, Slot.scenario_id == Scenario.id)
            .where(
                Scenario.is_active.is_(True),
                Slot.starts_at >= now,
                Slot.starts_at < now + timedelta(days=7),
            )
        )
        or 0
    )

    top_rows = (
        await session.execute(
            select(Scenario.title, func.count(Event.id).label("cnt"))
            .join(Scenario, Scenario.id == Event.scenario_id)
            .where(*view_filter)
            .group_by(Scenario.id, Scenario.title)
            .order_by(func.count(Event.id).desc())
            .limit(5)
        )
    ).all()
    top_view_share = [(title, round(cnt / views, 3)) for title, cnt in top_rows] if views else []

    indoor = (
        await session.scalar(
            select(func.count(Scenario.id)).where(
                Scenario.is_active.is_(True), Scenario.is_indoor.is_(True)
            )
        )
        or 0
    )

    return Stats(
        days=days,
        views=views,
        unique_viewers=unique_viewers,
        bookings=bookings,
        conversion=_ratio(bookings, unique_viewers),
        attendance_rate=attendance_rate,
        repeat_rate=repeat_rate,
        avg_rating=avg_rating,
        active_scenarios_week=active_week,
        top_view_share=top_view_share,
        indoor_active_count=indoor,
    )
