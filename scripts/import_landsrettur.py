"""Import all Landsréttur verdicts from island.is GraphQL into lausnir_v2."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import os
import uuid
from pathlib import Path
from typing import Any

import httpx
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from sqlalchemy import func, null as sa_null, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from engine.config.sources import SourceConfig, get_config
import engine.database.connection as _db_conn
from engine.database.connection import init_db
from engine.database.models import Document, Source
from engine.processors.extractor import Extractor
from engine.processors.http_utils import get_with_retry, make_client, post_with_retry
from engine.processors.renderer import unique_verdict_filename, verdict_filename, write_markdown
from engine.processors.validator import validate

log = logging.getLogger(__name__)

_GQL_ENDPOINT = "https://island.is/api/graphql"
_GQL_QUERY = """
query GetVerdicts($input: WebVerdictsInput!) {
  webVerdicts(input: $input) {
    total
    items {
      id title court caseNumber verdictDate keywords presentings
    }
  }
}
"""

# NOTE: GraphQL API requires "Landsrettur" (no accents) — "Landsréttur" returns 0 results
_COURT_FILTER = "Landsrettur"

_CHECKPOINT_DIR = Path("checkpoints")
_CHECKPOINT_FILE = _CHECKPOINT_DIR / "landsrettur.json"


# ── Checkpoint ────────────────────────────────────────────────────────────────

def _load_checkpoint() -> tuple[int, int, int]:
    """Return (start_page, total_pages, imported_count). Defaults to (1, 0, 0)."""
    if _CHECKPOINT_FILE.exists():
        data = json.loads(_CHECKPOINT_FILE.read_text())
        return data["last_completed_page"] + 1, data["total_pages"], data["imported"]
    return 1, 0, 0


def _save_checkpoint(page: int, total_pages: int, imported: int) -> None:
    _CHECKPOINT_DIR.mkdir(exist_ok=True)
    _CHECKPOINT_FILE.write_text(
        json.dumps(
            {"last_completed_page": page, "total_pages": total_pages, "imported": imported},
            indent=2,
        )
    )


# ── API helpers ───────────────────────────────────────────────────────────────

async def _get_build_id(client: httpx.AsyncClient) -> str:
    """Extract Next.js buildId from island.is/domar HTML."""
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
        raise ValueError("Empty buildId extracted from island.is/domar")
    return build_id


async def _fetch_list_page(client: httpx.AsyncClient, page: int) -> dict:
    """Fetch one page (10 items) from the GraphQL list endpoint."""
    payload = {
        "query": _GQL_QUERY,
        "variables": {"input": {"court": _COURT_FILTER, "page": page}},
    }
    data = await post_with_retry(client, _GQL_ENDPOINT, payload)
    return data["data"]["webVerdicts"]


async def _fetch_detail(
    client: httpx.AsyncClient,
    build_id: str,
    verdict_id: str,
) -> dict:
    """Fetch pdfString (and any richText/resolutionLink) via Next.js JSON route."""
    url = f"https://island.is/_next/data/{build_id}/domar/{verdict_id}.json"
    resp = await get_with_retry(client, url)
    return resp.json()["pageProps"]["pageProps"]["pageProps"]["componentProps"]["item"]


# ── Document builder ──────────────────────────────────────────────────────────

def _build_document(
    list_item: dict,
    detail: dict | Exception,
    source_id: uuid.UUID,
    config: SourceConfig,
) -> Document:
    """Merge list + detail into a Document. Handles failed detail gracefully."""
    ext_id = list_item["id"]

    if isinstance(detail, Exception):
        log.warning("Detail fetch failed for %s: %s", ext_id, detail)
        raw: dict[str, Any] = dict(list_item)
        extra_errors: list[dict] = [{"field": "detail_fetch", "message": str(detail)}]
    else:
        raw = {
            **list_item,
            "richText": detail.get("richText"),
            "pdfString": detail.get("pdfString"),
            "resolutionLink": detail.get("resolutionLink"),
        }
        extra_errors = []

    fields = Extractor(config).extract(raw)
    doc = Document(
        id=uuid.uuid4(),
        source_id=source_id,
        external_id=ext_id,
        url=f"https://island.is/domar/{ext_id}",
        **fields,
    )
    errors = validate(doc, config)
    errors.extend(extra_errors)
    doc.validation_errors = errors or None
    return doc


# ── DB helpers ────────────────────────────────────────────────────────────────

async def _get_new_ids(session: AsyncSession, source_id: uuid.UUID, candidate_ids: list[str]) -> set[str]:
    """Return the subset of candidate_ids not yet in the DB."""
    from sqlalchemy import text as sa_text
    rows = (await session.execute(
        sa_text("SELECT external_id FROM documents WHERE source_id = :sid AND external_id = ANY(:ids)"),
        {"sid": source_id, "ids": candidate_ids},
    )).scalars().all()
    return set(candidate_ids) - set(rows)


async def _ensure_source(session: AsyncSession, config: SourceConfig) -> uuid.UUID:
    """SELECT source by short_name; INSERT if absent. Returns the source UUID."""
    result = await session.execute(
        select(Source).where(Source.short_name == config.short_name)
    )
    source = result.scalar_one_or_none()
    if source is None:
        source_id = uuid.uuid4()
        session.add(Source(
            id=source_id,
            short_name=config.short_name,
            display_name=config.display_name,
            base_url="https://island.is/api/graphql",
        ))
        await session.commit()
        return source_id
    return source.id


async def _upsert_doc(session: AsyncSession, doc: Document) -> None:
    """INSERT or UPDATE by (source_id, external_id) — true idempotent upsert."""
    def _v(val: Any) -> Any:
        return sa_null() if val is None else val

    values: dict[str, Any] = {
        "id": doc.id,
        "source_id": doc.source_id,
        "external_id": doc.external_id,
        "url": _v(doc.url),
        "raw_api_data": _v(doc.raw_api_data),
        "case_number": _v(doc.case_number),
        "document_date": _v(doc.document_date),
        "court": _v(doc.court),
        "verdict_type": _v(doc.verdict_type),
        "instance_tier": _v(doc.instance_tier),
        "plaintiffs": _v(doc.plaintiffs),
        "defendants": _v(doc.defendants),
        "keywords": _v(doc.keywords),
        "summary": _v(doc.summary),
        "body_text": _v(doc.body_text),
        "lower_body_text": _v(doc.lower_body_text),
        "validation_errors": _v(doc.validation_errors),
    }
    update_cols = {
        k: v for k, v in values.items()
        if k not in ("id", "source_id", "external_id")
    }
    update_cols["updated_at"] = func.now()
    await session.execute(
        pg_insert(Document)
        .values(**values)
        .on_conflict_do_update(
            constraint="uq_doc_source_external",
            set_=update_cols,
        )
    )


def _render_and_save(doc: Document, config: SourceConfig, taken: set[str]) -> str | None:
    """Write .md and PDF to disk. Returns the unique verdict_filename stem or None."""
    base = verdict_filename(doc, config)
    vf = unique_verdict_filename(base, taken)
    taken.add(vf)

    try:
        write_markdown(doc, config, vf=vf)
    except Exception as exc:
        log.warning("write_markdown failed for %s: %s", doc.external_id, exc)
        return None

    try:
        pdf_b64 = (doc.raw_api_data or {}).get("pdfString")
        if pdf_b64:
            pdf_path = config.pdf_path(vf)
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            pdf_path.write_bytes(base64.b64decode(pdf_b64))
    except Exception as exc:
        log.warning("PDF save failed for %s: %s", doc.external_id, exc)

    return vf


# ── Main ──────────────────────────────────────────────────────────────────────

async def main(limit: int | None = None, new_only: bool = False) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    config = get_config("landsrettur")
    await init_db()

    async with _db_conn.AsyncSessionLocal() as session:
        source_id = await _ensure_source(session, config)

    async with _db_conn.AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(Document.verdict_filename)
            .where(Document.source_id == source_id)
            .where(Document.verdict_filename.isnot(None))
        )).scalars().all()
    taken: set[str] = set(rows)

    if new_only:
        start_page, saved_total, imported_count = 1, 0, 0
    else:
        start_page, saved_total, imported_count = _load_checkpoint()
    total_errors = 0

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
        task_id = progress.add_task("Importing landsrettur…", total=None)

        async with make_client() as client:
            build_id = await _get_build_id(client)
            log.info("build_id: %s", build_id)

            last_page: int | None = saved_total if saved_total > 0 else None
            page = start_page

            while True:
                if last_page is not None and page > last_page:
                    break

                # Refresh build_id every 100 pages — Next.js deploys can rotate it
                if page > 1 and page % 100 == 0:
                    build_id = await _get_build_id(client)
                    log.info("Refreshed build_id at page %d", page)

                # Sequential POST — WAF-sensitive
                data = await _fetch_list_page(client, page)

                if last_page is None:
                    last_page = math.ceil(data["total"] / 10)
                    progress.update(task_id, total=data["total"])

                # In new_only mode: skip detail fetch for existing docs
                items = data["items"]
                if new_only:
                    async with _db_conn.AsyncSessionLocal() as chk:
                        new_ids = await _get_new_ids(chk, source_id, [v["id"] for v in items])
                    if not new_ids:
                        log.info("Page %d: all %d docs already in DB — stopping", page, len(items))
                        break
                    items = [v for v in items if v["id"] in new_ids]
                    log.info("Page %d: %d new / %d total", page, len(items), len(data["items"]))

                # Concurrent GETs — WAF-safe (detail fetches parallelised per page)
                details = await asyncio.gather(
                    *[_fetch_detail(client, build_id, v["id"]) for v in items],
                    return_exceptions=True,
                )

                docs = [
                    _build_document(item, detail, source_id, config)
                    for item, detail in zip(items, details)
                ]

                # Batch upsert — one transaction per page
                async with _db_conn.AsyncSessionLocal() as session:
                    for doc in docs:
                        await _upsert_doc(session, doc)
                    await session.commit()

                # Batch render + verdict_filename update — one transaction per page
                async with _db_conn.AsyncSessionLocal() as session:
                    for doc in docs:
                        vf = _render_and_save(doc, config, taken)
                        if vf:
                            await session.execute(
                                update(Document)
                                .where(
                                    Document.source_id == doc.source_id,
                                    Document.external_id == doc.external_id,
                                )
                                .values(verdict_filename=vf)
                            )
                    await session.commit()

                page_errors = sum(1 for d in docs if d.validation_errors)
                total_errors += page_errors
                imported_count += len(docs)

                if not new_only:
                    _save_checkpoint(page, last_page, imported_count)

                if limit is not None and imported_count >= limit:
                    break

                progress.update(
                    task_id,
                    advance=len(docs),
                    description=(
                        f"Importing landsrettur  "
                        f"[Page {page}/{last_page}  Errors: {total_errors}]"
                    ),
                )

                page += 1

    log.info(
        "Done. %d docs imported, %d with validation errors.",
        imported_count,
        total_errors,
    )


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Stop after N documents")
    parser.add_argument("--new-only", action="store_true", help="Only import documents not yet in DB")
    args = parser.parse_args()
    asyncio.run(main(limit=args.limit, new_only=args.new_only))
