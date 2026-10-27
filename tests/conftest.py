"""Общие фикстуры: in-memory SQLite, схема через metadata.create_all."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import (
    Base,
    make_engine,
    make_sessionmaker,
    models,  # noqa: F401
)


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    # StaticPool: одна общая коннекция, иначе каждая сессия видела бы свою пустую :memory:
    eng = make_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
def sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return make_sessionmaker(engine)


@pytest.fixture
async def session(sessionmaker: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with sessionmaker() as s:
        yield s
