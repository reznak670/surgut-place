"""Форматирование данных для сообщений (docs/design.md §0.3, §5).

Все значения, которые вводят люди (заголовки, адреса, имена, комментарии), проходят через
`esc()` перед подстановкой в HTML-шаблон.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import quote, urlencode

from app.bot.texts import (
    INDOOR,
    STATUS,
    TIME_OF_DAY_LOWER,
    VIBE_TAG,
    T,
)
from app.config import settings
from app.db.models import Booking, Scenario, Slot, User
from app.services import bookings as bookings_service

CAPTION_LIMIT = 1024
MESSAGE_LIMIT = 4096
BUTTON_TITLE_MAX = 32
ADDRESS_MAX = 60
NBSP = " "
WEEKDAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
MONTHS_GEN = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def esc(value: object | None) -> str:
    return html.escape(str(value)) if value is not None else ""


def esc_url(url: str) -> str:
    return html.escape(url, quote=True)


# ---------------------------------------------------------------- дата/время


def local(dt: datetime) -> datetime:
    return dt.astimezone(settings.tz)


def fmt_slot(dt: datetime) -> str:
    """`Пт 31.10, 19:00` в settings.tz."""
    d = local(dt)
    return f"{WEEKDAYS[d.weekday()]} {d:%d.%m}, {d:%H:%M}"


def fmt_slot_short(dt: datetime, now: datetime) -> str:
    """`Сб 19:00` в пределах 7 дней, иначе `01.11, 19:00`."""
    d = local(dt)
    if timedelta(0) <= dt - now <= timedelta(days=7):
        return f"{WEEKDAYS[d.weekday()]} {d:%H:%M}"
    return f"{d:%d.%m}, {d:%H:%M}"


def fmt_time(dt: datetime) -> str:
    return f"{local(dt):%H:%M}"


def fmt_date(dt: datetime) -> str:
    return f"{local(dt):%d.%m}"


def fmt_weekend_dates(start: datetime) -> str:
    """Начало выходных (UTC) → `1–2 ноября` или `31 октября – 1 ноября`."""
    sat = local(start).date()
    sun = sat + timedelta(days=1)
    if sat.month == sun.month:
        return f"{sat.day}–{sun.day} {MONTHS_GEN[sat.month - 1]}"
    return f"{sat.day} {MONTHS_GEN[sat.month - 1]} – {sun.day} {MONTHS_GEN[sun.month - 1]}"


# ---------------------------------------------------------------- цена/длительность


def fmt_thousands(value: int) -> str:
    return f"{value:,}".replace(",", NBSP)


def fmt_price_value(value: int) -> str:
    """`3 500 ₽ на двоих` (неразрывный пробел), 0 → `Бесплатно`."""
    if value == 0:
        return "Бесплатно"
    return f"{fmt_thousands(value)} ₽ на двоих"


def fmt_price(sc: Scenario) -> str:
    return (sc.price_text or "").strip() or fmt_price_value(sc.price_value)


def fmt_minutes(minutes: int) -> str:
    """90 → `1,5 ч`, 45 → `45 мин`, 120 → `2 ч`."""
    if minutes < 60:
        return f"{minutes} мин"
    hours = round(minutes / 60, 1)
    text = f"{hours:g}".replace(".", ",")
    return f"{text} ч"


def fmt_duration(sc: Scenario) -> str:
    return (sc.duration_text or "").strip() or fmt_minutes(sc.duration_minutes)


# ---------------------------------------------------------------- мелочи


def title_short(title: str, limit: int = BUTTON_TITLE_MAX) -> str:
    """Для кнопок: ≤32 символов, иначе 31 + «…» (кнопки не HTML — без экранирования)."""
    title = " ".join(title.split())
    return title if len(title) <= limit else title[: limit - 1].rstrip() + "…"


def vibe_tags(vibes: list[str]) -> str:
    return " · ".join(VIBE_TAG[v] for v in vibes if v in VIBE_TAG)


def place_link(sc: Scenario) -> str:
    """`<a href="map">Место</a>` или просто название, если ссылки нет."""
    name = esc(sc.location_name)
    if sc.map_link:
        return f'<a href="{esc_url(sc.map_link)}">{name}</a>'
    return name


def status_parts(status: str) -> tuple[str, str]:
    """`✅ Подтверждено` → (`✅`, `Подтверждено`)."""
    emoji, _, label = STATUS[str(status)].partition(" ")
    return emoji, label


def strip_emoji_label(label: str) -> str:
    """`💞 Романтика` → `Романтика` (для ✓ в фильтрах и значений в корне фильтров)."""
    first, _, rest = label.partition(" ")
    if rest and not any(ch.isalnum() for ch in first):
        return rest
    return label


def visible_len(html_text: str) -> int:
    """Длина видимого текста после разбора HTML (как считает Telegram)."""
    return len(html.unescape(re.sub(r"<[^>]+>", "", html_text)))


def truncate_plain(text: str, limit: int) -> str:
    """Обрезка ИСХОДНОГО текста (до экранирования/разметки)."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def esc_trunc(text: str, limit: int) -> str:
    """html.escape с гарантией: результат ≤ limit символов. Режем исходник, а не HTML,
    поэтому сущности (&amp; и т.п.) не рвутся."""
    out = esc(text)
    if len(out) <= limit:
        return out
    lo, hi = 0, len(text)  # самый длинный префикс, чей esc(prefix + "…") влезает
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if len(esc(text[:mid] + "…")) <= limit:
            lo = mid
        else:
            hi = mid - 1
    return esc(text[:lo] + "…")


