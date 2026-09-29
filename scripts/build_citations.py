"""Extract, resolve and store citations between rulings (spec §7).

Extraction + resolution are CPU-bound and run in worker processes; every write
happens in the main process, one transaction per COMMIT_EVERY documents.
Idempotent: each document's `citations` rows and its `cites` edges are deleted
before the new ones are inserted, and `documents.citation_hash` is set in the
same transaction, so a rerun with unchanged text is a no-op.

Only the citing document's own rows are touched — never another document's
citations and never a relation other than `cites`.

Usage:
    set -a; . ./.env; set +a
    uv run python scripts/build_citations.py --all                     # every stale document
    uv run python scripts/build_citations.py --source landsrettur --limit 20
    uv run python scripts/build_citations.py --doc <uuid> --dry-run
    uv run python scripts/build_citations.py --relink-unresolved       # after an import

Order after an import (docs/wiki/04-innflutningur.md):
    backfill_passages.py -> build_citations.py --all -> build_citations.py --relink-unresolved
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import date
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text

from engine.database.connection import init_db, get_engine
from engine.processors.citation_build import (
    STALE_WHERE, build_rows, citation_hash, count_relinkable, relink_unresolved, write_citations,
)
from engine.processors.citation_resolver import COURT_SOURCES, INDEX_SQL, CitationIndex

try:
    sys.stdout.reconfigure(line_buffering=True)
except (AttributeError, ValueError):
    pass

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

BATCH = 200          # documents fetched + written per round
COMMIT_EVERY = 50
_DIGITS = re.compile(r"\d")

# One CitationIndex per worker process, built once in the initializer from the
# raw rows the parent read. The rows list pickles cheaply; rebuilding the dicts
# per worker is far cheaper than shipping a built index with every task.
_INDEX: CitationIndex | None = None


def _init_worker(index_rows: list[tuple]) -> None:
    global _INDEX
    _INDEX = CitationIndex(index_rows)


def _work(item: tuple) -> tuple:
    """Runs in a worker process. item = (doc_id, summary, body, lower, doc_date)."""
    doc_id, summary, body, lower, doc_date = item
    try:
        rows = build_rows(doc_id, summary=summary, body=body, lower=lower, doc_date=doc_date, index=_INDEX)
        return doc_id, rows, citation_hash(summary, body, lower), None
    except Exception as exc:  # noqa: BLE001 — reported and skipped, never aborts the run
        return doc_id, None, None, f"{type(exc).__name__}: {exc}"


class _Stats:
    """Everything the final summary needs, accumulated from the rows this run
    produced. Never queries `citations`, so --dry-run prints the same summary
    and works before the table exists."""

    def __init__(self) -> None:
        self.status: Counter = Counter()
        self.layer: Counter = Counter()
        self.matrix: dict[tuple[str, str], int] = defaultdict(int)
        self.shapes: Counter = Counter()
        self.edges = 0

    def add(self, citing_court: str | None, rows: list[dict]) -> None:
        for r in rows:
            self.status[r["status"]] += 1
            self.layer[r["layer"]] += 1
            if r["status"] == "resolved":
                self.matrix[(citing_court or "?", r["target_court"] or "?")] += 1
            elif r["status"] == "unresolved":
                self.shapes[_DIGITS.sub("#", r["raw_text"])] += 1

    def report(self, *, elapsed: float, docs: int, errors: int, dry_run: bool) -> None:
        total = sum(self.status.values())
        log.info("")
        log.info("%s %d documents, %s citations, %s cites edges, %d errors in %.0fs",
                 "DRY RUN —" if dry_run else "Done:", docs, f"{total:,}", f"{self.edges:,}", errors, elapsed)
        log.info("  by status: %s", ", ".join(f"{k}={v:,}" for k, v in self.status.most_common()) or "-")
        log.info("  by layer:  %s", ", ".join(f"{k}={v:,}" for k, v in self.layer.most_common()) or "-")
        if self.matrix:
            citing = sorted({c for c, _ in self.matrix})
            cited = sorted({t for _, t in self.matrix})
            log.info("  resolved, citing court x cited court:")
            log.info("    %-16s %s", "", " ".join(f"{t:>10}" for t in cited))
            for c in citing:
                log.info("    %-16s %s", c, " ".join(f"{self.matrix.get((c, t), 0):>10,}" for t in cited))
        if self.shapes:
            log.info("  ten most common unresolved shapes (digits -> #):")
            for shape, n in self.shapes.most_common(10):
                log.info("    %6d  %s", n, shape[:100])


def selection(*, force: bool, source: str | None, doc: str | None,
              since: str | date | None, has_col: bool) -> tuple[str, dict]:
    """WHERE clause + bind params for the documents to (re)build.

    Pure, so the predicate choice is unit-testable. `has_col` says whether
    documents.citation_hash exists: without it there is no staleness to speak
    of (migration 0004 has not run), so every document counts as stale.
    """
    if doc:
        return "d.id = :doc", {"doc": doc}
    params: dict = {"sources": list(COURT_SOURCES)}
    where = ["s.short_name = ANY(:sources)"]
    if force:
        pass                                   # --force: staleness ignored on purpose
    elif has_col:
        where.append(STALE_WHERE)
    else:
        log.warning("citation_hash missing — treating every document as stale (apply alembic 0004)")
    if source:
        where.append("s.short_name = :sn")
        params["sn"] = source
    if since:
        where.append("d.document_date >= :since")
        # asyncpg binds a DATE column strictly: a str here is a DataError.
        params["since"] = date.fromisoformat(since) if isinstance(since, str) else since
    return " AND ".join(where), params


async def _load_index_rows(conn) -> list[tuple]:
    return [tuple(r) for r in (await conn.execute(text(INDEX_SQL), {"sources": list(COURT_SOURCES)})).all()]


_HAS_COL_SQL = text("SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'documents' AND column_name = 'citation_hash'")


async def _has_citation_hash(conn) -> bool:
    return bool((await conn.execute(_HAS_COL_SQL)).scalar())


async def build(*, all_docs: bool, force: bool, source: str | None, doc: str | None, since: str | None,
                limit: int | None, workers: int, dry_run: bool) -> None:
    if not (all_docs or source or doc or since):
        raise SystemExit("nothing selected: pass --all, --source, --doc or --since "
                         "(--all alone means every stale document in the court sources)")
    if source and source not in COURT_SOURCES:
        log.warning("source %r is not one of the court sources (%s) — 0 documents",
                    source, ", ".join(COURT_SOURCES))
        return
    # create_tables=False: alembic owns the schema. create_all here would make an
    # empty `citations` table behind the migration's back, leaving the DB half
    # built (table without documents.citation_hash).
    await init_db(create_tables=False)
    engine = await get_engine()

    async with engine.connect() as conn:
        where_sql, params = selection(force=force, source=source, doc=doc, since=since,
                                      has_col=await _has_citation_hash(conn))
        index_rows = await _load_index_rows(conn)
        ids = (await conn.execute(text(f"""
            SELECT d.id FROM documents d JOIN sources s ON s.id = d.source_id
            WHERE {where_sql} ORDER BY d.id"""), params)).scalars().all()
    if limit:
        ids = ids[:limit]
    total = len(ids)
    log.info("Index: %s ruling(s). Documents to (re)build: %s  (workers=%d)%s",
             f"{len(index_rows):,}", f"{total:,}", workers, "  [dry run]" if dry_run else "")
    if not total:
        return

    t0 = time.monotonic()
    stats = _Stats()
    done = errors = 0
    conn_dead = False
    with Pool(processes=workers, initializer=_init_worker, initargs=(index_rows,)) as pool:
        async with engine.connect() as conn:
            for i in range(0, total, BATCH):
                batch_ids = ids[i:i + BATCH]
                rows = (await conn.execute(text("""
                    SELECT d.id, d.summary, d.body_text, d.lower_body_text, d.document_date, d.court
                    FROM documents d WHERE d.id = ANY(:ids)"""), {"ids": list(batch_ids)})).all()
                courts = {r.id: r.court for r in rows}
                items = [(r.id, r.summary, r.body_text, r.lower_body_text, r.document_date) for r in rows]
                for doc_id, cit_rows, hash_, err in pool.imap_unordered(_work, items, chunksize=4):
                    # Count every processed document toward done regardless of
                    # outcome, so rate/ETA aren't undercounted by write failures.
                    done += 1
                    if err:
                        errors += 1
                        log.warning("doc %s: %s", doc_id, err)
                    elif dry_run:
                        stats.add(courts.get(doc_id), cit_rows)
                        stats.edges += len({r["to_doc_id"] for r in cit_rows
                                            if r["status"] == "resolved" and r["layer"] in ("summary", "body")})
                    else:
                        try:
                            _, e = await write_citations(conn, doc_id, cit_rows, hash_=hash_)
                            stats.add(courts.get(doc_id), cit_rows)
                            stats.edges += e
                        except Exception as exc:  # noqa: BLE001
                            errors += 1
                            log.warning("doc %s write failed: %s", doc_id, exc)
                            # Rolling back discards only the up-to-COMMIT_EVERY docs
                            # written since the last commit; their citation_hash is
                            # unchanged so they stay stale and are rebuilt next run.
                            try:
                                await conn.rollback()
                            except Exception as rb_exc:  # noqa: BLE001
                                # Connection is unusable (e.g. server dropped it).
                                # Stop cleanly instead of raising out of an
                                # unattended background process.
                                log.error("doc %s: rollback failed, connection is dead (%s); stopping run",
                                          doc_id, rb_exc)
                                conn_dead = True
                                break
                    if not dry_run and done % COMMIT_EVERY == 0:
                        await conn.commit()
                elapsed = time.monotonic() - t0
                rate = done / elapsed if elapsed else 0
                eta = (total - done) / rate if rate else 0
                log.info("[%d/%d] %.1f%% — %.1f docs/s — %s citations — ETA %.0f min%s",
                         done, total, 100 * done / total, rate, f"{sum(stats.status.values()):,}", eta / 60,
                         f" ({errors} errors)" if errors else "")
                if conn_dead:
                    break
            if dry_run:
                # Nothing was written; end the read transaction without one.
                await conn.rollback()
            elif not conn_dead:
                await conn.commit()

    stats.report(elapsed=time.monotonic() - t0, docs=done, errors=errors, dry_run=dry_run)


async def relink(*, limit: int | None, dry_run: bool) -> None:
    await init_db(create_tables=False)
    engine = await get_engine()
    t0 = time.monotonic()
    async with engine.connect() as conn:
        index = await CitationIndex.load(conn)
        if dry_run:
            would, total = await count_relinkable(conn, index, limit=limit)
            log.info("[dry run] would resolve %s of %s unresolved/ambiguous citation(s) in %.0fs",
                     f"{would:,}", f"{total:,}", time.monotonic() - t0)
            await conn.rollback()
            return
        fixed = await relink_unresolved(conn, index, limit=limit)
        await conn.commit()
    log.info("Relinked %s previously unresolved/ambiguous citation(s) in %.0fs", f"{fixed:,}", time.monotonic() - t0)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="every stale document (citation_hash missing/changed)")
    ap.add_argument("--force", action="store_true", help="with --all/--source: also non-stale documents")
    ap.add_argument("--source", help="restrict to one source short_name")
    ap.add_argument("--doc", help="one document id")
    ap.add_argument("--since", type=date.fromisoformat, metavar="YYYY-MM-DD",
                    help="only documents with document_date >= YYYY-MM-DD")
    ap.add_argument("--relink-unresolved", action="store_true",
                    help="re-resolve unresolved/ambiguous rows without re-extracting")
    ap.add_argument("--dry-run", action="store_true", help="extract+resolve, print the summary, write nothing")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    a = ap.parse_args()
    if a.relink_unresolved:
        asyncio.run(relink(limit=a.limit, dry_run=a.dry_run))
    else:
        asyncio.run(build(all_docs=a.all, force=a.force, source=a.source, doc=a.doc, since=a.since,
                          limit=a.limit, workers=a.workers, dry_run=a.dry_run))
