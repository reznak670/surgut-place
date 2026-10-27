"""formatting: карточка (§5.2–5.3), форматы §0.3, экранирование HTML."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.bot import formatting as fm
from app.bot.texts import T
from app.db.models import Role, Vibe
from tests.bot.helpers import FRI_19, booking, sc, slot, user

NBSP = " "

# ---------------------------------------------------------------- даты


def test_fmt_slot_in_local_tz():
    assert fm.fmt_slot(FRI_19) == "Пт 30.10, 19:00"
    # 20:00 UTC субботы = 01:00 воскресенья в Екатеринбурге
    assert fm.fmt_slot(datetime(2026, 10, 31, 20, 0, tzinfo=UTC)) == "Вс 01.11, 01:00"


@pytest.mark.parametrize(
    ("day", "expected"),
    [(26, "Пн"), (27, "Вт"), (28, "Ср"), (29, "Чт"), (30, "Пт"), (31, "Сб"), (32, "Вс")],
)
def test_weekdays(day, expected):
    dt = datetime(2026, 10, 1, 7, 0, tzinfo=UTC) + timedelta(days=day - 1)
    assert fm.fmt_slot(dt).startswith(expected + " ")


def test_fmt_slot_short_within_and_beyond_week():
    now = FRI_19 - timedelta(days=1)
    sat = FRI_19 + timedelta(days=1)
    assert fm.fmt_slot_short(sat, now) == "Сб 19:00"
    far = FRI_19 + timedelta(days=10)
    assert fm.fmt_slot_short(far, now) == "09.11, 19:00"


def test_fmt_time_and_date():
    assert fm.fmt_time(FRI_19) == "19:00"
    assert fm.fmt_date(FRI_19) == "30.10"


def test_weekend_dates():
    # сб 31.10 00:00 локально
    start = datetime(2026, 10, 30, 19, 0, tzinfo=UTC)
    assert fm.fmt_weekend_dates(start) == "31 октября – 1 ноября"
    assert fm.fmt_weekend_dates(start + timedelta(days=7)) == "7–8 ноября"


# ---------------------------------------------------------------- цена/длительность


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (3500, f"3{NBSP}500 ₽ на двоих"),
        (999, "999 ₽ на двоих"),
        (1000, f"1{NBSP}000 ₽ на двоих"),
        (125000, f"125{NBSP}000 ₽ на двоих"),
        (0, "Бесплатно"),
    ],
)
def test_price_value(value, expected):
    assert fm.fmt_price_value(value) == expected
    assert fm.fmt_price(sc(price_text="", price_value=value)) == expected


def test_price_text_wins():
    assert fm.fmt_price(sc(price_text="3 500 ₽, напитки отдельно", price_value=1)) == (
        "3 500 ₽, напитки отдельно"
    )
    assert fm.fmt_price(sc(price_text="   ", price_value=0)) == "Бесплатно"


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [(90, "1,5 ч"), (45, "45 мин"), (150, "2,5 ч"), (120, "2 ч"), (60, "1 ч"), (59, "59 мин")],
)
def test_minutes(minutes, expected):
    assert fm.fmt_minutes(minutes) == expected
    assert fm.fmt_duration(sc(duration_text="", duration_minutes=minutes)) == expected


def test_duration_text_wins():
    assert fm.fmt_duration(sc(duration_text="весь вечер", duration_minutes=90)) == "весь вечер"


# ---------------------------------------------------------------- мелочи


def test_title_short():
    assert fm.title_short("x" * 32) == "x" * 32
    cut = fm.title_short("Ужин на крыше с видом на Обь и звёзды")
    assert len(cut) <= 32
    assert cut.endswith("…")
    assert fm.title_short("Ужин на крыше с видом на Обь и звёзды").startswith("Ужин на крыше")


def test_vibe_tags_and_unknown_ignored():
    assert fm.vibe_tags(["romance", "food", "nope"]) == "💞 романтика · 🍜 вкусно"


def test_status_parts():
    assert fm.status_parts("confirmed") == ("✅", "Подтверждено")
    assert fm.status_parts("pending") == ("⏳", "Ждёт подтверждения")


def test_strip_emoji_label():
    assert fm.strip_emoji_label("💞 Романтика") == "Романтика"
    assert fm.strip_emoji_label("до 1 000 ₽") == "до 1 000 ₽"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+7 912 345-67-89", "+79123456789"),
        ("8 (912) 345-67-89", "+79123456789"),
        ("9123456789", "+79123456789"),
        ("@surgut_love", "@surgut_love"),
        ("  @surgut_love ", "@surgut_love"),
        ("@abc", None),
        ("12345", None),
        ("позвоните мне", None),
        ("+1234567890123456", None),
    ],
)
def test_normalize_contact(raw, expected):
    assert fm.normalize_contact(raw) == expected


@pytest.mark.parametrize(
    ("raw", "ok"),
    [("Аня", True), ("А", False), ("x" * 40, True), ("x" * 41, False), ("/start", False)],
)
def test_valid_name(raw, ok):
    assert fm.valid_name(raw) is ok


def test_share_url_contains_encoded_deeplink():
    url = fm.share_url("surgut_bot", sc(id=17, title="Ужин & вино"))
    assert url.startswith("https://t.me/share/url?url=")
    assert "https%3A%2F%2Ft.me%2Fsurgut_bot%3Fstart%3Dsc_17" in url
    assert "%26" in url  # амперсанд из заголовка закодирован, а не рвёт query


def test_plural_scenarios():
    assert [fm.plural_scenarios(n) for n in (1, 2, 5, 11, 21, 22, 112)] == [
        "сценарий",
        "сценария",
        "сценариев",
        "сценариев",
        "сценарий",
        "сценария",
        "сценариев",
    ]


def test_user_link_escapes_name():
    link = fm.user_link(5, "<Аня>", "anya")
    assert link == '<a href="tg://user?id=5">&lt;Аня&gt;</a> @anya'


def test_partner_label():
    assert fm.partner_label(None) == "—"
    assert (
        fm.partner_label(user(Role.partner, first_name="Ваня", username="vanya")) == "Ваня @vanya"
    )


# ---------------------------------------------------------------- карточка


def test_next_line_variants():
    s = sc()
    assert fm.next_line(s, slot(s)) == "Ближайшее: <b>Пт 30.10, 19:00</b>"
    assert fm.next_line(s, None) == T["card.next_none"]
    free = sc(need_booking=False, time_of_day="night")
    assert fm.next_line(free, None) == "Без записи — когда удобно (ночью)"


def test_card_caption_field_order():
    s = sc(
        title="Ужин на крыше",
        price_text="3 500 ₽ на двоих",
        duration_text="2,5 ч",
        vibes=["romance", "food"],
        location_name="Крыша «Северная»",
        address="ул. Ленина, 1",
        is_indoor=True,
    )
    cap = fm.card_caption(s, slot(s))
    assert cap.truncated is False
    text = cap.text
    order = [
        "<b>Ужин на крыше</b>",
        "💸 <b>3 500 ₽ на двоих</b> · ⏱ 2,5 ч",
        "🗓 Ближайшее: <b>Пт 30.10, 19:00</b>",
        "💞 романтика · 🍜 вкусно · 🏠 Под крышей",
        '📍 <a href="https://yandex.ru/maps/-/CDabc123">Крыша «Северная»</a>, ул. Ленина, 1',
        "<b>Как пройдёт вечер</b>\n19:00 — встреча\n19:30 — ужин",
        "<b>Что входит</b>\nужин на двоих, глинтвейн",
        T["card.booking_needed"],
    ]
    positions = [text.index(part) for part in order]
    assert positions == sorted(positions)
    assert text.startswith("<b>Ужин на крыше</b>\n\n")


def test_card_caption_without_booking_and_blocks():
    s = sc(need_booking=False, timing_text=None, includes_text=None, map_link=None)
    text = fm.card_caption(s, None).text
    assert "Как пройдёт вечер" not in text
    assert "Что входит" not in text
    assert "<a href" not in text
    assert text.endswith(T["card.booking_not_needed"])
    assert "Без записи — когда удобно (вечером)" in text


def test_card_caption_escapes_user_input():
    s = sc(
        title="<b>Ужин</b> & вино",
        location_name="Бар <i>«Мост»</i>",
        address="ул. <script>alert(1)</script>",
        timing_text="19:00 — <u>вход</u>",
        includes_text="чай & <торт>",
        map_link='https://maps.example/?q="x"&y=1',
    )
    text = fm.card_caption(s, None).text
    assert "<b>&lt;b&gt;Ужин&lt;/b&gt; &amp; вино</b>" in text
    assert "Бар &lt;i&gt;«Мост»&lt;/i&gt;" in text
    assert "ул. &lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert "19:00 — &lt;u&gt;вход&lt;/u&gt;" in text
    assert "чай &amp; &lt;торт&gt;" in text
    assert 'href="https://maps.example/?q=&quot;x&quot;&amp;y=1"' in text
    for tag in ("<script", "<u>", "<i>«", "<торт"):
        assert tag not in text


def _long_lines(prefix: str, n: int, width: int = 60) -> str:
    return "\n".join(f"{prefix} {i:02d} " + "x" * width for i in range(n))


def test_caption_truncates_includes_first():
    s = sc(timing_text=_long_lines("шаг", 3), includes_text=_long_lines("пункт", 30))
    cap = fm.card_caption(s, slot(s))
    assert cap.truncated is True
    assert fm.visible_len(cap.text) <= fm.CAPTION_LIMIT
    assert "Что входит" in cap.text
    assert "шаг 02" in cap.text  # тайминг не тронут
    assert "пункт 29" not in cap.text
    includes = cap.text.split("<b>Что входит</b>\n")[1]
    assert "…" in includes


def test_caption_truncates_timing_after_includes():
    s = sc(timing_text=_long_lines("шаг", 30), includes_text=_long_lines("пункт", 30))
    cap = fm.card_caption(s, slot(s))
    assert cap.truncated is True
    assert fm.visible_len(cap.text) <= fm.CAPTION_LIMIT
    assert "шаг 00" in cap.text
    assert "шаг 29" not in cap.text


def test_caption_never_drops_essentials_on_extreme_input():
    s = sc(
        title="Т" * 200,
        address="А" * 300,
        timing_text="Ш" * 5000,  # одна гигантская строка
        includes_text="В" * 5000,
        price_text="3 500 ₽ на двоих",
        duration_text="2 ч",
    )
    cap = fm.card_caption(s, slot(s))
    assert cap.truncated is True
    assert fm.visible_len(cap.text) <= fm.CAPTION_LIMIT
    assert "Т" * 200 in cap.text
    assert "3 500 ₽ на двоих" in cap.text
    assert "Ближайшее: <b>Пт 30.10, 19:00</b>" in cap.text
    assert T["card.booking_needed"] in cap.text
    assert 'href="https://yandex.ru/maps/-/CDabc123"' in cap.text
    assert "А" * 61 not in cap.text  # адрес обрезан до 60


def test_caption_limit_counts_visible_text_with_entities():
    # «&» превращается в «&amp;» (5 символов в HTML, 1 видимый) — лимит по видимому
    s = sc(includes_text="&" * 700, timing_text="19:00 — встреча")
    cap = fm.card_caption(s, None)
    assert fm.visible_len(cap.text) <= fm.CAPTION_LIMIT
    assert cap.truncated is False
    assert len(cap.text) > fm.CAPTION_LIMIT


def test_visible_len():
    assert fm.visible_len('<b>a</b> <a href="x">bc</a> &amp;') == 6


def test_card_details_full_text_escaped():
    s = sc(title="A & B", timing_text="<1>", includes_text="<2>")
    text = fm.card_details(s)
    assert text.startswith("<b>A &amp; B</b>")
    assert "&lt;1&gt;" in text and "&lt;2&gt;" in text


# ---------------------------------------------------------------- список §5.5


def test_catalog_text():
    now = FRI_19 - timedelta(days=1)
    items = [
        sc(
            id=1,
            title="Раз",
            price_text="",
            price_value=3500,
            duration_text="",
            duration_minutes=150,
            vibes=[Vibe.romance.value],
        ),
        sc(id=2, title="Два <b>", need_booking=False, vibes=[]),
        sc(id=3, title="Три"),
    ]
    s1 = slot(items[0], FRI_19 + timedelta(days=1))
    text = fm.catalog_text("🌆 Вечера на выходные", items, {1: s1, 2: None, 3: None}, now, offset=5)
    assert text.startswith("<b>🌆 Вечера на выходные</b> · 6–8")
    assert f"<b>6. Раз</b>\n3{NBSP}500 ₽ на двоих · 2,5 ч · Сб 19:00\n💞 романтика" in text
    assert "<b>7. Два &lt;b&gt;</b>" in text
    assert "без записи" in text
    assert "<b>8. Три</b>" in text and "мест нет" in text


# ---------------------------------------------------------------- брони


def test_booking_new_text_escapes_contact_and_name():
    partner = user(Role.partner, id=2, tg_id=200)
    b = booking(sc(title="Ужин <x>", partner=partner), name="<b>Аня</b>", contact="@anya<script>")
    text = fm.booking_new_text(b, 1, with_prefix=True)
    assert "👤 &lt;b&gt;Аня&lt;/b&gt;" in text
    assert "📞 @anya&lt;script&gt;" in text
    assert "Ужин &lt;x&gt;" in text
    assert "после этой осталось мест: 1" in text
    assert T["partner.no_partner_prefix"] not in text


def test_booking_new_text_prefix_without_partner():
    b = booking(sc(partner=None))
    text = fm.booking_new_text(b, 0, with_prefix=True)
    assert text.startswith(T["partner.no_partner_prefix"] + "\n")
    assert not fm.booking_new_text(b, 0, with_prefix=False).startswith("<i>")


def test_booking_cancelled_partner_text():
    b = booking(sc(title="A&B", partner=user(Role.partner, id=2, tg_id=200)), name="<Оля>")
    text = fm.booking_cancelled_partner_text(b)
    assert "A&amp;B" in text and "&lt;Оля&gt;" in text
    assert "Бронь отменена гостем" in text


def test_join_within_whole_parts_and_oversized_first():
    parts = ["<b>Один</b>", "<b>Два</b>", "<b>Три</b>"]
    assert fm.join_within(parts, limit=4096) == "\n".join(parts)
    cut = fm.join_within(parts, limit=20)
    assert cut == "<b>Один</b>\n…"  # целые записи, теги не порваны

    # единственная часть длиннее лимита → простой текст, заново экранированный
    big = "<b>" + "x &lt;y&gt; " * 50 + "</b>"
    out = fm.join_within([big, "<b>z</b>"], limit=40)
    assert len(out) <= 40
    assert "<b>" not in out and "</b>" not in out
    assert out.startswith("x &lt;y&gt; ") and out.endswith("…")
    assert "&lt" not in out.replace("&lt;", "")  # сущности не разрезаны


def test_guest_contact_uses_active_partner_rule():
    with_partner = booking(sc(partner=user(Role.partner, id=2, tg_id=200)))
    assert fm.guest_contact(with_partner, for_admin=True) == fm.CONTACT_AT_PARTNER
    # бывший партнёр (роль снята) — заявку ведёт админ-чат, контакт админу нужен
    ex_partner = booking(sc(partner=user(Role.user, id=2, tg_id=200)))
    assert fm.guest_contact(ex_partner, for_admin=True) != fm.CONTACT_AT_PARTNER
    assert fm.booking_new_text(ex_partner, 1, with_prefix=True).startswith(
        T["partner.no_partner_prefix"]
    )
