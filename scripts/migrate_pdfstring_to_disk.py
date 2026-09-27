"""Move duplicated PDF bytes out of raw_api_data->>'pdfString' onto disk.

Background (see CLAUDE.md, docs/wiki/01-arkitektur.md): the RAW layer contract
is that PDF bytes live once, on disk, at SourceConfig.pdf_path(<key>). CLAUDE.md
describes <key> as external_id, but the importers that duplicated the PDF into
JSON (heradsdomstolar, haestirettur, landsrettur, endurupptokudomur — confirmed
by grepping each import_*.py) actually write it to disk keyed by
documents.verdict_filename ("HerdRvk_E-4047-2018_D_27-11-2020.pdf", not
"g-9b7fbb27-....pdf"), falling back to external_id only when verdict_filename
is NULL — the same fallback scripts/backfill_heradsdomstolar_detail.py uses
(`vf or doc.external_id`). This script follows that same convention; using
external_id alone would make every on-disk file look "missing" and write a
second, wrongly-named copy. That duplicate accounts for ~56 GB of the ~76 GB
`documents` table.

This script removes the duplicate, but only when it has proven the two copies
are byte-identical:

  1. Decode the base64 pdfString (tolerating a `data:...;base64,` prefix and
     an empty string) and sha256 the bytes.
  2. If the on-disk file is MISSING, write it atomically (temp file + rename)
     from the JSON bytes first — nothing is lost.
  3. If the on-disk file EXISTS, sha256 it and compare.
     - EQUAL      -> replace pdfString in raw_api_data with pdf_sha256 +
                     pdf_path (relative to DATA_DIR) markers.
     - DIFFERENT  -> log a MISMATCH with both hashes/sizes and leave the row
                     untouched. Never overwrite, never guess.
  4. Any exception for a document is logged and the run continues.

raw_api_data is otherwise immutable (Layer 1 RAW); this is the one controlled,
verified exception, applied once on 2026-09-27. See docs/wiki/09-gildrur.md.

The predicate `raw_api_data ? 'pdfString'` means already-migrated rows no
longer match, so the script is resumable — re-running it after a partial run
or a crash just picks up where it left off.

Usage:
    set -a; . ./.env; set +a
    uv run python scripts/migrate_pdfstring_to_disk.py --dry-run --limit 200 --source heradsdomstolar
    uv run python scripts/migrate_pdfstring_to_disk.py --source landsrettur --dry-run
    uv run python scripts/migrate_pdfstring_to_disk.py                       # full run, all sources
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import hashlib
import logging
import os
import sys
import tempfile
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text

from engine.config import sources as sources_config
from engine.config.sources import get_config

try:
    sys.stdout.reconfigure(line_buffering=True)
except (AttributeError, ValueError):
    pass

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

BATCH = 200          # documents fetched (incl. base64 pdfString) per round
COMMIT_EVERY = 50

# Kept as a module-level constant so both the script and the unit tests refer
# to the exact same statement.
UPDATE_SQL = (
    "UPDATE documents "
    "SET raw_api_data = (raw_api_data - 'pdfString') "
    "|| jsonb_build_object('pdf_sha256', CAST(:sha AS text), 'pdf_path', CAST(:rel AS text)) "
    "WHERE id = :id AND raw_api_data ? 'pdfString'"
)


# ─── Pure helpers (unit-tested without a DB) ──────────────────────────────────

def decode_pdf_string(raw: str | None) -> bytes:
    """Decode a base64 pdfString value.

    Tolerates a `data:<mime>;base64,` prefix and an empty/None string (-> b"").
    Raises binascii.Error on malformed base64.
    """
    if not raw:
        return b""
    s = raw
    if s.startswith("data:"):
        comma = s.find(",")
        if comma != -1:
            s = s[comma + 1:]
    return base64.b64decode(s, validate=False)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def decide(path_exists: bool, disk_sha256: str | None, json_sha256: str) -> str:
    """Pure decision function — no I/O.

    Returns "missing" (write the file from JSON), "equal" (safe to drop
    pdfString from JSON), or "mismatch" (leave the row untouched).
    """
    if not path_exists:
        return "missing"
    if disk_sha256 == json_sha256:
        return "equal"
    return "mismatch"


def write_atomic(path: Path, data: bytes) -> None:
    """Write `data` to `path` via a temp file + rename so a crash mid-write
    can never leave a truncated PDF on disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-migrate-", suffix=".pdf")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ─── Per-document worker (runs in a thread — file I/O bound) ─────────────────

