"""Демо-данные для Сургута: `uv run python -m scripts.seed`.

Идемпотентно: сценарий ищется по title (обновляется, если есть), слот — по (scenario, starts_at).
Слоты создаются на ближайшие 2 выходных (сб + вс) по Asia/Yekaterinburg.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta
from typing import Any
from urllib.parse import quote_plus

from sqlalchemy import select

from app.config import settings
from app.db import make_engine, make_sessionmaker
from app.db.models import Scenario, Slot, TimeOfDay
from app.services.timeutil import now_utc, weekend_window


def photos(slug: str, n: int = 3) -> list[str]:
    return [f"https://picsum.photos/seed/{slug}-{i}/800/600" for i in range(1, n + 1)]


def map_link(address: str) -> str:
    return f"https://yandex.ru/maps/?text={quote_plus(address)}"


SCENARIOS: list[dict[str, Any]] = [
    {
        "slug": "obskoy-most",
        "title": "Закат у Обского моста и кофе с собой",
        "location_name": "Смотровая у Югорского моста",
        "address": "Сургут, Югорский тракт, смотровая площадка",
        "vibes": ["romance"],
        "price_text": "≈ 600 ₽ на двоих (кофе и плед)",
        "price_value": 600,
        "duration_text": "1,5 часа",
        "duration_minutes": 90,
        "time_of_day": TimeOfDay.evening,
        "timing_text": "За час до заката",
        "includes_text": "Маршрут, термос с какао, плед напрокат",
        "need_booking": False,
        "is_indoor": False,
        "is_featured": True,
        "hours": [],
        "n_photos": 3,
    },
    {
        "slug": "sibirskiy-uzhin",
        "title": "Ужин-дегустация северной кухни",
        "location_name": "Гастробар «Тайга»",
        "address": "Сургут, ул. Ленина, 40",
        "vibes": ["food", "romance"],
        "price_text": "3 500 ₽ на двоих",
        "price_value": 3500,
        "duration_text": "2 часа",
        "duration_minutes": 120,
        "time_of_day": TimeOfDay.evening,
        "timing_text": "Начало в 19:00",
        "includes_text": "6 подач: строганина, муксун, оленина, морс из брусники",
        "need_booking": True,
        "is_indoor": True,
        "hours": [19],
        "capacity": 4,
        "n_photos": 3,
    },
    {
        "slug": "goncharka",
        "title": "Гончарный мастер-класс для двоих",
        "location_name": "Мастерская «Глина»",
        "address": "Сургут, пр. Мира, 31",
        "vibes": ["romance", "unusual"],
        "price_text": "3 000 ₽ на двоих",
        "price_value": 3000,
        "duration_text": "2 часа",
        "duration_minutes": 120,
        "time_of_day": TimeOfDay.day,
        "timing_text": "Сеансы в 12:00 и 15:00",
        "includes_text": "Глина, глазурь, обжиг; кружки заберёте через неделю",
        "need_booking": True,
        "is_indoor": True,
        "hours": [12, 15],
        "capacity": 3,
        "n_photos": 2,
    },
    {
        "slug": "katok-saima",
        "title": "Каток и глинтвейн в парке «За Саймой»",
        "location_name": "Парк «За Саймой»",
        "address": "Сургут, парк За Саймой",
        "vibes": ["active", "romance"],
        "price_text": "800 ₽ на двоих (прокат коньков)",
        "price_value": 800,
        "duration_text": "1,5 часа",
        "duration_minutes": 90,
        "time_of_day": TimeOfDay.evening,
        "timing_text": "С 17:00 до 21:00",
        "includes_text": "Прокат коньков, безалкогольный глинтвейн",
        "need_booking": True,
        "is_indoor": False,
        "hours": [17, 19],
        "capacity": 6,
        "n_photos": 3,
    },
    {
        "slug": "hudmuzey",
        "title": "Экскурсия по художественному музею",
        "location_name": "Сургутский художественный музей",
        "address": "Сургут, ул. Просвещения, 7",
        "vibes": ["culture"],
        "price_text": "1 200 ₽ на двоих",
        "price_value": 1200,
        "duration_text": "1 час",
        "duration_minutes": 60,
        "time_of_day": TimeOfDay.day,
        "timing_text": "Сбор в 13:00",
        "includes_text": "Экскурсовод, билеты, открытка на память",
        "need_booking": True,
        "is_indoor": True,
        "hours": [13],
        "capacity": 5,
        "n_photos": 2,
    },
    {
        "slug": "kvest-neft",
        "title": "Квест-комната «Нефтяная лихорадка»",
        "location_name": "Квест-центр «Ключ»",
        "address": "Сургут, ул. 30 лет Победы, 44",
        "vibes": ["unusual", "active"],
        "price_text": "4 000 ₽ на двоих",
        "price_value": 4000,
        "duration_text": "1 час",
        "duration_minutes": 60,
        "time_of_day": TimeOfDay.evening,
        "timing_text": "Сеансы каждые 1,5 часа",
        "includes_text": "Игра 60 минут, чай после",
        "need_booking": True,
        "is_indoor": True,
        "hours": [18, 20],
        "capacity": 1,
        "n_photos": 3,
    },
    {
        "slug": "nochnoy-streetfood",
        "title": "Ночной стрит-фуд тур по центру",
        "location_name": "Старт у ТРЦ «Аура»",
        "address": "Сургут, Нефтеюганское ш., 1",
        "vibes": ["food", "unusual"],
        "price_text": "1 500 ₽ на двоих",
        "price_value": 1500,
        "duration_text": "2,5 часа",
        "duration_minutes": 150,
        "time_of_day": TimeOfDay.night,
        "timing_text": "Старт в 21:30",
        "includes_text": "4 точки, дегустации, гид",
        "need_booking": True,
        "is_indoor": False,
        "hours": [21],
        "capacity": 4,
        "n_photos": 2,
    },
    {
        "slug": "skalodrom",
        "title": "Скалодром для новичков",
        "location_name": "Скалодром «Вертикаль»",
        "address": "Сургут, ул. Университетская, 21",
        "vibes": ["active"],
        "price_text": "1 800 ₽ на двоих",
        "price_value": 1800,
        "duration_text": "1,5 часа",
        "duration_minutes": 90,
        "time_of_day": TimeOfDay.day,
        "timing_text": "Сеансы в 11:00 и 14:00",
        "includes_text": "Инструктаж, страховка, скальники",
        "need_booking": True,
        "is_indoor": True,
        "hours": [11, 14],
        "capacity": 2,
        "n_photos": 2,
    },
]


def weekend_days(now: datetime) -> list:
    """Даты сб/вс ближайших двух выходных (локально), только не прошедшие."""
    start, _ = weekend_window(now)
    sat = start.astimezone(settings.tz).date()
    return [sat + timedelta(days=d) for d in (0, 1, 7, 8)]


async def seed() -> None:
    engine = make_engine(settings.DATABASE_URL)
    sm = make_sessionmaker(engine)
    now = now_utc()
    created_sc = updated_sc = created_slots = 0
    async with sm() as session:
        for raw in SCENARIOS:
            item = dict(raw)
            slug = item.pop("slug")
            hours = item.pop("hours")
            capacity = item.pop("capacity", 2)
            item["photos"] = photos(slug, item.pop("n_photos"))
            item["map_link"] = map_link(item["address"])
            item.setdefault("is_featured", False)
            item["is_active"] = True

            sc = await session.scalar(select(Scenario).where(Scenario.title == item["title"]))
            if sc is None:
                sc = Scenario(**item)
                session.add(sc)
                created_sc += 1
            else:
                for k, v in item.items():
                    setattr(sc, k, v)
                updated_sc += 1
            await session.flush()

            for day in weekend_days(now):
                for hour in hours:
                    local = datetime.combine(day, time(hour), tzinfo=settings.tz)
                    if local <= now:
                        continue
                    exists = await session.scalar(
                        select(Slot.id).where(Slot.scenario_id == sc.id, Slot.starts_at == local)
                    )
                    if exists:
                        continue
                    session.add(Slot(scenario_id=sc.id, starts_at=local, capacity=capacity))
                    created_slots += 1
        await session.commit()
    await engine.dispose()
    print(
        f"seed: сценариев создано {created_sc}, обновлено {updated_sc}; "
        f"новых слотов {created_slots}"
    )


if __name__ == "__main__":
    asyncio.run(seed())
