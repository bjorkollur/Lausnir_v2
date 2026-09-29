"""Re-read verdict_type from each document's own wording and repair the row.

The old rule in extractor.py filed a document as 'Úrskurður' whenever the body
mentioned 'úrskurðar' anywhere — which every kærumál does, because it discusses
the úrskurður under appeal.  5.368 Hæstaréttardómar, 151 Landsréttarmál and
~1.070 héraðsdómar carried the wrong type.  See docs/wiki/09-gildrur.md.

verdict_type also feeds verdict_filename (the ``_D_``/``_U_`` code), the .md
header and documents.fts_is, so a repaired row means: rename the .md and .pdf,
re-render the markdown, move the raw_api_data pdf_path marker, and clear
passage_hash so backfill_passages.py rebuilds fts_is.

A document whose text does not say, or says two contradictory things, is left
exactly as it is and listed in the report — never guessed at.

Usage:
    uv run python scripts/backfill_verdict_type.py --dry-run
    uv run python scripts/backfill_verdict_type.py --source landsrettur
    uv run python scripts/backfill_verdict_type.py --report /tmp/vt.csv
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TimeElapsedColumn,
)
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

import engine.database.connection as _db_conn
import engine.config.sources as sources_config
from engine.config.sources import get_config
from engine.database.connection import init_db
from engine.database.models import Document, Source
from engine.processors.extractor import _detect_verdict_type
from engine.processors.renderer import (
    unique_verdict_filename,
    verdict_filename,
    write_markdown,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

# Only the three court sources embed the wording this reads.
SOURCES = ("haestirettur", "landsrettur", "heradsdomstolar")
BATCH_SIZE = 200


@dataclass
class Tally:
    seen: int = 0
    unchanged: int = 0
    changed: int = 0
    unknown: int = 0
    renamed: int = 0
    collisions: int = 0
    render_errors: int = 0
    rows: list[tuple] = field(default_factory=list)


def _record(tally: Tally, kind: str, source: str, doc: Document, note: str) -> None:
    tally.rows.append((kind, source, doc.case_number or "", str(doc.document_date or ""),
                       doc.verdict_type or "", note, str(doc.id)))


async def _process_batch(
    session: AsyncSession,
    docs: list[Document],
    config,
    taken: set[str],
    tally: Tally,
    dry_run: bool,
) -> None:
    for doc in docs:
        tally.seen += 1
        new_type = _detect_verdict_type(doc.body_text, config)
        if new_type is None:
            tally.unknown += 1
            _record(tally, "óþekkt", config.short_name, doc, "textinn segir ekki eða mótsagnakenndur")
            continue
        if new_type == doc.verdict_type:
            tally.unchanged += 1
            continue

        old_type = doc.verdict_type
        old_vf = doc.verdict_filename
        doc.verdict_type = new_type          # verdict_filename + markdown read this
        base = verdict_filename(doc, config)
        # ``taken`` holds every name already spoken for — rows *and* orphaned
        # files from earlier runs, which belong to no row and whose only copy a
        # rename would destroy.  Our own current name must not block us from
        # taking the clean base.
        others = taken - {old_vf} if old_vf else taken
        new_vf = unique_verdict_filename(base, others)

        # Belt and braces: the type is repaired even if the file cannot move.
        if new_vf != old_vf and any(
            p.exists() for p in (config.markdown_path(new_vf), config.pdf_path(new_vf))
        ):
            tally.collisions += 1
            _record(tally, "árekstur", config.short_name, doc,
                    f"{new_vf} er þegar til — tegund lagfærð, {old_vf} heldur sér")
            new_vf = old_vf

        _record(tally, "breyting", config.short_name, doc,
                f"{old_type} → {new_type}" + (f"; {old_vf} → {new_vf}" if new_vf != old_vf else ""))
        if dry_run:
            doc.verdict_type = old_type
            tally.changed += 1
            if new_vf != old_vf:
                tally.renamed += 1
            continue

        try:
            if new_vf != old_vf:
                old_pdf = config.pdf_path(old_vf) if old_vf else None
                if old_pdf is not None and old_pdf.exists():
                    old_pdf.rename(config.pdf_path(new_vf))
            write_markdown(doc, config, vf=new_vf)
            if new_vf != old_vf and old_vf:
                config.markdown_path(old_vf).unlink(missing_ok=True)
        except OSError as exc:
            doc.verdict_type = old_type
            tally.render_errors += 1
            _record(tally, "villa", config.short_name, doc, f"{exc}")
            continue

        values: dict = {"verdict_type": new_type, "passage_hash": None}
        if new_vf != old_vf:
            taken.discard(old_vf)
            taken.add(new_vf)
            values["verdict_filename"] = new_vf
            raw = doc.raw_api_data or {}
            if raw.get("pdf_path"):
                # Layer 1 stays as the source returned it; pdf_path is our own
                # pointer, added when the base64 PDFs moved to disk.  Derived
                # from RAW_DIR like migrate_pdfstring_to_disk.py, so the two
                # always agree on what the marker is relative to.
                data_dir = Path(sources_config.RAW_DIR).parent
                values["raw_api_data"] = {
                    **raw,
                    "pdf_path": str(config.pdf_path(new_vf).relative_to(data_dir)),
                }
            tally.renamed += 1
        await session.execute(update(Document).where(Document.id == doc.id).values(**values))
        tally.changed += 1


async def run(source_name: str | None, limit: int | None, dry_run: bool, report: Path | None) -> None:
    await init_db()
    sources = [source_name] if source_name else list(SOURCES)
    tally = Tally()

    for short_name in sources:
        if short_name not in SOURCES:
            log.warning("%s reads no self-declared verdict type — skipping", short_name)
            continue
        config = get_config(short_name)

        async with _db_conn.AsyncSessionLocal() as session:
            source_id = (
                await session.execute(select(Source.id).where(Source.short_name == short_name))
            ).scalar_one_or_none()
            if source_id is None:
                log.warning("Source %r not in DB — skipping", short_name)
                continue
            ids = (
                await session.execute(
                    select(Document.id).where(Document.source_id == source_id).order_by(Document.id)
                )
            ).scalars().all()
            taken = set(
                (
                    await session.execute(
                        select(Document.verdict_filename)
                        .where(Document.source_id == source_id, Document.verdict_filename.isnot(None))
                    )
                ).scalars().all()
            )
        # Files with no row behind them are just as taken: landsrettur alone
        # carries ~6.000 orphans from a re-import that renamed every document.
        for directory in (config.markdown_path("x").parent, config.pdf_path("x").parent):
            if directory.is_dir():
                taken.update(p.stem for p in directory.iterdir() if p.suffix in (".md", ".pdf"))

        if limit is not None:
            ids = ids[:limit]
        log.info("%s: %d skjöl%s", short_name, len(ids), " (þurrkeyrsla)" if dry_run else "")

        with Progress(
            SpinnerColumn(), "[progress.description]{task.description}", BarColumn(),
            TaskProgressColumn(), TimeElapsedColumn(), refresh_per_second=2,
        ) as progress:
            task = progress.add_task(f"{short_name}…", total=len(ids))
            for start in range(0, len(ids), BATCH_SIZE):
                chunk = ids[start:start + BATCH_SIZE]
                async with _db_conn.AsyncSessionLocal() as session:
                    docs = (
                        await session.execute(select(Document).where(Document.id.in_(chunk)))
                    ).scalars().all()
                    await _process_batch(session, docs, config, taken, tally, dry_run)
                    if not dry_run:
                        await session.commit()
                progress.update(task, advance=len(chunk))

    log.info(
        "lesin %d | óbreytt %d | breytt %d (endurnefnd %d) | óþekkt %d | árekstrar %d | villur %d",
        tally.seen, tally.unchanged, tally.changed, tally.renamed,
        tally.unknown, tally.collisions, tally.render_errors,
    )
    for kind, source, case, date, vt, note, _id in tally.rows:
        if kind in ("árekstur", "villa"):
            log.warning("%s %s %s %s: %s", kind, source, case, date, note)

    if report:
        with report.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["tegund", "heimild", "malsnumer", "dagsetning", "geymd_tegund", "athugasemd", "id"])
            w.writerows(tally.rows)
        log.info("skýrsla: %s (%d línur)", report, len(tally.rows))

    if not dry_run and tally.changed:
        log.info("NEXT: uv run python scripts/backfill_passages.py   # endurbyggir fts_is fyrir breyttu skjölin")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", help="einn heimildarlykill (sjálfgefið: allir þrír)")
    ap.add_argument("--limit", type=int, help="hámarksfjöldi skjala á heimild")
    ap.add_argument("--dry-run", action="store_true", help="engin skrif, aðeins talning og skýrsla")
    ap.add_argument("--report", type=Path, help="CSV með breytingum, óþekktum og árekstrum")
    args = ap.parse_args()
    asyncio.run(run(args.source, args.limit, args.dry_run, args.report))


if __name__ == "__main__":
    main()
