"""Хелперы для создания тестовых данных."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Booking, BookingStatus, Role, Scenario, Slot, TimeOfDay, User

# Среда 28.10.2026, 12:00 по Екатеринбургу (UTC+5)
NOW = datetime(2026, 10, 28, 7, 0, tzinfo=UTC)
# Ближайшие выходные: сб 31.10 00:00 локально = пт 30.10 19:00 UTC
SAT = datetime(2026, 10, 30, 19, 0, tzinfo=UTC)

_ids = itertools.count(1000)


async def make_user(session: AsyncSession, role: Role = Role.user, **kw: Any) -> User:
    kw.setdefault("last_active_at", NOW)
    kw.setdefault("privacy_accepted_at", NOW - timedelta(days=60))
    kw.setdefault("created_at", NOW - timedelta(days=60))
    user = User(tg_id=next(_ids), role=role, first_name="Тест", **kw)
    session.add(user)
    await session.flush()
    return user


def scenario_data(**kw: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "title": "Сценарий",
        "photos": ["https://picsum.photos/seed/a/800/600", "https://picsum.photos/seed/b/800/600"],
        "location_name": "Место",
        "address": "Сургут, ул. Ленина, 1",
        "vibes": ["romance"],
        "price_text": "1000 ₽ на двоих",
        "price_value": 1000,
        "duration_text": "2 часа",
        "duration_minutes": 120,
        "time_of_day": TimeOfDay.evening,
    }
    data.update(kw)
    return data


async def make_scenario(session: AsyncSession, **kw: Any) -> Scenario:
    kw.setdefault("is_active", True)
    sc = Scenario(**scenario_data(**kw))
    session.add(sc)
    await session.flush()
    return sc


async def make_slot(
    session: AsyncSession,
    scenario: Scenario,
    starts_at: datetime,
    capacity: int = 2,
    booked: int = 0,
) -> Slot:
    slot = Slot(
        scenario_id=scenario.id, starts_at=starts_at, capacity=capacity, booked_count=booked
    )
    session.add(slot)
    await session.flush()
    return slot


async def make_booking(
    session: AsyncSession,
    user: User,
    slot: Slot,
    status: BookingStatus = BookingStatus.confirmed,
    **kw: Any,
) -> Booking:
    kw.setdefault("created_at", NOW - timedelta(days=1))
    b = Booking(user_id=user.id, slot_id=slot.id, status=status, name="Аня", contact="@anya", **kw)
    session.add(b)
    slot.booked_count += 1
    await session.flush()
    return b


@dataclass
class FakeNotifier:
    calls: list[tuple[str, Any]] = field(default_factory=list)
    result: bool = True

    async def booking_reminder(self, booking: Booking, hours: int) -> bool:
        self.calls.append(("reminder", (booking.id, hours)))
        return self.result

    async def feedback_request(self, booking: Booking) -> bool:
        self.calls.append(("feedback", booking.id))
        return self.result

    async def weekend_digest(
        self,
        tg_id: int,
        scenarios: list[Scenario],
        nearest: dict[int, datetime] | None = None,
    ) -> bool:
        self.calls.append(("digest", (tg_id, [s.id for s in scenarios])))
        self.calls.append(("digest_nearest", nearest))
        return self.result

    async def reactivation(self, tg_id: int) -> bool:
        self.calls.append(("reactivation", tg_id))
        return self.result

    async def overdue_booking_alert(self, booking: Booking) -> bool:
        self.calls.append(("overdue", booking.id))
        return self.result

    def of(self, kind: str) -> list[Any]:
        return [payload for k, payload in self.calls if k == kind]
