"""Правки по ревью, круг 1."""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.config import settings
from app.db.models import Booking, BookingStatus, Role, Slot
from app.middlewares import UserMiddleware
from app.services import bookings, broadcasts, feedback, scenarios, slots, users
from app.services.errors import InvalidState, NotFound, ValidationError
from tests.backend.factories import NOW, SAT, make_booking, make_scenario, make_slot, make_user

# ---------- 1. revoke_partner / partner_chat_id ----------


async def test_revoke_partner_unlinks_scenarios(session, monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_CHAT_ID", -1)
    partner = await make_user(session, Role.partner)
    other = await make_user(session, Role.partner)
    sc1 = await make_scenario(session, partner_id=partner.id)
    sc2 = await make_scenario(session, partner_id=partner.id)
    foreign = await make_scenario(session, partner_id=other.id)
    slot = await make_slot(session, sc1, SAT)
    user = await make_user(session)
    b = await bookings.create(session, user, slot.id, "Аня", "@anya", now=NOW)
    assert bookings.partner_chat_id(b) == partner.tg_id

    revoked = await users.revoke_partner(session, partner.tg_id)
    assert revoked.role == Role.user
    for sc_id in (sc1.id, sc2.id):
        assert (await scenarios.get(session, sc_id)).partner_id is None
    assert (await scenarios.get(session, foreign.id)).partner_id == other.id
    b = await bookings.get(session, b.id)
    assert bookings.partner_chat_id(b) == -1
    with pytest.raises(NotFound):
        await users.revoke_partner(session, 424242)


async def test_partner_chat_id_ignores_non_partner_role(session, monkeypatch):
    """Роль сняли через set_role, а partner_id остался — контакт всё равно уходит админам."""
    monkeypatch.setattr(settings, "ADMIN_CHAT_ID", -1)
    ex_partner = await make_user(session, Role.partner)
    sc = await make_scenario(session, partner_id=ex_partner.id)
    slot = await make_slot(session, sc, SAT)
    b = await bookings.create(session, await make_user(session), slot.id, "Аня", "@a1", now=NOW)
    await users.set_role(session, ex_partner.tg_id, Role.user)
    b = await bookings.get(session, b.id)
    assert bookings.partner_chat_id(b) == -1


# ---------- 3. touch не чаще раза в 10 минут ----------


async def test_touch_min_interval(session):
    u = await users.touch(session, 1, "a", "А", now=NOW)
    later = NOW + timedelta(minutes=5)
    u = await users.touch(session, 1, "a", "А", now=later, min_interval=timedelta(minutes=10))
    assert u.last_active_at == NOW  # не обновили
    assert not session.dirty
    u = await users.touch(session, 1, "b", "А", now=later, min_interval=timedelta(minutes=10))
    assert u.username == "b" and u.last_active_at == later  # сменилось имя → пишем
    much_later = later + timedelta(minutes=11)
    u = await users.touch(session, 1, "b", "А", now=much_later, min_interval=timedelta(minutes=10))
    assert u.last_active_at == much_later


async def test_user_middleware_short_session(sessionmaker):
    seen = {}

    async def handler(event, data):
        seen["user"] = data["user"]
        seen["session"] = data["session"]
        return "ok"

    mw = UserMiddleware(admin_ids=[], sessionmaker=sessionmaker)
    tg = SimpleNamespace(id=777, username="anya", first_name="Аня", is_bot=False)
    async with sessionmaker() as handler_session:
        data = {"event_from_user": tg, "session": handler_session}
        assert await mw(handler, object(), data) == "ok"
        # пользователь закоммичен короткой сессией, хотя сессия хендлера не коммитилась
        assert not handler_session.new and not handler_session.dirty
        assert seen["user"] in handler_session  # объект привязан к сессии хендлера
    async with sessionmaker() as s:
        assert (await users.get_by_tg_id(s, 777)) is not None


# ---------- 4. гонки статусов ----------


async def _two_sessions_with_pending(sessionmaker, capacity=1):
    async with sessionmaker() as s:
        admin = await make_user(s, Role.admin)
        user = await make_user(s)
        sc = await make_scenario(s)
        slot = await make_slot(s, sc, SAT, capacity=capacity)
        b = await bookings.create(s, user, slot.id, "Аня", "@anya", now=NOW)
        await s.commit()
    return admin, user, slot, b


async def _booked(sessionmaker, slot_id):
    async with sessionmaker() as s:
        return (await s.get(Slot, slot_id)).booked_count


async def test_confirm_vs_decline_race(sessionmaker):
    admin, _, slot, b = await _two_sessions_with_pending(sessionmaker)
    s1, s2 = sessionmaker(), sessionmaker()
    try:
        # обе сессии видят pending
        assert (await s1.get(Booking, b.id)).status == BookingStatus.pending
        assert (await s2.get(Booking, b.id)).status == BookingStatus.pending
        await bookings.confirm(s1, b.id, admin, now=NOW)
        await s1.commit()
        with pytest.raises(InvalidState):
            await bookings.decline(s2, b.id, admin, now=NOW)
        await s2.rollback()
    finally:
        await s1.close()
        await s2.close()
    assert await _booked(sessionmaker, slot.id) == 1  # место не освободилось
    async with sessionmaker() as s:
        assert (await s.get(Booking, b.id)).status == BookingStatus.confirmed


async def test_double_cancel_releases_once(sessionmaker):
    _, user, slot, b = await _two_sessions_with_pending(sessionmaker, capacity=2)
    async with sessionmaker() as s:
        other = await make_user(s)
        await bookings.create(s, other, slot.id, "Оля", "@olya", now=NOW)
        await s.commit()
    assert await _booked(sessionmaker, slot.id) == 2
    s1, s2 = sessionmaker(), sessionmaker()
    try:
        await s1.get(Booking, b.id)
        await s2.get(Booking, b.id)
        await bookings.cancel(s1, b.id, user, now=NOW)
        await s1.commit()
        with pytest.raises(InvalidState):
            await bookings.cancel(s2, b.id, user, now=NOW)
        await s2.rollback()
    finally:
        await s1.close()
        await s2.close()
    assert await _booked(sessionmaker, slot.id) == 1


async def test_decline_twice_releases_once(sessionmaker):
    admin, _, slot, b = await _two_sessions_with_pending(sessionmaker, capacity=3)
    async with sessionmaker() as s:
        await bookings.decline(s, b.id, admin, now=NOW)
        with pytest.raises(InvalidState):
            await bookings.decline(s, b.id, admin, now=NOW)
        await s.commit()
    assert await _booked(sessionmaker, slot.id) == 0


async def test_mark_attended_after_cancel_race(sessionmaker):
    admin, user, _, b = await _two_sessions_with_pending(sessionmaker)
    async with sessionmaker() as s:
        await bookings.confirm(s, b.id, admin)
        await s.commit()
    s1, s2 = sessionmaker(), sessionmaker()
    try:
        await s2.get(Booking, b.id)  # видит confirmed
        await bookings.cancel(s1, b.id, user, now=NOW)
        await s1.commit()
        with pytest.raises(InvalidState):
            await bookings.mark_attended(s2, b.id, admin, True)
    finally:
        await s1.close()
        await s2.close()


# ---------- мелочи ----------


async def test_slot_duplicate(session):
    sc = await make_scenario(session)
    await slots.create(session, sc.id, SAT, 2, now=NOW)
    with pytest.raises(ValidationError, match="Такой слот уже есть"):
        await slots.create(session, sc.id, SAT, 3, now=NOW)
    other = await make_scenario(session)
    await slots.create(session, other.id, SAT, 2, now=NOW)  # другой сценарий — можно
    assert len(await slots.list_for_scenario(session, sc.id)) == 1


async def test_feedback_before_start_invalid(session):
    u = await make_user(session)
    sc = await make_scenario(session)
    slot = await make_slot(session, sc, SAT)
    b = await make_booking(session, u, slot)
    with pytest.raises(InvalidState):
        await feedback.save_rating(session, b.id, u, 5, now=NOW)
    fb = await feedback.save_rating(session, b.id, u, 5, now=SAT + timedelta(hours=3))
    assert fb.rating == 5
    with pytest.raises(InvalidState):
        await feedback.save_comment(session, b.id, u, "рано", now=NOW)


async def test_segments_require_privacy(session):
    ok = await make_user(session)
    await make_user(session, privacy_accepted_at=None)
    assert await broadcasts.segment_user_ids(session, "all", now=NOW) == [ok.tg_id]


def test_engine_hides_parameters():
    from app.db import make_engine

    assert make_engine("sqlite+aiosqlite:///:memory:").sync_engine.hide_parameters is True


# ---------- круг 2 ----------


async def test_double_click_create_rejected_by_unique_index(sessionmaker):
    """Две сессии (двойное нажатие): вторая вставка упирается в частичный уникальный
    индекс, место возвращается, бросается InvalidState."""
    async with sessionmaker() as s:
        user = await make_user(s)
        sc = await make_scenario(s)
        slot = await make_slot(s, sc, SAT, capacity=5)
        await s.commit()
    s1, s2 = sessionmaker(), sessionmaker()
    try:
        await bookings.create(s1, user, slot.id, "Аня", "@anya", now=NOW)
        await s1.commit()
        with pytest.raises(InvalidState, match="уже есть заявка"):
            await bookings.create(s2, user, slot.id, "Аня", "@anya", now=NOW)
        await s2.commit()  # транзакция жива после отката savepoint
    finally:
        await s1.close()
        await s2.close()
    assert await _booked(sessionmaker, slot.id) == 1


async def test_rebook_after_cancel_allowed(session):
    """Индекс частичный: после отмены можно забронировать тот же слот снова."""
    user = await make_user(session)
    sc = await make_scenario(session)
    slot = await make_slot(session, sc, SAT, capacity=5)
    b = await bookings.create(session, user, slot.id, "Аня", "@anya", now=NOW)
    await bookings.cancel(session, b.id, user, now=NOW)
    b2 = await bookings.create(session, user, slot.id, "Аня", "@anya", now=NOW)
    assert b2.id != b.id and b2.slot.booked_count == 1


async def test_has_active_partner(session):
    partner = await make_user(session, Role.partner)
    with_partner = await make_scenario(session, partner_id=partner.id)
    without = await make_scenario(session)
    u = await make_user(session)
    b1 = await bookings.create(
        session, u, (await make_slot(session, with_partner, SAT)).id, "Аня", "@a1", now=NOW
    )
    b2 = await bookings.create(
        session, u, (await make_slot(session, without, SAT)).id, "Аня", "@a1", now=NOW
    )
    assert bookings.has_active_partner(b1) is True
    assert bookings.has_active_partner(b2) is False
    await users.set_role(session, partner.tg_id, Role.user)
    assert bookings.has_active_partner(await bookings.get(session, b1.id)) is False
