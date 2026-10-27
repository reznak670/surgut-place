import json
import logging
from datetime import timedelta
from types import SimpleNamespace

import pytest
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.methods import SendMessage

from app.db.models import Booking, Broadcast, Role, Who
from app.logging_setup import JsonFormatter
from app.middlewares import DbSessionMiddleware, RoleFilter, UserMiddleware
from app.services import broadcasts
from app.services.errors import Forbidden, InvalidState, ValidationError
from tests.backend.factories import NOW, SAT, make_booking, make_scenario, make_slot, make_user


async def test_segments(session):
    couple = await make_user(session, who=Who.couple)
    sleeping = await make_user(session, last_active_at=NOW - timedelta(days=40))
    booked = await make_user(session)
    await make_user(session, Role.admin)
    sc = await make_scenario(session)
    await make_booking(session, booked, await make_slot(session, sc, SAT))

    async def seg(name):
        return set(await broadcasts.segment_user_ids(session, name, now=NOW))

    assert await seg("all") == {couple.tg_id, sleeping.tg_id, booked.tg_id}
    assert await seg("active") == {couple.tg_id, booked.tg_id}
    assert await seg("couples") == {couple.tg_id}
    assert await seg("with_bookings") == {booked.tg_id}
    assert await seg("no_bookings") == {couple.tg_id, sleeping.tg_id}
    with pytest.raises(ValidationError):
        await seg("vip")


class FakeBot:
    def __init__(self, blocked: set[int]):
        self.blocked = blocked
        self.sent: list[int] = []

    async def send_message(self, chat_id, text, **kw):
        if chat_id in self.blocked:
            raise TelegramForbiddenError(
                method=SendMessage(chat_id=chat_id, text=text), message="bot was blocked"
            )
        self.sent.append(chat_id)


async def test_send_broadcast_skips_blocked(sessionmaker):
    async with sessionmaker() as s:
        admin = await make_user(s, Role.admin)
        u1, u2, u3 = [await make_user(s) for _ in range(3)]
        with pytest.raises(Forbidden):
            await broadcasts.create(s, "all", "Привет", u1)
        with pytest.raises(ValidationError):
            await broadcasts.create(s, "all", "   ", admin)
        bc = await broadcasts.create(s, "all", "Привет!", admin)
        await s.commit()

    bot = FakeBot(blocked={u2.tg_id})
    sent = await broadcasts.send_broadcast(sessionmaker, bot, bc.id, delay=0)
    assert sent == 2
    assert bot.sent == [u1.tg_id, u3.tg_id]
    async with sessionmaker() as s:
        fresh = await s.get(Broadcast, bc.id)
        assert fresh.sent_count == 2 and fresh.sent_at is not None
    with pytest.raises(InvalidState):
        await broadcasts.send_broadcast(sessionmaker, bot, bc.id, delay=0)


