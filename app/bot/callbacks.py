"""CallbackData-фабрики. Префиксы короткие, итоговая строка ≤ 64 байт (проверяется тестом).

Фильтры каталога кодируются прямо в callback (5 символов), чтобы экран фильтров
и пагинация переживали рестарт бота без FSM.
"""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData

from app.db.models import TimeOfDay, Vibe
from app.services.scenarios import Filters


class NavCb(CallbackData, prefix="nav"):
    """Переход на экран: weekend / filters / my / saved / suggest / menu."""

    to: str


class OnbCb(CallbackData, prefix="ob"):
    """Онбординг: step=go|who|bud, value — выбранное значение или skip."""

    step: str
    value: str = ""


class FilterCb(CallbackData, prefix="f"):
    """a: root (показать корень), g (открыть группу), s (выбрать значение), show, reset."""

    a: str
    f: str = "-----"
    g: str = ""
    v: str = ""


class CatCb(CallbackData, prefix="c"):
    """Пагинация списков: k=w (выходные) / r (по фильтрам) / s (сохранённое)."""

    k: str
    p: int
    f: str = "-----"


class CardCb(CallbackData, prefix="sc"):
    """a: open / save / ph (ещё фото) / det (подробнее) / back."""

    a: str
    id: int


class BookCb(CallbackData, prefix="bk"):
    """a: start (id=scenario) / slot (id=slot) / send / edit / abort."""

    a: str
    id: int = 0


class MyCb(CallbackData, prefix="my"):
    """Отмена брони: ask (edit списка) / askn (новым сообщением) / yes / no."""

    a: str
    id: int


class PbCb(CallbackData, prefix="pb"):
    """Партнёр/админ: ok — подтвердить, no — отклонить заявку."""

    a: str
    id: int


class AttCb(CallbackData, prefix="pa"):
    """Отметка явки: v=1 пришли, v=0 не пришли."""

    id: int
    v: int


class PmCb(CallbackData, prefix="pm"):
    """Кабинет партнёра: a=bookings / slots / attend."""

    a: str


class FbCb(CallbackData, prefix="fb"):
    """Оценка 1–5 (r) по брони id; r=0 — «Пропустить» комментарий."""

    id: int
    r: int


class CancelCb(CallbackData, prefix="x"):
    """Отмена FSM: w=suggest / admin."""

    w: str


class AdmCb(CallbackData, prefix="ad"):
    """Админка: a — действие, id — объект, p — страница/доп. число, v — строка."""

    a: str
    id: int = 0
    p: int = 0
    v: str = ""


class ScfCb(CallbackData, prefix="sf"):
    """Шаги формы сценария: a=vib|vdone|tod|nb|in|pdone|par|save|restart."""

    a: str
    v: str = ""


# ---------------------------------------------------------------- кодирование фильтров

VIBE_CODES = {"r": "romance", "a": "active", "f": "food", "c": "culture", "u": "unusual"}
BUDGET_CODES = {"1": "lt1000", "2": "1000_2000", "4": "2000_4000", "u": "unlimited"}
TIME_CODES = {"d": "day", "e": "evening", "n": "night"}
DATE_CODES = {"t": "today", "m": "tomorrow", "w": "weekend"}
NEED_CODES = {"y": "yes"}

GROUPS = ("vibe", "budget", "time", "date", "need")
GROUP_CODES: dict[str, dict[str, str]] = {
    "vibe": VIBE_CODES,
    "budget": BUDGET_CODES,
    "time": TIME_CODES,
    "date": DATE_CODES,
    "need": NEED_CODES,
}
EMPTY_FILTERS = "-----"


def _rev(d: dict[str, str]) -> dict[str, str]:
    return {v: k for k, v in d.items()}


def encode_filters(f: Filters) -> str:
    values = (
        f.vibe.value if f.vibe else None,
        f.budget,
        f.time_of_day.value if f.time_of_day else None,
        f.date if f.date != "any" else None,
        f.need_booking if f.need_booking != "any" else None,
    )
    out = []
    for group, value in zip(GROUPS, values, strict=True):
        out.append(_rev(GROUP_CODES[group]).get(value, "-") if value else "-")
    return "".join(out)


def decode_filters(code: str) -> Filters:
    code = (code + EMPTY_FILTERS)[:5]
    vals = {g: GROUP_CODES[g].get(ch) for g, ch in zip(GROUPS, code, strict=True)}
    return Filters(
        vibe=Vibe(vals["vibe"]) if vals["vibe"] else None,
        budget=vals["budget"],  # type: ignore[arg-type]
        time_of_day=TimeOfDay(vals["time"]) if vals["time"] else None,
        date=vals["date"] or "any",  # type: ignore[arg-type]
        need_booking=vals["need"] or "any",  # type: ignore[arg-type]
    )


def set_filter(code: str, group: str, value_code: str) -> str:
    """Меняет один символ в коде фильтров ("-" — любой)."""
    code = (code + EMPTY_FILTERS)[:5]
    i = GROUPS.index(group)
    if value_code != "-" and value_code not in GROUP_CODES[group]:
        value_code = "-"
    return code[:i] + value_code + code[i + 1 :]


def group_value(code: str, group: str) -> str:
    code = (code + EMPTY_FILTERS)[:5]
    return code[GROUPS.index(group)]
