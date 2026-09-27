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


# asyncpg runs everything through prepared statements, so PostgreSQL switches a
# statement to a *generic* plan after its 5th execution on a connection. For the
# passage search that is catastrophic: its tsquery comes from a bind parameter
# (``plainto_tsquery('simple', $1)``), so a generic plan has no selectivity
# information at all — every stage is estimated at ~1 row and the planner picks
# a per-candidate-document rescan of the ``ix_passage_fts_is`` GIN index
# (2000 loops x ~550k TIDs) instead of one GIN scan hash-joined to ``cand``.
# Measured on the 492-question golden set (2026-09-27): golden query g017 runs
# 25 ms on executions 1-5 and 9,745 ms from execution 6 onward without this
# setting, 27 ms throughout with it. Re-planning costs ~0.5-7 ms per statement,
# which is noise next to that, and every other statement in the app plans in
# microseconds. See docs/wiki/09-gildrur.md.
_CONNECT_ARGS = {"server_settings": {"plan_cache_mode": "force_custom_plan"}}


async def init_db() -> None:
    global _engine, AsyncSessionLocal
    _engine = create_async_engine(_get_db_url(), echo=False, pool_size=5, max_overflow=10,
                                  connect_args=_CONNECT_ARGS)
    AsyncSessionLocal = async_sessionmaker(_engine, expire_on_commit=False)
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_engine():
    if _engine is None:
        await init_db()
    return _engine