def strip_tags(html_text: str) -> str:
    """HTML-фрагмент → простой текст (теги убраны, сущности раскодированы)."""
    return html.unescape(re.sub(r"<[^>]+>", "", html_text))


def join_within(parts: list[str], sep: str = "\n", limit: int = MESSAGE_LIMIT) -> str:
    """Склеивает HTML-фрагменты целиком, пока влезает в limit: теги не режутся посередине.
    Если что-то не влезло — в конце «…»."""
    out = ""
    for i, part in enumerate(parts):
        candidate = f"{out}{sep}{part}" if i else part
        if len(candidate) > limit - 2:
            if out:
                return out + sep + "…"
            # единственная часть длиннее лимита: в простой текст и заново экранируем
            return esc_trunc(strip_tags(part), limit)
        out = candidate
    return out


def user_link(tg_id: int, first_name: str | None, username: str | None) -> str:
    link = f'<a href="tg://user?id={tg_id}">{esc(first_name or tg_id)}</a>'
    return link + (f" @{esc(username)}" if username else "")


def partner_label(user: User | None) -> str:
    if user is None:
        return "—"
    name = esc(user.first_name or user.tg_id)
    return f"{name} @{esc(user.username)}" if user.username else name


def stars(rating: int) -> str:
    return "★" * rating + "☆" * (5 - rating)


