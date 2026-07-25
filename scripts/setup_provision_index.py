"""Create GIN index on documents.cited_provisions for fast provision search.

Uses jsonb_path_ops operator class which supports @> (containment) queries.
Runs CONCURRENTLY so it doesn't block reads/writes during creation.

Usage:
    uv run python scripts/setup_provision_index.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def main() -> None:
    engine = create_async_engine(
        "postgresql+asyncpg://geiri@localhost/lausnir_v2",
        isolation_level="AUTOCOMMIT",
    )
    async with engine.connect() as conn:
        print("Creating GIN index ix_doc_cited_provisions (CONCURRENTLY)…")
        await conn.execute(text("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_doc_cited_provisions
            ON documents USING GIN (cited_provisions jsonb_path_ops)
        """))
        print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
