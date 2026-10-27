from datetime import UTC, datetime, timedelta

import pytest

from app.db.models import Budget, Role, TimeOfDay, Vibe
from app.services import scenarios
from app.services.errors import NotFound, ValidationError
from app.services.scenarios import Filters
from app.services.timeutil import weekend_window
from tests.backend.factories import (
    NOW,
    SAT,
    make_scenario,
    make_slot,
    make_user,
    scenario_data,
)

YEKT_OFFSET = timedelta(hours=5)


def local(y, m, d, h=12):
    return datetime(y, m, d, h, tzinfo=UTC) - YEKT_OFFSET


@pytest.mark.parametrize(
    ("now", "expected_sat"),
    [
        (local(2026, 10, 28), (2026, 10, 31)),  # ср → ближайшие
        (local(2026, 10, 30, 0) + timedelta(minutes=1), (2026, 10, 31)),  # пт 00:01
        (local(2026, 10, 29, 23), (2026, 10, 31)),  # чт поздно вечером
        (local(2026, 10, 31, 10), (2026, 10, 31)),  # сб → текущие
        (local(2026, 11, 1, 23), (2026, 10, 31)),  # вс 23:00 → текущие
        (local(2026, 11, 2, 0) + timedelta(minutes=1), (2026, 11, 7)),  # пн → следующие
    ],
)
def test_weekend_window(now, expected_sat):
    start, end = weekend_window(now)
    assert start == local(*expected_sat, 0)
    assert end - start == timedelta(days=2)


def test_weekend_window_following_on_saturday():
    start, _ = weekend_window(local(2026, 10, 31, 10), following=True)
    assert start == local(2026, 11, 7, 0)


async def test_search_filters(session):
    user = await make_user(session)
    romance_cheap = await make_scenario(
        session, title="A", vibes=["romance"], price_value=500, time_of_day=TimeOfDay.day
    )
    food_mid = await make_scenario(session, title="B", vibes=["food", "romance"], price_value=1500)
    active_exp = await make_scenario(session, title="C", vibes=["active"], price_value=3500)
    hidden = await make_scenario(session, title="H", is_active=False)
    for sc in (romance_cheap, food_mid, active_exp, hidden):
        await make_slot(session, sc, SAT + timedelta(hours=12))

    ids = lambda r: {s.id for s in r[0]}  # noqa: E731

    assert ids(await scenarios.search(session, user, Filters(), now=NOW)) == {
        romance_cheap.id,
        food_mid.id,
        active_exp.id,
    }
    r = await scenarios.search(session, user, Filters(vibe=Vibe.romance), now=NOW)
    assert ids(r) == {romance_cheap.id, food_mid.id}
    r = await scenarios.search(session, user, Filters(budget="lt1000"), now=NOW)
    assert ids(r) == {romance_cheap.id}
    r = await scenarios.search(session, user, Filters(budget="1000_2000"), now=NOW)
    assert ids(r) == {food_mid.id}
    r = await scenarios.search(session, user, Filters(budget="2000_4000"), now=NOW)
    assert ids(r) == {active_exp.id}
    r = await scenarios.search(session, user, Filters(time_of_day=TimeOfDay.day), now=NOW)
    assert ids(r) == {romance_cheap.id}
    with pytest.raises(ValidationError):
        await scenarios.search(session, user, Filters(budget="cheap"), now=NOW)  # type: ignore[arg-type]


async def test_search_date_windows(session):
    user = await make_user(session)
    today = await make_scenario(session, title="today")
    await make_slot(session, today, NOW + timedelta(hours=3))
    tomorrow = await make_scenario(session, title="tomorrow")
    await make_slot(session, tomorrow, NOW + timedelta(days=1))
    weekend = await make_scenario(session, title="weekend")
    await make_slot(session, weekend, SAT + timedelta(hours=10))
    full_weekend = await make_scenario(session, title="full")
    await make_slot(session, full_weekend, SAT + timedelta(hours=10), capacity=1, booked=1)
    no_booking = await make_scenario(session, title="walk", need_booking=False)

    async def ids(date, **kw):
        r, _ = await scenarios.search(session, user, Filters(date=date, **kw), now=NOW)
        return {s.id for s in r}

    assert await ids("today") == {today.id, no_booking.id}
    assert await ids("tomorrow") == {tomorrow.id, no_booking.id}
    assert await ids("weekend") == {weekend.id, no_booking.id}
    assert await ids("weekend", need_booking="yes") == {weekend.id}
    assert full_weekend.id in await ids("any")


