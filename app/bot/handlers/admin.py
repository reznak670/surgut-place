"""Админка (§13): меню, сценарии (список/карточка/скрыть/подборка), слоты, заявки,
фидбэк, статистика, партнёры. Форма сценария — admin_form.py, рассылка — admin_broadcast.py."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, MessageOriginUser
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards as kb
from app.bot.callbacks import AdmCb, CancelCb
from app.bot.commands import remove_partner_commands, set_partner_commands
from app.bot.common import (
    NO_PREVIEW,
    cb_message,
    next_slot,
    safe_answer,
    safe_edit_markup,
    send_safe,
    send_scenario_photo,
    show_text,
)
from app.bot.formatting import (
    card_caption,
    esc,
    esc_trunc,
    fmt_date,
    fmt_slot,
    join_within,
    partner_label,
    plural_scenarios,
    stars,
    truncate_plain,
)
from app.bot.handlers.partner import send_booking_card
from app.bot.states import PartnerForm, SlotsForm
from app.bot.texts import T
from app.config import settings
from app.db.models import Role, User
from app.middlewares import RoleFilter
from app.services import bookings, feedback, scenarios, slots, stats, users
from app.services.errors import InvalidState, NotFound, ValidationError
from app.services.timeutil import now_utc

log = logging.getLogger(__name__)

LIST_PAGE = 8
SLOT_LINE_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\s+(\d{1,2}):(\d{2})\s+(\d+)$")
MAX_CAPACITY = 50


# ---------------------------------------------------------------- меню


async def cmd_admin(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(T["admin.menu"], reply_markup=kb.admin_menu_kb())


async def on_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await safe_answer(callback)
    await state.clear()
    msg = cb_message(callback)
    if msg is not None and msg.text is None:
        await safe_edit_markup(msg, None)
    await show_text(callback, T["admin.menu"], kb.admin_menu_kb())


# ---------------------------------------------------------------- сценарии


async def render_scenarios(
    session: AsyncSession, page: int, action: str
) -> tuple[str, InlineKeyboardMarkup]:
    items, has_more = await scenarios.list_all(session, offset=page * LIST_PAGE, limit=LIST_PAGE)
    if action == "slv":
        title = T["admin.slots_pick"]
    else:
        title = T["admin.scenarios"] if items or page else T["admin.scenarios_empty"]
    return title, kb.admin_scenarios_kb(items, page=page, has_more=has_more, action=action)


async def send_admin_card(bot: Bot, chat_id: int, session: AsyncSession, scenario_id: int) -> None:
    try:
        sc = await scenarios.get(session, scenario_id, include_hidden=True)
    except NotFound:
        await bot.send_message(chat_id, T["card.not_found"])
        return
    caption = card_caption(sc, await next_slot(session, sc))
    await send_scenario_photo(
        bot, chat_id, sc, caption.text, lambda _has_photo: kb.admin_scenario_kb(sc)
    )


async def render_slots(session: AsyncSession, scenario_id: int) -> tuple[str, InlineKeyboardMarkup]:
    sc = await scenarios.get(session, scenario_id, include_hidden=True)
    now = now_utc()
    future = [s for s in await slots.list_for_scenario(session, scenario_id) if s.starts_at > now]
    lines = [T["admin.slots_list"].format(title=esc(sc.title)), ""]
    if future:
        lines += [
            T["admin.slots_item"].format(
                slot=fmt_slot(s.starts_at), booked=s.booked_count, capacity=s.capacity
            )
            for s in future
        ]
    else:
        lines.append(T["admin.slots_none"])
    return join_within(lines), kb.admin_slots_kb(scenario_id, future)


def parse_slot_line(line: str, now: datetime) -> tuple[datetime | None, str | None]:
    """`31.10 19:00 4` → (aware datetime в settings.tz, None) или (None, причина)."""
    m = SLOT_LINE_RE.match(line.strip())
    if not m:
        return None, T["admin.slot_reason_format"]
    day, month, hour, minute, cap = (int(x) for x in m.groups())
    if not 1 <= cap <= MAX_CAPACITY:
        return None, T["admin.slot_reason_capacity"]
    tz = settings.tz
    local_now = now.astimezone(tz)
    try:
        year = local_now.year
        if date(year, month, day) < local_now.date():
            year += 1  # ближайшая будущая дата
        dt = datetime(year, month, day, hour, minute, tzinfo=tz)
    except ValueError:
        return None, T["admin.slot_reason_format"]
    if dt <= now:
        return None, T["admin.slot_reason_past"]
    return dt, None


async def on_slots_lines(
    message: Message, session: AsyncSession, state: FSMContext, bot: Bot
) -> None:
    data = await state.get_data()
    scenario_id = data.get("scenario_id")
    if not scenario_id:
        await state.clear()
        return
    now = now_utc()
    ok = 0
    errors: list[str] = []
    lines = [ln for ln in (message.text or "").splitlines() if ln.strip()]
    for n, line in enumerate(lines, start=1):
        dt, reason = parse_slot_line(line, now)
        if dt is not None:
            cap = int(line.split()[-1])
            try:
                await slots.create(session, scenario_id, dt, cap)
                ok += 1
                continue
            except ValidationError:
                reason = T["admin.slot_reason_past"]
            except NotFound:
                await state.clear()
                await message.answer(T["card.not_found"])
                return
        errors.append(
            T["admin.slots_error_line"].format(
                n=n, line=esc(truncate_plain(line, 40)), reason=reason
            )
        )
    await state.clear()
    errors_block = T["admin.slots_errors_head"] + join_within(errors, limit=3500) if errors else ""
    await message.answer(T["admin.slots_result"].format(ok=ok, errors_block=errors_block))
    text, markup = await render_slots(session, scenario_id)
    await bot.send_message(message.chat.id, text, reply_markup=markup)


# ---------------------------------------------------------------- фидбэк / статистика


async def render_feedback(session: AsyncSession, only_low: bool) -> str:
    items = await feedback.list_recent(session, limit=20, only_low=only_low)
    parts = [T["admin.feedback"]]
    if not items:
        parts.append(T["admin.feedback_empty"])
    for fb in items:
        b = fb.booking
        parts.append(
            T["admin.feedback_item"].format(
                stars=stars(fb.rating),
                title=esc(b.slot.scenario.title),
                slot_date=fmt_date(b.slot.starts_at),
                name=esc(b.name),
                comment_or_dash=esc_trunc(fb.comment, 300) if fb.comment else "—",
            )
        )
    text = ""
    for part in parts:  # обрезаем по целым записям
        if len(text) + len(part) + 2 > 4096:
            break
        text = f"{text}\n\n{part}" if text else part
    return text


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{round(value * 100)}%"


async def render_stats(session: AsyncSession, days: int) -> str:
    s = await stats.collect(session, days=days)
    top = s.top_view_share[:3]
    top_lines = (
        "\n".join(
            T["admin.stats_top_line"].format(share=round(share * 100), title=esc(title))
            for title, share in top
        )
        or "—"
    )
    warnings = [
        T["admin.warn_top_share"].format(title=esc(title), share=round(share * 100))
        for title, share in s.top_view_share
        if share > 0.30
    ]
    if s.indoor_warning:
        warnings.append(
            T["admin.warn_indoor"].format(
                n=s.indoor_active_count, word=plural_scenarios(s.indoor_active_count)
            )
        )
    return T["admin.stats"].format(
        days=s.days,
        views=s.views,
        unique_viewers=s.unique_viewers,
        bookings=s.bookings,
        conversion=_pct(s.conversion),
        attendance_rate=_pct(s.attendance_rate),
        repeat_rate=_pct(s.repeat_rate),
        avg_rating="—" if s.avg_rating is None else f"{s.avg_rating:.1f}".replace(".", ","),
        active_scenarios_week=s.active_scenarios_week,
        top_lines=top_lines,
        warnings="\n".join(warnings) if warnings else T["admin.stats_ok"],
    )


# ---------------------------------------------------------------- партнёры


async def render_partners(session: AsyncSession) -> str:
    partners = await users.list_partners(session)
    parts = [T["admin.partners"], ""]
    if not partners:
        parts.append(T["admin.partners_empty"])
    for p in partners:
        n = len(await scenarios.list_for_partner(session, p))
        parts.append(T["admin.partners_item"].format(name=partner_label(p), n=n))
    return join_within(parts)


async def on_partner_id(
    message: Message, session: AsyncSession, state: FSMContext, bot: Bot
) -> None:
    tg_id: int | None = None
    origin = message.forward_origin
    if isinstance(origin, MessageOriginUser):
        tg_id = origin.sender_user.id
    elif message.text and message.text.strip().lstrip("-").isdigit():
        tg_id = int(message.text.strip())
    if tg_id is None or tg_id <= 0:
        await message.answer(T["admin.partner_bad_id"], reply_markup=kb.admin_cancel_kb())
        return
    if tg_id in settings.ADMIN_IDS:
        await message.answer(T["error.no_access"], reply_markup=kb.admin_cancel_kb())
        return
    partner = await users.set_role(session, tg_id, Role.partner)
    await session.commit()
    await state.clear()
    await set_partner_commands(bot, tg_id)
    await send_safe(bot, tg_id, T["partner.welcome"], reply_markup=kb.main_menu(Role.partner))
    who = esc(partner.first_name) if partner.first_name else f"<code>{tg_id}</code>"
    await message.answer(
        T["admin.partner_done"].format(who=who), reply_markup=kb.admin_partners_kb()
    )
    log.info("partner assigned", extra={"tg_id": tg_id})


# ---------------------------------------------------------------- колбэки


async def on_admin(
    callback: CallbackQuery,
    callback_data: AdmCb,
    session: AsyncSession,
    user: User,
    state: FSMContext,
    bot: Bot,
) -> None:
    msg = cb_message(callback)
    chat_id = msg.chat.id if msg else callback.from_user.id
    a, obj_id, p, v = callback_data.a, callback_data.id, callback_data.p, callback_data.v

    try:
        match a:
            case "menu":
                await safe_answer(callback)
                await state.clear()
                await show_text(callback, T["admin.menu"], kb.admin_menu_kb())
            case "scl" | "sll":
                await safe_answer(callback)
                await show_text(
                    callback, *await render_scenarios(session, p, "scv" if a == "scl" else "slv")
                )
            case "scv":
                await safe_answer(callback)
                await state.clear()
                await send_admin_card(bot, chat_id, session, obj_id)
            case "act" | "feat":
                if a == "act":
                    sc = await scenarios.set_active(session, obj_id, bool(p))
                    toast = T["admin.btn_show" if sc.is_active else "admin.btn_hide"]
                else:
                    sc = await scenarios.set_featured(session, obj_id, bool(p))
                    toast = T["admin.btn_feature" if sc.is_featured else "admin.btn_unfeature"]
                await safe_answer(callback, f"{toast} ✓")
                if msg is not None:
                    if msg.photo:
                        await safe_edit_markup(msg, kb.admin_scenario_kb(sc))
                    else:
                        await safe_edit_markup(msg, None)
                        await send_admin_card(bot, chat_id, session, sc.id)
            case "edit":
                await safe_answer(callback)
                sc = await scenarios.get(session, obj_id, include_hidden=True)
                await bot.send_message(
                    chat_id,
                    T["admin.sc_edit_pick"].format(title=esc(sc.title)),
                    reply_markup=kb.sc_edit_pick_kb(sc.id),
                )
            case "slv":
                await safe_answer(callback)
                await show_text(callback, *await render_slots(session, obj_id))
            case "sla":
                await safe_answer(callback)
                await scenarios.get(session, obj_id, include_hidden=True)
                await state.clear()
                await state.set_state(SlotsForm.lines)
                await state.update_data(scenario_id=obj_id)
                await bot.send_message(
                    chat_id, T["admin.slots_ask"], reply_markup=kb.admin_cancel_kb()
                )
            case "sld":
                try:
                    await slots.delete(session, obj_id)
                except InvalidState:
                    await safe_answer(callback, T["admin.slot_has_bookings"])
                    return
                await safe_answer(callback, T["admin.slot_deleted"])
                await show_text(callback, *await render_slots(session, p))
            case "bks":
                await safe_answer(callback)
                pending = await bookings.list_pending(session)
                if not pending:
                    await bot.send_message(chat_id, T["admin.bookings_empty"])
                    return
                for b in pending:
                    prefix = (
                        T["admin.bookings_prefix"].format(
                            partner_or_dash=partner_label(b.slot.scenario.partner)
                        )
                        + "\n"
                    )
                    # ПД: контакт гостя админу — только если у сценария нет партнёра
                    await send_booking_card(bot, chat_id, b, prefix, mask_contact=True)
            case "fbk":
                await safe_answer(callback)
                only_low = v == "low"
                await show_text(
                    callback,
                    await render_feedback(session, only_low),
                    kb.admin_feedback_kb(only_low),
                )
            case "st":
                await safe_answer(callback)
                days = p if p in (7, 28, 56) else 56
                await show_text(callback, await render_stats(session, days), kb.admin_stats_kb())
            case "prt":
                await safe_answer(callback)
                await state.clear()
                await show_text(callback, await render_partners(session), kb.admin_partners_kb())
            case "pra":
                await safe_answer(callback)
                await state.set_state(PartnerForm.tg_id)
                await bot.send_message(
                    chat_id, T["admin.partner_ask_id"], reply_markup=kb.admin_cancel_kb()
                )
            case "prr":
                await safe_answer(callback)
                partners = await users.list_partners(session)
                await show_text(
                    callback, T["admin.partner_remove_pick"], kb.partners_remove_kb(partners)
                )
            case "prd":
                # снимает роль и отвязывает его сценарии — заявки пойдут в админ-чат
                await users.revoke_partner(session, obj_id)
                await session.commit()
                await safe_answer(callback)
                await remove_partner_commands(bot, obj_id)
                await show_text(callback, T["admin.partner_removed"], kb.admin_partners_kb())
                log.info("partner role removed", extra={"tg_id": obj_id})
            case "tpl":
                await safe_answer(callback)
                await bot.send_message(
                    chat_id,
                    f"<code>{esc(T['partner.template'])}</code>",
                    link_preview_options=NO_PREVIEW,
                )
            case _:
                # ef / new / bc* обрабатываются в admin_form / admin_broadcast
                await safe_answer(callback, T["error.expired"])
    except NotFound:
        await safe_answer(callback, T["error.expired"])


def setup() -> Router:
    r = Router(name="admin")
    admin = RoleFilter(Role.admin)
    r.message.filter(admin)
    r.callback_query.filter(admin)
    r.message.register(cmd_admin, Command("admin"))
    r.message.register(cmd_admin, F.text == T["menu.btn_admin"])
    r.callback_query.register(on_cancel, CancelCb.filter(F.w == "admin"))
    r.message.register(on_slots_lines, SlotsForm.lines, F.text)
    r.message.register(on_partner_id, PartnerForm.tg_id)
    r.callback_query.register(
        on_admin,
        AdmCb.filter(~F.a.in_({"ef", "new", "bc", "bcs", "bcsend", "bcedit"})),
    )
    return r
