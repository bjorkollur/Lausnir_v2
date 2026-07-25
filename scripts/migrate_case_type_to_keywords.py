"""Migrate case_type column into keywords array and drop the column.

- haestirettur: appends existing case_type value as-is to keywords
- landsrettur:  normalises to Hæstiréttur form before appending
- logfraediritgerdir: appends degree label (Meistara ritgerð etc.) to keywords
- All other sources: case_type was NULL — no action needed

Run once, then drop the column.

Usage:
    uv run python scripts/migrate_case_type_to_keywords.py
    uv run python scripts/migrate_case_type_to_keywords.py --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text

import engine.database.connection as _db_conn
from engine.database.connection import init_db

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

# Landsréttur API labels → normalised keyword (same form as Hæstiréttur)
_LRD_NORMALISE = {
    "Einkamál áfrýjað": "Áfrýjað einkamál",
    "Einkamál kært":    "Kært einkamál",
    "Sakamál áfrýjað":  "Áfrýjað sakamál",
    "Sakamál kært":     "Kært sakamál",
}


async def migrate(dry_run: bool = False) -> None:
    await init_db()

    async with _db_conn.AsyncSessionLocal() as session:
        # 1. haestirettur — append case_type directly
        r = await session.execute(text("""
            UPDATE documents d
            SET keywords = CASE
                WHEN keywords IS NULL
                    THEN jsonb_build_array(d.case_type)
                WHEN keywords @> to_jsonb(d.case_type)
                    THEN keywords
                ELSE keywords || jsonb_build_array(d.case_type)
            END
            FROM sources s
            WHERE d.source_id = s.id
              AND s.short_name = 'haestirettur'
              AND d.case_type IS NOT NULL
            RETURNING d.id
        """))
        hrd_count = len(r.fetchall())
        log.info("haestirettur: %d docs updated", hrd_count)

        # 2. landsrettur — normalise then append
        for old, new in _LRD_NORMALISE.items():
            r = await session.execute(text("""
                UPDATE documents d
                SET keywords = CASE
                    WHEN keywords IS NULL
                        THEN jsonb_build_array(CAST(:new AS text))
                    WHEN keywords @> to_jsonb(CAST(:new AS text))
                        THEN keywords
                    ELSE keywords || jsonb_build_array(CAST(:new AS text))
                END
                FROM sources s
                WHERE d.source_id = s.id
                  AND s.short_name = 'landsrettur'
                  AND d.case_type = :old
                RETURNING d.id
            """), {"old": old, "new": new})
            count = len(r.fetchall())
            log.info("landsrettur '%s' → '%s': %d docs", old, new, count)

        # 3. logfraediritgerdir — append degree label
        r = await session.execute(text("""
            UPDATE documents d
            SET keywords = CASE
                WHEN keywords IS NULL
                    THEN jsonb_build_array(d.case_type)
                WHEN keywords @> to_jsonb(d.case_type)
                    THEN keywords
                ELSE keywords || jsonb_build_array(d.case_type)
            END
            FROM sources s
            WHERE d.source_id = s.id
              AND s.short_name = 'logfraediritgerdir'
              AND d.case_type IS NOT NULL
            RETURNING d.id
        """))
        log_count = len(r.fetchall())
        log.info("logfraediritgerdir: %d docs updated", log_count)

        if dry_run:
            log.info("[dry-run] Rolling back.")
            await session.rollback()
            return

        await session.commit()
        log.info("Committed.")

        # 4. Drop the column
        await session.execute(text("ALTER TABLE documents DROP COLUMN IF EXISTS case_type"))
        await session.commit()
        log.info("case_type column dropped.")

    # Verify
    async with _db_conn.AsyncSessionLocal() as session:
        rows = (await session.execute(text("""
            SELECT s.short_name, kw.value, count(*)
            FROM documents d
            JOIN sources s ON d.source_id = s.id
            JOIN LATERAL jsonb_array_elements_text(d.keywords) kw ON true
            WHERE s.short_name IN ('haestirettur', 'landsrettur', 'logfraediritgerdir')
              AND kw.value IN (
                  'Áfrýjað einkamál', 'Kært einkamál',
                  'Áfrýjað sakamál', 'Kært sakamál',
                  'Meistara ritgerð', 'Doktors ritgerð',
                  'Bakkalár ritgerð', 'Undergraduate diploma ritgerð',
                  'Graduate diploma ritgerð'
              )
            GROUP BY 1, 2
            ORDER BY 1, 3 DESC
        """))).all()

    log.info("\n=== Niðurstaða — keywords ===")
    for row in rows:
        log.info("  %-20s %-35s %d", row[0], row[1], row[2])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without writing to DB")
    args = parser.parse_args()
    asyncio.run(migrate(dry_run=args.dry_run))