def test_json_formatter_masks_pd():
    rec = logging.LogRecord("app.x", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    rec.booking_id = 7
    rec.contact = "+79990000000"
    out = json.loads(JsonFormatter().format(rec))
    assert out["msg"] == "hello world"
    assert out["level"] == "INFO" and out["logger"] == "app.x" and "ts" in out
    assert out["booking_id"] == 7
    assert out["contact"] == "***"


async def test_middlewares_and_role_filter(sessionmaker):
    seen = {}

    async def handler(event, data):
        seen.update(data)
        return "ok"

    tg_user = SimpleNamespace(id=555, username="anya", first_name="Аня", is_bot=False)
    db_mw = DbSessionMiddleware(sessionmaker)
    user_mw = UserMiddleware(admin_ids=[555])

    async def chain(event, data):
        return await user_mw(handler, event, data)

    assert await db_mw(chain, object(), {"event_from_user": tg_user}) == "ok"
    user = seen["user"]
    assert user.tg_id == 555 and user.role == Role.admin

    assert await RoleFilter(Role.admin)(object(), user=user) is True
    assert await RoleFilter(Role.partner, "user")(object(), user=user) is False
    assert await RoleFilter(Role.admin)(object(), user=None) is False

    # коммит произошёл — пользователь виден из новой сессии
    async with sessionmaker() as s:
        from app.services.users import get_by_tg_id

        assert (await get_by_tg_id(s, 555)) is not None


async def test_db_middleware_rollback_on_error(sessionmaker):
    async def bad_handler(event, data):
        sc = await make_scenario(data["session"])
        u = await make_user(data["session"])
        await make_booking(data["session"], u, await make_slot(data["session"], sc, SAT))
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await DbSessionMiddleware(sessionmaker)(bad_handler, object(), {})
    async with sessionmaker() as s:
        from sqlalchemy import select

        assert (await s.scalars(select(Booking))).all() == []


class MarkupBot:
    """Отвергает HTML-разметку, как Telegram при битых тегах; plain-текст принимает."""

    def __init__(self, bad_request_for: set[int] | None = None):
        self.calls: list[tuple[int, object]] = []
        self.bad_request_for = bad_request_for or set()

    async def send_message(self, chat_id, text, parse_mode=None, **kw):
        self.calls.append((chat_id, parse_mode))
        method = SendMessage(chat_id=chat_id, text=text)
        if chat_id in self.bad_request_for:
            raise TelegramBadRequest(method=method, message="Bad Request: chat not found")
        if parse_mode is not None:
            raise TelegramBadRequest(
                method=method, message="Bad Request: can't parse entities: unclosed tag"
            )


async def _broadcast(sessionmaker, text, n_users):
    async with sessionmaker() as s:
        admin = await make_user(s, Role.admin)
        users = [await make_user(s) for _ in range(n_users)]
        bc = await broadcasts.create(s, "all", text, admin)
        await s.commit()
    return bc, users


async def test_send_broadcast_html_fallback_to_plain(sessionmaker):
    bc, (u1, u2) = await _broadcast(sessionmaker, "<b>Привет", 2)
    bot = MarkupBot(bad_request_for={u2.tg_id})
    assert await broadcasts.send_broadcast(sessionmaker, bot, bc.id, delay=0) == 1
    # u1: HTML → отказ разметки → повтор plain; u2: другой BadRequest — без повтора
    assert bot.calls == [(u1.tg_id, ParseMode.HTML), (u1.tg_id, None), (u2.tg_id, ParseMode.HTML)]


async def test_send_broadcast_uses_html_first(sessionmaker):
    bc, _ = await _broadcast(sessionmaker, "<b>Привет</b>", 1)
    bot = FakeBot(blocked=set())
    calls = []

    async def send_message(chat_id, text, parse_mode=None, **kw):
        calls.append(parse_mode)

    bot.send_message = send_message
    assert await broadcasts.send_broadcast(sessionmaker, bot, bc.id, delay=0) == 1
    assert calls == [ParseMode.HTML]


async def test_send_broadcast_progress(sessionmaker):
    bc, users = await _broadcast(sessionmaker, "Привет", 45)
    bot = FakeBot(blocked={users[0].tg_id})
    seen: list[tuple[int, int]] = []

    async def progress(sent, total):
        seen.append((sent, total))
        raise RuntimeError("UI упал")  # исключения колбэка глотаются

    sent = await broadcasts.send_broadcast(sessionmaker, bot, bc.id, delay=0, progress=progress)
    assert sent == 44
    assert seen == [(19, 45), (39, 45), (44, 45)]


async def test_send_broadcast_progress_small(sessionmaker):
    bc, _ = await _broadcast(sessionmaker, "Привет", 20)
    seen = []

    async def progress(sent, total):
        seen.append((sent, total))

    await broadcasts.send_broadcast(
        sessionmaker, FakeBot(blocked=set()), bc.id, delay=0, progress=progress
    )
    assert seen == [(20, 20)]  # ровно 20 → только финальный вызов, без дубля


@pytest.mark.parametrize(
    ("text", "ok"),
    [
        ("Просто текст", True),
        ("<b>жирный</b> и <a href='https://x.ru'>ссылка</a>", True),
        ('<span class="tg-spoiler">сюрприз</span> &lt;3', True),
        ("<b>незакрытый", False),
        ("<b><i>криво</b></i>", False),
        ("<div>нельзя</div>", False),
        ("1 < 2", False),
    ],
)
def test_is_valid_html(text, ok):
    assert broadcasts.is_valid_html(text) is ok


async def test_create_accepts_invalid_html(session):
    admin = await make_user(session, Role.admin)
    bc = await broadcasts.create(session, "all", "<b>битый", admin)
    assert bc.id and bc.text == "<b>битый"
