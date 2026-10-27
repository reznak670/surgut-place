"""JSON-логи в stdout. Поля: ts, level, logger, msg + всё, что передано через extra=."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

# Стандартные атрибуты LogRecord — их не дублируем в вывод как extra.
_RESERVED = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys()) | {
    "message",
    "asctime",
    "taskName",
}

# Ключи, которые никогда не должны попасть в лог (персональные данные/секреты).
_SENSITIVE = {"contact", "phone", "name", "token", "password", "secret"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            payload[key] = "***" if key.lower() in _SENSITIVE else value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # aiogram на INFO пишет каждый апдейт — оставляем, но приглушаем шумные либы
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
