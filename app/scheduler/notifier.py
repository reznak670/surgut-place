"""Интерфейс между планировщиком и ботом. Реализация — app/bot/notify.py: TelegramNotifier."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from app.db.models import Booking, Scenario


class Notifier(Protocol):
    async def booking_reminder(self, booking: Booking, hours: int) -> bool:
        """hours ∈ {24, 3}. True — доставлено."""
        ...

    async def feedback_request(self, booking: Booking) -> bool: ...

    async def weekend_digest(
        self,
        tg_id: int,
        scenarios: list[Scenario],
        nearest: dict[int, datetime] | None = None,
    ) -> bool:
        """nearest: scenario_id → starts_at (aware UTC) ближайшего свободного слота на
        выходных; сценариев без слотов (need_booking=False) в словаре нет."""
        ...

    async def reactivation(self, tg_id: int) -> bool: ...

    async def overdue_booking_alert(self, booking: Booking) -> bool:
        """Алерт в ADMIN_CHAT_ID: заявка висит в pending > 2 ч."""
        ...
