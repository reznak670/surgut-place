"""Telegram-интерфейс «Сургутского плейса». app/main.py берёт отсюда get_routers()."""

from __future__ import annotations

from aiogram import Router


def get_routers() -> list[Router]:
    """Новый набор роутеров при каждом вызове (роутер можно подключить к диспетчеру один раз).

    Порядок важен: меню (работает в любом состоянии FSM) → экраны и FSM → ролевые →
    отказ в доступе → фолбэк (неизвестный ввод + error handler).
    """
    from app.bot.commands import on_startup
    from app.bot.handlers import (
        about,
        admin,
        admin_broadcast,
        admin_form,
        booking,
        card,
        catalog,
        fallback,
        feedback,
        menu,
        my_bookings,
        partner,
        start,
    )

    first = start.setup()
    first.startup.register(on_startup)  # set_my_commands при старте поллинга
    return [
        first,
        menu.setup(),
        catalog.setup(),
        card.setup(),
        booking.setup(),
        my_bookings.setup(),
        feedback.setup(),
        about.setup(),
        partner.setup(),
        admin.setup(),
        admin_form.setup(),
        admin_broadcast.setup(),
        fallback.setup_denied(),
        fallback.setup_fallback(),
    ]


__all__ = ["get_routers"]
