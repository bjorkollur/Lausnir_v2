"""Backfill passages for documents whose passage_hash is stale (or all with --all).

Segmentation + BÍN lemmatisation run in worker processes (CPU-bound); the main
process reads text in batches and writes rows. Idempotent: rebuild_passages
deletes before inserting and sets passage_hash in the same transaction.

Usage:
    set -a; . ./.env; set +a
    uv run python scripts/backfill_passages.py                    # stale only
    uv run python scripts/backfill_passages.py --source landsrettur --limit 20
    uv run python scripts/backfill_passages.py --all --workers 12
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text

from engine.database.connection import init_db, get_engine
from engine.search.passage_index import STALE_WHERE, build_passage_rows, lemmatize_rows, rebuild_passages

# Not a buffering issue (logging uses stderr, which Python flushes per record):
# each of the `workers` worker processes imports engine.processors.lemmatizer at
# start-up, which loads BÍN (a few hundred MB). With many workers spawned at once
# this can take on the order of a minute before the first imap_unordered() result
# comes back, so the log can look idle right after start-up even on a healthy run.
# Reconfigure stdout to line-buffered regardless, in case anything ever prints.
try:
    sys.stdout.reconfigure(line_buffering=True)
except (AttributeError, ValueError):
    pass

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

BATCH = 200          # documents fetched + written per round
COMMIT_EVERY = 50


def _work(item: tuple) -> tuple:
    """Runs in a worker process. item = (doc_id, summary, body, lower, short_name)."""
    doc_id, summary, body, lower, short_name = item
    try:
        rows = build_passage_rows(summary, body, lower, is_lagasafn=short_name.startswith("lagasafn_"))
        return doc_id, lemmatize_rows(rows), None
    except Exception as exc:  # noqa: BLE001 — reported and skipped, never aborts the run
        return doc_id, None, f"{type(exc).__name__}: {exc}"


async def backfill(*, source: str | None, limit: int | None, all_docs: bool, workers: int) -> None:
    await init_db()
    engine = await get_engine()

    where = ["TRUE" if all_docs else STALE_WHERE]
    params: dict = {}
    if source:
        where.append("s.short_name = :sn")
        params["sn"] = source
    where_sql = " AND ".join(where)

    async with engine.connect() as conn:
        ids = (await conn.execute(text(f"""
            SELECT d.id FROM documents d JOIN sources s ON s.id = d.source_id
            WHERE {where_sql} ORDER BY d.id"""), params)).scalars().all()
    if limit:
        ids = ids[:limit]
    total = len(ids)
    log.info("Documents to (re)build: %s  (workers=%d)", f"{total:,}", workers)
    if not total:
        return

    t0 = time.monotonic()
    done = errors = written = 0
    conn_dead = False
    with Pool(processes=workers) as pool:
        async with engine.connect() as conn:
            for i in range(0, total, BATCH):
                batch_ids = ids[i:i + BATCH]
                rows = (await conn.execute(text("""
                    SELECT d.id, d.summary, d.body_text, d.lower_body_text, s.short_name
                    FROM documents d JOIN sources s ON s.id = d.source_id
                    WHERE d.id = ANY(:ids)"""), {"ids": list(batch_ids)})).all()
                items = [tuple(r) for r in rows]
                for doc_id, lem_rows, err in pool.imap_unordered(_work, items, chunksize=4):
                    # Count every processed document toward done regardless of
                    # outcome, so rate/ETA/"Done: N docs" aren't undercounted by
                    # write failures.
                    done += 1
                    if err:
                        errors += 1
                        log.warning("doc %s: %s", doc_id, err)
                    else:
                        try:
                            written += await rebuild_passages(conn, doc_id, rows_with_lemmas=lem_rows)
                        except Exception as exc:  # noqa: BLE001
                            errors += 1
                            log.warning("doc %s write failed: %s", doc_id, exc)
                            # Rolling back discards only the up-to-COMMIT_EVERY docs
                            # written since the last commit; their passage_hash is
                            # unchanged so they stay stale and are rebuilt next run —
                            # nothing is lost permanently.
                            try:
                                await conn.rollback()
                            except Exception as rb_exc:  # noqa: BLE001
                                # Connection is unusable (e.g. server dropped it).
                                # Stop the run cleanly instead of raising out of an
                                # unattended background process — remaining stale
                                # docs are simply picked up by the next run.
                                log.error("doc %s: rollback failed, connection is dead (%s); stopping run",
                                          doc_id, rb_exc)
                                conn_dead = True
                                break
                    if done % COMMIT_EVERY == 0:
                        await conn.commit()
                elapsed = time.monotonic() - t0
                rate = done / elapsed if elapsed else 0
                eta = (total - done) / rate if rate else 0
                log.info("[%d/%d] %.1f%% — %.1f docs/s — %s passages — ETA %.0f min%s",
                         done, total, 100 * done / total, rate, f"{written:,}", eta / 60,
                         f" ({errors} errors)" if errors else "")
                if conn_dead:
                    break
            if not conn_dead:
                await conn.commit()

    log.info("Done: %d docs, %s passages, %d errors in %.0fs", done, f"{written:,}", errors, time.monotonic() - t0)

    async with engine.connect() as conn:
        cov = (await conn.execute(text(f"""
            SELECT s.short_name,
                   count(DISTINCT d.id) FILTER (WHERE d.body_text IS NOT NULL OR d.summary IS NOT NULL) AS with_text,
                   count(DISTINCT p.document_id) AS with_passages,
                   count(p.id) AS passages,
                   count(DISTINCT d.id) FILTER (WHERE {STALE_WHERE}) AS stale
            FROM documents d JOIN sources s ON s.id = d.source_id
            LEFT JOIN passages p ON p.document_id = d.id
            {"WHERE s.short_name = :sn" if source else ""}
            GROUP BY s.short_name ORDER BY passages DESC"""), params)).all()
        log.info("%-24s %10s %14s %10s %6s", "source", "with_text", "with_passages", "passages", "stale")
        for sn, wt, wp, np_, st in cov:
            log.info("%-24s %10d %14d %10d %6d", sn, wt, wp, np_, st)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--all", action="store_true", help="rebuild even if passage_hash matches")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    a = ap.parse_args()
    asyncio.run(backfill(source=a.source, limit=a.limit, all_docs=a.all, workers=a.workers))
