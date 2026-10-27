"""keyboards: меню по ролям, карточка, слоты, фильтры, каталог."""

from __future__ import annotations

from datetime import timedelta

from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup

from app.bot import keyboards as kb
from app.bot.callbacks import BookCb, CardCb, CatCb, FbCb, FilterCb, MyCb, PbCb
from app.bot.texts import T
from app.db.models import Role
from tests.bot.conftest import button_texts, buttons
from tests.bot.helpers import FRI_19, booking, sc, slot, user


def _rows(markup) -> list[list[str]]:
    rows = markup.inline_keyboard if isinstance(markup, InlineKeyboardMarkup) else markup.keyboard
    return [[b.text for b in row] for row in rows]


# ---------------------------------------------------------------- главное меню


BASE_MENU = [
    [T["menu.btn_weekend"]],
    [T["menu.btn_filters"], T["menu.btn_my"]],
    [T["menu.btn_saved"], T["menu.btn_suggest"]],
    [T["menu.btn_about"]],
]


def test_main_menu_user():
    m = kb.main_menu(Role.user)
    assert isinstance(m, ReplyKeyboardMarkup)
    assert _rows(m) == BASE_MENU
    assert m.resize_keyboard and m.is_persistent
    assert m.input_field_placeholder == T["menu.placeholder"]


def test_main_menu_partner_and_admin_differ():
    partner = _rows(kb.main_menu(Role.partner))
    admin = _rows(kb.main_menu(Role.admin))
    assert partner == [*BASE_MENU, [T["menu.btn_partner"]]]
    assert admin == [*BASE_MENU, [T["menu.btn_admin"]]]
    assert len({str(partner), str(admin), str(BASE_MENU)}) == 3


def test_menu_for_user_object_and_none():
    assert _rows(kb.menu_for(user(Role.partner))) == [*BASE_MENU, [T["menu.btn_partner"]]]
    assert _rows(kb.menu_for(user(Role.admin))) == [*BASE_MENU, [T["menu.btn_admin"]]]
    assert _rows(kb.menu_for(None)) == BASE_MENU


def test_main_menu_accepts_string_role():
    assert _rows(kb.main_menu("partner"))[-1] == [T["menu.btn_partner"]]


# ---------------------------------------------------------------- бронь: reply


def test_ask_name_kb():
    m = kb.ask_name_kb("Аня")
    assert _rows(m) == [["Аня"], [T["booking.btn_cancel"]]]
    assert m.one_time_keyboard
    assert _rows(kb.ask_name_kb(None)) == [[T["booking.btn_cancel"]]]
    assert _rows(kb.ask_name_kb("Я")) == [[T["booking.btn_cancel"]]]  # имя короче 2 — без кнопки


def test_ask_contact_kb():
    m = kb.ask_contact_kb("anya")
    btns = buttons(m)
    assert btns[0].text == "📱 Отправить номер" and btns[0].request_contact
    assert btns[1].text == "✈️ Взять мой @anya"
    assert btns[-1].text == T["booking.btn_cancel"]
    assert button_texts(kb.ask_contact_kb(None)) == ["📱 Отправить номер", T["booking.btn_cancel"]]


# ---------------------------------------------------------------- карточка


def test_card_kb_save_vs_saved():
    s = sc(photos=["a", "b", "c"])
    not_saved = button_texts(kb.card_kb(s, saved=False, bot_username="bot"))
    saved = button_texts(kb.card_kb(s, saved=True, bot_username="bot"))
    assert "🤍 Сохранить" in not_saved and "❤️ В сохранённом" not in not_saved
    assert "❤️ В сохранённом" in saved and "🤍 Сохранить" not in saved
    save_btn = next(
        b
        for b in buttons(kb.card_kb(s, saved=True, bot_username="bot"))
        if b.text == "❤️ В сохранённом"
    )
    assert CardCb.unpack(save_btn.callback_data) == CardCb(a="save", id=s.id)


def test_card_kb_layout_with_booking():
    s = sc(photos=["a", "b", "c"])
    rows = _rows(kb.card_kb(s, saved=False, bot_username="bot"))
    assert rows == [
        ["📅 Забронировать"],
        ["📤 Поделиться", "🤍 Сохранить"],
        ["📷 Ещё фото (2)", "← Назад"],
    ]
    book = buttons(kb.card_kb(s, saved=False, bot_username="bot"))[0]
    assert BookCb.unpack(book.callback_data) == BookCb(a="start", id=s.id)
    share = buttons(kb.card_kb(s, saved=False, bot_username="bot"))[1]
    assert share.url and "start%3Dsc_42" in share.url


def test_card_kb_no_booking_shows_map():
    s = sc(need_booking=False, photos=["a"])
    m = kb.card_kb(s, saved=False, bot_username=None, truncated=True)
    first = buttons(m)[0]
    assert first.text == "🗺 Как добраться" and first.url == s.map_link
    texts = button_texts(m)
    assert "📤 Поделиться" not in texts  # без username нет share
    assert not any(t.startswith("📷") for t in texts)  # одно фото
    assert texts[-1] == "📋 Подробнее"


