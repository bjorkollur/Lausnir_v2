"""Re-extract parties for héraðsdómar docs that have null plaintiffs/defendants.

For older docs (2006–2018), the island.is API title is a prose summary so
_parse_parties_role_based returns []. The extractor now has two additional fallbacks:
  1. _extract_parties_from_heradsdomstolar_preamble — ## gegn ## heading structure
  2. _extract_parties_from_body_start — intro sentence in old-format body text

This script runs those fallbacks by re-running Extractor on raw_api_data for
every doc where plaintiffs is JSON null, then re-renders the markdown.

Does NOT call the island.is API — re-extracts from raw_api_data already in DB.
"""
from __future__ import annotations

import asyncio
import logging
import os

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

import engine.database.connection as _db_conn
from engine.config.sources import get_config
from engine.database.connection import init_db
from engine.database.models import Document, Source
from engine.processors.extractor import Extractor
from engine.processors.renderer import write_markdown
from engine.processors.validator import validate

log = logging.getLogger(__name__)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    config = get_config("heradsdomstolar")
    data_dir = os.environ.get("DATA_DIR", "/Volumes/RuleOfLaw/Lausnir_Data")
    await init_db()

    async with _db_conn.AsyncSessionLocal() as session:
        src = (await session.execute(
            select(Source).where(Source.short_name == "heradsdomstolar")
        )).scalar_one()

        # Target: docs with no parties — either JSONB null or empty array [].
        from sqlalchemy import cast, Text, or_
        result = await session.execute(
            select(Document).where(
                Document.source_id == src.id,
                or_(
                    cast(Document.plaintiffs, Text) == 'null',
                    cast(Document.plaintiffs, Text) == '[]',
                ),
            )
        )
        all_docs = result.scalars().all()

    log.info("Found %d docs with null parties to process", len(all_docs))

    updated = 0
    got_parties = 0
    skipped = 0

    for i, doc in enumerate(all_docs):
        raw = doc.raw_api_data or {}
        if not raw.get("pdfString"):
            skipped += 1
            continue

        fields = Extractor(config).extract(raw)

        new_plf = fields.get("plaintiffs") or []
        new_dfd = fields.get("defendants") or []

        # Store whatever we found (even empty lists) so this doc doesn't
        # re-appear in future runs.  Storing [] marks it as "attempted, none
        # found" — cast(plaintiffs, text) == '[]', not 'null', so it won't
        # match the null query again.
        doc.plaintiffs = new_plf or None  # keep None in ORM for validation
        doc.defendants = new_dfd or None
        errors = validate(doc, config)
        doc.validation_errors = errors or None

        async with _db_conn.AsyncSessionLocal() as session:
            await session.execute(
                update(Document)
                .where(Document.id == doc.id)
                .values(
                    plaintiffs=new_plf,   # store [] not null — breaks the loop
                    defendants=new_dfd,
                    validation_errors=errors or None,
                )
            )
            await session.commit()

        # Re-render markdown only if we found something meaningful
        if doc.body_text and (new_plf or new_dfd):
            write_markdown(doc, config, data_dir)

        if new_plf or new_dfd:
            got_parties += 1
        updated += 1
        if updated % 500 == 0:
            log.info(
                "Progress: %d / %d — got parties: %d, skipped (no PDF): %d",
                i + 1, len(all_docs), got_parties, skipped,
            )

    log.info(
        "Done. %d docs now have parties, %d had no parties extractable, %d skipped (no PDF).",
        got_parties, len(all_docs) - got_parties - skipped, skipped,
    )


if __name__ == "__main__":
    asyncio.run(main())
