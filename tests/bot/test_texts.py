"""texts: сезоны по дате, ротации, плейсхолдеры (format без KeyError), ключевые тексты."""

from __future__ import annotations

import ast
import string
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.bot import texts
from app.bot.texts import FACTS, ROT, SEASONS, T, fact_of_week, pick, season, seasonal, with_privacy
from app.config import settings

BOT_DIR = Path(texts.__file__).resolve().parent
_fmt = string.Formatter()


def placeholders(template: str) -> set[str]:
    return {name for _, name, _, _ in _fmt.parse(template) if name is not None}


# Типовые данные для всех плейсхолдеров, встречающихся в текстах
TYPICAL: dict[str, object] = {
    "active_scenarios_week": 4,
    "address": "ул. Ленина, 1",
    "attendance_rate": "80%",
    "attended": " · были ✓",
    "avg_rating": "4,6",
    "booked": 1,
    "booking_line": "<i>Бронь не нужна</i>",
    "bookings": 3,
    "capacity": 4,
    "comment": "Было холодно",
    "comment_or_dash": "—",
    "contact": "+79123456789",
    "conversion": "12%",
    "count": 10,
    "days": 7,
    "duration": "2 ч",
    "end": 5,
    "error": "can't parse entities",
    "errors_block": "",
    "fact": "Сургут основали в 1594 году.",
    "failed": 1,
    "feedback_hint": "От 1 до 5.",
    "field_label": "Название",
    "includes_block": "",
    "includes_text": "ужин",
    "left": 2,
    "limit": 60,
    "line": "31.10 19:00 4",
    "list_title": "🌆 Вечера на выходные",
    "location_name": "Крыша",
    "n": 3,
    "name": "Аня",
    "next_line": "Ближайшее: <b>Сб 31.10, 19:00</b>",
    "next_short": "Сб 19:00",
    "ok": 2,
    "partner_or_dash": "—",
    "pending_count": 1,
    "place": "Крыша",
    "price": "3 500 ₽ на двоих",
    "privacy_url": "https://example.org/privacy",
    "rating": 2,
    "reason": "неверный формат",
    "repeat_rate": "10%",
    "segment_label": "Всем",
    "sent": 9,
    "share": 35,
    "slot": "Сб 31.10, 19:00",
    "slot_date": "31.10",
    "slot_short": "Сб 19:00",
    "stars": "★★☆☆☆",
    "start": 1,
    "status_emoji": "✅",
    "status_label": "Подтверждено",
    "tags": "💞 романтика",
    "text": "Классное место",
    "time": "19:00",
    "time_of_day": "вечером",
    "timing_block": "",
    "timing_text": "19:00 — встреча",
    "title": "Ужин на крыше",
    "title_short": "Ужин на крыше",
    "top_lines": "35% — Ужин",
    "unique_viewers": 5,
    "user_link": '<a href="tg://user?id=1">Аня</a>',
    "username": "anya",
    "value": "любой",
    "vibe_tags": "💞 романтика",
    "views": 100,
    "warnings": "✅ Без перекосов.",
    "weather_hint": "Всё под крышей.",
    "weekend_dates": "31 октября – 1 ноября",
    "who": "Ваня",
    "word": "сценария",
}


def _all_templates() -> list[tuple[str, str]]:
    items = [(k, v) for k, v in T.items()]
    items += [(f"{k}[{i}]", v) for k, vs in ROT.items() for i, v in enumerate(vs)]
    items += [(f"{k}[{s}]", v) for k, d in SEASONS.items() for s, v in d.items()]
    return items


@pytest.mark.parametrize(("key", "template"), _all_templates(), ids=lambda x: str(x)[:40])
def test_template_formats_with_typical_data(key, template):
    unknown = placeholders(template) - TYPICAL.keys()
    assert not unknown, f"{key}: неизвестные плейсхолдеры {unknown}"
    template.format(**TYPICAL)  # без KeyError/ValueError/IndexError


