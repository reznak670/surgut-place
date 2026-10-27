from datetime import timedelta

from app.db.models import Booking, BookingStatus, Role, User
from app.scheduler import jobs
from tests.backend.factories import (
    NOW,
    SAT,
    FakeNotifier,
    make_booking,
    make_scenario,
    make_slot,
    make_user,
)


async def test_reminders_24h_and_3h_sent_once(sessionmaker):
    async with sessionmaker() as s:
        u = await make_user(s)
        sc = await make_scenario(s)
        slot = await make_slot(s, sc, NOW + timedelta(hours=23, minutes=30))
        b = await make_booking(s, u, slot)
        pending_slot = await make_slot(s, sc, NOW + timedelta(hours=23, minutes=45))
        await make_booking(s, u, pending_slot, BookingStatus.pending)
        await s.commit()
    n = FakeNotifier()

    assert await jobs.send_reminders(sessionmaker, n, now=NOW) == 1
    assert await jobs.send_reminders(sessionmaker, n, now=NOW + timedelta(minutes=5)) == 0
    assert n.of("reminder") == [(b.id, 24)]

    t3 = NOW + timedelta(hours=21, minutes=30)  # за 2 часа до начала
    assert await jobs.send_reminders(sessionmaker, n, now=t3) == 1
    assert await jobs.send_reminders(sessionmaker, n, now=t3 + timedelta(minutes=5)) == 0
    assert n.of("reminder") == [(b.id, 24), (b.id, 3)]

    async with sessionmaker() as s:
        fresh = await s.get(Booking, b.id)
        assert fresh.reminded_24h_at == NOW and fresh.reminded_3h_at == t3


async def test_late_confirm_skips_24h_reminder(sessionmaker):
    """До начала меньше 23 ч → 24-часовое напоминание не шлём, только флаг; потом — за 3 ч."""
    async with sessionmaker() as s:
        u = await make_user(s)
        sc = await make_scenario(s)
        slot = await make_slot(s, sc, NOW + timedelta(hours=20))
        b = await make_booking(s, u, slot)
        await s.commit()
    n = FakeNotifier()
    assert await jobs.send_reminders(sessionmaker, n, now=NOW) == 0
    assert n.calls == []
    async with sessionmaker() as s:
        assert (await s.get(Booking, b.id)).reminded_24h_at == NOW
    assert await jobs.send_reminders(sessionmaker, n, now=NOW + timedelta(hours=18)) == 1
    assert n.of("reminder") == [(b.id, 3)]


async def test_reminder_not_retried_when_undelivered(sessionmaker):
    async with sessionmaker() as s:
        u = await make_user(s)
        sc = await make_scenario(s)
        slot = await make_slot(s, sc, NOW + timedelta(hours=2))
        await make_booking(s, u, slot)
        await s.commit()
    n = FakeNotifier(result=False)
    assert await jobs.send_reminders(sessionmaker, n, now=NOW) == 0
    await jobs.send_reminders(sessionmaker, n, now=NOW + timedelta(minutes=5))
    assert len(n.of("reminder")) == 1  # бот заблокирован → флаг всё равно ставим


async def test_feedback_two_hours_after_end(sessionmaker):
    async with sessionmaker() as s:
        u = await make_user(s)
        sc = await make_scenario(s, duration_minutes=120)
        slot = await make_slot(s, sc, NOW - timedelta(hours=3))  # конец = NOW-1h
        b = await make_booking(s, u, slot)
        old_slot = await make_slot(s, sc, NOW - timedelta(days=5))
        await make_booking(s, u, old_slot)  # старше 3 дней
        noshow_slot = await make_slot(s, sc, NOW - timedelta(hours=10))
        await make_booking(s, u, noshow_slot, attended=False)
        await s.commit()
    n = FakeNotifier()
    assert await jobs.request_feedback(sessionmaker, n, now=NOW) == 0  # прошло лишь 1 ч
    t = NOW + timedelta(hours=1)
    assert await jobs.request_feedback(sessionmaker, n, now=t) == 1
    assert await jobs.request_feedback(sessionmaker, n, now=t + timedelta(minutes=10)) == 0
    assert n.of("feedback") == [b.id]


