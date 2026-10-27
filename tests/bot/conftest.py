"""Фикстуры для тестов бота: фейковая HTTP-сессия aiogram, диспетчер на in-memory SQLite.

Подход: настоящий `Bot` + `Dispatcher.feed_update`, но `bot.session` подменён на `FakeSession`,
которая ничего не шлёт в сеть, а записывает каждый метод Bot API и возвращает правдоподобный
ответ (Message, True, User…). Так проходят все фильтры, middleware и FSM — как в проде.
"""

from __future__ import annotations

import itertools
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import (
    EditMessageCaption,
    EditMessageReplyMarkup,
    EditMessageText,
    GetMe,
    SendMediaGroup,
    SendMessage,
    SendPhoto,
    TelegramMethod,
)
from aiogram.types import (
    Chat,
    ErrorEvent,
    InlineKeyboardMarkup,
    Message,
    PhotoSize,
    Update,
)
from aiogram.types import User as TgUser
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import media
from app.config import settings

BOT_ID = 777000
BOT_USERNAME = "surgut_place_test_bot"
ADMIN_TG = 9_000_001
ADMIN_CHAT = -100_500
TOKEN = f"{BOT_ID}:TEST_TOKEN_abcdefghijklmnopqrstuvwxyz"


# ---------------------------------------------------------------- фейковая сессия


