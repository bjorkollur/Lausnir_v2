"""Rename all .md files to the canonical filename format.

Old format:  {case_number}.md              e.g.  385-2017.md
New format:  {court}_{case}_{DD-MM-YYYY}.md  e.g.  Hrd_385-2017_01-11-2018.md

Renames the file on disk (if it exists) and updates ``markdown_path`` in the DB.
Skips docs where the path is already in the new format.  Safe to re-run.

Usage:
    uv run python scripts/migrate_md_filenames.py
    uv run python scripts/migrate_md_filenames.py --dry-run   # preview only
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from sqlalchemy import select, update

from engine.config.sources import SOURCE_REGISTRY, get_config
import engine.database.connection as _db_conn
from engine.database.connection import init_db
from engine.database.models import Document, Source
from engine.processors.renderer import _md_filename

log = logging.getLogger(__name__)
_DATA_DIR = os.environ.get("DATA_DIR", "/Volumes/RuleOfLaw/Lausnir_Data")


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
            select(Document)
            .where(Document.source_id == src.id)
            .where(Document.markdown_path.isnot(None))
            .order_by(Document.external_id)
        )
        docs = result.scalars().all()

    out_dir = Path(_DATA_DIR) / "markdown" / short_name

    updates: list[tuple[object, Path]] = []  # (doc.id, new_path)
    for doc in docs:
        new_name = _md_filename(doc, config)
        new_path = out_dir / new_name

        old_path = Path(doc.markdown_path) if doc.markdown_path else None

        # Already using the new name?
        if old_path and old_path == new_path:
            already_ok += 1
            continue

        # Rename file on disk if the old path exists
        if old_path and old_path.exists():
            if not dry_run:
                old_path.rename(new_path)
            log.info("  %s → %s", old_path.name, new_name)
            renamed += 1
        elif new_path.exists():
            # File already has the new name but DB still points to old path
            log.info("  (file already renamed) updating DB: %s", new_name)
            renamed += 1
        else:
            log.debug("  missing: %s", old_path)
            missing += 1

        updates.append((doc.id, new_path))

    # Bulk-update the DB
    if updates and not dry_run:
        async with _db_conn.AsyncSessionLocal() as session:
            for doc_id, new_path in updates:
                await session.execute(
                    update(Document)
                    .where(Document.id == doc_id)
                    .values(markdown_path=str(new_path))
                )
            await session.commit()

    return renamed, already_ok, missing


async def main(dry_run: bool = False) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
    )
    await init_db()

    sources = [sn for sn in SOURCE_REGISTRY if sn in ("haestirettur", "landsrettur")]
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
