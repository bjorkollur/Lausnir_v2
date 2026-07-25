"""Re-render .md files for all documents from existing DB body_text.

Does NOT re-extract body_text — use backfill_landsrettur_body.py for that.
Useful when renderer.py or markdown format changes without body_text changes.

Usage:
    uv run python scripts/backfill_render_all.py
    uv run python scripts/backfill_render_all.py --source haestirettur
    uv run python scripts/backfill_render_all.py --source haestirettur --limit 100
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from engine.config.sources import SOURCE_REGISTRY, get_config
import engine.database.connection as _db_conn
from engine.database.connection import init_db
from engine.database.models import Document, Source
from engine.processors.renderer import verdict_filename, write_markdown

log = logging.getLogger(__name__)


async def _render_batch(
    session: AsyncSession,
    docs: list[Document],
    config,
) -> tuple[int, int]:
    """Re-render .md for a batch of documents. Returns (updated, errors)."""
    updated = 0
    errors = 0

    for doc in docs:
        if not doc.case_number:
            continue
        try:
            vf = verdict_filename(doc, config)
            write_markdown(doc, config)
        except Exception as exc:
            log.warning("write_markdown failed for %s: %s", doc.external_id, exc)
            errors += 1
            continue

        await session.execute(
            update(Document)
            .where(Document.id == doc.id)
            .values(verdict_filename=vf)
        )
        updated += 1

    return updated, errors


async def main(
    source_name: str | None = None,
    limit: int | None = None,
    batch_size: int = 500,
) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    await init_db()

    sources_to_process = (
        [source_name] if source_name else list(SOURCE_REGISTRY.keys())
    )

    for sname in sources_to_process:
        config = get_config(sname)

        async with _db_conn.AsyncSessionLocal() as session:
            src_result = await session.execute(
                select(Source).where(Source.short_name == sname)
            )
            source = src_result.scalar_one_or_none()
            if source is None:
                log.warning("Source %r not found in DB — skipping", sname)
                continue
            source_id = source.id

            count_result = await session.execute(
                select(func.count())
                .select_from(Document)
                .where(Document.source_id == source_id)
            )
            total = count_result.scalar()

        if limit is not None:
            total = min(total, limit)

        log.info("Re-rendering %d %s documents (batch_size=%d)", total, sname, batch_size)

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
            task_id = progress.add_task(f"Rendering {sname}…", total=total)

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
                    updated, errors = await _render_batch(session, docs, config)
                    await session.commit()

                total_updated += updated
                total_errors += errors
                offset += len(docs)

                progress.update(
                    task_id,
                    advance=len(docs),
                    description=(
                        f"Rendering {sname}  "
                        f"[{offset}/{total}  Errors: {total_errors}]"
                    ),
                )

        log.info("Done %s. %d rendered, %d errors.", sname, total_updated, total_errors)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Re-render .md for all sources.")
    parser.add_argument("--source", default=None, help="Only process this source")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()
    asyncio.run(main(source_name=args.source, limit=args.limit, batch_size=args.batch_size))
