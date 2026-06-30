"""Backfill cited_provisions JSONB column from body_text for all documents.

For each document with body_text:
  1. Run extract_provisions(body_text) to get structured provision list.
  2. Store result as JSONB in cited_provisions (NULL if no provisions found).

Idempotent: re-running overwrites existing values with freshly extracted ones.

Usage:
    uv run python scripts/backfill_cited_provisions.py
    uv run python scripts/backfill_cited_provisions.py --source haestirettur
    uv run python scripts/backfill_cited_provisions.py --limit 50
"""
import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from engine.processors.provision_extractor import extract_provisions

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

BATCH_SIZE = 200
DB_URL = "postgresql+asyncpg://geiri@localhost/lausnir_v2"


async def backfill(source_name: str | None = None, limit: int | None = None) -> None:
    engine = create_async_engine(DB_URL)

    # Fetch document IDs + body_text
    async with engine.connect() as conn:
        source_filter = ""
        params: dict = {}
        if source_name:
            source_filter = "AND s.short_name = :sn"
            params["sn"] = source_name

        rows = (await conn.execute(text(f"""
            SELECT d.id, d.body_text
            FROM documents d
            JOIN sources s ON s.id = d.source_id
            WHERE d.body_text IS NOT NULL AND d.body_text != ''
            {source_filter}
            ORDER BY d.id
        """), params)).fetchall()

    if limit:
        rows = rows[:limit]

    total = len(rows)
    log.info("Processing %d documents (source=%r)", total, source_name or "all")

    t0 = time.monotonic()
    done = 0
    with_provisions = 0

    # Process in batches, opening a fresh connection per batch
    for batch_start in range(0, total, BATCH_SIZE):
        batch = rows[batch_start: batch_start + BATCH_SIZE]

        updates = []
        for doc_id, body_text in batch:
            provisions = extract_provisions(body_text)
            value = json.dumps(provisions) if provisions else None
            if provisions:
                with_provisions += 1
            updates.append({"val": value, "id": doc_id})
            done += 1

        async with engine.begin() as conn:
            for upd in updates:
                await conn.execute(
                    text("UPDATE documents SET cited_provisions = CAST(:val AS jsonb) WHERE id = :id"),
                    upd,
                )
            # commit happens automatically on __aexit__ of engine.begin()

        elapsed = time.monotonic() - t0
        rate = done / elapsed if elapsed > 0 else 0
        eta = (total - done) / rate if rate > 0 else 0
        log.info(
            "[%d/%d] %.1f%% — %.1f docs/s — ETA %.0fs — %d with provisions",
            done, total, 100 * done / total, rate, eta, with_provisions,
        )

    elapsed = time.monotonic() - t0
    log.info(
        "Done: %d docs in %.1fs — %d (%.1f%%) had ≥1 provision citation",
        done, elapsed, with_provisions, 100 * with_provisions / done if done else 0,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=None, help="Limit to one source short_name")
    parser.add_argument("--limit", type=int, default=None, help="Process only N docs (smoke test)")
    args = parser.parse_args()
    asyncio.run(backfill(args.source, args.limit))


if __name__ == "__main__":
    main()