def _call_sites() -> list[tuple[str, int, list[str], set[str] | None]]:
    """Все `T["key"].format(...)` и `pick("rot.key").format(...)` в app/bot.

    Возвращает (файл, строка, ключи, переданные kwargs | None при **kwargs).
    """
    sites = []
    for path in sorted(BOT_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "format"
            ):
                continue
            target = node.func.value
            keys: list[str] = []
            if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
                if target.value.id != "T":
                    continue
                sl = target.slice
                if isinstance(sl, ast.Constant):
                    keys = [sl.value]
                elif isinstance(sl, ast.IfExp) and all(
                    isinstance(x, ast.Constant) for x in (sl.body, sl.orelse)
                ):
                    keys = [sl.body.value, sl.orelse.value]  # type: ignore[attr-defined]
            elif (
                isinstance(target, ast.Call)
                and isinstance(target.func, ast.Name)
                and target.func.id == "pick"
                and isinstance(target.args[0], ast.Constant)
            ):
                keys = [f"pick:{target.args[0].value}"]
            if not keys:
                continue
            kwargs: set[str] | None = {kw.arg for kw in node.keywords if kw.arg is not None}
            if any(kw.arg is None for kw in node.keywords):
                kwargs = None
            sites.append((path.name, node.lineno, keys, kwargs))
    return sites


def test_call_sites_found():
    assert len(_call_sites()) > 30


@pytest.mark.parametrize("site", _call_sites(), ids=lambda s: f"{s[0]}:{s[1]}")
def test_call_sites_pass_every_placeholder(site):
    """Каждый `.format(...)` в коде бота передаёт все плейсхолдеры шаблона — KeyError не будет."""
    filename, line, keys, kwargs = site
    if kwargs is None:
        pytest.skip("**kwargs — статически не проверить")
    for key in keys:
        if key.startswith("pick:"):
            templates = ROT[key[5:]]
        else:
            assert key in T, f"{filename}:{line}: нет ключа {key!r} в T"
            templates = [T[key]]
        for template in templates:
            missing = placeholders(template) - kwargs
            assert not missing, f"{filename}:{line} {key}: не передано {missing}"


def test_all_t_lookups_exist():
    """Любой `T["literal"]` в коде бота ссылается на существующий ключ."""
    missing = []
    for path in sorted(BOT_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name)
                and node.value.id == "T"
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.ctx, ast.Load)
                and node.slice.value not in T
            ):
                missing.append(f"{path.name}:{node.lineno} {node.slice.value}")
    assert not missing


# ---------------------------------------------------------------- ротации


def test_rotations_non_empty():
    expected = {
        "rot.empty_weekend",
        "rot.empty_search",
        "rot.empty_bookings",
        "rot.empty_saved",
        "rot.booking_wait",
        "rot.checking",
        "rot.error_unknown",
        "rot.error_generic",
        "rot.digest_intro",
        "rot.feedback_ask",
    }
    assert expected <= ROT.keys()
    for key, variants in ROT.items():
        assert variants, key
        assert all(v.strip() for v in variants), key


def test_rotation_variants_share_placeholders():
    for key, variants in ROT.items():
        sets = {frozenset(placeholders(v)) for v in variants}
        assert len(sets) == 1, key


def test_rotation_first_variant_matches_design():
    assert ROT["rot.error_unknown"][0] == "Не понял 🙂 Я пока умею только в кнопки — меню внизу."
    assert "{weekend_dates}" in ROT["rot.digest_intro"][0]


def test_pick_returns_variant():
    for key in ROT:
        assert pick(key) in ROT[key]


# ---------------------------------------------------------------- сезоны


@pytest.mark.parametrize(
    ("d", "expected"),
    [
        (date(2026, 12, 1), "deep_winter"),
        (date(2027, 1, 15), "deep_winter"),
        (date(2028, 2, 29), "deep_winter"),
        (date(2026, 11, 1), "winter"),
        (date(2026, 11, 30), "winter"),
        (date(2027, 3, 1), "winter"),
        (date(2027, 3, 31), "winter"),
        (date(2027, 4, 1), "thaw"),
        (date(2027, 5, 24), "thaw"),
        (date(2027, 5, 25), "white_nights"),
        (date(2027, 7, 15), "white_nights"),
        (date(2027, 7, 16), "summer"),
        (date(2027, 8, 31), "summer"),
        (date(2026, 9, 1), "autumn"),
        (date(2026, 10, 31), "autumn"),
    ],
)
def test_season_boundaries(d, expected):
    assert season(d) == expected


def test_season_covers_every_day():
    d = date(2027, 1, 1)
    valid = {"deep_winter", "winter", "thaw", "white_nights", "summer", "autumn"}
    while d.year == 2027:
        assert season(d) in valid
        d += timedelta(days=1)