async def test_sorting_featured_then_slot_then_budget(session):
    user = await make_user(session, budget=Budget.lt1000)
    late = await make_scenario(session, title="late", price_value=900)
    await make_slot(session, late, SAT + timedelta(days=1))
    early = await make_scenario(session, title="early", price_value=5000)
    await make_slot(session, early, SAT + timedelta(hours=1))
    featured = await make_scenario(session, title="feat", is_featured=True, price_value=9000)
    await make_slot(session, featured, SAT + timedelta(days=1, hours=5))
    # одинаковое время слота — решает близость к бюджету (~1000)
    same_a = await make_scenario(session, title="same_far", price_value=3000)
    same_b = await make_scenario(session, title="same_near", price_value=1100)
    for sc in (same_a, same_b):
        await make_slot(session, sc, SAT + timedelta(hours=5))
    no_slot = await make_scenario(session, title="walk", need_booking=False, price_value=1000)

    result, _ = await scenarios.search(session, user, Filters(), limit=10, now=NOW)
    assert [s.title for s in result] == [
        "feat",
        "early",
        "same_near",
        "same_far",
        "late",
        "walk",
    ]
    assert result[-1].id == no_slot.id

    # без бюджета профиля порядок при равном слоте — по id
    user.budget = None
    result, _ = await scenarios.search(session, user, Filters(), limit=10, now=NOW)
    titles = [s.title for s in result]
    assert titles.index("same_far") < titles.index("same_near")


async def test_pagination_has_more(session):
    user = await make_user(session)
    for i in range(7):
        sc = await make_scenario(session, title=f"s{i}")
        await make_slot(session, sc, SAT + timedelta(hours=i))
    page1, more1 = await scenarios.search(session, user, Filters(), 0, 5, now=NOW)
    page2, more2 = await scenarios.search(session, user, Filters(), 5, 5, now=NOW)
    assert len(page1) == 5 and more1 is True
    assert len(page2) == 2 and more2 is False
    assert {s.id for s in page1}.isdisjoint({s.id for s in page2})
    _, exact = await scenarios.search(session, user, Filters(), 2, 5, now=NOW)
    assert exact is False


async def test_weekend_shortcut_and_fallback(session):
    user = await make_user(session)
    sc = await make_scenario(session)
    await make_slot(session, sc, SAT + timedelta(hours=10))
    old = await make_scenario(session, title="old")
    await make_slot(session, old, NOW - timedelta(days=3))
    items, more = await scenarios.weekend(session, user, now=NOW)
    assert [s.id for s in items] == [sc.id] and more is False
    fallback = await scenarios.last_week_fallback(session, now=NOW)
    assert [s.id for s in fallback] == [old.id]


async def test_recommend_excludes_and_tops_up(session):
    user = await make_user(session)
    a = await make_scenario(session, title="a")
    await make_slot(session, a, SAT + timedelta(hours=10))
    b = await make_scenario(session, title="b")
    await make_slot(session, b, SAT + timedelta(hours=11))
    c = await make_scenario(session, title="c")  # слот только через 2 недели
    await make_slot(session, c, SAT + timedelta(days=14))
    rec = await scenarios.recommend(session, user, exclude_ids=[a.id], now=NOW)
    assert [s.id for s in rec] == [b.id, c.id]


async def test_get_and_register_view(session):
    user = await make_user(session)
    sc = await make_scenario(session)
    hidden = await make_scenario(session, is_active=False)
    assert (await scenarios.get(session, sc.id)).id == sc.id
    with pytest.raises(NotFound):
        await scenarios.get(session, hidden.id)
    assert (await scenarios.get(session, hidden.id, include_hidden=True)).id == hidden.id
    await scenarios.register_view(session, user, sc.id, now=NOW)


async def test_create_update_validation(session):
    sc = await scenarios.create(session, scenario_data())
    assert sc.is_active is False  # новый сценарий скрыт по умолчанию
    sc2 = await scenarios.create(session, scenario_data(is_active=True))
    assert sc2.is_active is True

    with pytest.raises(ValidationError):
        await scenarios.create(session, scenario_data(photos=["one"]))
    with pytest.raises(ValidationError):
        await scenarios.create(session, scenario_data(photos=[f"p{i}" for i in range(6)]))
    with pytest.raises(ValidationError):
        await scenarios.create(session, scenario_data(vibes=["party"]))
    with pytest.raises(ValidationError):
        await scenarios.create(session, scenario_data(title=""))
    with pytest.raises(ValidationError):
        await scenarios.create(session, scenario_data(unknown=1))
    data = scenario_data()
    del data["address"]
    with pytest.raises(ValidationError):
        await scenarios.create(session, data)

    sc = await scenarios.update(session, sc.id, {"title": "Новое", "price_value": 0})
    assert sc.title == "Новое" and sc.price_value == 0
    sc = await scenarios.set_active(session, sc.id, True)
    sc = await scenarios.set_featured(session, sc.id, True)
    assert sc.is_active and sc.is_featured


async def test_list_all_and_partner(session):
    partner = await make_user(session, Role.partner)
    await make_scenario(session, partner_id=partner.id)
    await make_scenario(session, is_active=False)
    await make_scenario(session)
    page, more = await scenarios.list_all(session, 0, 2)
    assert len(page) == 2 and more is True
    page, more = await scenarios.list_all(session, 2, 2)
    assert len(page) == 1 and more is False
    mine = await scenarios.list_for_partner(session, partner)
    assert len(mine) == 1 and mine[0].partner.id == partner.id