def plural_scenarios(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "сценарий"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "сценария"
    return "сценариев"


def share_url(bot_username: str, scenario: Scenario) -> str:
    deeplink = f"https://t.me/{bot_username}?start=sc_{scenario.id}"
    text = T["card.share_text"].format(title=scenario.title)
    return "https://t.me/share/url?" + urlencode({"url": deeplink, "text": text}, quote_via=quote)


# ---------------------------------------------------------------- карточка (§5)


def next_line(sc: Scenario, next_slot: Slot | None) -> str:
    if not sc.need_booking:
        return T["card.next_free"].format(time_of_day=TIME_OF_DAY_LOWER[str(sc.time_of_day)])
    if next_slot is not None:
        return T["card.next_slot"].format(slot=fmt_slot(next_slot.starts_at))
    return T["card.next_none"]


def next_short(sc: Scenario, next_slot: Slot | None, now: datetime) -> str:
    if not sc.need_booking:
        return T["catalog.next_free_short"]
    if next_slot is None:
        return T["catalog.next_none_short"]
    return fmt_slot_short(next_slot.starts_at, now)


def _lines(text: str | None) -> list[str]:
    return [ln for ln in (text or "").strip().splitlines() if ln.strip()]


@dataclass
class Caption:
    text: str
    truncated: bool


def card_caption(sc: Scenario, next_slot: Slot | None, limit: int = CAPTION_LIMIT) -> Caption:
    """Подпись карточки с сокращением до `limit` видимых символов (§5.3):
    «Что входит» по строкам → тайминг по строкам → убрать «Что входит» → адрес до 60."""
    timing = _lines(sc.timing_text)
    includes = _lines(sc.includes_text)
    timing_cut = includes_cut = False
    show_includes = bool(includes)
    address = sc.address
    hard_limit: int | None = None
    truncated = False

    def build() -> str:
        timing_text = "\n".join(esc(x) for x in timing) + ("…" if timing_cut else "")
        if hard_limit is not None:
            timing_text = esc(truncate_plain("\n".join(timing), hard_limit))
        includes_text = "\n".join(esc(x) for x in includes) + ("…" if includes_cut else "")
        tags = " · ".join(p for p in (vibe_tags(sc.vibes or []), INDOOR[bool(sc.is_indoor)]) if p)
        return T["card.caption"].format(
            title=esc(sc.title),
            price=esc(fmt_price(sc)),
            duration=esc(fmt_duration(sc)),
            next_line=next_line(sc, next_slot),
            tags=tags,
            place=place_link(sc),
            address=esc(address),
            timing_block=(
                T["card.timing_block"].format(timing_text=timing_text)
                if timing or hard_limit is not None
                else ""
            ),
            includes_block=(
                T["card.includes_block"].format(includes_text=includes_text)
                if show_includes
                else ""
            ),
            booking_line=T["card.booking_needed" if sc.need_booking else "card.booking_not_needed"],
        )

    text = build()
    while visible_len(text) > limit:
        truncated = True
        if show_includes and len(includes) > 1:
            includes.pop()
            includes_cut = True
        elif len(timing) > 1:
            timing.pop()
            timing_cut = True
        elif show_includes:
            show_includes = False
        elif len(address) > ADDRESS_MAX:
            address = truncate_plain(address, ADDRESS_MAX)
        else:
            # крайний случай: одна гигантская строка тайминга — режем по символам
            excess = visible_len(text) - limit
            current = hard_limit if hard_limit is not None else len("\n".join(timing))
            if current <= 1:
                break
            hard_limit = max(current - excess, 1)
        text = build()
    return Caption(text, truncated)


def card_details(sc: Scenario) -> str:
    """Полные тайминг и «что входит». Режем исходный текст до экранирования (≤4096 в сумме:
    заголовок ≤200 символов → ≤1000 после esc, блоки по ≤1400)."""
    timing = (sc.timing_text or "").strip()
    includes = (sc.includes_text or "").strip()
    return T["card.details"].format(
        title=esc_trunc(sc.title, 1000),
        timing_block=(
            T["card.timing_block"].format(timing_text=esc_trunc(timing, 1400)) if timing else ""
        ),
        includes_block=(
            T["card.includes_block"].format(includes_text=esc_trunc(includes, 1400))
            if includes
            else ""
        ),
    )


# ---------------------------------------------------------------- список (§5.5)


def catalog_items(
    scenarios: list[Scenario],
    next_slots: dict[int, Slot | None],
    now: datetime,
    start: int = 1,
) -> str:
    parts = []
    for i, sc in enumerate(scenarios, start=start):
        parts.append(
            T["catalog.item"]
            .format(
                n=i,
                title=esc(sc.title),
                price=esc(fmt_price(sc)),
                duration=esc(fmt_duration(sc)),
                next_short=next_short(sc, next_slots.get(sc.id), now),
                vibe_tags=vibe_tags(sc.vibes or []),
            )
            .rstrip()
        )
    return "\n\n".join(parts)


def catalog_text(
    list_title: str,
    scenarios: list[Scenario],
    next_slots: dict[int, Slot | None],
    now: datetime,
    offset: int = 0,
) -> str:
    header = T["catalog.header"].format(
        list_title=list_title, start=offset + 1, end=offset + len(scenarios)
    )
    return header + "\n\n" + catalog_items(scenarios, next_slots, now, start=offset + 1)


# ---------------------------------------------------------------- брони


CONTACT_AT_PARTNER = "контакт у партнёра"


def guest_contact(booking: Booking, *, for_admin: bool) -> str:
    """ПД: контакт гостя видит только партнёр этой брони. Админу — только если партнёра нет
    (тогда заявку обрабатывает админ-чат)."""
    if for_admin and bookings_service.has_active_partner(booking):
        return CONTACT_AT_PARTNER
    return esc(booking.contact)


def booking_new_text(
    booking: Booking, left: int, *, with_prefix: bool, mask_contact: bool = False
) -> str:
    sc = booking.slot.scenario
    text = T["partner.booking_new"].format(
        title=esc(sc.title),
        slot=fmt_slot(booking.slot.starts_at),
        left=left,
        name=esc(booking.name),
        contact=guest_contact(booking, for_admin=True) if mask_contact else esc(booking.contact),
    )
    if with_prefix and not bookings_service.has_active_partner(booking):
        text = T["partner.no_partner_prefix"] + "\n" + text
    return text


def booking_cancelled_partner_text(booking: Booking) -> str:
    sc = booking.slot.scenario
    text = T["partner.booking_cancelled"].format(
        title=esc(sc.title), slot=fmt_slot(booking.slot.starts_at), name=esc(booking.name)
    )
    if not bookings_service.has_active_partner(booking):
        text = T["partner.no_partner_prefix"] + "\n" + text
    return text


# ---------------------------------------------------------------- контакт/имя

_PHONE_RE = re.compile(r"^\+?[\d\s()\-]+$")
_USERNAME_RE = re.compile(r"^@[A-Za-z0-9_]{5,32}$")


def normalize_contact(text: str) -> str | None:
    """Телефон (10–15 цифр, допускаем +7/8) или @username (5–32). None — не похоже."""
    text = text.strip()
    if _USERNAME_RE.match(text):
        return text
    if _PHONE_RE.match(text):
        digits = re.sub(r"\D", "", text)
        if 10 <= len(digits) <= 15:
            if len(digits) == 11 and digits[0] == "8":
                digits = "7" + digits[1:]
            elif len(digits) == 10:
                digits = "7" + digits
            return "+" + digits
    return None


def valid_name(text: str) -> bool:
    text = text.strip()
    return 2 <= len(text) <= 40 and not text.startswith("/")