@dataclass
class DocResult:
    outcome: str  # "written" | "migrated" | "mismatch" | "error"
    json_sha256: str | None = None
    json_size: int | None = None
    disk_sha256: str | None = None
    disk_size: int | None = None
    rel_path: str | None = None
    error: str | None = None


def process_doc(short_name: str, external_id: str, verdict_filename: str | None,
                 pdf_string: str | None, dry_run: bool) -> DocResult:
    try:
        data = decode_pdf_string(pdf_string)
    except (binascii.Error, ValueError) as exc:
        return DocResult(outcome="error", error=f"base64 decode failed: {exc}")

    try:
        cfg = get_config(short_name)
    except ValueError as exc:
        return DocResult(outcome="error", error=str(exc))

    json_sha = sha256_hex(data)
    json_size = len(data)
    # Importers write the on-disk PDF keyed by verdict_filename, not external_id
    # (see module docstring) — fall back to external_id only when it's NULL, same
    # as scripts/backfill_heradsdomstolar_detail.py.
    key = verdict_filename or external_id
    path = cfg.pdf_path(key)
    # Derive DATA_DIR from RAW_DIR (both live in engine.config.sources) rather
    # than reading the DATA_DIR env var again here, so the two always agree —
    # and so tests can retarget both by monkeypatching sources_config.RAW_DIR.
    data_dir = Path(sources_config.RAW_DIR).parent
    try:
        rel = str(path.relative_to(data_dir))
    except ValueError:
        rel = str(path)

    exists = path.exists()
    disk_sha = disk_size = None
    if exists:
        try:
            disk_bytes = path.read_bytes()
        except OSError as exc:
            return DocResult(outcome="error", error=f"read failed: {exc}")
        disk_sha = sha256_hex(disk_bytes)
        disk_size = len(disk_bytes)

    decision = decide(exists, disk_sha, json_sha)

    if decision == "missing":
        if not dry_run:
            try:
                write_atomic(path, data)
            except OSError as exc:
                return DocResult(outcome="error", error=f"write failed: {exc}")
        return DocResult(outcome="written", json_sha256=json_sha, json_size=json_size, rel_path=rel)

    if decision == "equal":
        return DocResult(outcome="migrated", json_sha256=json_sha, json_size=json_size,
                          disk_sha256=disk_sha, disk_size=disk_size, rel_path=rel)

    return DocResult(outcome="mismatch", json_sha256=json_sha, json_size=json_size,
                      disk_sha256=disk_sha, disk_size=disk_size, rel_path=rel)


# ─── Driver ────────────────────────────────────────────────────────────────

