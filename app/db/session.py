from __future__ import annotations

from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def make_engine(url: str, **kwargs: Any) -> AsyncEngine:
    # параметры запросов (контакты, имена) не попадают в тексты исключений и логи
    kwargs.setdefault("hide_parameters", True)
    if url.startswith("sqlite"):
        kwargs.setdefault("connect_args", {"timeout": 30})
    else:
        kwargs.setdefault("pool_pre_ping", True)
    engine = create_async_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_pragmas(dbapi_conn: Any, _record: Any) -> None:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            if ":memory:" not in url:
                # WAL: читатели не блокируют писателя (только dev — в проде Postgres)
                cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

    return engine


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
