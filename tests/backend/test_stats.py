from datetime import timedelta

from app.db.models import BookingStatus, Feedback
from app.services import scenarios, stats
from tests.backend.factories import NOW, SAT, make_booking, make_scenario, make_slot, make_user


async def test_collect(session):
    u1, u2, u3, u4 = [await make_user(session) for _ in range(4)]
    popular = await make_scenario(session, title="Популярный", is_indoor=True)
    other = await make_scenario(session, title="Другой", is_indoor=True)
    await make_scenario(session, title="Скрытый", is_indoor=True, is_active=False)

    # 4 уникальных зрителя, 5 просмотров: 4 у popular (80%), 1 у other
    for u in (u1, u2, u3, u4):
        await scenarios.register_view(session, u, popular.id, now=NOW - timedelta(days=1))
    await scenarios.register_view(session, u1, other.id, now=NOW - timedelta(days=1))
    # старый просмотр вне окна
    await scenarios.register_view(session, u1, other.id, now=NOW - timedelta(days=100))

    past1 = await make_slot(session, popular, NOW - timedelta(days=3), capacity=10)
    past2 = await make_slot(session, popular, NOW - timedelta(days=2), capacity=10)
    await make_slot(session, other, SAT)  # слот на неделе → active_scenarios_week

    b1 = await make_booking(session, u1, past1, attended=True)
    await make_booking(session, u1, past2, attended=False)  # u1 — повторная бронь
    await make_booking(session, u2, past1, attended=True)
    await make_booking(session, u3, past1, BookingStatus.cancelled)
    session.add(Feedback(booking_id=b1.id, rating=4, created_at=NOW, updated_at=NOW))
    await session.flush()

    s = await stats.collect(session, days=56, now=NOW)
    assert s.views == 5
    assert s.unique_viewers == 4
    assert s.bookings == 4
    assert s.conversion == 1.0
    assert s.attendance_rate == 2 / 3
    assert s.repeat_rate == 1 / 2  # u1 (2 confirmed) из u1,u2
    assert s.avg_rating == 4.0
    assert s.active_scenarios_week == 1
    assert s.top_view_share[0] == ("Популярный", 0.8)
    assert s.top_view_alert is True
    assert s.indoor_active_count == 2
    assert s.indoor_warning is True


async def test_collect_empty(session):
    s = await stats.collect(session, now=NOW)
    assert s.views == 0 and s.conversion is None and s.avg_rating is None
    assert s.top_view_share == [] and s.top_view_alert is False
