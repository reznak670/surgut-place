"""callbacks: ≤64 байт на крайних значениях, encode/decode фильтров туда-обратно."""

from __future__ import annotations

import itertools
from datetime import timedelta

import pytest
from aiogram.filters.callback_data import CallbackData

from app.bot import callbacks as cb
from app.bot import keyboards as kb
from app.db.models import Role, TimeOfDay, Vibe
from app.services.scenarios import Filters
from tests.bot.helpers import FRI_19, booking, sc, slot, user

MAX_ID = 2**31 - 1  # Integer PK в Postgres
MAX_TG_ID = 2**52  # Telegram user id влезает в 52 бита
FULL_FILTERS = "ru" + "n" + "w" + "y"  # все группы заполнены

EXTREMES: list[CallbackData] = [
    cb.NavCb(to="weekend"),
    cb.NavCb(to="suggest"),
    cb.OnbCb(step="bud", value="unlimited"),
    cb.OnbCb(step="who", value="couple"),
    cb.FilterCb(a="reset"),
    cb.FilterCb(a="show", f=FULL_FILTERS),
    cb.FilterCb(a="s", f=FULL_FILTERS, g="budget", v="u"),
    cb.FilterCb(a="root", f=FULL_FILTERS, g="budget"),
    cb.CatCb(k="r", p=10**6, f=FULL_FILTERS),
    cb.CardCb(a="save", id=MAX_ID),
    cb.CardCb(a="open", id=MAX_ID),
    cb.BookCb(a="start", id=MAX_ID),
    cb.BookCb(a="abort"),
    cb.MyCb(a="askn", id=MAX_ID),
    cb.PbCb(a="ok", id=MAX_ID),
    cb.AttCb(id=MAX_ID, v=1),
    cb.PmCb(a="bookings"),
    cb.FbCb(id=MAX_ID, r=5),
    cb.CancelCb(w="suggest"),
    cb.AdmCb(a="bcsend", id=MAX_TG_ID, p=10**6, v="with_bookings"),
    cb.AdmCb(a="ef", id=MAX_ID, v="includes_text"),
    cb.AdmCb(a="prd", id=MAX_TG_ID),
    cb.AdmCb(a="sld", id=MAX_ID, p=MAX_ID),
    cb.ScfCb(a="restart", v=str(MAX_ID)),
    cb.ScfCb(a="vib", v="unusual"),
]


@pytest.mark.parametrize("data", EXTREMES, ids=lambda d: type(d).__name__)
def test_callback_fits_64_bytes(data: CallbackData):
    packed = data.pack()
    assert len(packed.encode()) <= 64, packed
    assert type(data).unpack(packed) == data


def _all_callback_data(markup) -> list[str]:
    return [
        b.callback_data
        for row in markup.inline_keyboard
        for b in row
        if b.callback_data is not None
    ]


