"""Re-extract body_text for footnote-bearing sources, pairing footnotes properly.

Theses were originally imported with poppler `pdftotext`, which reads the
foot-of-page note block column-first: the markers arrive detached from their
texts ("1\\n2\\n3\\n4" followed by four sentences) and the pairing cannot be
recovered afterwards. parse_pdf(footnotes=True) rebuilds from word positions
instead, so each marker stays on the line of the note it belongs to.

Only sources with `has_footnotes=True` are touched — court rulings have no
footnotes and are left alone.

The PDFs are read from disk; nothing is re-fetched from the source. Thesis PDFs
predate a filename migration, so they are located by recomputing the original
`thesis_stem()` rather than by the current verdict_filename.

Usage:
    set -a; . ./.env; set +a
    uv run python scripts/backfill_footnotes.py --source logfraediritgerdir --dry-run
    uv run python scripts/backfill_footnotes.py --source logfraediritgerdir --limit 50
    uv run python scripts/backfill_footnotes.py --source logfraediritgerdir
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text

import engine.database.connection as _db_conn
from engine.config.sources import SOURCE_REGISTRY, get_config
from engine.database.connection import init_db
from engine.processors.pdf_parser import parse_pdf

log = logging.getLogger(__name__)

_REF_RE = re.compile(r'\[\^(\d+)\](?!:)')
_DEF_RE = re.compile(r'(?m)^\[\^(\d+)\]:')
# Refuse to replace a body that would come back materially shorter.
_MIN_LENGTH_RATIO = 0.97


def _exists(p: Path) -> bool:
    """Path.exists() that tolerates unusable names.

    Some verdict_filenames are long enough that the filesystem rejects the path
    outright (ENAMETOOLONG), which raises rather than returning False.
    """
    try:
        return p.exists()
    except OSError:
        return False


def _pdf_for(row, config) -> Path | None:
    """Locate the stored PDF for a document.

    Tries the current verdict_filename first, then the pre-migration
    thesis_stem() name that most files on disk still use.
    """
    if row["verdict_filename"]:
        p = config.pdf_path(row["verdict_filename"])
        if _exists(p):
            return p
    # Books are stored under their external_id (the ISBN), not the .md filename.
    if row["external_id"]:
        p = config.pdf_path(row["external_id"])
        if _exists(p):
            return p
    if config.short_name == "logfraediritgerdir":
        from scripts.import_logfraediritgerdir import thesis_stem
        raw = row["raw_api_data"] or {}
        stem = thesis_stem(row["case_number"] or "", raw.get("author"), raw.get("degree"))
        p = config.pdf_path(stem)
        if _exists(p):
            return p
    return None


async def main(source: str, dry_run: bool, limit: int | None, redo: bool) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    config = get_config(source)
    if not config.has_footnotes:
        raise SystemExit(
            f"{source!r} is not configured with has_footnotes=True — nothing to do. "
            f"Footnote sources: "
            f"{[s for s, c in SOURCE_REGISTRY.items() if c.has_footnotes]}"
        )

    await init_db()
    async with _db_conn.AsyncSessionLocal() as session:
        rows = (await session.execute(text("""
            SELECT d.id, d.case_number, d.verdict_filename, d.external_id,
                   d.raw_api_data, length(d.body_text) AS old_len
            FROM documents d JOIN sources s ON s.id = d.source_id
            WHERE s.short_name = :sn AND d.body_text IS NOT NULL
              AND (:redo OR d.body_text NOT LIKE '%[^%]:%')
            ORDER BY d.created_at
        """), {"sn": source, "redo": redo})).mappings().all()

    if limit:
        rows = rows[:limit]

    stats = {"total": len(rows), "updated": 0, "no_pdf": 0, "no_footnotes": 0,
         "shrunk": 0, "errors": 0}
    log.info("%s: %d documents to consider", source, len(rows))

    for i, row in enumerate(rows, 1):
        pdf = _pdf_for(row, config)
        if pdf is None:
            stats["no_pdf"] += 1
            continue
        try:
            body = parse_pdf(pdf.read_bytes(), footnotes=True)
        except Exception as exc:  # noqa: BLE001
            log.warning("parse failed for %s: %s", pdf.name, exc)
            stats["errors"] += 1
            continue

        if not body or not body.strip():
            stats["errors"] += 1
            continue

        n_defs = len(set(_DEF_RE.findall(body)))
        if n_defs == 0:
            # Nothing gained — leave the existing text alone rather than swapping
            # one extraction for another with no footnotes to show for it.
            stats["no_footnotes"] += 1
            continue

        # Any line the footnote pass classifies as a note but cannot attach to a
        # number is dropped. On documents where that misfires — scanned pages have
        # no reliable font-size signal, and long books repeat small-type running
        # headers — real body text would disappear. Better to keep the worse
        # footnotes than to lose the text.
        if row["old_len"] and len(body) < row["old_len"] * _MIN_LENGTH_RATIO:
            log.warning(
                "skipping %s: %d chars vs %d before (%.0f%%) — texti myndi tapast",
                str(row["case_number"])[:50], len(body), row["old_len"],
                100 * len(body) / row["old_len"],
            )
            stats["shrunk"] += 1
            continue

        if dry_run:
            n_refs = len(set(_REF_RE.findall(body)))
            print(f"{str(row['case_number'])[:52]:54} {row['old_len']:>7} → {len(body):>7} "
                  f"| tilv {n_refs:>4} skilgr {n_defs:>4}")
            stats["updated"] += 1
            continue

        async with _db_conn.AsyncSessionLocal() as session:
            await session.execute(
                text("UPDATE documents SET body_text = :b, updated_at = now() WHERE id = :id"),
                {"b": body, "id": row["id"]},
            )
            await session.commit()
        stats["updated"] += 1

        if i % 100 == 0:
            log.info("... %d/%d (uppfærð: %d)", i, len(rows), stats["updated"])

    print(f"DONE {stats}")
    if not dry_run and stats["updated"]:
        print("Keyrðu svo:")
        print(f"  uv run python scripts/backfill_fts_is.py --source {source}")
        print(f"  uv run python scripts/backfill_chunks.py --source {source}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="logfraediritgerdir")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--redo", action="store_true",
                    help="Re-process documents that already carry footnotes")
    args = ap.parse_args()
    asyncio.run(main(args.source, args.dry_run, args.limit, args.redo))