async def test_overdue_pending_alert_once(sessionmaker):
    async with sessionmaker() as s:
        u = await make_user(s)
        sc = await make_scenario(s)
        slot = await make_slot(s, sc, SAT, capacity=5)
        old = await make_booking(
            s, u, slot, BookingStatus.pending, created_at=NOW - timedelta(hours=2, minutes=1)
        )
        u2, u3 = await make_user(s), await make_user(s)  # одна активная заявка на юзера
        await make_booking(
            s, u2, slot, BookingStatus.pending, created_at=NOW - timedelta(minutes=30)
        )
        await make_booking(s, u3, slot, BookingStatus.confirmed, created_at=NOW - timedelta(days=1))
        started = await make_slot(s, sc, NOW - timedelta(minutes=10))
        await make_booking(s, u, started, BookingStatus.pending, created_at=NOW - timedelta(days=1))
        await s.commit()
    n = FakeNotifier()
    assert await jobs.send_overdue_alerts(sessionmaker, n, now=NOW) == 1
    assert await jobs.send_overdue_alerts(sessionmaker, n, now=NOW + timedelta(minutes=10)) == 0
    assert n.of("overdue") == [old.id]


async def test_reactivation_once(sessionmaker):
    async with sessionmaker() as s:
        sleeping = await make_user(s, last_active_at=NOW - timedelta(days=31))
        await make_user(s, last_active_at=NOW - timedelta(days=5))
        await make_user(s, Role.partner, last_active_at=NOW - timedelta(days=40))
        await make_user(s, last_active_at=NOW - timedelta(days=40), privacy_accepted_at=None)
        await s.commit()
    n = FakeNotifier()
    assert await jobs.send_reactivation(sessionmaker, n, now=NOW, delay=0) == 1
    assert await jobs.send_reactivation(sessionmaker, n, now=NOW + timedelta(days=1), delay=0) == 0
    assert n.of("reactivation") == [sleeping.tg_id]
    async with sessionmaker() as s:
        assert (await s.get(User, sleeping.id)).reactivation_sent_at == NOW


async def test_digest_only_active_users(sessionmaker):
    async with sessionmaker() as s:
        active = await make_user(s, last_active_at=NOW - timedelta(days=3))
        await make_user(s, last_active_at=NOW - timedelta(days=45))
        await make_user(s, Role.admin)
        await make_user(s, privacy_accepted_at=None)  # без согласия на ПД — не пишем
        sc = await make_scenario(s)
        await make_slot(s, sc, SAT + timedelta(hours=14))
        await make_slot(s, sc, SAT + timedelta(hours=10))  # ближайший
        await make_slot(s, sc, SAT + timedelta(hours=8), capacity=1, booked=1)  # занят
        walk = await make_scenario(s, title="walk", need_booking=False)
        await s.commit()
    n = FakeNotifier()
    assert await jobs.send_weekend_digest(sessionmaker, n, now=NOW, delay=0) == 1
    assert n.of("digest") == [(active.tg_id, [sc.id, walk.id])]
    # ближайший свободный слот; у сценария без брони записи нет
    assert n.of("digest_nearest") == [{sc.id: SAT + timedelta(hours=10)}]


async def test_digest_not_called_when_no_weekend_scenarios(sessionmaker):
    async with sessionmaker() as s:
        await make_user(s)
        sc = await make_scenario(s)
        await make_slot(s, sc, SAT + timedelta(days=10))  # не на ближайших выходных
        await make_scenario(s, title="hidden", need_booking=False, is_active=False)
        await s.commit()
    n = FakeNotifier()
    assert await jobs.send_weekend_digest(sessionmaker, n, now=NOW, delay=0) == 0
    assert n.calls == []


async def test_notifier_exception_does_not_mark(sessionmaker):
    class Boom(FakeNotifier):
        async def reactivation(self, tg_id: int) -> bool:
            raise RuntimeError("network")

    async with sessionmaker() as s:
        u = await make_user(s, last_active_at=NOW - timedelta(days=31))
        await s.commit()
    assert await jobs.send_reactivation(sessionmaker, Boom(), now=NOW, delay=0) == 0
    async with sessionmaker() as s:
        assert (await s.get(User, u.id)).reactivation_sent_at is None


def test_setup_scheduler_jobs(sessionmaker):
    sched = jobs.setup_scheduler(sessionmaker, FakeNotifier())
    assert {j.id for j in sched.get_jobs()} == {
        "reminders",
        "feedback",
        "overdue",
        "weekend_digest",
        "reactivation",
    }
