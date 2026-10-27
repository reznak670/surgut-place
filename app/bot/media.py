"""Отправка картинок из assets/ с кешем file_id (docs/design.md §18)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile, Message

from app.config import settings

log = logging.getLogger(__name__)

MEDIA_KEYS = (
    "welcome",
    "empty_weekend",
    "empty_search",
    "empty_bookings",
    "empty_saved",
    "booking_confirmed",
    "feedback_thanks",
    "scenario_placeholder",
)

_cache: dict[str, str] = {}  # media-ключ -> file_id


def assets_dir() -> Path:
    default = Path(__file__).resolve().parents[2] / "assets"
    return Path(getattr(settings, "ASSETS_DIR", None) or default)


def media_path(key: str) -> Path:
    return assets_dir() / f"{key}.png"


async def send_media(bot: Bot, chat_id: int, key: str, **kwargs: Any) -> Message:
    """send_photo картинкой `key`; первый раз — файлом, дальше — по file_id."""
    cached = _cache.get(key)
    if cached:
        try:
            return await bot.send_photo(chat_id, photo=cached, **kwargs)
        except TelegramBadRequest:
            log.info("media file_id expired, reupload", extra={"key": key})
            _cache.pop(key, None)
    msg = await bot.send_photo(chat_id, photo=FSInputFile(media_path(key)), **kwargs)
    if msg.photo:
        _cache.setdefault(key, msg.photo[-1].file_id)
    return msg


def clear_cache() -> None:
    _cache.clear()
