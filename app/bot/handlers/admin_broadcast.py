"""Рассылка (§13.6): сегмент → текст → предпросмотр → отправка через broadcasts.send_broadcast."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import keyboards as kb
from app.bot.callbacks import AdmCb
from app.bot.common import cb_message, safe_answer, safe_edit_markup, show_text
from app.bot.formatting import esc
from app.bot.states import BroadcastForm
from app.bot.texts import SEGMENT, T
from app.db.models import Role, User
from app.middlewares import RoleFilter
from app.services import broadcasts
from app.services.errors import ServiceError

log = logging.getLogger(__name__)

TEXT_MAX = 1000
_tasks: set[asyncio.Task] = set()  # держим ссылки, чтобы фоновые задачи не собрал GC
_starting: set[int] = set()  # id админов, у которых рассылка прямо сейчас создаётся


async def _run_broadcast(
    sessionmaker: async_sessionmaker[AsyncSession],
    bot: Bot,
    broadcast_id: int,
    chat_id: int,
    message_id: int,
    count: int,
) -> None:
    async def progress(sent: int, _total: int) -> None:
        # backend зовёт каждые 20 получателей и в конце; ошибки edit не важны
        with contextlib.suppress(Exception):
            await bot.edit_message_text(
                T["admin.bc_progress"].format(sent=sent, count=count),
                chat_id=chat_id,
                message_id=message_id,
            )

    try:
        sent = await broadcasts.send_broadcast(sessionmaker, bot, broadcast_id, progress=progress)
    except Exception:
        log.exception("broadcast failed", extra={"broadcast_id": broadcast_id})
        return
    text = T["admin.bc_done"].format(sent=sent, count=count, failed=max(count - sent, 0))
    try:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id)
    except Exception:
        await bot.send_message(chat_id, text)


async def on_broadcast(
    callback: CallbackQuery,
    callback_data: AdmCb,
    session: AsyncSession,
    user: User,
    state: FSMContext,
    bot: Bot,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    msg = cb_message(callback)
    chat_id = msg.chat.id if msg else callback.from_user.id
    current = await state.get_state()
    match callback_data.a:
        case "bc":
            await safe_answer(callback)
            await state.clear()
            await state.set_state(BroadcastForm.segment)
            await show_text(callback, T["admin.bc_segment"], kb.bc_segment_kb())
        case "bcs":
            segment = callback_data.v
            if segment not in SEGMENT or current != BroadcastForm.segment.state:
                await safe_answer(callback, T["error.expired"])
                return
            await safe_answer(callback)
            count = len(await broadcasts.segment_user_ids(session, segment))
            await state.update_data(segment=segment, count=count)
            await state.set_state(BroadcastForm.text)
            chosen = T["admin.bc_segment_chosen"].format(
                segment_label=SEGMENT[segment], count=count
            )
            await show_text(callback, chosen + "\n\n" + T["admin.bc_text"], kb.admin_cancel_kb())
        case "bcedit":
            if current != BroadcastForm.preview.state:
                await safe_answer(callback, T["error.expired"])
                return
            await safe_answer(callback)
            if msg is not None:
                await safe_edit_markup(msg, None)
            await state.set_state(BroadcastForm.text)
            await bot.send_message(chat_id, T["admin.bc_text"], reply_markup=kb.admin_cancel_kb())
        case "bcsend":
            data = await state.get_data()
            if current != BroadcastForm.preview.state or not data.get("text"):
                await safe_answer(callback, T["error.expired"])
                return
            # двойной клик: проверка и отметка синхронно, без await между ними (в памяти
            # процесса — get/clear у RedisStorage не атомарны)
            if user.id in _starting:
                await safe_answer(callback, T["error.too_fast"])
                return
            _starting.add(user.id)
            try:
                await state.clear()
                await safe_answer(callback)
                try:
                    bc = await broadcasts.create(session, data["segment"], data["text"], user)
                except ServiceError as e:
                    await bot.send_message(chat_id, esc(str(e.args[0] if e.args else e)))
                    return
                await session.commit()  # send_broadcast читает рассылку в своей сессии
            finally:
                _starting.discard(user.id)
            count = int(data.get("count", 0))
            text = T["admin.bc_progress"].format(sent=0, count=count)
            if msg is not None:
                await safe_edit_markup(msg, None)
            progress = await bot.send_message(chat_id, text)
            task = asyncio.create_task(
                _run_broadcast(sessionmaker, bot, bc.id, chat_id, progress.message_id, count)
            )
            _tasks.add(task)
            task.add_done_callback(_tasks.discard)
            log.info("broadcast started", extra={"broadcast_id": bc.id, "count": count})


async def on_text(message: Message, state: FSMContext, bot: Bot) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer(T["error.blocked_fsm"], reply_markup=kb.admin_cancel_kb())
        return
    if len(text) > TEXT_MAX:
        await message.answer(
            T["admin.bc_too_long"].format(n=len(text)), reply_markup=kb.admin_cancel_kb()
        )
        return
    bad_html = T["admin.bc_bad_html"].format(error=T["admin.bc_bad_html_reason"])
    if not broadcasts.is_valid_html(text):
        await message.answer(bad_html, reply_markup=kb.admin_cancel_kb())
        return
    # ровно как увидит пользователь: send_broadcast шлёт с parse_mode=HTML
    try:
        await bot.send_message(message.chat.id, text, parse_mode=ParseMode.HTML)
    except TelegramBadRequest as e:
        await message.answer(
            T["admin.bc_bad_html"].format(error=esc(e.message or str(e))),
            reply_markup=kb.admin_cancel_kb(),
        )
        return
    data = await state.get_data()
    await state.update_data(text=text)
    await state.set_state(BroadcastForm.preview)
    await message.answer(
        T["admin.bc_preview"].format(count=data.get("count", 0)), reply_markup=kb.bc_preview_kb()
    )


def setup() -> Router:
    r = Router(name="admin_broadcast")
    admin = RoleFilter(Role.admin)
    r.message.filter(admin)
    r.callback_query.filter(admin)
    r.callback_query.register(
        on_broadcast, AdmCb.filter(F.a.in_({"bc", "bcs", "bcsend", "bcedit"}))
    )
    r.message.register(on_text, BroadcastForm.text)
    return r
