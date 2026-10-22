from __future__ import annotations

import functools

from sqlalchemy import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import get_settings


def normalize_database_url(raw_url: str) -> URL:
    url = make_url(raw_url)
    if url.drivername in {"postgres", "postgresql"}:
        query = dict(url.query)
        ssl_mode = query.pop("sslmode", None)
        query.pop("channel_binding", None)
        if ssl_mode and "ssl" not in query:
            query["ssl"] = ssl_mode
        return url.set(drivername="postgresql+asyncpg", query=query)
    return url


@functools.cache
def get_engine() -> AsyncEngine:
    raw_url = get_settings().database_url
    if not raw_url:
        raise RuntimeError("DATABASE_URL is required for persistence")
    url = normalize_database_url(raw_url)
    kwargs: dict[str, object] = {"pool_pre_ping": True}
    if url.drivername.startswith("sqlite"):
        kwargs["poolclass"] = StaticPool
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_async_engine(url, **kwargs)


@functools.cache
def get_session_factory() -> async_sessionmaker:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


def reset_database_caches() -> None:
    get_session_factory.cache_clear()
    get_engine.cache_clear()
