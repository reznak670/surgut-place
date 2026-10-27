"""Сохранённое (§8)."""

from __future__ import annotations

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards as kb
from app.bot.common import NO_PREVIEW
from app.bot.handlers.catalog import render_list
from app.bot.media import send_media
from app.bot.texts import pick
from app.db.models import User


async def send_saved(bot: Bot, chat_id: int, session: AsyncSession, user: User) -> None:
    page = await render_list(session, user, "s")
    if page is None:
        await send_media(
            bot,
            chat_id,
            "empty_saved",
            caption=pick("rot.empty_saved"),
            reply_markup=kb.weekend_btn_kb(),
        )
        return
    await bot.send_message(chat_id, page[0], reply_markup=page[1], link_preview_options=NO_PREVIEW)
