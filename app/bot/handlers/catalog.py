"""Каталог: выходные, фильтры (двухуровневые, на месте), пагинация по 5, пустые состояния."""

from __future__ import annotations

import logging

from aiogram import Bot, Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards as kb
from app.bot.callbacks import (
    EMPTY_FILTERS,
    CatCb,
    FilterCb,
    decode_filters,
    set_filter,
)
from app.bot.common import NO_PREVIEW, cb_message, next_slots, safe_answer, show_text
from app.bot.formatting import catalog_items, catalog_text, esc, join_within
from app.bot.media import send_media
from app.bot.texts import T, pick
from app.db.models import User
from app.services import saved, scenarios
from app.services.timeutil import now_utc

log = logging.getLogger(__name__)

PAGE = 5
SAVED_ALL_LIMIT = 20


async def render_list(
    session: AsyncSession, user: User, kind: str, page: int = 0, code: str = EMPTY_FILTERS
) -> tuple[str, InlineKeyboardMarkup | None] | None:
    """Страница списка (k=w выходные, r — по фильтрам, s — сохранённое). None — пусто.
    В сохранённом скрытые сценарии идут строкой «— сейчас недоступен» без кнопки (§8)."""
    offset = page * PAGE
    hidden: list[str] = []
    if kind == "w":
        items, has_more = await scenarios.weekend(session, user, offset=offset, limit=PAGE)
        title = T["catalog.title_weekend"]
    elif kind == "r":
        items, has_more = await scenarios.search(
            session, user, decode_filters(code), offset=offset, limit=PAGE
        )
        title = T["catalog.title_results"]
    else:
        everything = await saved.list_saved(session, user, include_hidden=True)
        all_saved = [sc for sc in everything if sc.is_active]
        if page == 0:
            hidden = [
                T["saved.unavailable"].format(title=esc(sc.title))
                for sc in everything
                if not sc.is_active
            ]
        if len(all_saved) <= SAVED_ALL_LIMIT:
            if page > 0:
                return None
            items, has_more = all_saved, False
        else:
            items = all_saved[offset : offset + PAGE]
            has_more = len(all_saved) > offset + PAGE
        title = T["catalog.title_saved"]
    if not items:
        if hidden:
            return f"<b>{title}</b>\n\n" + join_within(hidden, limit=4000), None
        return None
    text = catalog_text(title, items, await next_slots(session, items), now_utc(), offset)
    if hidden:
        text = join_within([text, *hidden], "\n\n")
    markup = kb.catalog_kb(items, kind=kind, page=page, has_more=has_more, offset=offset, code=code)
    return text, markup


async def send_weekend(bot: Bot, chat_id: int, session: AsyncSession, user: User) -> None:
    page = await render_list(session, user, "w")
    if page is not None:
        await bot.send_message(
            chat_id, page[0], reply_markup=page[1], link_preview_options=NO_PREVIEW
        )
        return
    filters_kb = kb.inline([[kb.btn(T["catalog.btn_filters_weekend"], FilterCb(a="root"))]])
    fallback = await scenarios.last_week_fallback(session)
    if not fallback:
        await send_media(
            bot, chat_id, "empty_weekend", caption=T["catalog.all_empty"], reply_markup=filters_kb
        )
        return
    await send_media(bot, chat_id, "empty_weekend", caption=pick("rot.empty_weekend"))
    text = f"<b>{T['catalog.title_fallback']}</b>\n\n" + catalog_items(
        fallback, await next_slots(session, fallback), now_utc()
    )
    markup = kb.catalog_kb(fallback, kind="w", page=0, has_more=False)
    await bot.send_message(chat_id, text, reply_markup=markup, link_preview_options=NO_PREVIEW)


async def send_filters_root(bot: Bot, chat_id: int, code: str = EMPTY_FILTERS) -> None:
    await bot.send_message(
        chat_id,
        T["filters.root"],
        reply_markup=kb.filters_root_kb(code),
        link_preview_options=NO_PREVIEW,
    )


# ---------------------------------------------------------------- колбэки


async def on_page(
    callback: CallbackQuery, callback_data: CatCb, session: AsyncSession, user: User
) -> None:
    page = await render_list(session, user, callback_data.k, callback_data.p, callback_data.f)
    if page is None:
        await safe_answer(callback, T["catalog.end"])
        return
    await safe_answer(callback)
    await show_text(callback, *page)


async def on_filters(
    callback: CallbackQuery,
    callback_data: FilterCb,
    session: AsyncSession,
    user: User,
    bot: Bot,
) -> None:
    code = callback_data.f or EMPTY_FILTERS
    match callback_data.a:
        case "g" if callback_data.g in kb.GROUP_TITLE_KEY:
            await safe_answer(callback)
            await show_text(
                callback,
                T[kb.GROUP_TITLE_KEY[callback_data.g]],
                kb.filters_group_kb(code, callback_data.g),
            )
        case "s" if callback_data.g in kb.GROUP_TITLE_KEY:
            await safe_answer(callback)
            code = set_filter(code, callback_data.g, callback_data.v or "-")
            await show_text(callback, T["filters.root"], kb.filters_root_kb(code))
        case "reset":
            await safe_answer(callback, T["filters.reset_done"])
            await show_text(callback, T["filters.root"], kb.filters_root_kb(EMPTY_FILTERS))
        case "show":
            await safe_answer(callback)
            page = await render_list(session, user, "r", 0, code)
            if page is None:
                msg = cb_message(callback)
                chat_id = msg.chat.id if msg else callback.from_user.id
                await send_media(
                    bot,
                    chat_id,
                    "empty_search",
                    caption=pick("rot.empty_search"),
                    reply_markup=kb.filters_empty_kb(code),
                )
                return
            await show_text(callback, *page)
        case _:  # root
            await safe_answer(callback)
            await show_text(callback, T["filters.root"], kb.filters_root_kb(code))


def setup() -> Router:
    r = Router(name="catalog")
    r.callback_query.register(on_page, CatCb.filter())
    r.callback_query.register(on_filters, FilterCb.filter())
    return r
