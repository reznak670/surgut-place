"""TelegramNotifier — реализация протокола app.scheduler.notifier.Notifier.

Каждый метод возвращает True, если сообщение доставлено, и False при любой ошибке
Telegram (бот заблокирован, чат не найден и т.п.). Исключения наружу не выходят.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from app.bot import keyboards as kb
from app.bot.callbacks import CardCb, NavCb
from app.bot.common import NO_PREVIEW
from app.bot.formatting import (
    esc,
    fmt_duration,
    fmt_price,
    fmt_slot,
    fmt_slot_short,
    fmt_time,
    fmt_weekend_dates,
    local,
    partner_label,
    place_link,
    title_short,
    vibe_tags,
)
from app.bot.texts import T, fact_of_week, pick, seasonal
from app.config import settings
from app.db.models import Booking, Scenario
from app.services.timeutil import now_utc, weekend_window

log = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def _deliver(
        self, kind: str, chat_id: int | None, send: Callable[[], Awaitable[Any]]
    ) -> bool:
        if chat_id is None:
            log.warning("notify: no chat", extra={"kind": kind})
            return False
        try:
            await send()
            return True
        except TelegramAPIError as e:
            log.info("notify: not delivered", extra={"kind": kind, "error": type(e).__name__})
            return False
        except Exception:
            log.exception("notify: unexpected error", extra={"kind": kind})
            return False

    # ------------------------------------------------------------ напоминания

    async def booking_reminder(self, booking: Booking, hours: int) -> bool:
        sc = booking.slot.scenario
        starts = booking.slot.starts_at
        if hours == 24:
            text = T["notify.reminder_24h"].format(
                title=esc(sc.title),
                slot=fmt_slot(starts),
                place=place_link(sc),
                address=esc(sc.address),
            )
        else:
            hint = (
                T["notify.indoor_hint"]
                if sc.is_indoor
                else seasonal("season.outdoor_hint", local(starts).date())
            )
            text = T["notify.reminder_3h"].format(
                title=esc(sc.title),
                time=fmt_time(starts),
                place=place_link(sc),
                address=esc(sc.address),
                weather_hint=hint,
            )
        return await self._deliver(
            "reminder",
            booking.user.tg_id,
            lambda: self.bot.send_message(
                booking.user.tg_id,
                text,
                reply_markup=kb.reminder_kb(booking, hours),
                link_preview_options=NO_PREVIEW,
            ),
        )

    # ------------------------------------------------------------ фидбэк

    async def feedback_request(self, booking: Booking) -> bool:
        text = T["feedback.ask"].format(
            title=esc(booking.slot.scenario.title), feedback_hint=pick("rot.feedback_ask")
        )
        return await self._deliver(
            "feedback",
            booking.user.tg_id,
            lambda: self.bot.send_message(
                booking.user.tg_id,
                text,
                reply_markup=kb.rating_kb(booking.id),
                link_preview_options=NO_PREVIEW,
            ),
        )

    # ------------------------------------------------------------ дайджест

    async def weekend_digest(
        self,
        tg_id: int,
        scenarios: list[Scenario],
        nearest: dict[int, datetime] | None = None,
    ) -> bool:
        """nearest: scenario_id → начало ближайшего свободного слота (aware UTC)."""
        if not scenarios:
            return False
        now = now_utc()
        start, _ = weekend_window(now)
        today = local(now).date()
        parts = [pick("rot.digest_intro").format(weekend_dates=fmt_weekend_dates(start))]
        for i, sc in enumerate(scenarios[:5], start=1):
            meta = [esc(fmt_price(sc)), esc(fmt_duration(sc))]
            if not sc.need_booking:
                meta.append(T["catalog.next_free_short"])
            elif nearest is not None:
                at = nearest.get(sc.id)
                meta.append(fmt_slot_short(at, now) if at else T["catalog.next_none_short"])
            parts.append(
                f"<b>{i}. {esc(sc.title)}</b>\n{' · '.join(meta)}\n"
                f"{vibe_tags(sc.vibes or [])}".rstrip()
            )
        tail = seasonal("season.digest_tail", today)
        if tail:
            parts.append(tail)
        parts.append(T["facts.line"].format(fact=esc(fact_of_week(today))))
        markup = kb.inline(
            [
                *(
                    [kb.btn(f"{i}. {title_short(sc.title, 28)}", CardCb(a="open", id=sc.id))]
                    for i, sc in enumerate(scenarios[:5], start=1)
                ),
                [kb.btn(T["notify.btn_digest_filters"], NavCb(to="filters"))],
            ]
        )
        text = "\n\n".join(parts)
        return await self._deliver(
            "digest",
            tg_id,
            lambda: self.bot.send_message(
                tg_id, text, reply_markup=markup, link_preview_options=NO_PREVIEW
            ),
        )

    # ------------------------------------------------------------ реактивация

    async def reactivation(self, tg_id: int) -> bool:
        text = seasonal("season.reactivation", local(now_utc()).date())
        return await self._deliver(
            "reactivation",
            tg_id,
            lambda: self.bot.send_message(
                tg_id, text, reply_markup=kb.nav_kb("notify.btn_watch", "weekend")
            ),
        )

    # ------------------------------------------------------------ просрочка (админ-чат)

    async def overdue_booking_alert(self, booking: Booking) -> bool:
        sc = booking.slot.scenario
        text = T["admin.alert_overdue"].format(
            title=esc(sc.title),
            slot=fmt_slot(booking.slot.starts_at),
            partner_or_dash=partner_label(sc.partner),
        )
        chat_id = settings.ADMIN_CHAT_ID
        return await self._deliver(
            "overdue",
            chat_id,
            lambda: self.bot.send_message(chat_id, text, link_preview_options=NO_PREVIEW),  # type: ignore[arg-type]
        )
