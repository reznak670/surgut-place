"""FSM создания/редактирования сценария (§13.1): 14 шагов, предпросмотр, сохранение."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards as kb
from app.bot.callbacks import AdmCb, ScfCb
from app.bot.common import cb_message, safe_answer, safe_edit_markup, send_scenario_photo
from app.bot.formatting import card_caption, esc, fmt_minutes, fmt_price_value
from app.bot.handlers.admin import send_admin_card
from app.bot.states import ScenarioForm
from app.bot.texts import FIELD_LABELS, T
from app.db.models import Role, Scenario, TimeOfDay, Vibe
from app.middlewares import RoleFilter
from app.services import scenarios, users
from app.services.errors import NotFound, ValidationError

log = logging.getLogger(__name__)

STEPS = (
    "title",
    "location_name",
    "address",
    "map_link",
    "vibes",
    "price",
    "duration",
    "time_of_day",
    "timing_text",
    "includes_text",
    "need_booking",
    "is_indoor",
    "photos",
    "partner",
)
TEXT_STEPS = (
    "title",
    "location_name",
    "address",
    "map_link",
    "price",
    "duration",
    "timing_text",
    "includes_text",
)
# Жёсткие лимиты — по длине колонок БД; мягкие — рекомендации §5.3 (предупреждаем).
HARD_LIMITS = {
    "title": 200,
    "location_name": 200,
    "address": 300,
    "map_link": 500,
    "timing_text": 2000,  # Text в БД; разумный потолок для подписи/«Подробнее»
    "includes_text": 2000,
}
SOFT_LIMITS = {"title": 60, "timing_text": 450, "includes_text": 250}
MAX_PHOTOS = 5
MIN_PHOTOS = 2
MAX_VIBES = 3
URL_RE = re.compile(r"^https?://\S+$")
PRICE_RE = re.compile(r"^\s*(\d[\d\s]{0,9})(?:\s+(.*))?$", re.S)
STEP_PREFIX_RE = re.compile(r"^Шаг \d+/14\.\s*")

_photo_locks: dict[int, asyncio.Lock] = {}
_saving: set[int] = set()  # tg_id админов, у которых сценарий прямо сейчас сохраняется


def _lock(user_id: int) -> asyncio.Lock:
    return _photo_locks.setdefault(user_id, asyncio.Lock())


async def ask(
    bot: Bot,
    chat_id: int,
    session: AsyncSession,
    state: FSMContext,
    step: str,
    error: str | None = None,
) -> None:
    data = await state.get_data()
    await state.set_state(getattr(ScenarioForm, step))
    text = T[f"admin.sc_ask_{step}"]
    if data.get("edit_id"):
        text = STEP_PREFIX_RE.sub("", text)
    if error:
        text = T["admin.sc_error"].format(error=esc(error)) + text
    markup: InlineKeyboardMarkup
    match step:
        case "vibes":
            markup = kb.vibes_kb(data.get("sc", {}).get("vibes", []))
        case "time_of_day":
            markup = kb.time_of_day_kb()
        case "need_booking":
            markup = kb.need_booking_kb()
        case "is_indoor":
            markup = kb.indoor_kb()
        case "partner":
            markup = kb.partners_pick_kb(await users.list_partners(session))
        case _:
            markup = kb.admin_cancel_kb()
    await bot.send_message(chat_id, text, reply_markup=markup)


def _field_values(step: str, text: str) -> tuple[dict[str, Any], str | None]:
    """Разбор текстового шага → (поля для сохранения, ошибка)."""
    text = text.strip()
    if step in HARD_LIMITS:
        if not text:
            return {}, "Пусто — пришлите текст."
        if len(text) > HARD_LIMITS[step]:
            return {}, f"Слишком длинно: максимум {HARD_LIMITS[step]} символов."
    match step:
        case "map_link":
            if not URL_RE.match(text):
                return {}, "Нужна ссылка, которая начинается с http:// или https://"
            return {"map_link": text}, None
        case "price":
            m = PRICE_RE.match(text)
            if not m:
                return {}, "Начните с числа, например 3500."
            value = int(re.sub(r"\s", "", m.group(1)))
            note = (m.group(2) or "").strip()
            price_text = fmt_price_value(value) + (f", {note}" if note else "")
            if len(price_text) > 200:
                return {}, "Уточнение слишком длинное."
            return {"price_value": value, "price_text": price_text}, None
        case "duration":
            if not text.isdigit() or not 0 < int(text) <= 24 * 60:
                return {}, "Нужно целое число минут, например 150."
            minutes = int(text)
            return {"duration_minutes": minutes, "duration_text": fmt_minutes(minutes)}, None
    return {step: text}, None


async def _next(
    bot: Bot, chat_id: int, session: AsyncSession, state: FSMContext, step: str
) -> None:
    """После шага: в режиме редактирования — сохранить поле, иначе — следующий вопрос."""
    data = await state.get_data()
    edit_id = data.get("edit_id")
    if edit_id:
        await _apply_edit(bot, chat_id, session, state, step, edit_id)
        return
    idx = STEPS.index(step)
    if idx + 1 < len(STEPS):
        await ask(bot, chat_id, session, state, STEPS[idx + 1])
    else:
        await _preview(bot, chat_id, state)


_EDIT_KEYS = {
    "price": ("price_value", "price_text"),
    "duration": ("duration_minutes", "duration_text"),
    "partner": ("partner_id",),
}


async def _apply_edit(
    bot: Bot, chat_id: int, session: AsyncSession, state: FSMContext, step: str, edit_id: int
) -> None:
    sc_data: dict[str, Any] = (await state.get_data()).get("sc", {})
    keys = _EDIT_KEYS.get(step, (step,))
    patch = {k: sc_data.get(k) for k in keys}
    try:
        await scenarios.update(session, edit_id, patch)
    except ValidationError as e:
        await ask(bot, chat_id, session, state, step, error=e.args[0] if e.args else None)
        return
    except NotFound:
        await state.clear()
        await bot.send_message(chat_id, T["card.not_found"])
        return
    await state.clear()
    await bot.send_message(chat_id, T["admin.sc_updated"].format(field_label=FIELD_LABELS[step]))
    await send_admin_card(bot, chat_id, session, edit_id)


async def _update_sc(state: FSMContext, **fields: Any) -> None:
    data = await state.get_data()
    sc = dict(data.get("sc", {}))
    sc.update(fields)
    await state.update_data(sc=sc)


def _preview_scenario(sc_data: dict[str, Any]) -> Scenario:
    return Scenario(
        **{k: v for k, v in sc_data.items() if k != "partner_id"},
        is_active=False,
        is_featured=False,
    )


async def _preview(bot: Bot, chat_id: int, state: FSMContext) -> None:
    await state.set_state(ScenarioForm.preview)
    sc_data = (await state.get_data()).get("sc", {})
    sc = _preview_scenario(sc_data)
    caption = card_caption(sc, None)
    await send_scenario_photo(bot, chat_id, sc, caption.text, lambda _p: kb.sc_preview_kb())
    await bot.send_message(chat_id, T["admin.sc_preview"])


# ---------------------------------------------------------------- старт формы


async def on_start(
    callback: CallbackQuery,
    callback_data: AdmCb,
    session: AsyncSession,
    state: FSMContext,
    bot: Bot,
) -> None:
    await safe_answer(callback)
    msg = cb_message(callback)
    chat_id = msg.chat.id if msg else callback.from_user.id
    await state.clear()
    if callback_data.a == "new":
        await state.update_data(sc={}, edit_id=None)
        await ask(bot, chat_id, session, state, "title")
        return
    # ef — редактирование одного поля
    field = callback_data.v
    if field not in STEPS:
        return
    try:
        sc = await scenarios.get(session, callback_data.id, include_hidden=True)
    except NotFound:
        await bot.send_message(chat_id, T["card.not_found"])
        return
    initial: dict[str, Any] = {"vibes": list(sc.vibes or [])} if field == "vibes" else {}
    await state.update_data(sc=initial, edit_id=sc.id)
    await ask(bot, chat_id, session, state, field)


# ---------------------------------------------------------------- текстовые шаги


async def on_text_step(
    message: Message, session: AsyncSession, state: FSMContext, bot: Bot
) -> None:
    current = await state.get_state()
    step = (current or "").split(":")[-1]
    if step not in TEXT_STEPS:
        return
    values, error = _field_values(step, message.text or "")
    if error:
        await ask(bot, message.chat.id, session, state, step, error=error)
        return
    await _update_sc(state, **values)
    raw_len = len((message.text or "").strip())
    if step in SOFT_LIMITS and raw_len > SOFT_LIMITS[step]:
        await message.answer(T["admin.sc_warn_long"].format(n=raw_len, limit=SOFT_LIMITS[step]))
    await _next(bot, message.chat.id, session, state, step)


async def on_not_text(message: Message) -> None:
    await message.answer(T["error.blocked_fsm"], reply_markup=kb.admin_cancel_kb())


# ---------------------------------------------------------------- кнопочные шаги


async def on_form_button(
    callback: CallbackQuery,
    callback_data: ScfCb,
    session: AsyncSession,
    state: FSMContext,
    bot: Bot,
) -> None:
    msg = cb_message(callback)
    chat_id = msg.chat.id if msg else callback.from_user.id
    current = (await state.get_state() or "").split(":")[-1]
    data = await state.get_data()
    sc_data: dict[str, Any] = dict(data.get("sc", {}))
    a, v = callback_data.a, callback_data.v

    expected = {
        "vib": "vibes",
        "vdone": "vibes",
        "tod": "time_of_day",
        "nb": "need_booking",
        "in": "is_indoor",
        "pdone": "photos",
        "par": "partner",
        "save": "preview",
        "restart": "preview",
    }
    if expected.get(a) != current:
        await safe_answer(callback, T["error.expired"])
        return

    match a:
        case "vib":
            selected = list(sc_data.get("vibes", []))
            if v in selected:
                selected.remove(v)
            elif v in Vibe.__members__:
                if len(selected) >= MAX_VIBES:
                    await safe_answer(callback, "Не больше трёх")
                    return
                selected.append(v)
            await _update_sc(state, vibes=selected)
            await safe_answer(callback)
            if msg is not None:
                await safe_edit_markup(msg, kb.vibes_kb(selected))
            return
        case "vdone":
            if not sc_data.get("vibes"):
                await safe_answer(callback, T["admin.vibes_need_one"])
                return
        case "tod":
            if v not in TimeOfDay.__members__:
                await safe_answer(callback, T["error.expired"])
                return
            await _update_sc(state, time_of_day=v)
        case "nb":
            await _update_sc(state, need_booking=v == "1")
        case "in":
            await _update_sc(state, is_indoor=v == "1")
        case "pdone":
            if len(sc_data.get("photos", [])) < MIN_PHOTOS:
                await safe_answer(callback, T["admin.photos_need_two"])
                return
        case "par":
            partner_id = int(v) if v.isdigit() and v != "0" else None
            await _update_sc(state, partner_id=partner_id)
        case "save":
            await _save(callback, session, state, bot, sc_data)
            return
        case "restart":
            await safe_answer(callback)
            if msg is not None:
                await safe_edit_markup(msg, None)
            await state.update_data(sc={}, edit_id=None)
            await ask(bot, chat_id, session, state, "title")
            return

    await safe_answer(callback)
    if msg is not None:
        await safe_edit_markup(msg, None)
    await _next(bot, chat_id, session, state, current)


async def _save(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    bot: Bot,
    sc_data: dict[str, Any],
) -> None:
    msg = cb_message(callback)
    chat_id = msg.chat.id if msg else callback.from_user.id
    admin_id = callback.from_user.id
    # двойной клик «Сохранить» → дубль сценария: синхронная проверка+отметка без await
    if admin_id in _saving:
        await safe_answer(callback, T["error.too_fast"])
        return
    _saving.add(admin_id)
    try:
        await state.set_state(None)  # данные формы остаются — для повтора при ошибке
        try:
            sc = await scenarios.create(session, {**sc_data, "is_active": False})
        except ValidationError as e:
            await state.set_state(ScenarioForm.preview)
            await safe_answer(callback)
            await bot.send_message(
                chat_id,
                T["admin.sc_error"].format(error=esc(e.args[0] if e.args else "")).strip(),
            )
            return
        await session.commit()
    finally:
        _saving.discard(admin_id)
    await safe_answer(callback)
    if msg is not None:
        await safe_edit_markup(msg, None)
    await state.clear()
    await bot.send_message(
        chat_id,
        T["admin.sc_created"].format(title=esc(sc.title)),
        reply_markup=kb.sc_created_kb(sc.id),
    )
    log.info("scenario created", extra={"scenario_id": sc.id})


# ---------------------------------------------------------------- фото


async def on_photo(message: Message, session: AsyncSession, state: FSMContext, bot: Bot) -> None:
    if not message.photo or message.from_user is None:
        return
    file_id = message.photo[-1].file_id
    # альбом приходит несколькими апдейтами параллельно — сериализуем запись в FSM
    async with _lock(message.from_user.id):
        if await state.get_state() != ScenarioForm.photos.state:
            return
        data = await state.get_data()
        sc_data = dict(data.get("sc", {}))
        photos = list(sc_data.get("photos", []))
        if len(photos) >= MAX_PHOTOS:
            return
        photos.append(file_id)
        await _update_sc(state, photos=photos)
        if len(photos) >= MAX_PHOTOS:
            await message.answer(T["admin.photo_added"].format(n=len(photos)))
            await _next(bot, message.chat.id, session, state, "photos")
            return
    await message.answer(
        T["admin.photo_added"].format(n=len(photos)), reply_markup=kb.photos_done_kb()
    )


async def on_photo_other(message: Message) -> None:
    await message.answer(T["admin.sc_ask_photos"], reply_markup=kb.photos_done_kb())


async def on_preview_text(message: Message) -> None:
    await message.answer(T["admin.sc_preview"], reply_markup=kb.sc_preview_kb())


def setup() -> Router:
    r = Router(name="admin_form")
    admin = RoleFilter(Role.admin)
    r.message.filter(admin)
    r.callback_query.filter(admin)
    r.callback_query.register(on_start, AdmCb.filter(F.a.in_({"new", "ef"})))
    r.callback_query.register(on_form_button, ScfCb.filter())
    text_states = [getattr(ScenarioForm, s) for s in TEXT_STEPS]
    r.message.register(on_text_step, StateFilter(*text_states), F.text)
    r.message.register(on_not_text, StateFilter(*text_states))
    r.message.register(on_photo, ScenarioForm.photos, F.photo)
    r.message.register(on_photo_other, ScenarioForm.photos)
    r.message.register(on_preview_text, ScenarioForm.preview)
    return r
