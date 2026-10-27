"""Клавиатуры (docs/design.md §0.5): reply — только меню и шаги брони, остальное inline."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime

from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.bot.callbacks import (
    BUDGET_CODES,
    DATE_CODES,
    NEED_CODES,
    TIME_CODES,
    VIBE_CODES,
    AdmCb,
    AttCb,
    BookCb,
    CancelCb,
    CardCb,
    CatCb,
    FbCb,
    FilterCb,
    MyCb,
    NavCb,
    OnbCb,
    PbCb,
    PmCb,
    ScfCb,
    group_value,
)
from app.bot.formatting import (
    fmt_slot,
    fmt_slot_short,
    share_url,
    strip_emoji_label,
    title_short,
)
from app.bot.texts import (
    BUDGET_FILTER,
    DATE_FILTER,
    FIELD_LABELS,
    NEED_BOOKING,
    SEGMENT,
    TIME_OF_DAY,
    VIBE,
    T,
)
from app.db.models import Booking, Role, Scenario, Slot, User

Rows = list[list[InlineKeyboardButton]]


def btn(text: str, cb: CallbackData) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=cb.pack())


def url_btn(text: str, url: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, url=url)


def inline(rows: Iterable[Sequence[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows if r])


def pairs(buttons: list[InlineKeyboardButton], n: int = 2) -> Rows:
    return [buttons[i : i + n] for i in range(0, len(buttons), n)]


# ---------------------------------------------------------------- reply-меню (§3)


def main_menu(role: Role | str | None = None) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=T["menu.btn_weekend"])],
        [KeyboardButton(text=T["menu.btn_filters"]), KeyboardButton(text=T["menu.btn_my"])],
        [KeyboardButton(text=T["menu.btn_saved"]), KeyboardButton(text=T["menu.btn_suggest"])],
        [KeyboardButton(text=T["menu.btn_about"])],
    ]
    if role == Role.partner:
        rows.append([KeyboardButton(text=T["menu.btn_partner"])])
    elif role == Role.admin:
        rows.append([KeyboardButton(text=T["menu.btn_admin"])])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder=T["menu.placeholder"],
    )


def menu_for(user: User | None) -> ReplyKeyboardMarkup:
    return main_menu(user.role if user else None)


def ask_name_kb(first_name: str | None) -> ReplyKeyboardMarkup:
    rows = []
    if first_name and 2 <= len(first_name) <= 40:
        rows.append([KeyboardButton(text=first_name)])
    rows.append([KeyboardButton(text=T["booking.btn_cancel"])])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def ask_contact_kb(username: str | None) -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text=T["booking.btn_send_phone"], request_contact=True)]]
    if username:
        rows.append([KeyboardButton(text=T["booking.btn_use_username"].format(username=username))])
    rows.append([KeyboardButton(text=T["booking.btn_cancel"])])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


# ---------------------------------------------------------------- общие


def nav_kb(text_key: str, to: str) -> InlineKeyboardMarkup:
    return inline([[btn(T[text_key], NavCb(to=to))]])


def weekend_btn_kb() -> InlineKeyboardMarkup:
    return nav_kb("menu.btn_weekend", "weekend")


def cancel_kb(where: str) -> InlineKeyboardMarkup:
    return inline([[btn(T["common.btn_cancel"], CancelCb(w=where))]])


# ---------------------------------------------------------------- онбординг (§2)


def onb_start_kb() -> InlineKeyboardMarkup:
    return inline([[btn(T["onboarding.btn_start"], OnbCb(step="go"))]])


def onb_who_kb() -> InlineKeyboardMarkup:
    return inline(
        [
            [
                btn(T["onboarding.btn_who_couple"], OnbCb(step="who", value="couple")),
                btn(T["onboarding.btn_who_idea"], OnbCb(step="who", value="idea")),
            ],
            [btn(T["onboarding.btn_skip"], OnbCb(step="who", value="skip"))],
        ]
    )


def onb_budget_kb() -> InlineKeyboardMarkup:
    return inline(
        [
            [
                btn(T["onboarding.btn_budget_lt1000"], OnbCb(step="bud", value="lt1000")),
                btn(T["onboarding.btn_budget_lt2000"], OnbCb(step="bud", value="lt2000")),
            ],
            [
                btn(T["onboarding.btn_budget_unlimited"], OnbCb(step="bud", value="unlimited")),
                btn(T["onboarding.btn_skip"], OnbCb(step="bud", value="skip")),
            ],
        ]
    )


# ---------------------------------------------------------------- фильтры (§4)

GROUP_LABELS: dict[str, dict[str, str]] = {
    "vibe": {c: VIBE[v] for c, v in VIBE_CODES.items()},
    "budget": {c: BUDGET_FILTER[v] for c, v in BUDGET_CODES.items()},
    "time": {c: TIME_OF_DAY[v] for c, v in TIME_CODES.items()},
    "date": {c: DATE_FILTER[v] for c, v in DATE_CODES.items()},
    "need": {c: NEED_BOOKING[v] for c, v in NEED_CODES.items()},
}
GROUP_ANY_VALUE = {
    "vibe": T["filters.any_m"],
    "budget": T["filters.any_m"],
    "time": T["filters.any_n"],
    "date": DATE_FILTER["any"],
    "need": NEED_BOOKING["any"],
}
GROUP_ANY_BTN = {
    "vibe": T["filters.btn_any_m"],
    "budget": T["filters.btn_any_m"],
    "time": T["filters.btn_any_n"],
    "date": DATE_FILTER["any"],
    "need": NEED_BOOKING["any"],
}
GROUP_BTN_KEY = {
    "vibe": "filters.btn_vibe",
    "budget": "filters.btn_budget",
    "time": "filters.btn_time",
    "date": "filters.btn_date",
    "need": "filters.btn_need_booking",
}
GROUP_TITLE_KEY = {
    "vibe": "filters.title_vibe",
    "budget": "filters.title_budget",
    "time": "filters.title_time",
    "date": "filters.title_date",
    "need": "filters.title_need_booking",
}


def filters_root_kb(code: str) -> InlineKeyboardMarkup:
    rows: Rows = []
    for group in ("vibe", "budget", "time", "date", "need"):
        ch = group_value(code, group)
        value = strip_emoji_label(GROUP_LABELS[group][ch]) if ch != "-" else GROUP_ANY_VALUE[group]
        rows.append(
            [btn(T[GROUP_BTN_KEY[group]].format(value=value), FilterCb(a="g", f=code, g=group))]
        )
    rows.append(
        [
            btn(T["filters.btn_show"], FilterCb(a="show", f=code)),
            btn(T["filters.btn_reset"], FilterCb(a="reset")),
        ]
    )
    return inline(rows)


def filters_group_kb(code: str, group: str) -> InlineKeyboardMarkup:
    current = group_value(code, group)
    buttons = []
    for ch, label in GROUP_LABELS[group].items():
        text = f"✓ {strip_emoji_label(label)}" if ch == current else label
        buttons.append(btn(text, FilterCb(a="s", f=code, g=group, v=ch)))
    any_text = GROUP_ANY_BTN[group]
    if current == "-":
        any_text = f"✓ {any_text}"
    rows = pairs(buttons)
    rows.append(
        [
            btn(any_text, FilterCb(a="s", f=code, g=group, v="-")),
            btn(T["filters.btn_back"], FilterCb(a="root", f=code)),
        ]
    )
    return inline(rows)


# ---------------------------------------------------------------- каталог (§5.5)


def catalog_kb(
    scenarios: list[Scenario],
    *,
    kind: str,
    page: int,
    has_more: bool,
    offset: int = 0,
    code: str = "-----",
) -> InlineKeyboardMarkup:
    rows: Rows = [
        [
            btn(
                T["catalog.btn_item"].format(n=i, title_short=title_short(sc.title, 28)),
                CardCb(a="open", id=sc.id),
            )
        ]
        for i, sc in enumerate(scenarios, start=offset + 1)
    ]
    last: list[InlineKeyboardButton] = []
    if kind == "w":
        last.append(btn(T["catalog.btn_filters_weekend"], FilterCb(a="root")))
    elif kind == "r":
        last.append(btn(T["catalog.btn_filters"], FilterCb(a="root", f=code)))
    if has_more:
        last.append(btn(T["catalog.btn_more"], CatCb(k=kind, p=page + 1, f=code)))
    rows.append(last)
    return inline(rows)


def scenario_buttons_kb(
    scenarios: list[Scenario], extra: Rows | None = None
) -> InlineKeyboardMarkup:
    rows: Rows = [[btn(title_short(sc.title), CardCb(a="open", id=sc.id))] for sc in scenarios]
    rows.extend(extra or [])
    return inline(rows)


def filters_empty_kb(code: str) -> InlineKeyboardMarkup:
    return inline(
        [
            [btn(T["catalog.btn_reset_filters"], FilterCb(a="reset"))],
            [btn(T["catalog.btn_change_filters"], FilterCb(a="root", f=code))],
            [btn(T["menu.btn_weekend"], NavCb(to="weekend"))],
        ]
    )


# ---------------------------------------------------------------- карточка (§5.4)


def card_kb(
    sc: Scenario,
    *,
    saved: bool,
    bot_username: str | None,
    truncated: bool = False,
    show_more_photos: bool = True,
) -> InlineKeyboardMarkup:
    rows: Rows = []
    if sc.need_booking:
        rows.append([btn(T["card.btn_book"], BookCb(a="start", id=sc.id))])
    elif sc.map_link:
        rows.append([url_btn(T["card.btn_map"], sc.map_link)])
    second = []
    if bot_username:
        second.append(url_btn(T["card.btn_share"], share_url(bot_username, sc)))
    second.append(
        btn(T["card.btn_saved" if saved else "card.btn_save"], CardCb(a="save", id=sc.id))
    )
    rows.append(second)
    third = []
    extra = len(sc.photos or []) - 1
    if show_more_photos and extra > 0:
        third.append(btn(T["card.btn_more_photos"].format(n=extra), CardCb(a="ph", id=sc.id)))
    third.append(btn(T["card.btn_back"], CardCb(a="back", id=sc.id)))
    rows.append(third)
    if truncated:
        rows.append([btn(T["card.btn_details"], CardCb(a="det", id=sc.id))])
    return inline(rows)


# ---------------------------------------------------------------- бронь (§6)


def slots_kb(slots: list[Slot]) -> InlineKeyboardMarkup:
    rows: Rows = []
    for slot in slots[:8]:
        label = fmt_slot(slot.starts_at)
        text = (
            T["booking.btn_slot_left"].format(slot=label, left=slot.free)
            if slot.free <= 3
            else T["booking.btn_slot"].format(slot=label)
        )
        rows.append([btn(text, BookCb(a="slot", id=slot.id))])
    rows.append([btn(T["booking.btn_cancel"], BookCb(a="abort"))])
    return inline(rows)


def booking_confirm_kb() -> InlineKeyboardMarkup:
    return inline(
        [
            [btn(T["booking.btn_send"], BookCb(a="send"))],
            [btn(T["booking.btn_edit"], BookCb(a="edit"))],
            [btn(T["booking.btn_cancel"], BookCb(a="abort"))],
        ]
    )


def other_time_kb(scenario_id: int) -> InlineKeyboardMarkup:
    return inline([[btn(T["booking.btn_other_time"], BookCb(a="start", id=scenario_id))]])


def no_slots_kb() -> InlineKeyboardMarkup:
    return nav_kb("booking.btn_other_evenings", "weekend")


def confirmed_push_kb(sc: Scenario) -> InlineKeyboardMarkup:
    row = []
    if sc.map_link:
        row.append(url_btn(T["booking.btn_on_map"], sc.map_link))
    row.append(btn(T["booking.btn_my"], NavCb(to="my")))
    return inline([row])


def declined_push_kb(scenario_id: int) -> InlineKeyboardMarkup:
    return inline(
        [
            [btn(T["booking.btn_another_time"], BookCb(a="start", id=scenario_id))],
            [btn(T["booking.btn_other_evenings"], NavCb(to="weekend"))],
        ]
    )


def partner_booking_kb(booking_id: int) -> InlineKeyboardMarkup:
    return inline(
        [
            [
                btn(T["partner.btn_confirm"], PbCb(a="ok", id=booking_id)),
                btn(T["partner.btn_decline"], PbCb(a="no", id=booking_id)),
            ]
        ]
    )


def my_bookings_kb(bookings: list[Booking], now: datetime) -> InlineKeyboardMarkup | None:
    rows: Rows = []
    for b in bookings:
        text = T["my_bookings.btn_cancel"].format(
            slot_short=fmt_slot_short(b.slot.starts_at, now),
            title_short=title_short(b.slot.scenario.title, 20),
        )
        rows.append([btn(text, MyCb(a="ask", id=b.id))])
    return inline(rows) if rows else None


def cancel_confirm_kb(booking_id: int, *, new: bool = False) -> InlineKeyboardMarkup:
    return inline(
        [
            [
                btn(T["booking.btn_cancel_yes"], MyCb(a="yes", id=booking_id)),
                btn(T["booking.btn_cancel_no"], MyCb(a="non" if new else "no", id=booking_id)),
            ]
        ]
    )


def reminder_kb(booking: Booking, hours: int) -> InlineKeyboardMarkup:
    sc = booking.slot.scenario
    row = []
    if sc.map_link:
        row.append(url_btn(T["notify.btn_map"], sc.map_link))
    key = "notify.btn_cancel_booking" if hours == 24 else "notify.btn_cancel_short"
    row.append(btn(T[key], MyCb(a="askn", id=booking.id)))
    return inline([row])


# ---------------------------------------------------------------- фидбэк (§10)


def rating_kb(booking_id: int) -> InlineKeyboardMarkup:
    return inline([[btn(str(r), FbCb(id=booking_id, r=r)) for r in range(1, 6)]])


def feedback_skip_kb(booking_id: int) -> InlineKeyboardMarkup:
    return inline([[btn(T["feedback.btn_skip"], FbCb(id=booking_id, r=0))]])


# ---------------------------------------------------------------- about (§11)


def about_kb(privacy_url: str) -> InlineKeyboardMarkup:
    rows: Rows = []
    if privacy_url:
        rows.append([url_btn(T["about.btn_privacy"], privacy_url)])
    rows.append([btn(T["about.btn_suggest"], NavCb(to="suggest"))])
    return inline(rows)


# ---------------------------------------------------------------- партнёр (§12)


def partner_menu_kb() -> InlineKeyboardMarkup:
    return inline(
        [
            [btn(T["partner.btn_bookings"], PmCb(a="bookings"))],
            [btn(T["partner.btn_slots"], PmCb(a="slots"))],
            [btn(T["partner.btn_attendance"], PmCb(a="attend"))],
        ]
    )


def attendance_kb(booking_id: int) -> InlineKeyboardMarkup:
    return inline(
        [
            [
                btn(T["partner.btn_attended"], AttCb(id=booking_id, v=1)),
                btn(T["partner.btn_no_show"], AttCb(id=booking_id, v=0)),
            ]
        ]
    )


# ---------------------------------------------------------------- админка (§13)


def admin_menu_kb() -> InlineKeyboardMarkup:
    return inline(
        [
            [
                btn(T["admin.btn_scenarios"], AdmCb(a="scl")),
                btn(T["admin.btn_slots"], AdmCb(a="sll")),
            ],
            [
                btn(T["admin.btn_bookings"], AdmCb(a="bks")),
                btn(T["admin.btn_feedback"], AdmCb(a="fbk")),
            ],
            [
                btn(T["admin.btn_stats"], AdmCb(a="st", p=56)),
                btn(T["admin.btn_broadcast"], AdmCb(a="bc")),
            ],
            [btn(T["admin.btn_partners"], AdmCb(a="prt"))],
        ]
    )


def admin_flag(sc: Scenario) -> str:
    if not sc.is_active:
        return "🙈 "
    if sc.is_featured:
        return "⭐ "
    return ""


def admin_scenarios_kb(
    scenarios: list[Scenario], *, page: int, has_more: bool, action: str
) -> InlineKeyboardMarkup:
    """action: scv (карточка) или slv (слоты сценария); пагинация по 8."""
    list_action = "scl" if action == "scv" else "sll"
    rows: Rows = [
        [btn(admin_flag(sc) + title_short(sc.title), AdmCb(a=action, id=sc.id))] for sc in scenarios
    ]
    if action == "scv":
        rows.append([btn(T["admin.btn_new_scenario"], AdmCb(a="new"))])
    nav = []
    if page > 0:
        nav.append(btn(T["admin.btn_prev"], AdmCb(a=list_action, p=page - 1)))
    if has_more:
        nav.append(btn(T["admin.btn_next"], AdmCb(a=list_action, p=page + 1)))
    rows.append(nav)
    rows.append([btn(T["admin.btn_menu"], AdmCb(a="menu"))])
    return inline(rows)


def admin_scenario_kb(sc: Scenario) -> InlineKeyboardMarkup:
    return inline(
        [
            [
                btn(T["admin.btn_edit"], AdmCb(a="edit", id=sc.id)),
                btn(T["admin.btn_sc_slots"], AdmCb(a="slv", id=sc.id)),
            ],
            [
                btn(
                    T["admin.btn_hide" if sc.is_active else "admin.btn_show"],
                    AdmCb(a="act", id=sc.id, p=0 if sc.is_active else 1),
                ),
                btn(
                    T["admin.btn_unfeature" if sc.is_featured else "admin.btn_feature"],
                    AdmCb(a="feat", id=sc.id, p=0 if sc.is_featured else 1),
                ),
            ],
            [btn(T["admin.btn_to_list"], AdmCb(a="scl"))],
        ]
    )


def admin_cancel_kb(extra: Rows | None = None) -> InlineKeyboardMarkup:
    rows: Rows = list(extra or [])
    rows.append([btn(T["admin.btn_cancel"], CancelCb(w="admin"))])
    return inline(rows)


def vibes_kb(selected: list[str]) -> InlineKeyboardMarkup:
    buttons = []
    for value, label in VIBE.items():
        text = f"✓ {strip_emoji_label(label)}" if value in selected else label
        buttons.append(btn(text, ScfCb(a="vib", v=value)))
    rows = pairs(buttons)
    rows.append([btn(T["admin.btn_done"], ScfCb(a="vdone"))])
    return admin_cancel_kb(rows)


def time_of_day_kb() -> InlineKeyboardMarkup:
    return admin_cancel_kb(
        [[btn(label, ScfCb(a="tod", v=value)) for value, label in TIME_OF_DAY.items()]]
    )


def need_booking_kb() -> InlineKeyboardMarkup:
    return admin_cancel_kb(
        [
            [btn(T["admin.btn_nb_yes"], ScfCb(a="nb", v="1"))],
            [btn(T["admin.btn_nb_no"], ScfCb(a="nb", v="0"))],
        ]
    )


def indoor_kb() -> InlineKeyboardMarkup:
    return admin_cancel_kb(
        [
            [btn(T["admin.btn_indoor_yes"], ScfCb(a="in", v="1"))],
            [btn(T["admin.btn_indoor_no"], ScfCb(a="in", v="0"))],
        ]
    )


def photos_done_kb() -> InlineKeyboardMarkup:
    return admin_cancel_kb([[btn(T["admin.btn_done"], ScfCb(a="pdone"))]])


def partners_pick_kb(partners: list[User]) -> InlineKeyboardMarkup:
    rows: Rows = []
    for p in partners:
        label = (p.first_name or str(p.tg_id)) + (f" @{p.username}" if p.username else "")
        rows.append([btn(title_short(label, 40), ScfCb(a="par", v=str(p.id)))])
    rows.append([btn(T["admin.btn_no_partner"], ScfCb(a="par", v="0"))])
    return admin_cancel_kb(rows)


def sc_preview_kb() -> InlineKeyboardMarkup:
    return inline(
        [
            [btn(T["admin.btn_save"], ScfCb(a="save"))],
            [btn(T["admin.btn_restart"], ScfCb(a="restart"))],
            [btn(T["admin.btn_cancel"], CancelCb(w="admin"))],
        ]
    )


def sc_created_kb(scenario_id: int) -> InlineKeyboardMarkup:
    return inline(
        [
            [btn(T["admin.btn_add_slots"], AdmCb(a="sla", id=scenario_id))],
            [btn(T["admin.btn_show"], AdmCb(a="act", id=scenario_id, p=1))],
        ]
    )


EDIT_FIELDS = (
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


def sc_edit_pick_kb(scenario_id: int) -> InlineKeyboardMarkup:
    buttons = [btn(FIELD_LABELS[f], AdmCb(a="ef", id=scenario_id, v=f)) for f in EDIT_FIELDS]
    rows = pairs(buttons)
    rows.append([btn(T["admin.btn_back"], AdmCb(a="scv", id=scenario_id))])
    return inline(rows)


def admin_slots_kb(scenario_id: int, slots: list[Slot]) -> InlineKeyboardMarkup:
    rows: Rows = [[btn(T["admin.btn_slot_add"], AdmCb(a="sla", id=scenario_id))]]
    for slot in slots:
        if slot.booked_count == 0:
            rows.append(
                [
                    btn(
                        T["admin.btn_slot_delete"].format(slot=fmt_slot(slot.starts_at)),
                        AdmCb(a="sld", id=slot.id, p=scenario_id),
                    )
                ]
            )
    rows.append([btn(T["admin.btn_back"], AdmCb(a="sll"))])
    return inline(rows)


def admin_feedback_kb(only_low: bool) -> InlineKeyboardMarkup:
    toggle = (
        btn(T["admin.btn_all"], AdmCb(a="fbk", v="all"))
        if only_low
        else btn(T["admin.btn_only_low"], AdmCb(a="fbk", v="low"))
    )
    return inline([[toggle], [btn(T["admin.btn_menu"], AdmCb(a="menu"))]])


def admin_stats_kb() -> InlineKeyboardMarkup:
    return inline(
        [
            [btn(T["admin.btn_days"].format(n=d), AdmCb(a="st", p=d)) for d in (7, 28, 56)],
            [btn(T["admin.btn_menu"], AdmCb(a="menu"))],
        ]
    )


def bc_segment_kb() -> InlineKeyboardMarkup:
    rows: Rows = [[btn(label, AdmCb(a="bcs", v=seg))] for seg, label in SEGMENT.items()]
    return admin_cancel_kb(rows)


def bc_preview_kb() -> InlineKeyboardMarkup:
    return inline(
        [
            [btn(T["admin.btn_bc_send"], AdmCb(a="bcsend"))],
            [btn(T["admin.btn_bc_edit"], AdmCb(a="bcedit"))],
            [btn(T["admin.btn_cancel"], CancelCb(w="admin"))],
        ]
    )


def admin_partners_kb() -> InlineKeyboardMarkup:
    return inline(
        [
            [btn(T["admin.btn_partner_add"], AdmCb(a="pra"))],
            [btn(T["admin.btn_partner_remove"], AdmCb(a="prr"))],
            [btn(T["admin.btn_template"], AdmCb(a="tpl"))],
            [btn(T["admin.btn_menu"], AdmCb(a="menu"))],
        ]
    )


def partners_remove_kb(partners: list[User]) -> InlineKeyboardMarkup:
    rows: Rows = []
    for p in partners:
        label = (p.first_name or str(p.tg_id)) + (f" @{p.username}" if p.username else "")
        rows.append([btn("➖ " + title_short(label, 40), AdmCb(a="prd", id=p.tg_id))])
    rows.append([btn(T["admin.btn_back"], AdmCb(a="prt"))])
    return inline(rows)
