"""Rename all .md files to the canonical filename format and update verdict_filename in DB.

Safe to re-run — skips docs where verdict_filename already matches.

Usage:
    uv run python scripts/migrate_md_filenames.py
    uv run python scripts/migrate_md_filenames.py --dry-run   # preview only
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from sqlalchemy import Row, select, update

from engine.config.sources import SOURCE_REGISTRY, get_config
import engine.database.connection as _db_conn
from engine.database.connection import init_db
from engine.database.models import Document, Source
from engine.processors.renderer import verdict_filename as compute_vf

log = logging.getLogger(__name__)


async def migrate_source(short_name: str, dry_run: bool) -> tuple[int, int, int]:
    """Rename files for one source.  Returns (renamed, already_ok, missing)."""
    config = get_config(short_name)
    renamed = already_ok = missing = 0

    async with _db_conn.AsyncSessionLocal() as session:
        src = (
            await session.execute(
                select(Source).where(Source.short_name == short_name)
            )
        ).scalar_one_or_none()
        if src is None:
            log.warning("Source not found in DB: %s", short_name)
            return 0, 0, 0

        result = await session.execute(
            select(
                Document.id,
                Document.court,
                Document.case_number,
                Document.document_date,
                Document.verdict_type,
                Document.verdict_filename,
            )
            .where(Document.source_id == src.id)
            .order_by(Document.external_id)
        )
        docs: list[Row] = result.all()

    updates: list[tuple[object, str]] = []  # (doc.id, new_vf)
    for doc in docs:
        new_vf = compute_vf(doc, config)
        old_vf = doc.verdict_filename

        if old_vf == new_vf:
            already_ok += 1
            continue

        # Rename .md on disk if old file exists
        if old_vf:
            old_path = config.markdown_path(old_vf)
            new_path = config.markdown_path(new_vf)
            if old_path.exists():
                if not dry_run:
                    old_path.rename(new_path)
                log.info("  %s → %s", old_vf, new_vf)
                renamed += 1
            elif new_path.exists():
                log.info("  (already renamed on disk) updating DB: %s", new_vf)
                renamed += 1
            else:
                log.debug("  missing: %s", old_vf)
                missing += 1
        else:
            missing += 1

        updates.append((doc.id, new_vf))

    if updates and not dry_run:
        async with _db_conn.AsyncSessionLocal() as session:
            for doc_id, new_vf in updates:
                await session.execute(
                    update(Document)
                    .where(Document.id == doc_id)
                    .values(verdict_filename=new_vf)
                )
            await session.commit()

    return renamed, already_ok, missing


async def main(dry_run: bool = False) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
    )
    await init_db()

    sources = [sn for sn in SOURCE_REGISTRY if sn in ("haestirettur", "landsrettur", "heradsdomstolar")]
    for short_name in sources:
        log.info("=== %s ===", short_name)
        renamed, ok, missing = await migrate_source(short_name, dry_run)
        log.info(
            "  renamed=%d  already_ok=%d  missing=%d%s",
            renamed, ok, missing,
            "  (DRY RUN — no changes written)" if dry_run else "",
        )


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
