"""TelegramNotifier на фейковой сессии Bot API и на AsyncMock-боте."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.methods import SendMessage

from app.bot.notify import TelegramNotifier
from app.bot.texts import T
from app.config import settings
from app.db.models import BookingStatus, Role
from app.services import bookings
from app.services.timeutil import now_utc, weekend_window
from tests.backend.factories import make_booking, make_scenario, make_slot, make_user
from tests.bot.conftest import ADMIN_CHAT, FakeSession, button_texts


@pytest.fixture
async def data(session):
    partner = await make_user(session, role=Role.partner, username="vanya")
    guest = await make_user(session)
    sc = await make_scenario(
        session, title="Каток <ночью>", partner_id=partner.id, map_link="https://m.ru"
    )
    slot = await make_slot(session, sc, now_utc() + timedelta(hours=3), capacity=4)
    b = await make_booking(session, guest, slot, status=BookingStatus.confirmed)
    await session.commit()
    booking = await bookings.get(session, b.id)
    return {"booking": booking, "scenario": sc, "guest": guest}


async def _call_all(notifier: TelegramNotifier, data) -> list[bool]:
    b = data["booking"]
    return [
        await notifier.booking_reminder(b, 24),
        await notifier.booking_reminder(b, 3),
        await notifier.feedback_request(b),
        await notifier.weekend_digest(data["guest"].tg_id, [data["scenario"]]),
        await notifier.reactivation(data["guest"].tg_id),
        await notifier.overdue_booking_alert(b),
    ]


async def test_all_methods_deliver(bot, fake_session: FakeSession, data):
    results = await _call_all(TelegramNotifier(bot), data)
    assert results == [True] * 6
    sent = [r for r in fake_session.requests if isinstance(r, SendMessage)]
    assert len(sent) == 6
    tg = data["guest"].tg_id
    assert [r.chat_id for r in sent] == [tg] * 5 + [ADMIN_CHAT]

    r24, r3, fb, digest, react, overdue = (r.text for r in sent)
    assert r24.startswith("⏰ <b>Завтра у вас вечер</b>\nКаток &lt;ночью&gt;")
    assert '<a href="https://m.ru">' in r24
    assert "⏰ <b>Через 3 часа</b> — Каток &lt;ночью&gt;" in r3
    assert fb.startswith("<b>Как прошло?</b> Каток &lt;ночью&gt;")
    assert "1. Каток &lt;ночью&gt;" in digest and "Факт недели" in digest
    assert react
    assert overdue.startswith("⏳ Заявка ждёт больше 2 часов: Каток &lt;ночью&gt;")
    assert "Партнёр: Тест @vanya" in overdue

    assert button_texts(sent[0].reply_markup) == ["🗺 На карте", "Отменить бронь"]
    assert button_texts(sent[1].reply_markup) == ["🗺 На карте", "Отменить"]
    assert button_texts(sent[2].reply_markup) == ["1", "2", "3", "4", "5"]
    assert button_texts(sent[3].reply_markup)[-1] == T["notify.btn_digest_filters"]
    assert button_texts(sent[4].reply_markup) == [T["notify.btn_watch"]]


async def test_forbidden_returns_false(bot, fake_session: FakeSession, data):
    fake_session.fail_chats |= {data["guest"].tg_id, ADMIN_CHAT}
    results = await _call_all(TelegramNotifier(bot), data)
    assert results == [False] * 6


async def test_forbidden_on_async_mock_bot(data):
    bot = AsyncMock()
    bot.send_message.side_effect = TelegramForbiddenError(
        method=AsyncMock(), message="Forbidden: bot was blocked by the user"
    )
    assert await _call_all(TelegramNotifier(bot), data) == [False] * 6


async def test_async_mock_bot_success(data):
    bot = AsyncMock()
    assert await _call_all(TelegramNotifier(bot), data) == [True] * 6
    assert bot.send_message.await_count == 6


async def test_other_api_errors_return_false(data):
    bot = AsyncMock()
    bot.send_message.side_effect = TelegramBadRequest(method=AsyncMock(), message="chat not found")
    assert await TelegramNotifier(bot).reactivation(1) is False


async def test_digest_empty_and_no_admin_chat(bot, fake_session, data, monkeypatch):
    n = TelegramNotifier(bot)
    assert await n.weekend_digest(1, []) is False
    monkeypatch.setattr(settings, "ADMIN_CHAT_ID", None)
    assert await n.overdue_booking_alert(data["booking"]) is False
    assert fake_session.requests == []


async def test_reminder_3h_weather_hint(bot, fake_session, data, session):
    n = TelegramNotifier(bot)
    await n.booking_reminder(data["booking"], 3)
    assert T["notify.indoor_hint"] not in fake_session.requests[-1].text  # на улице
    data["scenario"].is_indoor = True
    await n.booking_reminder(data["booking"], 3)
    assert T["notify.indoor_hint"] in fake_session.requests[-1].text


async def test_digest_accepts_nearest(bot, fake_session, data):
    start, _ = weekend_window(now_utc())
    nearest = {data["scenario"].id: start + timedelta(hours=19)}  # сб 00:00 + 19 ч
    ok = await TelegramNotifier(bot).weekend_digest(
        data["guest"].tg_id, [data["scenario"]], nearest
    )
    assert ok is True
    assert "Сб 19:00" in fake_session.requests[-1].text
