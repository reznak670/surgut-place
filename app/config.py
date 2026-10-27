"""Настройки приложения из переменных окружения (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _int_list(raw: str) -> list[int]:
    result: list[int] = []
    for part in raw.replace(" ", "").split(","):
        if part.lstrip("-").isdigit():
            result.append(int(part))
    return result


def _bool(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes"}


def _opt_int(raw: str) -> int | None:
    raw = raw.strip()
    return int(raw) if raw.lstrip("-").isdigit() else None


@dataclass
class Settings:
    BOT_TOKEN: str = ""
    ADMIN_IDS: list[int] = field(default_factory=list)
    ADMIN_CHAT_ID: int | None = None
    DATABASE_URL: str = "sqlite+aiosqlite:///./surgut_place.db"
    REDIS_URL: str = ""
    PHOTO_STORAGE: str = "telegram"
    S3_ENDPOINT: str = ""
    S3_BUCKET: str = ""
    S3_ACCESS_KEY: str = ""
    S3_SECRET_KEY: str = ""
    TZ: str = "Asia/Yekaterinburg"
    PRIVACY_POLICY_URL: str = ""
    LOG_LEVEL: str = "INFO"
    FROST_MODE: bool = False
    ASSETS_DIR: Path = field(default_factory=lambda: PROJECT_ROOT / "assets")

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.TZ)

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ.get
        d = cls()
        return cls(
            BOT_TOKEN=env("BOT_TOKEN", "").strip(),
            ADMIN_IDS=_int_list(env("ADMIN_IDS", "")),
            ADMIN_CHAT_ID=_opt_int(env("ADMIN_CHAT_ID", "")),
            DATABASE_URL=env("DATABASE_URL", "").strip() or d.DATABASE_URL,
            REDIS_URL=env("REDIS_URL", "").strip(),
            PHOTO_STORAGE=env("PHOTO_STORAGE", "").strip() or d.PHOTO_STORAGE,
            S3_ENDPOINT=env("S3_ENDPOINT", ""),
            S3_BUCKET=env("S3_BUCKET", ""),
            S3_ACCESS_KEY=env("S3_ACCESS_KEY", ""),
            S3_SECRET_KEY=env("S3_SECRET_KEY", ""),
            TZ=env("TZ", "").strip() or d.TZ,
            PRIVACY_POLICY_URL=env("PRIVACY_POLICY_URL", "").strip(),
            LOG_LEVEL=(env("LOG_LEVEL", "").strip() or d.LOG_LEVEL).upper(),
            FROST_MODE=_bool(env("FROST_MODE", "")),
            ASSETS_DIR=Path(env("ASSETS_DIR", "").strip() or d.ASSETS_DIR),
        )

    def __repr__(self) -> str:  # не светим токен/ключи в логах
        return f"Settings(DATABASE_URL={self.DATABASE_URL.split('@')[-1]!r}, TZ={self.TZ!r})"


settings = Settings.from_env()