async def migrate(*, source: str | None, limit: int | None, dry_run: bool, workers: int) -> None:
    from engine.database.connection import init_db, get_engine

    await init_db()
    engine = await get_engine()

    where = ["d.raw_api_data ? 'pdfString'"]
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
    log.info("Documents to process: %s (dry_run=%s, workers=%d)", f"{total:,}", dry_run, workers)
    if not total:
        return

    async with engine.connect() as conn:
        before_rows = (await conn.execute(text("""
            SELECT s.short_name, sum(pg_column_size(d.raw_api_data))
            FROM documents d JOIN sources s ON s.id = d.source_id
            WHERE d.id = ANY(:ids) GROUP BY s.short_name"""), {"ids": list(ids)})).all()
    size_before = {sn: sz for sn, sz in before_rows}

    stats: dict[str, Counter] = defaultdict(Counter)
    t0 = time.monotonic()
    done = 0
    conn_dead = False
    with ThreadPoolExecutor(max_workers=workers) as pool:
        loop = asyncio.get_running_loop()
        async with engine.connect() as conn:
            for i in range(0, total, BATCH):
                batch_ids = ids[i:i + BATCH]
                rows = (await conn.execute(text("""
                    SELECT d.id, d.external_id, d.verdict_filename, s.short_name,
                           d.raw_api_data->>'pdfString' AS pdf_string
                    FROM documents d JOIN sources s ON s.id = d.source_id
                    WHERE d.id = ANY(:ids)"""), {"ids": list(batch_ids)})).all()

                futures = [
                    loop.run_in_executor(pool, process_doc, r.short_name, r.external_id,
                                          r.verdict_filename, r.pdf_string, dry_run)
                    for r in rows
                ]
                results = await asyncio.gather(*futures)

                for r, result in zip(rows, results):
                    done += 1
                    sn = r.short_name
                    if result.outcome == "error":
                        stats[sn]["errors"] += 1
                        log.warning("doc %s (%s/%s): ERROR %s", r.id, sn, r.external_id, result.error)
                    elif result.outcome == "mismatch":
                        stats[sn]["mismatch"] += 1
                        log.warning(
                            "doc %s (%s/%s): MISMATCH json_sha=%s (%s bytes) disk_sha=%s (%s bytes) path=%s",
                            r.id, sn, r.external_id, result.json_sha256, f"{result.json_size:,}",
                            result.disk_sha256, f"{result.disk_size:,}", result.rel_path)
                    else:
                        # "written" or "migrated" both end with the same JSON update.
                        if dry_run:
                            stats[sn][result.outcome] += 1
                            log.info("doc %s (%s/%s): [dry-run] would %s (sha=%s)",
                                      r.id, sn, r.external_id, result.outcome, result.json_sha256)
                        else:
                            try:
                                await conn.execute(text(UPDATE_SQL), {
                                    "sha": result.json_sha256, "rel": result.rel_path, "id": r.id,
                                })
                                stats[sn][result.outcome] += 1
                            except Exception as exc:  # noqa: BLE001 — logged and skipped
                                stats[sn]["errors"] += 1
                                log.warning("doc %s: DB update failed: %s", r.id, exc)
                                # A failed statement aborts the whole transaction on this
                                # connection — every later UPDATE would raise
                                # InFailedSQLTransactionError until we roll back. Rolling
                                # back only discards the up-to-COMMIT_EVERY docs written
                                # since the last commit; their raw_api_data is unchanged so
                                # they still match the predicate and are retried next run.
                                try:
                                    await conn.rollback()
                                except Exception as rb_exc:  # noqa: BLE001
                                    log.error("doc %s: rollback failed, connection is dead (%s); stopping run",
                                              r.id, rb_exc)
                                    conn_dead = True

                    if conn_dead:
                        break
                    if not dry_run and done % COMMIT_EVERY == 0:
                        await conn.commit()

                if conn_dead:
                    break

                elapsed = time.monotonic() - t0
                rate = done / elapsed if elapsed else 0
                eta = (total - done) / rate if rate else 0
                errs = sum(c["errors"] for c in stats.values())
                mism = sum(c["mismatch"] for c in stats.values())
                log.info("[%d/%d] %.1f%% — %.1f docs/s — ETA %.0f min%s",
                          done, total, 100 * done / total, rate, eta / 60,
                          f" ({errs} errors, {mism} mismatches)" if errs or mism else "")

            if not dry_run and not conn_dead:
                await conn.commit()

    if conn_dead:
        log.error("Run stopped early after a dead connection — remaining stale docs are picked up next run.")
    log.info("Done: %d docs in %.0fs", done, time.monotonic() - t0)

    async with engine.connect() as conn:
        after_rows = (await conn.execute(text("""
            SELECT s.short_name, sum(pg_column_size(d.raw_api_data))
            FROM documents d JOIN sources s ON s.id = d.source_id
            WHERE d.id = ANY(:ids) GROUP BY s.short_name"""), {"ids": list(ids)})).all()
    size_after = {sn: sz for sn, sz in after_rows}

    log.info("%-20s %10s %20s %10s %8s %16s %16s",
              "source", "migrated", "written-then-migrated", "mismatch", "errors", "size before", "size after")
    for sn in sorted(set(stats) | set(size_before) | set(size_after)):
        c = stats[sn]
        b = size_before.get(sn, 0) or 0
        a = size_after.get(sn, 0) or 0
        log.info("%-20s %10d %20d %10d %8d %14.1f MB %14.1f MB",
                  sn, c["migrated"], c["written"], c["mismatch"], c["errors"],
                  b / 1e6, a / 1e6)
    if dry_run:
        log.info("(dry-run: 'size after' equals 'size before' — no writes were made)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", help="restrict to one source's short_name (e.g. heradsdomstolar)")
    ap.add_argument("--limit", type=int, help="process at most N documents")
    ap.add_argument("--dry-run", action="store_true", help="verify only — decode, hash, compare; no writes")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1),
                     help="thread pool size for file I/O (decode/sha256 is CPU-light)")
    a = ap.parse_args()
    asyncio.run(migrate(source=a.source, limit=a.limit, dry_run=a.dry_run, workers=a.workers))
