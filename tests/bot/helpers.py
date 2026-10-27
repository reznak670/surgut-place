"""Построение объектов моделей без БД (для чистых функций formatting/keyboards)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.db.models import Booking, BookingStatus, Role, Scenario, Slot, TimeOfDay, User
from tests.backend.factories import scenario_data

# Пт 30.10.2026 19:00 по Екатеринбургу (UTC+5)
FRI_19 = datetime(2026, 10, 30, 14, 0, tzinfo=UTC)


def sc(**kw: Any) -> Scenario:
    """Транзиентный Scenario: дефолты колонок не применяются без flush — задаём явно."""
    kw.setdefault("id", 42)
    kw.setdefault("need_booking", True)
    kw.setdefault("is_indoor", False)
    kw.setdefault("is_featured", False)
    kw.setdefault("is_active", True)
    kw.setdefault("map_link", "https://yandex.ru/maps/-/CDabc123")
    kw.setdefault("timing_text", "19:00 — встреча\n19:30 — ужин")
    kw.setdefault("includes_text", "ужин на двоих, глинтвейн")
    kw.setdefault("time_of_day", TimeOfDay.evening)
    s = Scenario(**scenario_data(**kw))
    s.partner = kw.get("partner")
    return s


def slot(
    scenario: Scenario, starts_at: datetime = FRI_19, capacity: int = 4, booked: int = 0
) -> Slot:
    s = Slot(
        id=7, scenario_id=scenario.id, starts_at=starts_at, capacity=capacity, booked_count=booked
    )
    s.scenario = scenario
    return s


def user(role: Role = Role.user, **kw: Any) -> User:
    kw.setdefault("id", 1)
    kw.setdefault("tg_id", 100)
    kw.setdefault("first_name", "Аня")
    return User(role=role, **kw)


def booking(scenario: Scenario | None = None, **kw: Any) -> Booking:
    scenario = scenario or sc()
    s = slot(scenario, kw.pop("starts_at", FRI_19 + timedelta(days=1)))
    b = Booking(
        id=kw.pop("id", 5),
        user_id=1,
        slot_id=s.id,
        status=kw.pop("status", BookingStatus.pending),
        name=kw.pop("name", "Аня"),
        contact=kw.pop("contact", "+79123456789"),
    )
    b.slot = s
    b.user = kw.pop("user", user())
    return b
