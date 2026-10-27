from datetime import timedelta

import pytest

from app.db.models import BookingStatus, Budget, Role, Who
from app.services import feedback, saved, users
from app.services.errors import Forbidden, InvalidState, NotFound, ValidationError
from tests.backend.factories import NOW, SAT, make_booking, make_scenario, make_slot, make_user


async def test_touch_upsert_and_admin(session):
    u = await users.touch(session, 42, "anya", "Аня", admin_ids=[], now=NOW)
    assert u.role == Role.user and u.last_active_at == NOW
    u.reactivation_sent_at = NOW
    later = NOW + timedelta(hours=1)
    u2 = await users.touch(session, 42, "anya2", "Аня", admin_ids=[42], now=later)
    assert u2.id == u.id
    assert u2.role == Role.admin
    assert u2.username == "anya2"
    assert u2.reactivation_sent_at is None
    u3 = await users.touch(session, 42, "anya2", "Аня", admin_ids=[], now=later)
    assert u3.role == Role.user  # ADMIN_IDS — источник правды


async def test_profile_role_onboarding_privacy(session):
    u = await make_user(session, privacy_accepted_at=None)
    u = await users.set_profile(session, u, who=Who.couple)
    assert u.who == Who.couple and u.budget is None
    u = await users.set_profile(session, u, budget=Budget.lt2000)
    assert u.who == Who.couple and u.budget == Budget.lt2000
    u = await users.set_profile(session, u, who=None)
    assert u.who is None and u.budget == Budget.lt2000

    u = await users.mark_onboarded(session, u)
    assert u.onboarded is True

    u = await users.accept_privacy(session, u, now=NOW)
    assert u.privacy_accepted_at == NOW
    u = await users.accept_privacy(session, u, now=NOW + timedelta(days=1))
    assert u.privacy_accepted_at == NOW  # первая дата согласия сохраняется

    p = await users.set_role(session, 777, Role.partner)  # заглушка
    assert p.tg_id == 777 and p.role == Role.partner
    p2 = await users.set_role(session, u.tg_id, Role.partner)
    assert p2.id == u.id
    assert {x.id for x in await users.list_partners(session)} == {p.id, u.id}


async def test_saved_toggle(session):
    u = await make_user(session)
    sc = await make_scenario(session)
    assert await saved.toggle(session, u, sc.id) is True
    assert await saved.is_saved(session, u, sc.id) is True
    assert [s.id for s in await saved.list_saved(session, u)] == [sc.id]
    assert await saved.toggle(session, u, sc.id) is False
    assert await saved.is_saved(session, u, sc.id) is False
    assert await saved.list_saved(session, u) == []
    with pytest.raises(NotFound):
        await saved.toggle(session, u, 9999)


async def test_saved_include_hidden(session):
    u = await make_user(session)
    visible = await make_scenario(session, title="visible")
    hidden = await make_scenario(session, title="hidden", is_active=False)
    await saved.toggle(session, u, visible.id)
    await saved.toggle(session, u, hidden.id)
    assert [s.id for s in await saved.list_saved(session, u)] == [visible.id]
    ids = {s.id for s in await saved.list_saved(session, u, include_hidden=True)}
    assert ids == {visible.id, hidden.id}


async def _confirmed_booking(session):
    u = await make_user(session)
    sc = await make_scenario(session)
    slot = await make_slot(session, sc, NOW - timedelta(days=1))
    b = await make_booking(session, u, slot)
    return u, b, sc


async def test_feedback_upsert(session):
    u, b, _ = await _confirmed_booking(session)
    fb = await feedback.save_rating(session, b.id, u, 2, now=NOW)
    assert fb.rating == 2
    fb2 = await feedback.save_rating(session, b.id, u, 5, now=NOW + timedelta(minutes=1))
    assert fb2.id == fb.id and fb2.rating == 5
    fb3 = await feedback.save_comment(session, b.id, u, "  Было здорово ")
    assert fb3.id == fb.id and fb3.comment == "Было здорово" and fb3.rating == 5

    recent = await feedback.list_recent(session)
    assert [f.id for f in recent] == [fb.id]
    assert recent[0].booking.slot.scenario.title
    assert await feedback.list_recent(session, only_low=True) == []
    await feedback.save_rating(session, b.id, u, 3)
    assert len(await feedback.list_recent(session, only_low=True)) == 1


async def test_feedback_errors(session):
    u, b, sc = await _confirmed_booking(session)
    other = await make_user(session)
    with pytest.raises(ValidationError):
        await feedback.save_rating(session, b.id, u, 6)
    with pytest.raises(Forbidden):
        await feedback.save_rating(session, b.id, other, 5)
    with pytest.raises(InvalidState):
        await feedback.save_comment(session, b.id, u, "без оценки")
    with pytest.raises(NotFound):
        await feedback.save_rating(session, 999, u, 5)
    slot2 = await make_slot(session, sc, SAT)
    pending = await make_booking(session, u, slot2, BookingStatus.pending)
    with pytest.raises(InvalidState):
        await feedback.save_rating(session, pending.id, u, 5)
