"""Хендлеры ключевого пути: настоящий Dispatcher + in-memory SQLite + фейковая сессия Bot API."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from aiogram.methods import (
    DeleteMessage,
    EditMessageCaption,
    EditMessageReplyMarkup,
    EditMessageText,
    SendMessage,
    SendPhoto,
)
from aiogram.types import ReplyKeyboardMarkup, ReplyKeyboardRemove
from sqlalchemy import func, select

from app.bot.callbacks import BookCb, CardCb, CatCb, FbCb, FilterCb, MyCb, NavCb, OnbCb, PbCb
from app.bot.states import BookingFlow, FeedbackFlow
from app.bot.texts import ROT, T
from app.db.models import (
    Booking,
    BookingStatus,
    Budget,
    Event,
    Feedback,
    Role,
    Slot,
    User,
    Who,
)
from app.services import users
from app.services.timeutil import now_utc, weekend_window
from tests.backend.factories import make_booking, make_scenario, make_slot, make_user
from tests.bot.conftest import ADMIN_CHAT, ADMIN_TG, BOT_USERNAME, Harness, body, button_texts

CONTACT = "+79123456789"


# ---------------------------------------------------------------- данные


def weekend_slot_time():
    now = now_utc()
    start, end = weekend_window(now)
    at = max(start + timedelta(hours=19), now + timedelta(hours=1))
    return min(at, end - timedelta(minutes=30))


async def seed(h: Harness, fn) -> Any:
    async with h.sessionmaker() as s:
        result = await fn(s)
        await s.commit()
        return result


async def weekend_scenarios(h: Harness, n: int, **kw: Any) -> list[int]:
    async def go(s):
        ids = []
        for i in range(n):
            sc = await make_scenario(s, title=f"Вечер №{i + 1}", **kw)
            await make_slot(s, sc, weekend_slot_time(), capacity=4)
            ids.append(sc.id)
        return ids

    return await seed(h, go)


async def onboarded_user(h: Harness, role: Role = Role.user) -> User:
    return await seed(h, lambda s: make_user(s, role=role, onboarded=True))


async def get_user(h: Harness, tg_id: int) -> User | None:
    async with h.sessionmaker() as s:
        return await users.get_by_tg_id(s, tg_id)


async def get_booking(h: Harness, booking_id: int) -> Booking:
    async with h.sessionmaker() as s:
        b = await s.get(Booking, booking_id)
        assert b is not None
        return b


# ---------------------------------------------------------------- онбординг


async def test_start_onboarding_full_flow(harness: Harness):
    h = harness
    await weekend_scenarios(h, 1)
    tg = 5001

    sent = await h.send(tg, "/start")
    [photo] = sent.of(SendPhoto)
    assert photo.chat_id == tg
    assert "Привет, это «Сургутский плейс»" in photo.caption
    assert 'href="https://example.org/privacy"' in photo.caption
    assert button_texts(photo.reply_markup) == ["🌆 Смотреть вечера"]
    user = await get_user(h, tg)
    assert user is not None and not user.onboarded

    sent = await h.click(tg, OnbCb(step="go"), photo=True, message_text=photo.caption)
    [edit] = sent.of(EditMessageCaption)
    assert "<b>Кто вы?</b>" in edit.caption
    assert (await get_user(h, tg)).privacy_accepted_at is not None

    sent = await h.click(tg, OnbCb(step="who", value="couple"), photo=True)
    [edit] = sent.of(EditMessageCaption)
    assert "Сколько готовы потратить" in edit.caption

    sent = await h.click(tg, OnbCb(step="bud", value="lt2000"), photo=True)
    messages = sent.of(SendMessage)
    assert messages[0].text == T["onboarding.done"]
    assert isinstance(messages[0].reply_markup, ReplyKeyboardMarkup)
    assert "🌆 Вечера на выходные" in messages[1].text and "Вечер №1" in messages[1].text

    user = await get_user(h, tg)
    assert user.onboarded and user.who == Who.couple and user.budget == Budget.lt2000

    sent = await h.send(tg, "/start")
    assert sent.texts(tg) == [T["menu.welcome_back"]]


async def test_onboarding_skip_answers(harness: Harness):
    h = harness
    tg = 5002
    await h.send(tg, "/start")
    await h.click(tg, OnbCb(step="go"), photo=True)
    await h.click(tg, OnbCb(step="who", value="skip"), photo=True)
    await h.click(tg, OnbCb(step="bud", value="skip"), photo=True)
    user = await get_user(h, tg)
    assert user.onboarded and user.who is None and user.budget is None


async def test_start_deeplink_new_user(harness: Harness):
    h = harness
    [sc_id] = await weekend_scenarios(h, 1)
    tg = 5003

    sent = await h.send(tg, f"/start sc_{sc_id}")
    [welcome] = sent.of(SendMessage)
    assert welcome.text.startswith("Привет! С вами поделились вечером")
    assert isinstance(welcome.reply_markup, ReplyKeyboardMarkup)
    [card] = sent.of(SendPhoto)
    assert card.photo == "https://picsum.photos/seed/a/800/600"
    assert "<b>Вечер №1</b>" in card.caption
    texts = button_texts(card.reply_markup)
    assert "📅 Забронировать" in texts and "🤍 Сохранить" in texts
    share = next(b for b in card.reply_markup.inline_keyboard[1] if b.url)
    assert f"t.me%2F{BOT_USERNAME}%3Fstart%3Dsc_{sc_id}" in share.url

    user = await get_user(h, tg)
    assert user.onboarded and user.privacy_accepted_at is not None
    async with h.sessionmaker() as s:
        views = await s.scalar(select(func.count()).select_from(Event))
    assert views == 1


async def test_start_deeplink_existing_user_skips_welcome(harness: Harness):
    h = harness
    [sc_id] = await weekend_scenarios(h, 1)
    u = await onboarded_user(h)
    sent = await h.send(u.tg_id, f"/start sc_{sc_id}")
    assert not sent.of(SendMessage)
    assert len(sent.of(SendPhoto)) == 1


@pytest.mark.parametrize("arg", ["sc_999999", "hidden"])
async def test_start_deeplink_missing_or_hidden(harness: Harness, arg):
    h = harness
    if arg == "hidden":
        sc_id = (await seed(h, lambda s: make_scenario(s, is_active=False))).id
        arg = f"sc_{sc_id}"
    sent = await h.send(5004, f"/start {arg}")
    assert T["card.not_found"] in sent.texts(5004)
    assert not sent.of(SendPhoto)


# ---------------------------------------------------------------- каталог


async def test_weekend_pagination(harness: Harness):
    h = harness
    await weekend_scenarios(h, 7)
    u = await onboarded_user(h)

    sent = await h.send(u.tg_id, "/weekend")
    [page1] = sent.of(SendMessage)
    assert page1.text.startswith("<b>🌆 Вечера на выходные</b> · 1–5")
    item_buttons = [t for t in button_texts(page1.reply_markup) if t[0].isdigit()]
    assert len(item_buttons) == 5
    assert button_texts(page1.reply_markup)[-2:] == ["🔎 Фильтры", "Дальше →"]

    more = page1.reply_markup.inline_keyboard[-1][-1].callback_data
    sent = await h.click(u.tg_id, more, message_text=page1.text)
    [page2] = sent.of(EditMessageText)
    assert page2.text.startswith("<b>🌆 Вечера на выходные</b> · 6–7")
    assert "<b>6. " in page2.text and "<b>7. " in page2.text
    assert "Дальше →" not in button_texts(page2.reply_markup)

    sent = await h.click(u.tg_id, CatCb(k="w", p=5), message_text=page2.text)
    assert sent.toasts() == [T["catalog.end"]]
    assert not sent.of(EditMessageText)


async def test_weekend_menu_button_and_empty(harness: Harness):
    h = harness
    u = await onboarded_user(h)
    sent = await h.send(u.tg_id, T["menu.btn_weekend"])
    [photo] = sent.of(SendPhoto)  # пусто и нет прошлой недели → картинка all_empty
    assert photo.caption == T["catalog.all_empty"]


async def test_filters_select_show_and_empty(harness: Harness):
    h = harness

    async def go(s):
        food = await make_scenario(s, title="Дегустация", vibes=["food"])
        rom = await make_scenario(s, title="Свечи", vibes=["romance"])
        for sc in (food, rom):
            await make_slot(s, sc, weekend_slot_time())

    await seed(h, go)
    u = await onboarded_user(h)

    sent = await h.send(u.tg_id, T["menu.btn_filters"])
    [root] = sent.of(SendMessage)
    assert root.text == T["filters.root"]

    sent = await h.click(u.tg_id, FilterCb(a="g", g="vibe"), message_text=root.text)
    [group] = sent.of(EditMessageText)
    assert group.text == T["filters.title_vibe"]

    sent = await h.click(u.tg_id, FilterCb(a="s", g="vibe", v="f"), message_text=group.text)
    [root2] = sent.of(EditMessageText)
    assert button_texts(root2.reply_markup)[0] == "Вайб: Вкусно"
    show = root2.reply_markup.inline_keyboard[-1][0].callback_data
    assert FilterCb.unpack(show) == FilterCb(a="show", f="f----")

    sent = await h.click(u.tg_id, show, message_text=root2.text)
    [results] = sent.of(EditMessageText)
    assert results.text.startswith("<b>🔎 Подобрали</b> · 1–1")
    assert "Дегустация" in results.text and "Свечи" not in results.text

    sent = await h.click(u.tg_id, FilterCb(a="show", f="u----"), message_text=root2.text)
    [empty] = sent.of(SendPhoto)
    assert empty.caption in ROT["rot.empty_search"]
    assert "Сбросить фильтры" in button_texts(empty.reply_markup)

    sent = await h.click(u.tg_id, FilterCb(a="reset"), message_text=root2.text)
    assert sent.toasts() == [T["filters.reset_done"]]
    [reset] = sent.of(EditMessageText)
    assert button_texts(reset.reply_markup)[0] == "Вайб: любой"


async def test_card_save_toggle(harness: Harness):
    h = harness
    [sc_id] = await weekend_scenarios(h, 1)
    u = await onboarded_user(h)
    sent = await h.click(u.tg_id, CardCb(a="open", id=sc_id))
    [card] = sent.of(SendPhoto)

    sent = await h.click(
        u.tg_id, CardCb(a="save", id=sc_id), photo=True, reply_markup=card.reply_markup
    )
    assert sent.toasts() == [T["card.saved_toast"]]
    [edit] = sent.of(EditMessageReplyMarkup)
    assert "❤️ В сохранённом" in button_texts(edit.reply_markup)
    assert "📅 Забронировать" in button_texts(edit.reply_markup)  # остальное не тронуто

    sent = await h.click(
        u.tg_id, CardCb(a="save", id=sc_id), photo=True, reply_markup=edit.reply_markup
    )
    assert sent.toasts() == [T["card.unsaved_toast"]]
    [edit] = sent.of(EditMessageReplyMarkup)
    assert "🤍 Сохранить" in button_texts(edit.reply_markup)


# ---------------------------------------------------------------- бронь


async def partner_scenario(h: Harness, *, with_partner: bool = True) -> dict[str, int]:
    async def go(s):
        partner = await make_user(s, role=Role.partner, onboarded=True) if with_partner else None
        sc = await make_scenario(
            s, title="Ужин <на> крыше", partner_id=partner.id if partner else None
        )
        slot = await make_slot(s, sc, now_utc() + timedelta(days=3), capacity=4)
        return {
            "partner_tg": partner.tg_id if partner else 0,
            "sc_id": sc.id,
            "slot_id": slot.id,
        }

    return await seed(h, go)


async def book_through(h: Harness, tg: int, ids: dict[str, int]) -> list:
    """Проходит весь FSM брони до «Отправить заявку». Возвращает все запросы к API."""
    log: list = []

    async def step(coro):
        sent = await coro
        log.extend(sent.requests)
        return sent

    sent = await step(h.click(tg, BookCb(a="start", id=ids["sc_id"])))
    [slots_msg] = sent.of(SendMessage)
    assert "Выберите время" in slots_msg.text
    assert "Ужин &lt;на&gt; крыше" in slots_msg.text

    sent = await step(h.click(tg, BookCb(a="slot", id=ids["slot_id"]), message_text=slots_msg.text))
    [ask_name] = sent.of(SendMessage)
    assert "Как к вам обращаться?" in ask_name.text
    assert button_texts(ask_name.reply_markup) == ["Аня", "Отмена"]
    assert await h.state(tg).get_state() == BookingFlow.name.state

    sent = await step(h.send(tg, "А"))
    assert sent.texts(tg) == [T["booking.name_invalid"]]

    sent = await step(h.send(tg, "Аня"))
    [ask_contact] = sent.of(SendMessage)
    assert ask_contact.text == T["booking.ask_contact"]

    sent = await step(h.send(tg, "позвоните"))
    assert sent.texts(tg) == [T["booking.contact_invalid"]]

    foreign = {"phone_number": "+79990000000", "first_name": "Чужой", "user_id": tg + 1}
    sent = await step(h.send(tg, None, contact=foreign))
    assert sent.texts(tg) == [T["booking.contact_foreign"]]

    sent = await step(h.send(tg, "+7 912 345-67-89"))
    checking, confirm = sent.of(SendMessage)
    assert checking.text in ROT["rot.checking"]
    assert isinstance(checking.reply_markup, ReplyKeyboardRemove)
    assert "<b>Проверьте заявку</b>" in confirm.text
    assert f"👤 Аня, {CONTACT}" in confirm.text
    assert button_texts(confirm.reply_markup) == ["✅ Отправить заявку", "✏️ Изменить", "Отмена"]
    assert sent.of(DeleteMessage)  # служебное «Проверяем…» убрано
    assert await h.state(tg).get_state() == BookingFlow.confirm.state

    sent = await step(h.click(tg, BookCb(a="send"), message_text=confirm.text))
    return [*log, sent]


async def test_booking_flow_sends_request_only_to_partner(harness: Harness):
    h = harness
    ids = await partner_scenario(h)
    tg = 5010
    *flow, final = await book_through(h, tg, ids)

    user_msgs = final.to(tg)
    assert any("<b>Заявка принята</b>" in body(m) for m in user_msgs)
    assert isinstance(user_msgs[-1].reply_markup, ReplyKeyboardMarkup)  # меню вернулось

    [to_partner] = final.to(ids["partner_tg"])
    assert "🆕 <b>Новая заявка</b>" in to_partner.text
    assert f"📞 {CONTACT}" in to_partner.text and "👤 Аня" in to_partner.text
    assert "Ужин &lt;на&gt; крыше" in to_partner.text
    assert "осталось мест: 3" in to_partner.text
    assert T["partner.no_partner_prefix"] not in to_partner.text
    assert button_texts(to_partner.reply_markup) == ["✅ Подтвердить", "❌ Отклонить"]
    async with h.sessionmaker() as s:
        booking_id = await s.scalar(select(Booking.id))
    assert [b.callback_data for b in to_partner.reply_markup.inline_keyboard[0]] == [
        PbCb(a="ok", id=booking_id).pack(),
        PbCb(a="no", id=booking_id).pack(),
    ]

    # контакт уходит только партнёру (и в собственный чат пользователя на экране проверки)
    assert final.chats_with(CONTACT) == {ids["partner_tg"]}
    everything = [*flow, *final.requests]
    all_chats = {getattr(r, "chat_id", None) for r in everything if CONTACT in body(r)}
    assert all_chats <= {tg, ids["partner_tg"]}
    assert not final.to(ADMIN_CHAT)

    assert await h.state(tg).get_state() is None
    async with h.sessionmaker() as s:
        [b] = list(await s.scalars(select(Booking)))
        assert b.status == BookingStatus.pending
        assert (b.name, b.contact) == ("Аня", CONTACT)
        assert (await s.get(Slot, ids["slot_id"])).booked_count == 1


async def test_booking_without_partner_goes_to_admin_chat(harness: Harness):
    h = harness
    ids = await partner_scenario(h, with_partner=False)
    *_, final = await book_through(h, 5011, ids)
    [to_admin] = final.to(ADMIN_CHAT)
    assert to_admin.text.startswith(T["partner.no_partner_prefix"])
    assert CONTACT in to_admin.text
    assert final.chats_with(CONTACT) == {ADMIN_CHAT}


async def test_booking_partner_blocked_bot_does_not_break_user(harness: Harness):
    h = harness
    ids = await partner_scenario(h)
    h.fake.fail_chats.add(ids["partner_tg"])
    *_, final = await book_through(h, 5012, ids)
    assert any("Заявка принята" in t for t in final.texts(5012))
    async with h.sessionmaker() as s:
        assert await s.scalar(select(func.count()).select_from(Booking)) == 1


async def test_booking_send_twice_is_expired(harness: Harness):
    h = harness
    ids = await partner_scenario(h)
    await book_through(h, 5013, ids)
    sent = await h.click(5013, BookCb(a="send"))
    assert sent.toasts() == [T["error.expired"]]
    async with h.sessionmaker() as s:
        assert await s.scalar(select(func.count()).select_from(Booking)) == 1


async def test_booking_cancel_text_aborts(harness: Harness):
    h = harness
    ids = await partner_scenario(h)
    tg = 5014
    await h.click(tg, BookCb(a="start", id=ids["sc_id"]))
    await h.click(tg, BookCb(a="slot", id=ids["slot_id"]))
    sent = await h.send(tg, "Отмена")
    assert sent.texts(tg) == [T["booking.aborted"]]
    assert await h.state(tg).get_state() is None


async def test_menu_button_works_inside_booking_fsm(harness: Harness):
    h = harness
    ids = await partner_scenario(h)
    tg = 5015
    await h.click(tg, BookCb(a="start", id=ids["sc_id"]))
    await h.click(tg, BookCb(a="slot", id=ids["slot_id"]))
    await h.send(tg, T["menu.btn_about"])
    assert await h.state(tg).get_state() is None


async def test_booking_no_slots(harness: Harness):
    h = harness
    sc = await seed(h, lambda s: make_scenario(s))
    sent = await h.click(5016, BookCb(a="start", id=sc.id))
    assert sent.texts(5016) == [T["booking.no_slots"]]


async def test_booking_slot_full_at_send(harness: Harness):
    h = harness
    ids = await partner_scenario(h)
    tg = 5017
    await h.click(tg, BookCb(a="start", id=ids["sc_id"]))
    await h.click(tg, BookCb(a="slot", id=ids["slot_id"]))
    await h.send(tg, "Аня")
    await h.send(tg, "@anya_surgut")

    async def fill(s):
        slot = await s.get(Slot, ids["slot_id"])
        slot.booked_count = slot.capacity

    await seed(h, fill)
    sent = await h.click(tg, BookCb(a="send"))
    assert T["booking.slot_taken"] in sent.texts(tg)
    assert not sent.to(ids["partner_tg"])


# ---------------------------------------------------------------- партнёр


async def pending_booking(h: Harness, *, status=BookingStatus.pending, days: float = 3) -> dict:
    async def go(s):
        partner = await make_user(s, role=Role.partner, onboarded=True)
        guest = await make_user(s, onboarded=True)
        sc = await make_scenario(s, title="Каток", partner_id=partner.id, map_link="https://m.ru")
        slot = await make_slot(s, sc, now_utc() + timedelta(days=days), capacity=4)
        b = await make_booking(s, guest, slot, status=status)
        return {
            "partner_tg": partner.tg_id,
            "guest_tg": guest.tg_id,
            "booking_id": b.id,
            "slot_id": slot.id,
            "sc_id": sc.id,
        }

    return await seed(h, go)


async def test_partner_confirms_booking(harness: Harness):
    h = harness
    d = await pending_booking(h)
    text = "🆕 <b>Новая заявка</b>\nКаток"
    sent = await h.click(d["partner_tg"], PbCb(a="ok", id=d["booking_id"]), message_text=text)

    assert (await get_booking(h, d["booking_id"])).status == BookingStatus.confirmed
    [edit] = sent.of(EditMessageText)
    assert edit.chat_id == d["partner_tg"]
    assert "✅ Подтверждено · " in edit.text
    assert edit.reply_markup is None
    [push] = sent.to(d["guest_tg"])
    assert isinstance(push, SendPhoto)
    assert "✅ <b>Подтверждено, ждём вас</b>" in push.caption
    assert "🗺 На карте" in button_texts(push.reply_markup)

    # повторное нажатие
    sent = await h.click(d["partner_tg"], PbCb(a="ok", id=d["booking_id"]), message_text=text)
    assert sent.toasts() == [T["partner.booking_already"]]
    assert not sent.to(d["guest_tg"])


async def test_foreign_partner_forbidden(harness: Harness):
    h = harness
    d = await pending_booking(h)
    stranger = await onboarded_user(h, Role.partner)
    sent = await h.click(stranger.tg_id, PbCb(a="ok", id=d["booking_id"]))
    assert sent.toasts() == [T["partner.booking_forbidden"]]
    assert not sent.to(d["guest_tg"])
    assert (await get_booking(h, d["booking_id"])).status == BookingStatus.pending


async def test_regular_user_cannot_confirm(harness: Harness):
    h = harness
    d = await pending_booking(h)
    sent = await h.click(d["guest_tg"], PbCb(a="ok", id=d["booking_id"]))
    assert sent.toasts() == [T["error.no_access"]]
    assert (await get_booking(h, d["booking_id"])).status == BookingStatus.pending


async def test_admin_declines_booking(harness: Harness):
    h = harness
    d = await pending_booking(h)
    sent = await h.click(ADMIN_TG, PbCb(a="no", id=d["booking_id"]), message_text="заявка")
    b = await get_booking(h, d["booking_id"])
    assert b.status == BookingStatus.declined
    [push] = sent.to(d["guest_tg"])
    assert push.text.startswith("❌ <b>Отклонено</b>: Каток")
    assert "📅 Другое время" in button_texts(push.reply_markup)
    async with h.sessionmaker() as s:
        assert (await s.get(Slot, d["slot_id"])).booked_count == 0


async def test_partner_menu_and_role_denied(harness: Harness):
    h = harness
    d = await pending_booking(h)
    sent = await h.send(d["partner_tg"], T["menu.btn_partner"])
    assert "Ждут ответа: <b>1</b>" in sent.texts(d["partner_tg"])[0]
    sent = await h.send(d["guest_tg"], "/partner")
    assert sent.texts(d["guest_tg"]) == [T["error.no_access"]]


# ---------------------------------------------------------------- отмена пользователем


async def test_user_cancels_booking_partner_notified(harness: Harness):
    h = harness
    d = await pending_booking(h, status=BookingStatus.confirmed)
    tg = d["guest_tg"]

    sent = await h.send(tg, "/bookings")
    [listing] = sent.of(SendMessage)
    assert "<b>🎟 Мои брони</b>" in listing.text and "Каток" in listing.text
    cancel_cb = listing.reply_markup.inline_keyboard[0][0].callback_data
    assert MyCb.unpack(cancel_cb) == MyCb(a="ask", id=d["booking_id"])

    sent = await h.click(tg, cancel_cb, message_text=listing.text)
    [ask] = sent.of(EditMessageText)
    assert ask.text.startswith("Отменить бронь?")

    sent = await h.click(tg, MyCb(a="yes", id=d["booking_id"]), message_text=ask.text)
    assert (await get_booking(h, d["booking_id"])).status == BookingStatus.cancelled
    [done] = sent.of(EditMessageText)
    assert done.text.startswith("⚪️ <b>Отменена</b>: Каток")
    [to_partner] = sent.to(d["partner_tg"])
    assert "Бронь отменена гостем" in to_partner.text and "Аня" in to_partner.text
    async with h.sessionmaker() as s:
        assert (await s.get(Slot, d["slot_id"])).booked_count == 0


async def test_foreign_user_cannot_cancel(harness: Harness):
    h = harness
    d = await pending_booking(h, status=BookingStatus.confirmed)
    other = await onboarded_user(h)
    sent = await h.click(other.tg_id, MyCb(a="yes", id=d["booking_id"]))
    assert sent.toasts() == [T["error.no_access"]]
    assert not sent.to(d["partner_tg"])
    assert (await get_booking(h, d["booking_id"])).status == BookingStatus.confirmed


async def test_cancel_started_booking(harness: Harness):
    h = harness
    d = await pending_booking(h, status=BookingStatus.confirmed, days=-0.1)
    sent = await h.click(d["guest_tg"], MyCb(a="yes", id=d["booking_id"]), message_text="x")
    assert any(t.startswith("Нельзя отменить — уже началось") for t in sent.texts())
    assert not sent.to(d["partner_tg"])


# ---------------------------------------------------------------- фидбэк


async def past_confirmed(h: Harness, extra_scenarios: int = 0) -> dict:
    d = await pending_booking(h, status=BookingStatus.confirmed, days=-1)
    if extra_scenarios:
        await weekend_scenarios(h, extra_scenarios)
    return d


@pytest.mark.parametrize("rating", [1, 2, 3])
async def test_low_rating_alerts_admin_and_asks_comment(harness: Harness, rating):
    h = harness
    d = await past_confirmed(h)
    tg = d["guest_tg"]
    sent = await h.click(tg, FbCb(id=d["booking_id"], r=rating), message_text="<b>Как прошло?</b>")

    [edit] = sent.of(EditMessageText)
    assert edit.text.endswith(f"Ваша оценка: {rating}/5")
    assert T["feedback.ask_comment"] in sent.texts(tg)
    [alert] = sent.to(ADMIN_CHAT)
    assert f"⚠️ <b>Низкая оценка: {rating}/5</b>" in alert.text
    # ПД: у сценария есть партнёр → контакт гостя в админ-чат не уходит
    assert "Каток" in alert.text and "💬 —" in alert.text
    assert "👤 Аня (контакт у партнёра)" in alert.text and "@anya" not in alert.text
    assert await h.state(tg).get_state() == FeedbackFlow.comment.state

    sent = await h.send(tg, "Было <холодно>")
    [thanks] = sent.of(SendPhoto)
    assert thanks.caption == T["feedback.thanks_comment"]
    [alert2] = sent.to(ADMIN_CHAT)
    assert alert2.text == (f"💬 Комментарий к оценке {rating}/5 (Каток): Было &lt;холодно&gt;")
    assert await h.state(tg).get_state() is None
    async with h.sessionmaker() as s:
        fb = await s.scalar(select(Feedback))
        assert (fb.rating, fb.comment) == (rating, "Было <холодно>")


async def test_low_rating_alert_once_and_contact_without_partner(harness: Harness):
    h = harness
    d = await past_confirmed(h)

    async def drop_partner(s):
        from app.db.models import Scenario

        (await s.get(Scenario, d["sc_id"])).partner_id = None

    await seed(h, drop_partner)
    tg = d["guest_tg"]
    sent = await h.click(tg, FbCb(id=d["booking_id"], r=2), message_text="Как прошло?")
    [alert] = sent.to(ADMIN_CHAT)
    assert "👤 Аня (@anya)" in alert.text  # партнёра нет — заявку ведёт админ, контакт нужен
    # повторное нажатие ≤3 → второго алерта нет; переход 5 → 1 → алерт снова
    sent = await h.click(tg, FbCb(id=d["booking_id"], r=1), message_text="Как прошло?")
    assert sent.to(ADMIN_CHAT) == []
    await h.click(tg, FbCb(id=d["booking_id"], r=5), message_text="Как прошло?")
    sent = await h.click(tg, FbCb(id=d["booking_id"], r=1), message_text="Как прошло?")
    assert len(sent.to(ADMIN_CHAT)) == 1


@pytest.mark.parametrize("rating", [4, 5])
async def test_good_rating_recommends(harness: Harness, rating):
    h = harness
    d = await past_confirmed(h, extra_scenarios=3)
    tg = d["guest_tg"]
    sent = await h.click(tg, FbCb(id=d["booking_id"], r=rating), message_text="Как прошло?")
    assert not sent.to(ADMIN_CHAT)
    [thanks] = sent.of(SendPhoto)
    assert thanks.caption == T["feedback.thanks_good"]
    ideas = button_texts(thanks.reply_markup)
    assert len(ideas) == 2
    assert all(t.startswith("Вечер №") for t in ideas)
    assert "Каток" not in ideas
    assert await h.state(tg).get_state() is None


async def test_good_rating_without_ideas(harness: Harness):
    h = harness
    d = await past_confirmed(h)
    sent = await h.click(d["guest_tg"], FbCb(id=d["booking_id"], r=5), message_text="x")
    [thanks] = sent.of(SendPhoto)
    assert thanks.caption == T["feedback.thanks_good_short"]


async def test_feedback_skip_comment(harness: Harness):
    h = harness
    d = await past_confirmed(h)
    tg = d["guest_tg"]
    await h.click(tg, FbCb(id=d["booking_id"], r=2), message_text="x")
    sent = await h.click(tg, FbCb(id=d["booking_id"], r=0), message_text=T["feedback.ask_comment"])
    assert T["feedback.skipped"] in sent.texts(tg)
    assert await h.state(tg).get_state() is None


async def test_feedback_on_foreign_booking_expired(harness: Harness):
    h = harness
    d = await past_confirmed(h)
    other = await onboarded_user(h)
    sent = await h.click(other.tg_id, FbCb(id=d["booking_id"], r=1))
    assert sent.toasts() == [T["error.expired"]]
    assert not sent.to(ADMIN_CHAT)


# ---------------------------------------------------------------- сохранённое


async def test_saved_list(harness: Harness):
    h = harness
    [sc_id] = await weekend_scenarios(h, 1)
    u = await onboarded_user(h)
    await h.click(u.tg_id, CardCb(a="save", id=sc_id), photo=True)
    sent = await h.send(u.tg_id, T["menu.btn_saved"])
    [listing] = sent.of(SendMessage)
    assert listing.text.startswith("<b>❤️ Сохранённое</b> · 1–1")
    assert "Вечер №1" in listing.text


async def test_saved_hidden_scenario_shown_as_unavailable(harness: Harness):
    h = harness
    [sc_id] = await weekend_scenarios(h, 1)
    u = await onboarded_user(h)
    await h.click(u.tg_id, CardCb(a="save", id=sc_id), photo=True)

    async def hide(s):
        from app.db.models import Scenario

        (await s.get(Scenario, sc_id)).is_active = False

    await seed(h, hide)
    sent = await h.send(u.tg_id, T["menu.btn_saved"])
    assert any("Вечер №1 — сейчас недоступен" in t for t in sent.texts(u.tg_id))


# ---------------------------------------------------------------- фолбэк


async def test_unknown_text_and_admin_denied(harness: Harness):
    h = harness
    u = await onboarded_user(h)
    sent = await h.send(u.tg_id, "привет")
    assert sent.texts(u.tg_id)[0] in ROT["rot.error_unknown"]
    sent = await h.send(u.tg_id, "/admin")
    assert sent.texts(u.tg_id) == [T["error.no_access"]]


async def test_nav_callback_opens_screen(harness: Harness):
    h = harness
    u = await onboarded_user(h)
    sent = await h.click(u.tg_id, NavCb(to="my"))
    [photo] = sent.of(SendPhoto)
    assert photo.caption in ROT["rot.empty_bookings"]


async def test_admin_role_from_admin_ids(harness: Harness):
    h = harness
    await h.send(ADMIN_TG, "/start")
    admin = await get_user(h, ADMIN_TG)
    assert admin.role == Role.admin
    sent = await h.send(ADMIN_TG, "/menu")
    assert button_texts(sent.of(SendMessage)[0].reply_markup)[-1] == T["menu.btn_admin"]


# ---------------------------------------------------------------- двойной клик


async def test_broadcast_double_click_creates_one(harness: Harness, monkeypatch):
    import asyncio

    from app.bot.callbacks import AdmCb
    from app.bot.states import BroadcastForm
    from app.db.models import Broadcast
    from app.services import broadcasts

    async def fake_send(*_a, **_kw):
        return 0

    monkeypatch.setattr(broadcasts, "send_broadcast", fake_send)
    h = harness
    await h.send(ADMIN_TG, "/start")
    st = h.state(ADMIN_TG)
    await st.set_state(BroadcastForm.preview)
    await st.update_data(segment="all", text="Привет", count=0)

    await asyncio.gather(h.click(ADMIN_TG, AdmCb(a="bcsend")), h.click(ADMIN_TG, AdmCb(a="bcsend")))
    await asyncio.sleep(0)
    async with h.sessionmaker() as s:
        assert await s.scalar(select(func.count(Broadcast.id))) == 1
    assert await st.get_state() is None


async def test_scenario_save_double_click_creates_one(harness: Harness):
    import asyncio

    from app.bot.callbacks import ScfCb
    from app.bot.states import ScenarioForm
    from app.db.models import Scenario
    from tests.backend.factories import scenario_data

    h = harness
    await h.send(ADMIN_TG, "/start")
    st = h.state(ADMIN_TG)
    await st.set_state(ScenarioForm.preview)
    await st.update_data(sc=scenario_data(title="Дубль?"), edit_id=None)

    await asyncio.gather(h.click(ADMIN_TG, ScfCb(a="save")), h.click(ADMIN_TG, ScfCb(a="save")))
    async with h.sessionmaker() as s:
        n = await s.scalar(select(func.count(Scenario.id)).where(Scenario.title == "Дубль?"))
        assert n == 1
    assert await st.get_state() is None
