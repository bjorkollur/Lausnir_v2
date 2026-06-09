"""Re-extract body_text for all héraðsdómar docs using the updated extractor.

Fixes documents where the preamble stripper incorrectly consumed the full body
(Structure B: verdict-type heading at top + same heading as dispositional near end).

Reads PDFs from raw_api_data (already in DB). Updates body_text, lower_body_text,
validation_errors, and re-renders .md for any doc where body_text changes.

Safe to re-run: only writes when body_text actually differs.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from sqlalchemy import select, update, func as sa_func, text

import engine.database.connection as _db_conn
from engine.config.sources import get_config
from engine.database.connection import init_db
from engine.database.models import Document, Source
from engine.processors.extractor import Extractor
from engine.processors.renderer import verdict_filename, write_markdown
from engine.processors.validator import validate

log = logging.getLogger(__name__)

_BATCH = 500


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    config = get_config("heradsdomstolar")
    data_dir = os.environ.get("DATA_DIR", "/Volumes/RuleOfLaw/Lausnir_Data")
    await init_db()

    async with _db_conn.AsyncSessionLocal() as session:
        src = (await session.execute(
            select(Source).where(Source.short_name == "heradsdomstolar")
        )).scalar_one()
        result = await session.execute(
            select(Document).where(Document.source_id == src.id)
        )
        docs = result.scalars().all()

    log.info("Loaded %d docs", len(docs))

    changed = 0
    skipped = 0

    for i, doc in enumerate(docs):
        raw = doc.raw_api_data or {}
        if not raw.get("pdfString"):
            skipped += 1
            continue

        fields = Extractor(config).extract(raw)
        new_body = fields.get("body_text") or ""
        old_body = doc.body_text or ""

        if new_body == old_body:
            if (i + 1) % _BATCH == 0:
                log.info("Progress: %d / %d — changed: %d, same: %d, skipped: %d",
                         i + 1, len(docs), changed, i + 1 - changed - skipped, skipped)
            continue

        # body_text changed — update DB
        doc.body_text = new_body
        doc.lower_body_text = fields.get("lower_body_text")
        errors = validate(doc, config)
        doc.validation_errors = errors or None

        async with _db_conn.AsyncSessionLocal() as session:
            await session.execute(
                update(Document).where(Document.id == doc.id).values(
                    body_text=new_body or None,
                    lower_body_text=fields.get("lower_body_text"),
                    validation_errors=errors or None,
                    updated_at=sa_func.now(),
                )
            )
            await session.commit()

        # Re-render .md
        vf = None
        try:
            write_markdown(doc, config)
            vf = verdict_filename(doc, config)
        except Exception as exc:
            log.warning("write_markdown failed for %s: %s", doc.external_id, exc)

        if vf:
            async with _db_conn.AsyncSessionLocal() as session:
                await session.execute(
                    update(Document).where(Document.id == doc.id).values(
                        verdict_filename=vf
                    )
                )
                await session.commit()

        changed += 1
        log.info("Updated %s: %d → %d chars", doc.case_number, len(old_body), len(new_body))

        if (i + 1) % _BATCH == 0:
            log.info("Progress: %d / %d — changed: %d, same: %d, skipped: %d",
                     i + 1, len(docs), changed, i + 1 - changed - skipped, skipped)

    log.info("Done. %d docs updated, %d unchanged, %d skipped (no PDF).",
             changed, len(docs) - changed - skipped, skipped)


if __name__ == "__main__":
    asyncio.run(main())