def test_card_kb_placeholder_hides_more_photos():
    s = sc(photos=["a", "b"])
    texts = button_texts(kb.card_kb(s, saved=False, bot_username="b", show_more_photos=False))
    assert not any(t.startswith("📷") for t in texts)


# ---------------------------------------------------------------- слоты


def test_slots_kb_left_suffix_and_cancel_last():
    s = sc()
    few = slot(s, FRI_19, capacity=4, booked=2)
    many = slot(s, FRI_19 + timedelta(days=1), capacity=10, booked=2)
    many.id = 8
    m = kb.slots_kb([few, many])
    assert _rows(m) == [
        ["Пт 30.10, 19:00 · осталось 2"],
        ["Сб 31.10, 19:00"],
        ["Отмена"],
    ]
    assert BookCb.unpack(buttons(m)[0].callback_data) == BookCb(a="slot", id=7)
    assert BookCb.unpack(buttons(m)[-1].callback_data).a == "abort"


def test_slots_kb_max_8():
    s = sc()
    items = [slot(s, FRI_19 + timedelta(hours=i)) for i in range(12)]
    rows = _rows(kb.slots_kb(items))
    assert len(rows) == 9
    assert rows[-1] == ["Отмена"]


def test_booking_confirm_kb():
    assert _rows(kb.booking_confirm_kb()) == [
        ["✅ Отправить заявку"],
        ["✏️ Изменить"],
        ["Отмена"],
    ]


def test_partner_booking_kb():
    m = kb.partner_booking_kb(9)
    assert _rows(m) == [["✅ Подтвердить", "❌ Отклонить"]]
    assert [PbCb.unpack(b.callback_data) for b in buttons(m)] == [
        PbCb(a="ok", id=9),
        PbCb(a="no", id=9),
    ]


def test_my_bookings_kb():
    b = booking(sc(title="Ужин на крыше с видом на Обь"), starts_at=FRI_19 + timedelta(days=1))
    m = kb.my_bookings_kb([b], FRI_19)
    assert m is not None
    text = buttons(m)[0].text
    assert text.startswith("Отменить · Сб 19:00 Ужин на крыше")
    assert MyCb.unpack(buttons(m)[0].callback_data) == MyCb(a="ask", id=b.id)
    assert kb.my_bookings_kb([], FRI_19) is None


def test_rating_kb_one_row_of_five():
    m = kb.rating_kb(3)
    assert _rows(m) == [["1", "2", "3", "4", "5"]]
    assert FbCb.unpack(buttons(m)[4].callback_data) == FbCb(id=3, r=5)


# ---------------------------------------------------------------- фильтры/каталог


def test_filters_root_defaults():
    rows = _rows(kb.filters_root_kb("-----"))
    assert rows == [
        ["Вайб: любой"],
        ["Бюджет: любой"],
        ["Время: любое"],
        ["Когда: Когда угодно"],
        ["Бронь: Неважно"],
        ["✨ Показать", "Сбросить"],
    ]


def test_filters_root_selected_values():
    rows = _rows(kb.filters_root_kb("f2ewy"))
    assert rows[:5] == [
        ["Вайб: Вкусно"],
        ["Бюджет: 1 000–2 000 ₽"],
        ["Время: Вечером"],
        ["Когда: Выходные"],
        ["Бронь: Только с записью"],
    ]


def test_filters_group_marks_selected():
    m = kb.filters_group_kb("--e--", "time")
    texts = button_texts(m)
    assert "✓ Вечером" in texts
    assert "☀️ Днём" in texts
    assert texts[-2:] == ["Любое", "← Назад"]
    pick = next(b for b in buttons(m) if b.text == "☀️ Днём")
    assert FilterCb.unpack(pick.callback_data) == FilterCb(a="s", f="--e--", g="time", v="d")
    any_selected = button_texts(kb.filters_group_kb("-----", "vibe"))
    assert "✓ Любой" in any_selected


def test_catalog_kb_more_only_when_has_more():
    items = [sc(id=i, title=f"Вечер {i}") for i in range(1, 6)]
    m = kb.catalog_kb(items, kind="w", page=0, has_more=True)
    rows = _rows(m)
    assert rows[0] == ["1. Вечер 1"]
    assert rows[-1] == ["🔎 Фильтры", "Дальше →"]
    more = buttons(m)[-1]
    assert CatCb.unpack(more.callback_data) == CatCb(k="w", p=1, f="-----")
    last = _rows(kb.catalog_kb(items, kind="r", page=1, has_more=False, offset=5, code="f----"))
    assert last[0] == ["6. Вечер 1"]
    assert last[-1] == ["🔎 Изменить фильтры"]


def test_filters_empty_kb():
    assert _rows(kb.filters_empty_kb("f----")) == [
        ["Сбросить фильтры"],
        ["🔎 Изменить"],
        ["🌆 Вечера на выходные"],
    ]
