"""Async SQLAlchemy engine and session factory."""
from __future__ import annotations

import os
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from engine.database.models import Base

_engine = None
AsyncSessionLocal: async_sessionmaker[AsyncSession] | None = None


def _get_db_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL environment variable not set")
    return url


async def init_db() -> None:
    global _engine, AsyncSessionLocal
    _engine = create_async_engine(_get_db_url(), echo=False, pool_size=5, max_overflow=10)
    AsyncSessionLocal = async_sessionmaker(_engine, expire_on_commit=False)
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_engine():
    if _engine is None:
        await init_db()
    return _engine
