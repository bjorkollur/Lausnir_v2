"""One-time remediation for the pdfString-migration "empty" bug (2026-09-27).

Background: `scripts/migrate_pdfstring_to_disk.py` originally treated an empty
`pdfString` (importers have always guarded PDF writes with `if pdf_b64:`, so
empty has always meant "no PDF for this doc") the same as a genuinely missing
file — it wrote a 0-byte `.pdf` into the RAW directory and stored
`pdf_sha256` = sha256 of the empty string
(`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`) plus a
`pdf_path`. The script has since been fixed (see docs/wiki/09-gildrur.md) to
record `{"pdf_sha256": null, "pdf_path": null}` for these docs and never write
a file. This script cleans up the ~11,800-document partial run that happened
under the old, buggy behaviour before the fix landed.

Two passes:

**Pass 1 (DB-driven)** — every row where `raw_api_data->>'pdf_sha256'` equals
the empty-string sha256:

  - If `DATA_DIR/<pdf_path>` exists, has size 0, AND was modified on the
    migration day (2026-09-27) -> delete that 0-byte file and null out
    `pdf_sha256`/`pdf_path` in the JSON.
  - If the file exists but has size > 0 -> this should never happen (a real
    PDF ending up with the empty-string hash would mean earlier data
    corruption); log it and skip — never delete, never touch the JSON.
  - If the file does not exist -> just null out the two JSON fields.

**Pass 2 (filesystem-driven)** — covers a gap pass 1 can't see: the migration
was killed mid-batch, so up to `COMMIT_EVERY` documents can have had their
0-byte file written but their UPDATE rolled back (raw_api_data still carries
the original, un-migrated `pdfString`, never got a `pdf_sha256` marker at
all). Pass 1's WHERE clause can't find these. Pass 2 instead scans
`DATA_DIR/raw/<short_name>/*.pdf` for 0-byte files modified on the migration
day (skipping any path pass 1 already handled), maps each one back to its
document by `short_name` + `verdict_filename` (falling back to `external_id`
when `verdict_filename` is NULL — the same key `migrate_pdfstring_to_disk.py`
uses), and only deletes it when the matched document's `pdfString` key is
still present and decodes to zero bytes (reusing
`migrate_pdfstring_to_disk.decode_pdf_string` — this covers the empty string
`""` *and* JSON `null`, since `->>'pdfString'` yields Python `None` for a
JSON-null value and `decode_pdf_string(None) == b""`, exactly like the fixed
migration script's "empty" outcome) or its `pdf_sha256` is already null
(migrated, by pass 1 or a previous run). Anything else — no matching row, an
ambiguous multiple match, a non-empty/undecodable `pdfString`, or a real
(non-null) `pdf_sha256` sitting next to a 0-byte file — is logged and left
untouched.

Nothing outside of these exact conditions is ever deleted. `--dry-run` is the
default; pass `--apply` to actually write. Re-running is idempotent: once a
row's `pdf_sha256`/`pdf_path` are null (and its file gone), neither pass
touches it again.

Usage:
    set -a; . ./.env; set +a
    uv run python scripts/remediate_pdfstring_empty.py               # dry-run (default)
    uv run python scripts/remediate_pdfstring_empty.py --apply        # actually write
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text

# Reuse the exact same "is this pdfString actually empty" logic the (now
# fixed) migration script uses -- covers None, "", whitespace-only, and a
# bare "data:...;base64," prefix all decoding to b"". Importing rather than
# re-implementing means the two scripts can never disagree about what counts
# as empty.
from scripts.migrate_pdfstring_to_disk import decode_pdf_string

try:
    sys.stdout.reconfigure(line_buffering=True)
except (AttributeError, ValueError):
    pass

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
MIGRATION_DAY = date(2026, 9, 27)

# The four sources migrate_pdfstring_to_disk.py ever touched (see its module
# docstring). Pass 2's filesystem scan is scoped to these so it can never
# stumble onto an unrelated 0-byte file from a completely different source.
AFFECTED_SOURCES = ("heradsdomstolar", "haestirettur", "landsrettur", "endurupptokudomur")

NULL_OUT_SQL = (
    "UPDATE documents "
    "SET raw_api_data = raw_api_data || jsonb_build_object('pdf_sha256', NULL, 'pdf_path', NULL) "
    "WHERE id = :id AND raw_api_data->>'pdf_sha256' = :empty_sha"
)

# Pass 2's cleanup: the row still has an empty pdfString (never migrated at
# all) -- strip it and null the markers in one go, same shape as the fixed
# migrate_pdfstring_to_disk.py "empty" outcome. The emptiness itself was
# already verified in Python (decode_pdf_string); this guard just re-checks
# the key is still present so a concurrent change can't be clobbered.
NULL_OUT_AND_DROP_PDFSTRING_SQL = (
    "UPDATE documents "
    "SET raw_api_data = (raw_api_data - 'pdfString') "
    "|| jsonb_build_object('pdf_sha256', NULL, 'pdf_path', NULL) "
    "WHERE id = :id AND raw_api_data ? 'pdfString'"
)

SELECT_SQL = (
    "SELECT d.id, d.external_id, d.raw_api_data->>'pdf_path' AS pdf_path, s.short_name "
    "FROM documents d JOIN sources s ON s.id = d.source_id "
    "WHERE d.raw_api_data->>'pdf_sha256' = :empty_sha "
    "ORDER BY d.id"
)

# Pass 2: map a filesystem path back to its owning document by short_name +
# verdict_filename, falling back to external_id only when verdict_filename is
# NULL -- the exact key convention migrate_pdfstring_to_disk.py uses.
FIND_DOC_BY_KEY_SQL = (
    "SELECT d.id, d.external_id, "
    "       d.raw_api_data ? 'pdfString' AS has_pdfstring, "
    "       d.raw_api_data->>'pdfString' AS pdf_string, "
    "       d.raw_api_data ? 'pdf_sha256' AS has_pdf_sha256, "
    "       d.raw_api_data->>'pdf_sha256' AS pdf_sha256 "
    "FROM documents d JOIN sources s ON s.id = d.source_id "
    "WHERE s.short_name = :sn "
    "  AND (d.verdict_filename = :key OR (d.verdict_filename IS NULL AND d.external_id = :key))"
)


def decide_file_action(path: Path | None) -> str:
    """Pure decision, no I/O side effects beyond stat-ing `path`.

    Returns "delete" (0-byte file, modified on the migration day -> safe to
    remove), "skip_nonempty" (file exists but isn't the 0-byte artifact — log
    and leave everything alone), or "null_only" (no file to worry about,
    just clear the JSON markers).
    """
    if path is None or not path.exists():
        return "null_only"
    size = path.stat().st_size
    if size > 0:
        return "skip_nonempty"
    mtime = datetime.fromtimestamp(path.stat().st_mtime).date()
    if mtime == MIGRATION_DAY:
        return "delete"
    # 0 bytes but not touched on the migration day -- be conservative, don't
    # delete something this script can't attribute to the known bug.
    return "null_only"


async def _run_pass1(engine, data_dir: Path, apply: bool) -> tuple[dict[str, Counter], set[Path]]:
    """DB-driven pass: rows already marked with the empty-string sha256.

    Returns (per-source stats, set of file paths this pass deleted or would
    delete) — the second is used by pass 2 to avoid double-reporting the same
    file.
    """
    async with engine.connect() as conn:
        rows = (await conn.execute(text(SELECT_SQL), {"empty_sha": EMPTY_SHA256})).all()

    total = len(rows)
    log.info("Pass 1 (DB-driven): rows with pdf_sha256 = empty-string hash: %s (apply=%s)",
              f"{total:,}", apply)

    stats: dict[str, Counter] = {}
    handled_paths: set[Path] = set()
    if not total:
        return stats, handled_paths

    async with engine.connect() as conn:
        for r in rows:
            sn = r.short_name
            stats.setdefault(sn, Counter())

            path = None
            if r.pdf_path:
                path = data_dir / r.pdf_path

            action = decide_file_action(path)

            if action == "skip_nonempty":
                stats[sn]["skip_nonempty"] += 1
                log.warning("doc %s (%s/%s): pdf_path=%s has size %s > 0 — NOT the empty-migration "
                            "artifact, skipping (should not happen; investigate)",
                            r.id, sn, r.external_id, r.pdf_path, path.stat().st_size if path else "?")
                continue

            if action == "delete":
                stats[sn]["delete"] += 1
                handled_paths.add(path.resolve())
                if apply:
                    try:
                        path.unlink()
                        log.info("doc %s (%s/%s): deleted 0-byte file %s", r.id, sn, r.external_id, path)
                    except OSError as exc:
                        stats[sn]["delete_failed"] += 1
                        log.warning("doc %s (%s/%s): failed to delete %s: %s",
                                    r.id, sn, r.external_id, path, exc)
                        continue
                else:
                    log.info("doc %s (%s/%s): [dry-run] would delete 0-byte file %s",
                              r.id, sn, r.external_id, path)
            else:  # null_only
                stats[sn]["null_only"] += 1
                if not apply:
                    log.info("doc %s (%s/%s): [dry-run] would null pdf_sha256/pdf_path (path=%s)",
                              r.id, sn, r.external_id, r.pdf_path)

            if apply:
                await conn.execute(text(NULL_OUT_SQL), {"id": r.id, "empty_sha": EMPTY_SHA256})

        if apply:
            await conn.commit()

    verb = "deleted" if apply else "would_delete"
    null_verb = "nulled" if apply else "would_null"
    log.info("Pass 1 results:")
    log.info("%-20s %13s %13s %14s %13s", "source", verb, null_verb, "skip(nonempty)", "delete_failed")
    for sn in sorted(stats):
        c = stats[sn]
        log.info("%-20s %13d %13d %14d %13d",
                  sn, c["delete"], c["delete"] + c["null_only"], c["skip_nonempty"], c["delete_failed"])
    return stats, handled_paths


async def _run_pass2(engine, data_dir: Path, apply: bool, skip_paths: set[Path]) -> dict[str, Counter]:
    """Filesystem-driven pass: 0-byte files modified on the migration day that
    pass 1's DB query can't find (their row's UPDATE never committed, so
    raw_api_data still has the original, un-migrated pdfString). Maps each
    file back to its document by short_name + verdict_filename (external_id
    fallback) and only deletes it when the document confirms it should have
    no PDF."""
    stats: dict[str, Counter] = {}
    candidates: list[Path] = []
    for sn in AFFECTED_SOURCES:
        source_dir = data_dir / "raw" / sn
        if not source_dir.is_dir():
            continue
        for p in source_dir.glob("*.pdf"):
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_size != 0:
                continue
            if datetime.fromtimestamp(st.st_mtime).date() != MIGRATION_DAY:
                continue
            if p.resolve() in skip_paths:
                continue  # already handled by pass 1
            candidates.append(p)

    log.info("Pass 2 (filesystem-driven): %s orphan 0-byte files found (not already handled by pass 1)",
              f"{len(candidates):,}")

    async with engine.connect() as conn:
        for p in candidates:
            sn = p.parent.name
            key = p.stem
            stats.setdefault(sn, Counter())

            match_rows = (await conn.execute(text(FIND_DOC_BY_KEY_SQL), {"sn": sn, "key": key})).all()

            if not match_rows:
                stats[sn]["no_match"] += 1
                log.warning("orphan file %s: no document row found for short_name=%s key=%s — leaving untouched",
                            p, sn, key)
                continue
            if len(match_rows) > 1:
                stats[sn]["ambiguous"] += 1
                log.warning("orphan file %s: %d matching document rows for short_name=%s key=%s "
                            "(ambiguous) — leaving untouched", p, len(match_rows), sn, key)
                continue

            r = match_rows[0]
            already_nulled = bool(r.has_pdf_sha256) and r.pdf_sha256 is None
            still_empty_pdfstring = False
            if bool(r.has_pdfstring):
                try:
                    still_empty_pdfstring = len(decode_pdf_string(r.pdf_string)) == 0
                except (ValueError,) as exc:  # binascii.Error is a ValueError subclass
                    stats[sn]["skip_anomaly"] += 1
                    log.warning("orphan file %s: doc %s (%s) has an undecodable pdfString (%s) — "
                                "leaving untouched", p, r.id, r.external_id, exc)
                    continue

            if not (still_empty_pdfstring or already_nulled):
                stats[sn]["skip_anomaly"] += 1
                log.warning("orphan file %s: doc %s (%s) has pdfString=%r pdf_sha256=%r — does not "
                            "match the known empty-pdfString bug, leaving untouched",
                            p, r.id, r.external_id, r.pdf_string, r.pdf_sha256)
                continue

            stats[sn]["delete"] += 1
            if apply:
                try:
                    p.unlink()
                    log.info("doc %s (%s/%s): deleted orphan 0-byte file %s", r.id, sn, r.external_id, p)
                except OSError as exc:
                    stats[sn]["delete_failed"] += 1
                    log.warning("doc %s (%s/%s): failed to delete orphan %s: %s",
                                r.id, sn, r.external_id, p, exc)
                    continue
                if still_empty_pdfstring:
                    await conn.execute(text(NULL_OUT_AND_DROP_PDFSTRING_SQL), {"id": r.id})
            else:
                verb = "still has empty pdfString" if still_empty_pdfstring else "already nulled by pass 1 rules"
                log.info("doc %s (%s/%s): [dry-run] would delete orphan 0-byte file %s (%s)",
                          r.id, sn, r.external_id, p, verb)

        if apply:
            await conn.commit()

    verb = "deleted" if apply else "would_delete"
    log.info("Pass 2 results:")
    log.info("%-20s %13s %10s %10s %14s %13s", "source", verb, "no_match", "ambiguous", "skip_anomaly", "delete_failed")
    for sn in sorted(stats):
        c = stats[sn]
        log.info("%-20s %13d %10d %10d %14d %13d",
                  sn, c["delete"], c["no_match"], c["ambiguous"], c["skip_anomaly"], c["delete_failed"])
    return stats


async def remediate(*, apply: bool) -> None:
    from engine.config import sources as sources_config
    from engine.database.connection import init_db, get_engine

    await init_db()
    engine = await get_engine()
    data_dir = Path(sources_config.RAW_DIR).parent

    _pass1_stats, handled_paths = await _run_pass1(engine, data_dir, apply)
    await _run_pass2(engine, data_dir, apply, handled_paths)

    if not apply:
        log.info("(dry-run: nothing was deleted or written — pass --apply to make changes)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                     help="actually delete 0-byte files and null the JSON markers (default: dry-run)")
    a = ap.parse_args()
    asyncio.run(remediate(apply=a.apply))
