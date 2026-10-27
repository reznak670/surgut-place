"""Главное меню (§3): reply-кнопки, команды-дубли и inline-переходы NavCb.

Кнопки и команды меню работают в любом состоянии FSM и сбрасывают его.
"""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards as kb
from app.bot.callbacks import NavCb
from app.bot.common import cb_message, safe_answer
from app.bot.handlers.about import send_about, send_help, start_suggest
from app.bot.handlers.catalog import send_filters_root, send_weekend
from app.bot.handlers.my_bookings import send_my
from app.bot.handlers.saved import send_saved
from app.bot.states import BookingFlow
from app.bot.texts import T
from app.db.models import User

SCREENS = {
    "weekend": ("menu.btn_weekend", "weekend"),
    "filters": ("menu.btn_filters", "filters"),
    "my": ("menu.btn_my", "bookings"),
    "saved": ("menu.btn_saved", "saved"),
    "suggest": ("menu.btn_suggest", "suggest"),
    "about": ("menu.btn_about", "about"),
    "help": (None, "help"),
}


async def open_screen(
    screen: str, bot: Bot, chat_id: int, session: AsyncSession, user: User, state: FSMContext
) -> None:
    await state.clear()
    match screen:
        case "weekend":
            await send_weekend(bot, chat_id, session, user)
        case "filters":
            await send_filters_root(bot, chat_id)
        case "my":
            await send_my(bot, chat_id, session, user)
        case "saved":
            await send_saved(bot, chat_id, session, user)
        case "suggest":
            await start_suggest(bot, chat_id, state)
        case "about":
            await send_about(bot, chat_id)
        case "help":
            await send_help(bot, chat_id)
        case _:
            await bot.send_message(chat_id, T["menu.show"], reply_markup=kb.menu_for(user))


def _handler(screen: str):
    async def handler(
        message: Message, session: AsyncSession, user: User, state: FSMContext, bot: Bot
    ) -> None:
        await open_screen(screen, bot, message.chat.id, session, user, state)

    handler.__name__ = f"menu_{screen}"
    return handler


async def cmd_menu(message: Message, state: FSMContext, user: User) -> None:
    await state.clear()
    await message.answer(T["menu.show"], reply_markup=kb.menu_for(user))


async def cmd_cancel(message: Message, state: FSMContext, user: User) -> None:
    current = await state.get_state()
    await state.clear()
    in_booking = current is not None and current.startswith(BookingFlow.__name__)
    text = T["booking.aborted"] if in_booking else T["menu.show"]
    await message.answer(text, reply_markup=kb.menu_for(user))


async def on_nav(
    callback: CallbackQuery,
    callback_data: NavCb,
    session: AsyncSession,
    user: User,
    state: FSMContext,
    bot: Bot,
) -> None:
    await safe_answer(callback)
    msg = cb_message(callback)
    chat_id = msg.chat.id if msg else callback.from_user.id
    await open_screen(callback_data.to, bot, chat_id, session, user, state)


def setup() -> Router:
    r = Router(name="menu")
    r.message.filter(F.chat.type == "private")  # меню и reply-кнопки — только в личке
    r.message.register(cmd_menu, Command("menu"))
    r.message.register(cmd_cancel, Command("cancel"))
    for screen, (btn_key, command) in SCREENS.items():
        handler = _handler(screen)
        r.message.register(handler, Command(command))
        if btn_key:
            r.message.register(handler, F.text == T[btn_key])
    r.callback_query.register(on_nav, NavCb.filter())
    return r
