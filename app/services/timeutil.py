"""Временные окна в локальной TZ (Asia/Yekaterinburg). Все границы возвращаются в UTC."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.config import settings


def now_utc() -> datetime:
    return datetime.now(UTC)


def ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("naive datetime is not allowed")
    return dt.astimezone(UTC)


def _local_midnight(day: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, time.min, tzinfo=tz)


def day_window(
    now: datetime, days_ahead: int = 0, tz: ZoneInfo | None = None
) -> tuple[datetime, datetime]:
    """[начало дня, конец дня) для сегодня (+days_ahead) по локальному времени."""
    tz = tz or settings.tz
    local_day = now.astimezone(tz).date() + timedelta(days=days_ahead)
    start = _local_midnight(local_day, tz)
    end = _local_midnight(local_day + timedelta(days=1), tz)
    return start.astimezone(UTC), end.astimezone(UTC)


def weekend_window(
    now: datetime, tz: ZoneInfo | None = None, *, following: bool = False
) -> tuple[datetime, datetime]:
    """Окно [сб 00:00, пн 00:00) ближайших выходных по локальному времени.

    Пн–пт → ближайшие сб–вс; сб/вс → текущие выходные.
    following=True → выходные после текущих/ближайших (для «идеи на следующие выходные»,
    если сейчас уже выходные).
    """
    tz = tz or settings.tz
    local = now.astimezone(tz)
    weekday = local.weekday()  # пн=0 … сб=5, вс=6
    if weekday >= 5:
        saturday = local.date() - timedelta(days=weekday - 5)
        if following:
            saturday += timedelta(days=7)
    else:
        saturday = local.date() + timedelta(days=5 - weekday)
    start = _local_midnight(saturday, tz)
    end = _local_midnight(saturday + timedelta(days=2), tz)
    return start.astimezone(UTC), end.astimezone(UTC)
