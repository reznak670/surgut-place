"""Фоновые задачи. Каждая функция джоба: (sessionmaker, notifier, now=None) -> int (сколько
уведомлений отправлено), поэтому их легко вызывать в тестах с фиксированным `now`.

Флаг «отправлено» ставится и при False от notifier (юзер заблокировал бота) — чтобы
не долбить его каждые 5 минут. Если notifier бросил исключение — флаг не ставим,
повторим на следующем тике.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.db.models import Booking, BookingStatus, Role, Slot, User
from app.scheduler.notifier import Notifier
from app.services import scenarios
from app.services.timeutil import now_utc

log = logging.getLogger(__name__)

SM = async_sessionmaker[AsyncSession]
DIGEST_LIMIT = 5
ACTIVE_DAYS = 30
OVERDUE_AFTER = timedelta(hours=2)
LATE_24H = timedelta(hours=23)


async def _safe(coro: Awaitable[bool], job: str, **extra: object) -> bool | None:
    """True/False — ответ notifier, None — исключение (не помечаем, повторим)."""
    try:
        return bool(await coro)
    except Exception:
        log.exception("notifier failed", extra={"job": job, **extra})
        return None


async def send_reminders(sm: SM, notifier: Notifier, now: datetime | None = None) -> int:
    """Напоминания за 24 ч (окно [start-24h, start-3h)) и за 3 ч (окно [start-3h, start))."""
    now = now or now_utc()
    sent = 0
    async with sm() as session:
        rows = await session.scalars(
            select(Booking)
            .join(Slot, Slot.id == Booking.slot_id)
            .where(
                Booking.status == BookingStatus.confirmed,
                Slot.starts_at > now,
                Slot.starts_at <= now + timedelta(hours=24),
            )
            .order_by(Slot.starts_at)
        )
        for booking in list(rows):
            left = booking.slot.starts_at - now
            if left <= timedelta(hours=3):
                if booking.reminded_3h_at is not None:
                    continue
                hours = 3
            else:
                if booking.reminded_24h_at is not None:
                    continue
                if left < LATE_24H:
                    # подтвердили меньше чем за 23 ч — «завтра» уже неактуально,
                    # молча ставим флаг и ждём напоминания за 3 ч
                    booking.reminded_24h_at = now
                    await session.commit()
                    continue
                hours = 24
            ok = await _safe(
                notifier.booking_reminder(booking, hours), "reminder", booking_id=booking.id
            )
            if ok is None:
                continue
            if hours == 3:
                booking.reminded_3h_at = now
                booking.reminded_24h_at = booking.reminded_24h_at or now
            else:
                booking.reminded_24h_at = now
            await session.commit()
            sent += int(ok)
    return sent


async def request_feedback(sm: SM, notifier: Notifier, now: datetime | None = None) -> int:
    """Запрос отзыва через 2 ч после окончания (start + duration + 2h <= now), не старше 3 дней."""
    now = now or now_utc()
    sent = 0
    async with sm() as session:
        rows = await session.scalars(
            select(Booking)
            .join(Slot, Slot.id == Booking.slot_id)
            .where(
                Booking.status == BookingStatus.confirmed,
                Booking.feedback_requested_at.is_(None),
                Slot.starts_at <= now - timedelta(hours=2),
                Slot.starts_at >= now - timedelta(days=4),
            )
        )
        for booking in list(rows):
            if booking.attended is False:  # партнёр отметил неявку
                continue
            ends_at = booking.slot.starts_at + timedelta(
                minutes=booking.slot.scenario.duration_minutes
            )
            if ends_at + timedelta(hours=2) > now or ends_at < now - timedelta(days=3):
                continue
            ok = await _safe(notifier.feedback_request(booking), "feedback", booking_id=booking.id)
            if ok is None:
                continue
            booking.feedback_requested_at = now
            await session.commit()
            sent += int(ok)
    return sent


async def send_overdue_alerts(sm: SM, notifier: Notifier, now: datetime | None = None) -> int:
    """Один алерт админу на каждую заявку, висящую в pending дольше 2 часов."""
    now = now or now_utc()
    sent = 0
    async with sm() as session:
        rows = await session.scalars(
            select(Booking)
            .join(Slot, Slot.id == Booking.slot_id)
            .where(
                Booking.status == BookingStatus.pending,
                Booking.overdue_alerted_at.is_(None),
                Booking.created_at <= now - OVERDUE_AFTER,
                Slot.starts_at > now,  # слот уже начался — алерт бессмысленен
            )
            .order_by(Booking.created_at)
        )
        for booking in list(rows):
            ok = await _safe(
                notifier.overdue_booking_alert(booking), "overdue", booking_id=booking.id
            )
            if ok is None:
                continue
            booking.overdue_alerted_at = now
            await session.commit()
            sent += int(ok)
    return sent


async def send_weekend_digest(
    sm: SM, notifier: Notifier, now: datetime | None = None, *, delay: float = 0.05
) -> int:
    """Чт 18:00: до 5 сценариев выходных активным за 30 дней (role=user).
    Если сценариев нет — никому ничего не шлём."""
    now = now or now_utc()
    sent = 0
    async with sm() as session:
        any_items, _ = await scenarios.weekend(session, None, limit=1, now=now)
        if not any_items:
            log.info("digest skipped: no weekend scenarios")
            return 0
        start, end = scenarios.date_window("weekend", now)
        nearest_all = await scenarios.next_free_slots(session, start, end)
        users = list(
            await session.scalars(
                select(User).where(
                    User.role == Role.user,
                    User.privacy_accepted_at.is_not(None),
                    User.last_active_at >= now - timedelta(days=ACTIVE_DAYS),
                )
            )
        )
        for user in users:
            items, _ = await scenarios.weekend(session, user, limit=DIGEST_LIMIT, now=now)
            if not items:
                continue
            nearest = {s.id: nearest_all[s.id] for s in items if s.id in nearest_all}
            ok = await _safe(
                notifier.weekend_digest(user.tg_id, items, nearest), "digest", user_id=user.id
            )
            sent += int(bool(ok))
            if delay:
                await asyncio.sleep(delay)
    return sent


async def send_reactivation(
    sm: SM, notifier: Notifier, now: datetime | None = None, *, delay: float = 0.05
) -> int:
    """Ежедневно 12:00: один раз пишем тем, кто не заходил 30+ дней."""
    now = now or now_utc()
    sent = 0
    async with sm() as session:
        users = list(
            await session.scalars(
                select(User).where(
                    User.role == Role.user,
                    User.privacy_accepted_at.is_not(None),
                    User.last_active_at < now - timedelta(days=ACTIVE_DAYS),
                    User.reactivation_sent_at.is_(None),
                )
            )
        )
        for user in users:
            ok = await _safe(notifier.reactivation(user.tg_id), "reactivation", user_id=user.id)
            if ok is None:
                continue
            user.reactivation_sent_at = now
            await session.commit()
            sent += int(ok)
            if delay:
                await asyncio.sleep(delay)
    return sent


def setup_scheduler(sm: SM, notifier: Notifier) -> AsyncIOScheduler:
    """Создаёт (но не запускает) планировщик. main.py вызывает .start()."""
    tz = settings.tz
    scheduler = AsyncIOScheduler(
        timezone=tz,
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300},
    )
    args = (sm, notifier)
    scheduler.add_job(send_reminders, IntervalTrigger(minutes=5), args=args, id="reminders")
    scheduler.add_job(request_feedback, IntervalTrigger(minutes=10), args=args, id="feedback")
    scheduler.add_job(send_overdue_alerts, IntervalTrigger(minutes=10), args=args, id="overdue")
    scheduler.add_job(
        send_weekend_digest,
        CronTrigger(day_of_week="thu", hour=18, minute=0, timezone=tz),
        args=args,
        id="weekend_digest",
    )
    scheduler.add_job(
        send_reactivation,
        CronTrigger(hour=12, minute=0, timezone=tz),
        args=args,
        id="reactivation",
    )
    return scheduler
