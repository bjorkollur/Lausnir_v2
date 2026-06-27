"""Backfill fts_is TSVECTOR column with BÍN-lemmatized Icelandic text.

Lemmatizes body_text + summary for all documents using islenska.Bin (BÍN).
Structured fields (case_number, court, verdict_type) are included as-is for
exact matching. The combined text is stored as to_tsvector('simple', ...).

Usage:
    uv run python scripts/backfill_fts_is.py
    uv run python scripts/backfill_fts_is.py --limit 100   # test run
    uv run python scripts/backfill_fts_is.py --source haestirettur
"""
import argparse
import asyncio
import logging
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import func, select, text, update

import engine.database.connection as _db_conn
from engine.database.connection import init_db, get_engine
from engine.database.models import Document, Source
from engine.processors.lemmatizer import lemmatize_text

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

BATCH_SIZE = 200


def _build_fts_text(doc_row) -> str:
    """Combine structured + lemmatized text for a document row."""
    structured = " ".join(filter(None, [
        doc_row.case_number,
        doc_row.court,
        doc_row.verdict_type,
    ]))
    text_blob = " ".join(filter(None, [
        doc_row.summary or "",
        doc_row.body_text or "",
    ]))
    lemmatized = lemmatize_text(text_blob)
    return f"{structured} {lemmatized}".strip()


async def backfill(limit: int | None = None, source_name: str | None = None) -> None:
    await init_db()
    engine = await get_engine()

    async with engine.connect() as conn:
        # Count total to process
        q = select(func.count()).select_from(Document).where(Document.fts_is.is_(None))
        if source_name:
            q = q.join(Source).where(Source.short_name == source_name)
        total = (await conn.execute(q)).scalar()

    if limit:
        total = min(total, limit)

    log.info(f"Documents to process: {total:,}")
    if total == 0:
        log.info("Nothing to do.")
        return

    processed = 0
    errors = 0
    t_start = time.time()

    async with _db_conn.AsyncSessionLocal() as session:
        offset = 0
        while True:
            q = (
                select(
                    Document.id,
                    Document.case_number,
                    Document.court,
                    Document.verdict_type,
                    Document.summary,
                    Document.body_text,
                )
                .where(Document.fts_is.is_(None))
            )
            if source_name:
                q = q.join(Source).where(Source.short_name == source_name)
            q = q.order_by(Document.id).limit(BATCH_SIZE)

            rows = (await session.execute(q)).all()
            if not rows:
                break

            updates = []
            for row in rows:
                try:
                    fts_text = _build_fts_text(row)
                    updates.append({"doc_id": row.id, "fts_text": fts_text})
                except Exception as e:
                    log.warning(f"Error lemmatizing {row.id}: {e}")
                    errors += 1

            if updates:
                await session.execute(
                    text("""
                        UPDATE documents
                        SET fts_is = to_tsvector('simple', :fts_text)
                        WHERE id = :doc_id
                    """),
                    updates,
                )
                await session.commit()

            processed += len(rows)
            elapsed = time.time() - t_start
            rate = processed / elapsed if elapsed > 0 else 0
            eta = (total - processed) / rate if rate > 0 else 0
            log.info(
                f"{processed:>6,}/{total:,} — {rate:.0f} docs/s — ETA {eta/60:.1f} min"
                + (f" ({errors} errors)" if errors else "")
            )

            if limit and processed >= limit:
                break

    # Build GIN index if it doesn't exist yet
    # CONCURRENTLY requires autocommit (no transaction block)
    async with engine.connect() as conn:
        existing = (await conn.execute(text(
            "SELECT 1 FROM pg_indexes WHERE indexname = 'ix_doc_fts_is'"
        ))).fetchone()

    if not existing:
        log.info("Building GIN index ix_doc_fts_is (this may take a few minutes)...")
        async with engine.execution_options(
            isolation_level="AUTOCOMMIT"
        ).connect() as conn:
            await conn.execute(text(
                "CREATE INDEX CONCURRENTLY ix_doc_fts_is ON documents USING gin (fts_is)"
            ))
        log.info("GIN index created.")
    else:
        log.info("GIN index already exists.")

    total_time = time.time() - t_start
    log.info(
        f"Done. {processed:,} documents in {total_time/60:.1f} min. {errors} errors."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--source", type=str, default=None)
    args = parser.parse_args()

    asyncio.run(backfill(limit=args.limit, source_name=args.source))
