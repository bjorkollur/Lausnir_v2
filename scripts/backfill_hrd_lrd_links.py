"""Backfill Hæstiréttur ↔ lægra dómstig document_links using resolutionLink field.

Hæstiréttur raw_api_data contains a `resolutionLink` field of the form
  https://island.is/domar/<uuid>
where <uuid> is the external_id of either a Landsréttur or Héraðsdóm document.

Creates:
  - haestirettur → landsrettur/heradsdomstolar  appealed_to   (resolution_link, 1.0)
  - landsrettur/heradsdomstolar → haestirettur  appealed_from (resolution_link, 1.0)
  - chain_inference links if Lrd already has a Hérd → Lrd link

Usage:
    uv run python scripts/backfill_hrd_lrd_links.py
    uv run python scripts/backfill_hrd_lrd_links.py --dry-run
"""
from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text

import engine.database.connection as _db_conn
from engine.database.connection import init_db

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)


async def backfill(dry_run: bool = False) -> None:
    await init_db()

    async with _db_conn.AsyncSessionLocal() as session:
        # Find all Hrd docs with non-empty resolutionLink matching any lower court doc
        pairs = (await session.execute(text("""
            WITH hrd_links AS (
                SELECT
                    d.id      AS hrd_id,
                    d.case_number AS hrd_case,
                    CASE
                        WHEN raw_api_data->>'resolutionLink' LIKE '%?id=%'
                            THEN split_part(split_part(raw_api_data->>'resolutionLink', '?id=', -1), '&', 1)
                        ELSE split_part(raw_api_data->>'resolutionLink', '/', -1)
                    END AS lower_ext_id
                FROM documents d
                JOIN sources s ON d.source_id = s.id
                WHERE s.short_name = 'haestirettur'
                AND raw_api_data->>'resolutionLink' IS NOT NULL
                AND raw_api_data->>'resolutionLink' != ''
            ),
            lower_docs AS (
                SELECT d.id AS lower_id, d.external_id, d.case_number AS lower_case,
                       s.short_name AS lower_source
                FROM documents d
                JOIN sources s ON d.source_id = s.id
                WHERE s.short_name IN ('landsrettur', 'heradsdomstolar', 'felagsdomur')
            )
            SELECT h.hrd_id, h.hrd_case, l.lower_id, l.lower_case, l.lower_source
            FROM hrd_links h
            JOIN lower_docs l ON h.lower_ext_id = l.external_id
        """))).fetchall()

    log.info("Found %d Hrd↔lægra dómstig pairs via resolutionLink", len(pairs))

    if dry_run:
        for row in pairs[:10]:
            log.info("  [dry-run] Hrd %s → %s %s", row[1], row[4], row[3])
        log.info("  [dry-run] ... (showing first 10 of %d)", len(pairs))
        return

    inserted = 0
    skipped = 0
    chain_added = 0

    async with _db_conn.AsyncSessionLocal() as session:
        # Load existing document_links to avoid duplicates
        existing = set(
            (r[0], r[1])
            for r in (await session.execute(text(
                "SELECT from_doc_id, to_doc_id FROM document_links"
            ))).fetchall()
        )

        # Load Hérd→Lrd links for chain inference: map lrd_id → herd_id
        # (Hérd is from_doc_id, Lrd is to_doc_id, relation='appealed_to')
        lrd_to_herd = {
            r[0]: r[1]
            for r in (await session.execute(text("""
                SELECT dl.to_doc_id AS lrd_id, dl.from_doc_id AS herd_id
                FROM document_links dl
                JOIN documents herd ON dl.from_doc_id = herd.id
                JOIN sources sh ON herd.source_id = sh.id
                WHERE sh.short_name = 'heradsdomstolar'
                AND dl.relation = 'appealed_to'
            """))).fetchall()
        }
        log.info("Loaded %d existing links, %d Hérd→Lrd chain candidates", len(existing), len(lrd_to_herd))

        for hrd_id, hrd_case, lower_id, lower_case, lower_source in pairs:
            hrd_id = uuid.UUID(str(hrd_id))
            lower_id = uuid.UUID(str(lower_id))

            # Hrd → lower  appealed_to
            if (hrd_id, lower_id) not in existing:
                await session.execute(text("""
                    INSERT INTO document_links (id, from_doc_id, to_doc_id, relation, confidence, method)
                    VALUES (:id, :from_id, :to_id, 'appealed_to', 1.0, 'resolution_link')
                    ON CONFLICT DO NOTHING
                """), {"id": uuid.uuid4(), "from_id": hrd_id, "to_id": lower_id})
                existing.add((hrd_id, lower_id))
                inserted += 1
            else:
                skipped += 1

            # lower → Hrd  appealed_from
            if (lower_id, hrd_id) not in existing:
                await session.execute(text("""
                    INSERT INTO document_links (id, from_doc_id, to_doc_id, relation, confidence, method)
                    VALUES (:id, :from_id, :to_id, 'appealed_from', 1.0, 'resolution_link')
                    ON CONFLICT DO NOTHING
                """), {"id": uuid.uuid4(), "from_id": lower_id, "to_id": hrd_id})
                existing.add((lower_id, hrd_id))
                inserted += 1

            # Chain: if lower is Lrd and it has a Hérd link, add Hrd → Hérd
            if lower_source != 'landsrettur':
                continue
            lrd_id = lower_id
            herd_id = lrd_to_herd.get(lrd_id)
            if herd_id:
                herd_id = uuid.UUID(str(herd_id))
                if (hrd_id, herd_id) not in existing:
                    await session.execute(text("""
                        INSERT INTO document_links (id, from_doc_id, to_doc_id, relation, confidence, method)
                        VALUES (:id, :from_id, :to_id, 'appealed_to', 0.95, 'chain_inference')
                        ON CONFLICT DO NOTHING
                    """), {"id": uuid.uuid4(), "from_id": hrd_id, "to_id": herd_id})
                    existing.add((hrd_id, herd_id))
                    chain_added += 1
                if (herd_id, hrd_id) not in existing:
                    await session.execute(text("""
                        INSERT INTO document_links (id, from_doc_id, to_doc_id, relation, confidence, method)
                        VALUES (:id, :from_id, :to_id, 'appealed_from', 0.95, 'chain_inference')
                        ON CONFLICT DO NOTHING
                    """), {"id": uuid.uuid4(), "from_id": herd_id, "to_id": hrd_id})
                    existing.add((herd_id, hrd_id))
                    chain_added += 1

        await session.commit()

    log.info("Done: %d links inserted, %d skipped (already existed), %d chain links", inserted, skipped, chain_added)

    # Summary
    async with _db_conn.AsyncSessionLocal() as session:
        total = (await session.execute(text("SELECT count(*) FROM document_links"))).scalar()
        by_method = (await session.execute(text("""
            SELECT method, relation, count(*) FROM document_links
            GROUP BY 1, 2 ORDER BY 1, 2
        """))).fetchall()

    log.info("Total document_links: %d", total)
    for row in by_method:
        log.info("  %-20s %-16s %d", row[0], row[1], row[2])


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(backfill(dry_run=args.dry_run))
