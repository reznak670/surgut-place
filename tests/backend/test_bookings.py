from datetime import timedelta

import pytest

from app.config import settings
from app.db.models import BookingStatus, Role, Slot
from app.services import bookings, slots
from app.services.errors import Forbidden, InvalidState, NotFound, SlotFull, SlotInPast
from app.services.errors import ValidationError as SvcValidationError
from tests.backend.factories import NOW, SAT, make_scenario, make_slot, make_user


async def test_create_success(session):
    user = await make_user(session)
    sc = await make_scenario(session)
    slot = await make_slot(session, sc, SAT + timedelta(hours=10), capacity=2)

    b = await bookings.create(session, user, slot.id, " Аня ", "+7 900 000-00-00", now=NOW)

    assert b.status == BookingStatus.pending
    assert b.name == "Аня"
    assert b.slot.scenario.id == sc.id  # связи подгружены
    assert b.user.id == user.id
    assert (await slots.get(session, slot.id)).booked_count == 1


async def test_create_overflow_raises_slot_full(session):
    sc = await make_scenario(session)
    slot = await make_slot(session, sc, SAT + timedelta(hours=10), capacity=1)
    u1, u2 = await make_user(session), await make_user(session)

    await bookings.create(session, u1, slot.id, "Аня", "@anya", now=NOW)
    with pytest.raises(SlotFull):
        await bookings.create(session, u2, slot.id, "Оля", "@olya", now=NOW)
    assert (await slots.get(session, slot.id)).booked_count == 1


async def test_race_stale_session_cannot_overbook(sessionmaker):
    """Две сессии видят свободное место; атомарный UPDATE пропускает только одну бронь."""
    async with sessionmaker() as s:
        sc = await make_scenario(s)
        slot = await make_slot(s, sc, SAT + timedelta(hours=10), capacity=1)
        u1, u2 = await make_user(s), await make_user(s)
        await s.commit()

    s1, s2 = sessionmaker(), sessionmaker()
    try:
        # обе сессии загрузили слот, когда место ещё было
        assert (await s1.get(Slot, slot.id)).booked_count == 0
        assert (await s2.get(Slot, slot.id)).booked_count == 0
        await bookings.create(s1, u1, slot.id, "Аня", "@anya", now=NOW)
        await s1.commit()
        with pytest.raises(SlotFull):
            await bookings.create(s2, u2, slot.id, "Оля", "@olya", now=NOW)
        await s2.rollback()
    finally:
        await s1.close()
        await s2.close()

    async with sessionmaker() as s:
        assert (await slots.get(s, slot.id)).booked_count == 1


async def test_create_in_past(session):
    user = await make_user(session)
    sc = await make_scenario(session)
    slot = await make_slot(session, sc, NOW - timedelta(minutes=1))
    with pytest.raises(SlotInPast):
        await bookings.create(session, user, slot.id, "Аня", "@anya", now=NOW)


async def test_create_validation_and_not_found(session):
    user = await make_user(session)
    sc = await make_scenario(session)
    slot = await make_slot(session, sc, SAT)
    with pytest.raises(SvcValidationError):
        await bookings.create(session, user, slot.id, "", "@anya", now=NOW)
    with pytest.raises(SvcValidationError):
        await bookings.create(session, user, slot.id, "Аня", "1", now=NOW)
    with pytest.raises(NotFound):
        await bookings.create(session, user, 999, "Аня", "@anya", now=NOW)


async def test_duplicate_booking_same_slot(session):
    user = await make_user(session)
    sc = await make_scenario(session)
    slot = await make_slot(session, sc, SAT, capacity=5)
    await bookings.create(session, user, slot.id, "Аня", "@anya", now=NOW)
    with pytest.raises(InvalidState):
        await bookings.create(session, user, slot.id, "Аня", "@anya", now=NOW)
    assert (await slots.get(session, slot.id)).booked_count == 1  # место вернули


async def test_cancel_releases_seat(session):
    user = await make_user(session)
    sc = await make_scenario(session)
    slot = await make_slot(session, sc, SAT, capacity=1)
    b = await bookings.create(session, user, slot.id, "Аня", "@anya", now=NOW)

    b = await bookings.cancel(session, b.id, user, now=NOW)
    assert b.status == BookingStatus.cancelled
    assert b.cancelled_at == NOW
    assert b.slot.booked_count == 0
    # место снова доступно
    other = await make_user(session)
    await bookings.create(session, other, slot.id, "Оля", "@olya", now=NOW)


async def test_cancel_after_start_invalid(session):
    user = await make_user(session)
    sc = await make_scenario(session)
    slot = await make_slot(session, sc, SAT)
    b = await bookings.create(session, user, slot.id, "Аня", "@anya", now=NOW)
    with pytest.raises(InvalidState):
        await bookings.cancel(session, b.id, user, now=SAT + timedelta(minutes=1))


async def test_cancel_by_other_user_forbidden_and_twice_invalid(session):
    user, other = await make_user(session), await make_user(session)
    sc = await make_scenario(session)
    slot = await make_slot(session, sc, SAT)
    b = await bookings.create(session, user, slot.id, "Аня", "@anya", now=NOW)
    with pytest.raises(Forbidden):
        await bookings.cancel(session, b.id, other, now=NOW)
    await bookings.cancel(session, b.id, user, now=NOW)
    with pytest.raises(InvalidState):
        await bookings.cancel(session, b.id, user, now=NOW)


