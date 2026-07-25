"""Create the indexes the search API needs.

The big one is a pg_trgm GIN index on ``body_text`` — this is what makes regex
(``~*``) and ILIKE over 1.7 GB of verdict text tractable instead of a full
sequential scan. Also adds btree indexes used for faceting and scoped date
queries.

All indexes are built CONCURRENTLY (no table lock) and IF NOT EXISTS, so this is
safe to re-run and safe to run against the live DB.

Usage:
    uv run python scripts/setup_search_indexes.py
    uv run python scripts/setup_search_indexes.py --skip-trgm   # btree only (fast)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text

from engine.database.connection import get_engine, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

# (name, DDL). Order matters only for logging; each is independent.
_TRGM_INDEXES: list[tuple[str, str]] = [
    ("ix_doc_body_trgm",
     "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_doc_body_trgm "
     "ON documents USING gin (body_text gin_trgm_ops)"),
    ("ix_doc_summary_trgm",
     "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_doc_summary_trgm "
     "ON documents USING gin (summary gin_trgm_ops)"),
    ("ix_doc_case_number_trgm",
     "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_doc_case_number_trgm "
     "ON documents USING gin (case_number gin_trgm_ops)"),
]

_BTREE_INDEXES: list[tuple[str, str]] = [
    ("ix_doc_source_date",
     "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_doc_source_date "
     "ON documents (source_id, document_date)"),
    ("ix_doc_verdict_type",
     "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_doc_verdict_type "
     "ON documents (verdict_type)"),
    ("ix_doc_instance_tier",
     "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_doc_instance_tier "
     "ON documents (instance_tier)"),
    ("ix_doc_case_type",
     "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_doc_case_type "
     "ON documents (case_type)"),
]


async def _run_concurrently(engine, name: str, ddl: str) -> None:
    """Run one CREATE INDEX CONCURRENTLY in its own AUTOCOMMIT connection."""
    t0 = time.time()
    log.info("Building %s …", name)
    async with engine.execution_options(isolation_level="AUTOCOMMIT").connect() as conn:
        await conn.execute(text(ddl))
    log.info("  ✓ %s  (%.0fs)", name, time.time() - t0)


async def main(skip_trgm: bool = False) -> None:
    await init_db()
    engine = await get_engine()

    # pg_trgm is required for the gin_trgm_ops index. Idempotent.
    async with engine.execution_options(isolation_level="AUTOCOMMIT").connect() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    log.info("pg_trgm extension ensured.")

    indexes = list(_BTREE_INDEXES)
    if not skip_trgm:
        indexes = _TRGM_INDEXES + indexes
    else:
        log.info("Skipping trigram indexes (--skip-trgm).")

    for name, ddl in indexes:
        try:
            await _run_concurrently(engine, name, ddl)
        except Exception as exc:  # noqa: BLE001 — log and continue with the rest
            log.error("  ✗ %s failed: %s", name, exc)

    log.info("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-trgm", action="store_true",
                        help="Only build the fast btree indexes")
    args = parser.parse_args()
    asyncio.run(main(skip_trgm=args.skip_trgm))
