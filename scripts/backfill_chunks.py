"""Backfill document_chunks for lögfræðiritgerðir (or any source).

For each document in the target source:
  1. Delete existing chunks (idempotent).
  2. Split body_text into ~500-word paragraphs-aware chunks.
  3. Lemmatize each chunk and insert with to_tsvector('simple', :lemmas).

Usage:
    uv run python scripts/backfill_chunks.py
    uv run python scripts/backfill_chunks.py --source logfraediritgerdir
    uv run python scripts/backfill_chunks.py --limit 10   # smoke test
"""
import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text

import engine.database.connection as _db_conn
from engine.database.connection import init_db, get_engine
from engine.processors.chunker import chunk_document
from engine.processors.lemmatizer import lemmatize_text

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

BATCH_SIZE = 50
DEFAULT_SOURCE = "logfraediritgerdir"


async def backfill(source_name: str = DEFAULT_SOURCE, limit: int | None = None) -> None:
    await init_db()
    engine = await get_engine()

    async with engine.connect() as conn:
        # Fetch all document IDs + body_text for the source
        rows = (await conn.execute(text("""
            SELECT d.id, d.body_text
            FROM documents d
            JOIN sources s ON s.id = d.source_id
            WHERE s.short_name = :sn
              AND d.body_text IS NOT NULL
              AND d.body_text != ''
            ORDER BY d.id
        """), {"sn": source_name})).fetchall()

    if limit:
        rows = rows[:limit]

    total = len(rows)
    log.info("Processing %d documents from %r", total, source_name)
    t0 = time.monotonic()

    done = 0
    errors = 0

    async with engine.connect() as conn:
        for i, (doc_id, body_text) in enumerate(rows):
            try:
                chunks = chunk_document(body_text)
                if not chunks:
                    done += 1
                    continue

                # Delete existing chunks for this document (idempotent)
                await conn.execute(
                    text("DELETE FROM document_chunks WHERE document_id = :doc_id"),
                    {"doc_id": doc_id},
                )

                # Insert new chunks
                await conn.execute(
                    text("""
                        INSERT INTO document_chunks (id, document_id, chunk_index, chunk_text, fts_is)
                        VALUES (gen_random_uuid(), :doc_id, :idx, :chunk_text,
                                to_tsvector('simple', :lemmas))
                    """),
                    [
                        {
                            "doc_id": doc_id,
                            "idx": j,
                            "chunk_text": chunk,
                            "lemmas": lemmatize_text(chunk),
                        }
                        for j, chunk in enumerate(chunks)
                    ],
                )
            except Exception as e:
                log.warning("Error processing doc %s: %s", doc_id, e)
                errors += 1
                done += 1
                continue

            done += 1
            if (i + 1) % BATCH_SIZE == 0:
                await conn.commit()
                elapsed = time.monotonic() - t0
                rate = done / elapsed if elapsed > 0 else 0
                eta = (total - done) / rate if rate > 0 else 0
                log.info(
                    "[%d/%d] %.1f%% — %.1f docs/s — ETA %.0fs%s",
                    done, total, 100 * done / total, rate, eta,
                    f" ({errors} errors)" if errors else "",
                )

        await conn.commit()

    elapsed = time.monotonic() - t0
    log.info(
        "Done: %d documents chunked in %.1fs%s",
        done, elapsed,
        f" ({errors} errors)" if errors else "",
    )

    # Summary
    async with engine.connect() as conn:
        n_chunks = (await conn.execute(text("""
            SELECT count(*) FROM document_chunks dc
            JOIN documents d ON d.id = dc.document_id
            JOIN sources s ON s.id = d.source_id
            WHERE s.short_name = :sn
        """), {"sn": source_name})).scalar()
    log.info("Total chunks in DB for %r: %d", source_name, n_chunks)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--limit", type=int, default=None,
                        help="Process only this many documents (smoke test)")
    args = parser.parse_args()
    asyncio.run(backfill(args.source, args.limit))


if __name__ == "__main__":
    main()