async def test_confirm_permissions(session):
    owner_partner = await make_user(session, Role.partner)
    alien_partner = await make_user(session, Role.partner)
    admin = await make_user(session, Role.admin)
    plain = await make_user(session)
    sc = await make_scenario(session, partner_id=owner_partner.id)
    slot = await make_slot(session, sc, SAT, capacity=5)
    b1 = await bookings.create(session, plain, slot.id, "Аня", "@anya", now=NOW)

    with pytest.raises(Forbidden):
        await bookings.confirm(session, b1.id, alien_partner)
    with pytest.raises(Forbidden):
        await bookings.confirm(session, b1.id, plain)

    b1 = await bookings.confirm(session, b1.id, owner_partner, now=NOW)
    assert b1.status == BookingStatus.confirmed
    assert b1.confirmed_at == NOW
    with pytest.raises(InvalidState):
        await bookings.confirm(session, b1.id, owner_partner)

    other = await make_user(session)
    b2 = await bookings.create(session, other, slot.id, "Оля", "@olya", now=NOW)
    b2 = await bookings.confirm(session, b2.id, admin)
    assert b2.status == BookingStatus.confirmed


async def test_decline_releases_seat(session):
    admin = await make_user(session, Role.admin)
    user = await make_user(session)
    sc = await make_scenario(session)
    slot = await make_slot(session, sc, SAT, capacity=1)
    b = await bookings.create(session, user, slot.id, "Аня", "@anya", now=NOW)
    b = await bookings.decline(session, b.id, admin, now=NOW)
    assert b.status == BookingStatus.declined
    assert b.slot.booked_count == 0


async def test_partner_lists_and_chat_id(session, monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_CHAT_ID", -100500)
    partner = await make_user(session, Role.partner)
    user = await make_user(session)
    mine = await make_scenario(session, partner_id=partner.id)
    alien = await make_scenario(session)
    s_mine = await make_slot(session, mine, SAT)
    s_alien = await make_slot(session, alien, SAT)
    b_mine = await bookings.create(session, user, s_mine.id, "Аня", "@anya", now=NOW)
    b_alien = await bookings.create(session, user, s_alien.id, "Аня", "@anya", now=NOW)

    listed = await bookings.list_for_partner(session, partner)
    assert [b.id for b in listed] == [b_mine.id]
    assert await bookings.list_for_partner(session, partner, [BookingStatus.confirmed]) == []
    assert {b.id for b in await bookings.list_pending(session)} == {b_mine.id, b_alien.id}
    assert bookings.partner_chat_id(b_mine) == partner.tg_id
    assert bookings.partner_chat_id(b_alien) == -100500
    assert [s.id for s in await slots.list_for_partner(session, partner, now=NOW)] == [s_mine.id]


async def test_mark_attended(session):
    partner = await make_user(session, Role.partner)
    user = await make_user(session)
    sc = await make_scenario(session, partner_id=partner.id)
    slot = await make_slot(session, sc, SAT)
    b = await bookings.create(session, user, slot.id, "Аня", "@anya", now=NOW)
    with pytest.raises(InvalidState):
        await bookings.mark_attended(session, b.id, partner, True)
    await bookings.confirm(session, b.id, partner)
    b = await bookings.mark_attended(session, b.id, partner, True)
    assert b.attended is True


async def test_list_for_user_future_first(session):
    user = await make_user(session)
    sc = await make_scenario(session)
    past = await make_slot(session, sc, NOW - timedelta(days=2))
    far = await make_slot(session, sc, SAT + timedelta(days=7))
    near = await make_slot(session, sc, SAT)
    from tests.backend.factories import make_booking

    b_past = await make_booking(session, user, past)
    b_far = await make_booking(session, user, far)
    b_near = await make_booking(session, user, near)
    result = await bookings.list_for_user(session, user, now=NOW)
    assert [b.id for b in result] == [b_near.id, b_far.id, b_past.id]


async def test_slot_create_and_delete(session):
    sc = await make_scenario(session)
    with pytest.raises(SvcValidationError):
        await slots.create(session, sc.id, SAT.replace(tzinfo=None), 2, now=NOW)
    with pytest.raises(SvcValidationError):
        await slots.create(session, sc.id, NOW - timedelta(hours=1), 2, now=NOW)
    with pytest.raises(SvcValidationError):
        await slots.create(session, sc.id, SAT, 0, now=NOW)
    slot = await slots.create(session, sc.id, SAT, 2, now=NOW)
    assert slot.starts_at == SAT and slot.starts_at.tzinfo is not None

    user = await make_user(session)
    b = await bookings.create(session, user, slot.id, "Аня", "@anya", now=NOW)
    with pytest.raises(InvalidState):
        await slots.delete(session, slot.id)
    await bookings.cancel(session, b.id, user, now=NOW)
    await slots.delete(session, slot.id)
    assert await slots.list_for_scenario(session, sc.id) == []


async def test_available_slots(session):
    sc = await make_scenario(session)
    await make_slot(session, sc, NOW - timedelta(hours=1))
    full = await make_slot(session, sc, SAT, capacity=1, booked=1)
    late = await make_slot(session, sc, SAT + timedelta(days=1))
    early = await make_slot(session, sc, SAT + timedelta(hours=2))
    result = await slots.available(session, sc.id, now=NOW)
    assert [s.id for s in result] == [early.id, late.id]
    assert full.id not in [s.id for s in result]
