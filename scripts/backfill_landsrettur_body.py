"""Backfill body_text and lower_body_text for all Landsréttur documents.

Re-runs the PDF extraction (fixed extractor) on all existing Landsréttur
documents and regenerates their markdown files.  Other NORM fields are left
unchanged — only body_text, lower_body_text, validation_errors, and
markdown_path are updated.

Usage:
    uv run python scripts/backfill_landsrettur_body.py
    uv run python scripts/backfill_landsrettur_body.py --limit 50  # dry-run
    uv run python scripts/backfill_landsrettur_body.py --batch-size 100
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from engine.config.sources import get_config
import engine.database.connection as _db_conn
from engine.database.connection import init_db
from engine.database.models import Document, Source
from engine.processors.extractor import Extractor
from engine.processors.renderer import verdict_filename, write_markdown
from engine.processors.validator import validate

log = logging.getLogger(__name__)

_DATA_DIR = os.environ.get("DATA_DIR", "/Volumes/RuleOfLaw/Lausnir_Data")


async def _backfill_batch(
    session: AsyncSession,
    docs: list[Document],
    config,
) -> tuple[int, int]:
    """Re-extract and re-render a batch of documents. Returns (updated, errors)."""
    extractor = Extractor(config)
    updated = 0
    errors = 0

    for doc in docs:
        raw = doc.raw_api_data or {}
        try:
            fields = extractor.extract(raw)
        except Exception as exc:
            log.warning("Extractor failed for %s: %s", doc.external_id, exc)
            errors += 1
            continue

        new_body = fields.get("body_text")
        new_lower = fields.get("lower_body_text")

        # Build a temporary Document for validation and rendering
        tmp = Document(
            id=doc.id,
            source_id=doc.source_id,
            external_id=doc.external_id,
            url=doc.url,
            raw_api_data=raw,
            case_number=doc.case_number,
            document_date=doc.document_date,
            court=doc.court,
            verdict_type=doc.verdict_type,
            instance_tier=doc.instance_tier,
            plaintiffs=doc.plaintiffs,
            defendants=doc.defendants,
            keywords=doc.keywords,
            summary=doc.summary,
            body_text=new_body,
            lower_body_text=new_lower,
        )
        val_errors = validate(tmp, config)
        tmp.validation_errors = val_errors or None

        # Render markdown
        vf: str | None = None
        try:
            write_markdown(tmp, config)
            vf = verdict_filename(tmp, config)
        except Exception as exc:
            log.warning("write_markdown failed for %s: %s", doc.external_id, exc)

        # Update only the fields we changed
        await session.execute(
            update(Document)
            .where(Document.id == doc.id)
            .values(
                body_text=new_body,
                lower_body_text=new_lower,
                validation_errors=val_errors or None,
                **({"verdict_filename": vf} if vf else {}),
            )
        )
        updated += 1

    return updated, errors


async def main(limit: int | None = None, batch_size: int = 200) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    config = get_config("landsrettur")
    await init_db()

    async with _db_conn.AsyncSessionLocal() as session:
        src_result = await session.execute(
            select(Source).where(Source.short_name == "landsrettur")
        )
        source = src_result.scalar_one()
        source_id = source.id

        # Count total
        from sqlalchemy import func
        count_result = await session.execute(
            select(func.count()).select_from(Document).where(Document.source_id == source_id)
        )
        total = count_result.scalar()

    if limit is not None:
        total = min(total, limit)
    log.info("Backfilling %d Landsréttur documents (batch_size=%d)", total, batch_size)

    total_updated = 0
    total_errors = 0
    offset = 0

    with Progress(
        SpinnerColumn(),
        "[progress.description]{task.description}",
        BarColumn(),
        "[progress.percentage]{task.percentage:>3.0f}%",
        TaskProgressColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        refresh_per_second=2,
    ) as progress:
        task_id = progress.add_task("Backfilling landsrettur body_text…", total=total)

        while offset < total:
            batch_limit = min(batch_size, total - offset)

            async with _db_conn.AsyncSessionLocal() as session:
                result = await session.execute(
                    select(Document)
                    .where(Document.source_id == source_id)
                    .order_by(Document.external_id)
                    .offset(offset)
                    .limit(batch_limit)
                )
                docs = result.scalars().all()

            if not docs:
                break

            async with _db_conn.AsyncSessionLocal() as session:
                updated, errors = await _backfill_batch(session, docs, config)
                await session.commit()

            total_updated += updated
            total_errors += errors
            offset += len(docs)

            progress.update(
                task_id,
                advance=len(docs),
                description=(
                    f"Backfilling landsrettur  "
                    f"[{offset}/{total}  Errors: {total_errors}]"
                ),
            )

    log.info(
        "Done. %d documents updated, %d errors.",
        total_updated,
        total_errors,
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Backfill Landsréttur body_text.")
    parser.add_argument("--limit", type=int, default=None, help="Process at most N docs")
    parser.add_argument("--batch-size", type=int, default=200, help="DB batch size")
    args = parser.parse_args()
    asyncio.run(main(limit=args.limit, batch_size=args.batch_size))