def test_seasonal_default_and_frost(monkeypatch):
    autumn = date(2026, 10, 1)
    assert seasonal("season.outdoor_hint", autumn) == SEASONS["season.outdoor_hint"]["autumn"]
    # нет варианта сезона → default
    assert seasonal("season.reactivation", autumn) == SEASONS["season.reactivation"]["default"]
    assert (
        seasonal("season.outdoor_hint", autumn, frost=True)
        == SEASONS["season.outdoor_hint"]["frost"]
    )
    monkeypatch.setattr(settings, "FROST_MODE", True)
    assert seasonal("season.digest_tail", autumn) == SEASONS["season.digest_tail"]["frost"]
    # у ключа без frost-варианта мороз не ломает выбор
    assert seasonal("season.reactivation", autumn) == SEASONS["season.reactivation"]["default"]


def test_seasons_have_defaults():
    for key, variants in SEASONS.items():
        assert "default" in variants, key
    assert SEASONS["season.outdoor_hint"]["default"]
    assert SEASONS["season.reactivation"]["default"]


# ---------------------------------------------------------------- факты и ПД


def test_facts():
    assert len(FACTS) == 20
    assert all(f.strip() for f in FACTS)
    monday = date(2026, 10, 26)
    week = {fact_of_week(monday + timedelta(days=i)) for i in range(7)}
    assert len(week) == 1
    first_monday_2027 = date(2027, 1, 4)
    weeks = {fact_of_week(first_monday_2027 + timedelta(weeks=i)) for i in range(20)}
    assert len(weeks) == 20  # внутри года 20 недель без повторов
    assert "{fact}" in T["facts.line"]


def test_facts_no_repeat_across_new_year():
    monday = date(2026, 10, 26)
    facts = {fact_of_week(monday + timedelta(weeks=i)) for i in range(20)}
    assert len(facts) == 20


def test_with_privacy_url_and_empty():
    text = with_privacy(T["onboarding.deeplink_welcome"], 'https://x.org/p?a=1&b="2"')
    assert 'href="https://x.org/p?a=1&amp;b=&quot;2&quot;"' in text
    plain = with_privacy(T["onboarding.deeplink_welcome"], "")
    assert "<a " not in plain
    assert "политикой обработки персональных данных" in plain


# ---------------------------------------------------------------- тексты как в design.md


def test_menu_buttons_match_design():
    assert T["menu.btn_weekend"] == "🌆 Вечера на выходные"
    assert T["menu.btn_filters"] == "🔎 Найти по фильтрам"
    assert T["menu.btn_my"] == "🎟 Мои брони"
    assert T["menu.btn_saved"] == "❤️ Сохранённое"
    assert T["menu.btn_suggest"] == "📍 Предложить место"
    assert T["menu.btn_about"] == "ℹ️ О проекте"
    assert T["menu.btn_partner"] == "🏪 Кабинет партнёра"
    assert T["menu.btn_admin"] == "🛠 Админка"


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("error.no_access", "Сюда нет доступа."),
        ("error.expired", "Устарело — откройте заново"),
        ("error.blocked_fsm", "Сейчас жду текст. Или нажмите «Отмена»."),
        ("partner.booking_forbidden", "Это не ваша заявка"),
        ("partner.booking_already", "Эту заявку уже обработали"),
        ("card.saved_toast", "Сохранили ❤️"),
        ("card.unsaved_toast", "Убрали из сохранённого"),
        ("filters.reset_done", "Фильтры сброшены"),
        ("catalog.end", "Это всё, что есть"),
        ("booking.aborted", "Ок, бронь не оформляем. Вечер никуда не денется."),
        ("feedback.thanks_comment", "Спасибо, что написали. Прочитаем лично."),
        ("feedback.skipped", "Понимаем. Спасибо за оценку."),
    ],
)
def test_texts_match_design(key, expected):
    assert T[key] == expected


def test_enum_dictionaries_complete():
    from app.db.models import BookingStatus, TimeOfDay, Vibe

    assert set(texts.VIBE) == {v.value for v in Vibe} == set(texts.VIBE_TAG)
    assert set(texts.TIME_OF_DAY) == {t.value for t in TimeOfDay} == set(texts.TIME_OF_DAY_LOWER)
    assert set(texts.STATUS) == {s.value for s in BookingStatus}
