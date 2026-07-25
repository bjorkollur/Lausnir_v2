"""Retry detail fetch for héraðsdómar docs that failed body_text/detail_fetch.

Fetches pdfString + richText from island.is Next.js API for docs where
detail_fetch validation error is present (or body_text is missing).
Re-extracts, re-validates, updates DB, and writes .md files.

Does NOT re-fetch the list page — uses raw_api_data already in DB.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
from pathlib import Path

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import func as sa_func, null as sa_null

import engine.database.connection as _db_conn
from engine.config.sources import get_config
from engine.database.connection import init_db
from engine.database.models import Document, Source
from engine.processors.extractor import Extractor
from engine.processors.http_utils import get_with_retry, make_client
from engine.processors.renderer import unique_verdict_filename, verdict_filename, write_markdown
from engine.processors.validator import validate

log = logging.getLogger(__name__)

_DETAIL_URL = "https://island.is/_next/data/{build_id}/domar/{verdict_id}.json"


async def _get_build_id(client) -> str:
    resp = await get_with_retry(client, "https://island.is/domar")
    html = resp.text
    marker = '"buildId":"'
    start = html.find(marker)
    if start == -1:
        raise ValueError("Could not find buildId in island.is/domar response")
    start += len(marker)
    end = html.index('"', start)
    build_id = html[start:end]
    if not build_id:
        raise ValueError("Empty buildId")
    return build_id


async def _fetch_detail(client, build_id: str, verdict_id: str) -> dict:
    url = _DETAIL_URL.format(build_id=build_id, verdict_id=verdict_id)
    resp = await get_with_retry(client, url)
    return resp.json()["pageProps"]["pageProps"]["pageProps"]["componentProps"]["item"]


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    config = get_config("heradsdomstolar")
    await init_db()

    async with _db_conn.AsyncSessionLocal() as session:
        src = (await session.execute(
            select(Source).where(Source.short_name == "heradsdomstolar")
        )).scalar_one()

        result = await session.execute(
            select(Document).where(
                Document.source_id == src.id,
                or_(
                    Document.body_text == None,
                    Document.body_text == "",
                ),
            )
        )
        docs = result.scalars().all()

    log.info("Found %d docs with missing body_text", len(docs))

    succeeded = 0
    failed = 0

    async with make_client() as client:
        build_id = await _get_build_id(client)
        log.info("build_id: %s", build_id)

        for i, doc in enumerate(docs):
            # Refresh build_id every 100 docs
            if i > 0 and i % 100 == 0:
                try:
                    build_id = await _get_build_id(client)
                    log.info("Refreshed build_id at doc %d", i)
                except Exception as exc:
                    log.warning("build_id refresh failed at doc %d: %s", i, exc)

            try:
                detail = await _fetch_detail(client, build_id, doc.external_id)
            except Exception as exc:
                log.warning("Detail fetch still failing for %s: %s", doc.external_id, exc)
                failed += 1
                if (i + 1) % 50 == 0:
                    log.info("Progress: %d / %d — succeeded: %d, still failing: %d",
                             i + 1, len(docs), succeeded, failed)
                continue

            list_item = dict(doc.raw_api_data or {})
            raw = {
                **list_item,
                "pdfString": detail.get("pdfString"),
                "richText": detail.get("richText"),
                "resolutionLink": detail.get("resolutionLink"),
            }

            fields = Extractor(config).extract(raw)

            # Only update if we actually got body_text this time
            new_body = fields.get("body_text")
            if not new_body:
                log.warning("Detail fetched but still no body_text for %s", doc.external_id)
                failed += 1
                if (i + 1) % 50 == 0:
                    log.info("Progress: %d / %d — succeeded: %d, still failing: %d",
                             i + 1, len(docs), succeeded, failed)
                continue

            # Apply new fields to doc for re-validation
            for k, v in fields.items():
                setattr(doc, k, v)
            doc.raw_api_data = raw

            errors = validate(doc, config)
            doc.validation_errors = errors or None

            async with _db_conn.AsyncSessionLocal() as session:
                await session.execute(
                    update(Document)
                    .where(Document.id == doc.id)
                    .values(
                        raw_api_data=raw,
                        body_text=fields.get("body_text"),
                        lower_body_text=fields.get("lower_body_text"),
                        summary=fields.get("summary"),
                        plaintiffs=fields.get("plaintiffs"),
                        defendants=fields.get("defendants"),
                        keywords=fields.get("keywords"),
                        document_date=fields.get("document_date"),
                        validation_errors=errors or None,
                        updated_at=sa_func.now(),
                    )
                )
                await session.commit()

            # Write .md and compute verdict_filename
            vf = None
            try:
                write_markdown(doc, config)
                vf = verdict_filename(doc, config)
            except Exception as exc:
                log.warning("write_markdown failed for %s: %s", doc.external_id, exc)

            # Save PDF bytes
            pdf_b64 = raw.get("pdfString")
            if pdf_b64:
                try:
                    pdf_path = config.pdf_path(vf or doc.external_id)
                    pdf_path.parent.mkdir(parents=True, exist_ok=True)
                    pdf_path.write_bytes(base64.b64decode(pdf_b64))
                except Exception as exc:
                    log.warning("PDF save failed for %s: %s", doc.external_id, exc)

            if vf:
                async with _db_conn.AsyncSessionLocal() as session:
                    await session.execute(
                        update(Document)
                        .where(Document.id == doc.id)
                        .values(verdict_filename=vf)
                    )
                    await session.commit()

            succeeded += 1
            if (i + 1) % 50 == 0:
                log.info("Progress: %d / %d — succeeded: %d, still failing: %d",
                         i + 1, len(docs), succeeded, failed)

    log.info("Done. %d succeeded, %d still failing.", succeeded, failed)


if __name__ == "__main__":
    asyncio.run(main())