class FakeSession(BaseSession):
    """Записывает все вызовы Bot API. `fail_chats` — chat_id, куда «бот заблокирован»."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[TelegramMethod[Any]] = []
        self.fail_chats: set[int] = set()
        self._ids = itertools.count(10_000)

    async def close(self) -> None:
        return None

    async def stream_content(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError

    def _message(self, bot: Bot, chat_id: Any, **kw: Any) -> Message:
        markup = kw.pop("reply_markup", None)
        return Message(
            message_id=next(self._ids),
            date=datetime.now(UTC),
            chat=Chat(id=int(chat_id), type="private"),
            from_user=TgUser(id=BOT_ID, is_bot=True, first_name="Bot", username=BOT_USERNAME),
            reply_markup=markup if isinstance(markup, InlineKeyboardMarkup) else None,
            **kw,
        ).as_(bot)

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[Any],
        timeout: int | None = None,  # noqa: ASYNC109 (сигнатура BaseSession)
    ) -> Any:
        self.requests.append(method)
        chat_id = getattr(method, "chat_id", None)
        if chat_id is not None and chat_id in self.fail_chats:
            raise TelegramForbiddenError(
                method=method, message="Forbidden: bot was blocked by the user"
            )
        if isinstance(method, GetMe):
            return TgUser(id=BOT_ID, is_bot=True, first_name="Bot", username=BOT_USERNAME)
        if isinstance(method, SendMessage):
            return self._message(
                bot, method.chat_id, text=method.text, reply_markup=method.reply_markup
            )
        if isinstance(method, SendPhoto):
            photo = [
                PhotoSize(file_id=f"fid-{next(self._ids)}", file_unique_id="u", width=1, height=1)
            ]
            return self._message(
                bot,
                method.chat_id,
                photo=photo,
                caption=method.caption,
                reply_markup=method.reply_markup,
            )
        if isinstance(method, SendMediaGroup):
            return [self._message(bot, method.chat_id) for _ in method.media]
        if isinstance(method, (EditMessageText, EditMessageCaption, EditMessageReplyMarkup)):
            return True
        return True


def body(method: TelegramMethod[Any]) -> str:
    """Текст сообщения/подписи метода (для поиска подстрок)."""
    for attr in ("text", "caption"):
        value = getattr(method, attr, None)
        if isinstance(value, str):
            return value
    return ""


def markup_of(method: TelegramMethod[Any]) -> Any:
    return getattr(method, "reply_markup", None)


def buttons(markup: Any) -> list[Any]:
    """Все кнопки inline- или reply-клавиатуры плоским списком."""
    if markup is None:
        return []
    rows = getattr(markup, "inline_keyboard", None) or getattr(markup, "keyboard", None) or []
    return [b for row in rows for b in row]


def button_texts(markup: Any) -> list[str]:
    return [b.text for b in buttons(markup)]


# ---------------------------------------------------------------- харнесс


@dataclass
class Sent:
    """Новые запросы к Bot API, сделанные за один апдейт."""

    requests: list[TelegramMethod[Any]] = field(default_factory=list)

    def of(self, *types: type) -> list[TelegramMethod[Any]]:
        return [r for r in self.requests if isinstance(r, types)]

    def to(self, chat_id: int) -> list[TelegramMethod[Any]]:
        return [r for r in self.requests if getattr(r, "chat_id", None) == chat_id]

    def texts(self, chat_id: int | None = None) -> list[str]:
        items = self.requests if chat_id is None else self.to(chat_id)
        return [body(r) for r in items if body(r)]

    def toasts(self) -> list[str | None]:
        from aiogram.methods import AnswerCallbackQuery

        return [r.text for r in self.of(AnswerCallbackQuery)]

    def chats_with(self, needle: str) -> set[Any]:
        return {getattr(r, "chat_id", None) for r in self.requests if needle in body(r)}


class Harness:
    def __init__(
        self,
        dp: Dispatcher,
        bot: Bot,
        fake: FakeSession,
        sessionmaker: async_sessionmaker[AsyncSession],
        errors: list[BaseException],
    ) -> None:
        self.dp = dp
        self.bot = bot
        self.fake = fake
        self.sessionmaker = sessionmaker
        self.errors = errors
        self._update_ids = itertools.count(1)
        self._msg_ids = itertools.count(1)

    @staticmethod
    def tg_user(tg_id: int, first_name: str = "Аня", username: str | None = "anya_surgut") -> dict:
        data: dict[str, Any] = {"id": tg_id, "is_bot": False, "first_name": first_name}
        if username:
            data["username"] = username
        return data

    async def _feed(self, update: dict[str, Any]) -> Sent:
        start = len(self.fake.requests)
        upd = Update.model_validate(
            {"update_id": next(self._update_ids), **update}, context={"bot": self.bot}
        )
        await self.dp.feed_update(self.bot, upd)
        if self.errors:
            raise self.errors.pop()
        return Sent(self.fake.requests[start:])

    async def send(
        self,
        tg_id: int,
        text: str | None = None,
        *,
        first_name: str = "Аня",
        username: str | None = "anya_surgut",
        **extra: Any,
    ) -> Sent:
        msg: dict[str, Any] = {
            "message_id": next(self._msg_ids),
            "date": int(datetime.now(UTC).timestamp()),
            "chat": {"id": tg_id, "type": "private"},
            "from": self.tg_user(tg_id, first_name, username),
            **extra,
        }
        if text is not None:
            msg["text"] = text
            if text.startswith("/"):
                cmd = text.split()[0]
                msg["entities"] = [{"type": "bot_command", "offset": 0, "length": len(cmd)}]
        return await self._feed({"message": msg})

    async def click(
        self,
        tg_id: int,
        data: str | Any,
        *,
        message_text: str | None = "Сообщение бота",
        photo: bool = False,
        first_name: str = "Аня",
        username: str | None = "anya_surgut",
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> Sent:
        if not isinstance(data, str):
            data = data.pack()
        message: dict[str, Any] = {
            "message_id": next(self._msg_ids),
            "date": int(datetime.now(UTC).timestamp()),
            "chat": {"id": tg_id, "type": "private"},
            "from": {"id": BOT_ID, "is_bot": True, "first_name": "Bot"},
        }
        if photo:
            message["photo"] = [{"file_id": "p", "file_unique_id": "p", "width": 1, "height": 1}]
            message["caption"] = message_text or ""
        elif message_text is not None:
            message["text"] = message_text
        if reply_markup is not None:
            message["reply_markup"] = reply_markup.model_dump(exclude_none=True)
        return await self._feed(
            {
                "callback_query": {
                    "id": str(next(self._update_ids)),
                    "from": self.tg_user(tg_id, first_name, username),
                    "chat_instance": "ci",
                    "data": data,
                    "message": message,
                }
            }
        )

    def state(self, tg_id: int) -> FSMContext:
        key = StorageKey(bot_id=self.bot.id, chat_id=tg_id, user_id=tg_id)
        return FSMContext(storage=self.dp.storage, key=key)


# ---------------------------------------------------------------- фикстуры


@pytest.fixture(autouse=True)
def _stable_settings(monkeypatch: pytest.MonkeyPatch) -> Iterable[None]:
    """Не зависим от .env: фиксированные TZ, админ-чат, без ПД-ссылки и мороза."""
    monkeypatch.setattr(settings, "TZ", "Asia/Yekaterinburg")
    monkeypatch.setattr(settings, "ADMIN_CHAT_ID", ADMIN_CHAT)
    monkeypatch.setattr(settings, "ADMIN_IDS", [ADMIN_TG])
    monkeypatch.setattr(settings, "PRIVACY_POLICY_URL", "https://example.org/privacy")
    monkeypatch.setattr(settings, "FROST_MODE", False)
    media.clear_cache()
    yield
    media.clear_cache()


@pytest.fixture
def fake_session() -> FakeSession:
    return FakeSession()


@pytest.fixture
async def bot(fake_session: FakeSession) -> AsyncIterator[Bot]:
    b = Bot(TOKEN, session=fake_session, default=DefaultBotProperties(parse_mode="HTML"))
    yield b


@pytest.fixture
async def harness(
    bot: Bot,
    fake_session: FakeSession,
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> Harness:
    from app.bot import get_routers
    from app.bot.handlers import fallback
    from app.middlewares import DbSessionMiddleware, UserMiddleware

    errors: list[BaseException] = []

    async def record_error(event: ErrorEvent) -> bool:
        errors.append(event.exception)
        return True

    # прод-обработчик ошибок глушит исключения — в тестах они должны валить тест
    monkeypatch.setattr(fallback, "on_error", record_error)

    dp = Dispatcher(storage=MemoryStorage(), sessionmaker=sessionmaker, settings=settings)
    dp.update.outer_middleware(DbSessionMiddleware(sessionmaker))
    dp.update.outer_middleware(UserMiddleware(admin_ids=[ADMIN_TG]))
    dp.include_routers(*get_routers())
    return Harness(dp, bot, fake_session, sessionmaker, errors)
