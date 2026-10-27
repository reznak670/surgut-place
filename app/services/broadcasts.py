from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from html.parser import HTMLParser
from typing import Any

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Booking, BookingStatus, Broadcast, Role, User, Who
from app.services.errors import Forbidden, InvalidState, NotFound, ValidationError
from app.services.timeutil import now_utc

log = logging.getLogger(__name__)

SEGMENTS: tuple[str, ...] = ("all", "active", "couples", "with_bookings", "no_bookings")
ACTIVE_DAYS = 30
SEND_DELAY = 0.05  # ~20 сообщений/с — ниже лимита Telegram (30/с)
PROGRESS_EVERY = 20

Progress = Callable[[int, int], Awaitable[None]]

# Теги, которые понимает Telegram в parse_mode=HTML
_TG_TAGS = {
    "b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "span", "tg-spoiler",
    "a", "code", "pre", "blockquote", "tg-emoji",
}  # fmt: skip


class _TgHtmlChecker(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)  # &lt; не превращать в "<"
        self.stack: list[str] = []
        self.ok = True

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in _TG_TAGS:
            self.ok = False
        self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self.stack or self.stack.pop() != tag:
            self.ok = False

    def handle_data(self, data: str) -> None:
        if "<" in data or ">" in data:  # неэкранированные угловые скобки
            self.ok = False


def is_valid_html(text: str) -> bool:
    """Грубая проверка Telegram-HTML: известные теги, правильная вложенность, без голых < >.
    False не ошибка: send_broadcast при отказе Telegram отправит plain-текстом."""
    checker = _TgHtmlChecker()
    try:
        checker.feed(text)
        checker.close()
    except Exception:
        return False
    return checker.ok and not checker.stack


def _is_markup_error(e: TelegramBadRequest) -> bool:
    msg = (e.message or "").lower()
    return "parse entities" in msg or "unsupported start tag" in msg or "can't find end" in msg


async def segment_user_ids(
    session: AsyncSession, segment: str, *, now: datetime | None = None
) -> list[int]:
    """tg_id пользователей сегмента (role=user и privacy_accepted_at не NULL)."""
    if segment not in SEGMENTS:
        raise ValidationError(f"Неизвестный сегмент: {segment}")
    now = now or now_utc()
    has_booking = exists().where(
        Booking.user_id == User.id,
        Booking.status.in_((BookingStatus.pending, BookingStatus.confirmed)),
    )
    # только role=user, давшие согласие на обработку ПД
    stmt = select(User.tg_id).where(User.role == Role.user, User.privacy_accepted_at.is_not(None))
    match segment:
        case "active":
            stmt = stmt.where(User.last_active_at >= now - timedelta(days=ACTIVE_DAYS))
        case "couples":
            stmt = stmt.where(User.who == Who.couple)
        case "with_bookings":
            stmt = stmt.where(has_booking)
        case "no_bookings":
            stmt = stmt.where(~has_booking)
    return list(await session.scalars(stmt.order_by(User.id)))


async def create(session: AsyncSession, segment: str, text: str, admin: User) -> Broadcast:
    if admin.role != Role.admin:
        raise Forbidden()
    if segment not in SEGMENTS:
        raise ValidationError(f"Неизвестный сегмент: {segment}")
    text = (text or "").strip()
    if not text:
        raise ValidationError("Текст рассылки пустой")
    if len(text) > 4096:
        raise ValidationError("Текст длиннее 4096 символов")
    bc = Broadcast(segment=segment, text=text, created_by=admin.id, sent_count=0)
    session.add(bc)
    await session.flush()
    if not is_valid_html(text):
        # не ошибка: при отправке Telegram откажет в разметке → уйдёт plain-текстом
        log.warning("broadcast: text is not valid Telegram HTML", extra={"broadcast_id": bc.id})
    return bc


async def mark_sent(
    session: AsyncSession, broadcast_id: int, count: int, *, now: datetime | None = None
) -> Broadcast:
    bc = await session.get(Broadcast, broadcast_id)
    if bc is None:
        raise NotFound(f"broadcast {broadcast_id}")
    bc.sent_at = now or now_utc()
    bc.sent_count = count
    await session.flush()
    return bc


async def send_broadcast(
    sessionmaker: async_sessionmaker[AsyncSession],
    bot: Any,
    broadcast_id: int,
    *,
    delay: float = SEND_DELAY,
    progress: Progress | None = None,
) -> int:
    """Отправляет рассылку и возвращает число доставленных сообщений.

    Запускать фоном: `asyncio.create_task(send_broadcast(sessionmaker, bot, bc.id))`
    ПОСЛЕ коммита create(). Текст уходит с parse_mode=HTML; если Telegram отверг
    разметку (TelegramBadRequest «can't parse entities»), этому юзеру — повтор plain-текстом.
    progress(sent, total) вызывается каждые 20 обработанных получателей и в конце;
    его исключения глотаются. Заблокировавшие бота пропускаются, цикл не падает.
    """
    async with sessionmaker() as session:
        bc = await session.get(Broadcast, broadcast_id)
        if bc is None:
            raise NotFound(f"broadcast {broadcast_id}")
        if bc.sent_at is not None:
            raise InvalidState("Рассылка уже отправлена")
        text, segment = bc.text, bc.segment
        tg_ids = await segment_user_ids(session, segment)

    total = len(tg_ids)
    sent = 0

    async def report() -> None:
        if progress is None:
            return
        try:
            await progress(sent, total)
        except Exception:
            log.warning("broadcast: progress callback failed", exc_info=True)

    for done, tg_id in enumerate(tg_ids, start=1):
        if await _deliver(bot, tg_id, text):
            sent += 1
        if done % PROGRESS_EVERY == 0 and done != total:
            await report()
        if delay:
            await asyncio.sleep(delay)

    async with sessionmaker() as session:
        await mark_sent(session, broadcast_id, sent)
        await session.commit()
    await report()
    log.info(
        "broadcast finished",
        extra={"broadcast_id": broadcast_id, "sent": sent, "total": total},
    )
    return sent


async def _deliver(bot: Any, tg_id: int, text: str) -> bool:
    parse_mode: str | None = ParseMode.HTML
    retried_after = False
    while True:
        try:
            await bot.send_message(tg_id, text, parse_mode=parse_mode)
            return True
        except TelegramRetryAfter as e:
            if retried_after:
                log.warning("broadcast: retry_after twice, skip", extra={"tg_id": tg_id})
                return False
            retried_after = True
            await asyncio.sleep(e.retry_after)
        except TelegramBadRequest as e:
            if parse_mode is not None and _is_markup_error(e):
                parse_mode = None  # разметка битая → повтор plain-текстом
                continue
            log.info("broadcast: bad request", extra={"tg_id": tg_id})
            return False
        except TelegramAPIError as e:  # Forbidden (бот заблокирован) и т.п.
            log.info("broadcast: not delivered", extra={"tg_id": tg_id, "error": type(e).__name__})
            return False
        except Exception:
            log.exception("broadcast: unexpected error", extra={"tg_id": tg_id})
            return False
