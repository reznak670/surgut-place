"""Каталог сценариев: поиск с фильтрами, сортировка, CRUD для админки.

Объём каталога в MVP — десятки сценариев, поэтому фильтр по вайбам (JSON-список)
и сортировка делаются в Python: так код одинаково работает на SQLite и Postgres.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Budget, Event, EventType, Scenario, Slot, TimeOfDay, User, Vibe
from app.services.errors import NotFound, ValidationError
from app.services.timeutil import day_window, now_utc, weekend_window

BudgetFilter = Literal["lt1000", "1000_2000", "2000_4000", "unlimited"]
DateFilter = Literal["today", "tomorrow", "weekend", "any"]
NeedBookingFilter = Literal["yes", "any"]

BUDGET_FILTERS: tuple[str, ...] = ("lt1000", "1000_2000", "2000_4000", "unlimited")
DATE_FILTERS: tuple[str, ...] = ("today", "tomorrow", "weekend", "any")

# Ориентир цены для сортировки «ближе к бюджету профиля»
_PROFILE_BUDGET_TARGET: dict[Budget, int] = {Budget.lt1000: 1000, Budget.lt2000: 2000}


@dataclass
class Filters:
    vibe: Vibe | None = None
    budget: BudgetFilter | None = None
    time_of_day: TimeOfDay | None = None
    date: DateFilter = "any"
    need_booking: NeedBookingFilter = "any"


def _price_ok(price: int, budget: str | None) -> bool:
    match budget:
        case None | "unlimited":
            return True
        case "lt1000":
            return price < 1000
        case "1000_2000":
            return 1000 <= price <= 2000
        case "2000_4000":
            return 2000 < price <= 4000
    raise ValidationError(f"Неизвестный фильтр бюджета: {budget}")


def date_window(date: str, now: datetime) -> tuple[datetime, datetime | None]:
    """Окно поиска слотов [start, end). end=None — без верхней границы."""
    match date:
        case "any":
            return now, None
        case "today":
            _, end = day_window(now)
            return now, end
        case "tomorrow":
            return day_window(now, 1)
        case "weekend":
            start, end = weekend_window(now)
            return max(start, now), end
    raise ValidationError(f"Неизвестный фильтр даты: {date}")


async def next_free_slots(
    session: AsyncSession, start: datetime, end: datetime | None
) -> dict[int, datetime]:
    """scenario_id → ближайший свободный слот в окне."""
    stmt = (
        select(Slot.scenario_id, func.min(Slot.starts_at))
        .where(Slot.starts_at >= start, Slot.booked_count < Slot.capacity)
        .group_by(Slot.scenario_id)
    )
    if end is not None:
        stmt = stmt.where(Slot.starts_at < end)
    rows = await session.execute(stmt)
    # func.min теряет тип колонки на SQLite → нормализуем вручную
    result: dict[int, datetime] = {}
    for scenario_id, starts_at in rows:
        result[scenario_id] = _as_aware(starts_at)
    return result


def _as_aware(value: Any) -> datetime:
    from datetime import UTC

    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def search(
    session: AsyncSession,
    user: User | None,
    filters: Filters,
    offset: int = 0,
    limit: int = 5,
    *,
    now: datetime | None = None,
    exclude_ids: list[int] | None = None,
) -> tuple[list[Scenario], bool]:
    """Возвращает (страница сценариев, есть ли ещё)."""
    now = now or now_utc()
    if filters.date not in DATE_FILTERS:
        raise ValidationError(f"Неизвестный фильтр даты: {filters.date}")
    if filters.budget is not None and filters.budget not in BUDGET_FILTERS:
        raise ValidationError(f"Неизвестный фильтр бюджета: {filters.budget}")

    stmt = select(Scenario).where(Scenario.is_active.is_(True))
    if filters.time_of_day is not None:
        stmt = stmt.where(Scenario.time_of_day == TimeOfDay(filters.time_of_day))
    if filters.need_booking == "yes":
        stmt = stmt.where(Scenario.need_booking.is_(True))
    if exclude_ids:
        stmt = stmt.where(Scenario.id.not_in(exclude_ids))
    candidates = list(await session.scalars(stmt))

    start, end = date_window(filters.date, now)
    next_slot = await next_free_slots(session, start, end)

    vibe = Vibe(filters.vibe).value if filters.vibe else None
    items: list[Scenario] = []
    for sc in candidates:
        if vibe and vibe not in (sc.vibes or []):
            continue
        if not _price_ok(sc.price_value, filters.budget):
            continue
        if filters.date != "any" and sc.need_booking and sc.id not in next_slot:
            continue
        items.append(sc)

    target = _PROFILE_BUDGET_TARGET.get(user.budget) if user and user.budget else None
    far_future = datetime.max.replace(tzinfo=start.tzinfo)

    def sort_key(sc: Scenario) -> tuple:
        slot_at = next_slot.get(sc.id)
        return (
            not sc.is_featured,
            slot_at is None,
            slot_at or far_future,
            abs(sc.price_value - target) if target is not None else 0,
            sc.id,
        )

    items.sort(key=sort_key)
    offset = max(offset, 0)
    page = items[offset : offset + limit]
    return page, len(items) > offset + limit


async def weekend(
    session: AsyncSession,
    user: User | None,
    offset: int = 0,
    limit: int = 5,
    *,
    now: datetime | None = None,
) -> tuple[list[Scenario], bool]:
    return await search(session, user, Filters(date="weekend"), offset, limit, now=now)


async def last_week_fallback(
    session: AsyncSession, limit: int = 5, *, now: datetime | None = None
) -> list[Scenario]:
    """Активные сценарии, у которых были слоты за последние 7 дней (для пустой выдачи)."""
    now = now or now_utc()
    sub = (
        select(Slot.scenario_id)
        .where(Slot.starts_at >= now - timedelta(days=7), Slot.starts_at < now)
        .distinct()
    )
    stmt = (
        select(Scenario)
        .where(Scenario.is_active.is_(True), Scenario.id.in_(sub))
        .order_by(Scenario.is_featured.desc(), Scenario.id)
        .limit(limit)
    )
    return list(await session.scalars(stmt))


async def get(session: AsyncSession, scenario_id: int, *, include_hidden: bool = False) -> Scenario:
    sc = await session.get(Scenario, scenario_id)
    if sc is None or (not sc.is_active and not include_hidden):
        raise NotFound(f"scenario {scenario_id}")
    return sc


async def register_view(
    session: AsyncSession, user: User | None, scenario_id: int, *, now: datetime | None = None
) -> None:
    session.add(
        Event(
            user_id=user.id if user else None,
            type=EventType.card_view.value,
            scenario_id=scenario_id,
            created_at=now or now_utc(),
        )
    )
    await session.flush()


async def recommend(
    session: AsyncSession,
    user: User | None,
    exclude_ids: list[int],
    limit: int = 2,
    *,
    now: datetime | None = None,
) -> list[Scenario]:
    """«2 идеи на следующие выходные»: сценарии со слотами на ближайших (если сейчас
    выходные — на следующих) выходных; если не хватает — добираем из общего каталога."""
    now = now or now_utc()
    start, _ = weekend_window(now, following=True)
    # search(date=weekend) от момента начала нужных выходных
    base_now = max(now, start - timedelta(microseconds=1))
    picked, _ = await search(
        session, user, Filters(date="weekend"), 0, limit, now=base_now, exclude_ids=exclude_ids
    )
    if len(picked) < limit:
        more, _ = await search(
            session,
            user,
            Filters(),
            0,
            limit - len(picked),
            now=now,
            exclude_ids=[*exclude_ids, *(s.id for s in picked)],
        )
        picked.extend(more)
    return picked


# ---------- админка ----------

_REQUIRED = (
    "title",
    "photos",
    "location_name",
    "address",
    "vibes",
    "price_text",
    "price_value",
    "duration_text",
    "duration_minutes",
    "time_of_day",
)
_OPTIONAL = (
    "map_link",
    "timing_text",
    "includes_text",
    "need_booking",
    "is_indoor",
    "is_featured",
    "is_active",
    "partner_id",
)
_ALLOWED = set(_REQUIRED) | set(_OPTIONAL)


def _validate(data: dict[str, Any], *, partial: bool) -> dict[str, Any]:
    unknown = set(data) - _ALLOWED
    if unknown:
        raise ValidationError(f"Неизвестные поля: {', '.join(sorted(unknown))}")
    if not partial:
        missing = [k for k in _REQUIRED if data.get(k) in (None, "", [])]
        if missing:
            raise ValidationError(f"Не заполнены поля: {', '.join(missing)}")
    clean: dict[str, Any] = {}
    for key, value in data.items():
        if key in ("title", "location_name", "address", "price_text", "duration_text"):
            if not isinstance(value, str) or not value.strip():
                raise ValidationError(f"Поле {key} не может быть пустым")
            value = value.strip()
        elif key == "photos":
            if not isinstance(value, list) or not all(isinstance(p, str) and p for p in value):
                raise ValidationError("Фото — список file_id/URL")
            if not 2 <= len(value) <= 5:
                raise ValidationError("Нужно от 2 до 5 фото")
            value = list(value)
        elif key == "vibes":
            if not isinstance(value, list) or not value:
                raise ValidationError("Укажите хотя бы один вайб")
            allowed = {v.value for v in Vibe}
            bad = [v for v in value if str(v) not in allowed]
            if bad:
                raise ValidationError(f"Неизвестные вайбы: {', '.join(map(str, bad))}")
            value = list(dict.fromkeys(str(v) for v in value))
        elif key in ("price_value", "duration_minutes"):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValidationError(f"Поле {key} должно быть целым числом")
            if value < 0 or (key == "duration_minutes" and value == 0):
                raise ValidationError(f"Поле {key} должно быть положительным")
        elif key == "time_of_day":
            try:
                value = TimeOfDay(value)
            except ValueError as e:
                raise ValidationError("Время суток: day, evening или night") from e
        elif key in ("need_booking", "is_indoor", "is_featured", "is_active"):
            value = bool(value)
        elif key in ("map_link", "timing_text", "includes_text"):
            value = value.strip() if isinstance(value, str) and value.strip() else None
        elif key == "partner_id" and value is not None and not isinstance(value, int):
            raise ValidationError("partner_id должен быть числом")
        clean[key] = value
    return clean


async def create(session: AsyncSession, data: dict[str, Any]) -> Scenario:
    """Новый сценарий по умолчанию скрыт (is_active=False), если явно не передано иное."""
    clean = _validate(data, partial=False)
    clean.setdefault("is_active", False)
    sc = Scenario(**clean)
    session.add(sc)
    await session.flush()
    await session.refresh(sc, attribute_names=["partner"])
    return sc


async def update(session: AsyncSession, scenario_id: int, data: dict[str, Any]) -> Scenario:
    sc = await get(session, scenario_id, include_hidden=True)
    clean = _validate(data, partial=True)
    for key, value in clean.items():
        setattr(sc, key, value)
    await session.flush()
    await session.refresh(sc, attribute_names=["partner"])
    return sc


async def set_active(session: AsyncSession, scenario_id: int, active: bool) -> Scenario:
    return await update(session, scenario_id, {"is_active": active})


async def set_featured(session: AsyncSession, scenario_id: int, featured: bool) -> Scenario:
    return await update(session, scenario_id, {"is_featured": featured})


async def list_all(
    session: AsyncSession, offset: int = 0, limit: int = 10
) -> tuple[list[Scenario], bool]:
    stmt = (
        select(Scenario)
        .order_by(Scenario.is_active.desc(), Scenario.id)
        .offset(max(offset, 0))
        .limit(limit + 1)
    )
    rows = list(await session.scalars(stmt))
    return rows[:limit], len(rows) > limit


async def list_for_partner(session: AsyncSession, partner: User) -> list[Scenario]:
    stmt = select(Scenario).where(Scenario.partner_id == partner.id).order_by(Scenario.id)
    return list(await session.scalars(stmt))
