"""FSM-состояния."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class BookingFlow(StatesGroup):
    slot = State()
    name = State()
    contact = State()
    confirm = State()


class SuggestFlow(StatesGroup):
    text = State()


class FeedbackFlow(StatesGroup):
    comment = State()


class ScenarioForm(StatesGroup):
    title = State()
    location_name = State()
    address = State()
    map_link = State()
    vibes = State()
    price = State()
    duration = State()
    time_of_day = State()
    timing_text = State()
    includes_text = State()
    need_booking = State()
    is_indoor = State()
    photos = State()
    partner = State()
    preview = State()


class SlotsForm(StatesGroup):
    lines = State()


class BroadcastForm(StatesGroup):
    segment = State()
    text = State()
    preview = State()


class PartnerForm(StatesGroup):
    tg_id = State()