def test_all_keyboards_with_extreme_ids_fit_64_bytes():
    """Все inline-клавиатуры, собранные на крайних значениях id."""
    big_sc = sc(id=MAX_ID, title="Я" * 200, photos=["a", "b", "c"], is_featured=True)
    big_slot = slot(big_sc, FRI_19, capacity=50, booked=0)
    big_slot.id = MAX_ID
    big_booking = booking(big_sc, id=MAX_ID)
    partner = user(Role.partner, id=MAX_ID, tg_id=MAX_TG_ID, username="p" * 32)
    markups = [
        kb.onb_start_kb(),
        kb.onb_who_kb(),
        kb.onb_budget_kb(),
        kb.filters_root_kb(FULL_FILTERS),
        *[kb.filters_group_kb(FULL_FILTERS, g) for g in cb.GROUPS],
        kb.catalog_kb(
            [big_sc] * 5, kind="r", page=10**6, has_more=True, offset=10**6, code=FULL_FILTERS
        ),
        kb.filters_empty_kb(FULL_FILTERS),
        kb.card_kb(big_sc, saved=True, bot_username="b" * 32, truncated=True),
        kb.slots_kb([big_slot] * 8),
        kb.booking_confirm_kb(),
        kb.other_time_kb(MAX_ID),
        kb.confirmed_push_kb(big_sc),
        kb.declined_push_kb(MAX_ID),
        kb.partner_booking_kb(MAX_ID),
        kb.my_bookings_kb([big_booking], FRI_19 - timedelta(days=1)),
        kb.cancel_confirm_kb(MAX_ID, new=True),
        kb.reminder_kb(big_booking, 24),
        kb.rating_kb(MAX_ID),
        kb.feedback_skip_kb(MAX_ID),
        kb.partner_menu_kb(),
        kb.attendance_kb(MAX_ID),
        kb.admin_menu_kb(),
        kb.admin_scenarios_kb([big_sc], page=10**6, has_more=True, action="slv"),
        kb.admin_scenario_kb(big_sc),
        kb.vibes_kb(list(cb.VIBE_CODES.values())),
        kb.time_of_day_kb(),
        kb.need_booking_kb(),
        kb.indoor_kb(),
        kb.photos_done_kb(),
        kb.partners_pick_kb([partner]),
        kb.sc_preview_kb(),
        kb.sc_created_kb(MAX_ID),
        kb.sc_edit_pick_kb(MAX_ID),
        kb.admin_slots_kb(MAX_ID, [big_slot]),
        kb.admin_feedback_kb(True),
        kb.admin_stats_kb(),
        kb.bc_segment_kb(),
        kb.bc_preview_kb(),
        kb.admin_partners_kb(),
        kb.partners_remove_kb([partner]),
    ]
    datas = [d for m in markups for d in _all_callback_data(m)]
    assert datas
    too_long = [d for d in datas if len(d.encode()) > 64]
    assert not too_long


# ---------------------------------------------------------------- фильтры


def _all_filters():
    vibes = [None, *Vibe]
    budgets = [None, *cb.BUDGET_CODES.values()]
    times = [None, *TimeOfDay]
    dates = ["any", *cb.DATE_CODES.values()]
    needs = ["any", "yes"]
    for v, b, t, d, n in itertools.product(vibes, budgets, times, dates, needs):
        yield Filters(vibe=v, budget=b, time_of_day=t, date=d, need_booking=n)


def test_filters_roundtrip_all_combinations():
    seen = set()
    for f in _all_filters():
        code = cb.encode_filters(f)
        assert len(code) == 5
        assert cb.decode_filters(code) == f
        seen.add(code)
    assert len(seen) == 6 * 5 * 4 * 4 * 2  # коды уникальны


def test_empty_filters_code():
    assert cb.encode_filters(Filters()) == cb.EMPTY_FILTERS
    assert cb.decode_filters(cb.EMPTY_FILTERS) == Filters()
    assert cb.decode_filters("") == Filters()  # короткий/пустой код не падает


def test_decode_ignores_garbage():
    assert cb.decode_filters("?????") == Filters()


def test_filter_code_survives_callback_pack():
    f = Filters(vibe=Vibe.food, budget="2000_4000", date="weekend", need_booking="yes")
    packed = cb.CatCb(k="r", p=3, f=cb.encode_filters(f)).pack()
    restored = cb.CatCb.unpack(packed)
    assert cb.decode_filters(restored.f) == f


def test_set_filter_and_group_value():
    code = cb.set_filter(cb.EMPTY_FILTERS, "vibe", "f")
    assert code == "f----"
    code = cb.set_filter(code, "need", "y")
    assert code == "f---y"
    assert cb.group_value(code, "vibe") == "f"
    assert cb.group_value(code, "date") == "-"
    assert cb.set_filter(code, "vibe", "-") == "----y"
    # неизвестное значение → «любой»
    assert cb.set_filter(code, "budget", "Z") == "f---y"
    assert cb.set_filter(code, "vibe", "Z") == "----y"
