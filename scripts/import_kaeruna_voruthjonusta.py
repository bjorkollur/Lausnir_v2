"""Import Kærunefnd vöru- og þjónustukaupa rulings into lausnir_v2.

The source is a public REST API at eldrigatt.kvth.is/dashboard/odr/rulings (POST).
No authentication required for the listing or PDF downloads.
595 rulings from 2020–2026.

External ID: uniqueId (UUID) from the API response.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import date
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
from engine.processors.http_utils import make_client
from engine.processors.pdf_parser import parse_pdf
from engine.processors.renderer import unique_verdict_filename, verdict_filename, write_markdown
from engine.processors.validator import validate

log = logging.getLogger(__name__)

_API_URL = "https://eldrigatt.kvth.is/dashboard/odr/rulings"
_PDF_BASE = "https://eldrigatt.kvth.is/ruling"
_PAGE_URL = "https://eldrigatt.kvth.is/#/urskurdir"
_CHECKPOINT_DIR = Path("checkpoints")
_CHECKPOINT_FILE = _CHECKPOINT_DIR / "kaeruna_voruthjonusta.json"
_REQUEST_DELAY = 0.5

_MONTHS = {
    "janúar": 1, "febrúar": 2, "mars": 3, "apríl": 4, "maí": 5, "júní": 6,
    "júlí": 7, "ágúst": 8, "september": 9, "október": 10, "nóvember": 11, "desember": 12,
}
_DATE_RE = re.compile(
    r'uppkveðinn\s+(\d{1,2})\.\s+(' + '|'.join(_MONTHS) + r')\s+(\d{4})',
    re.IGNORECASE,
)
_CASE_NUM_RE = re.compile(r'(\d+/\d{4})')


# ── Checkpoint ────────────────────────────────────────────────────────────────

def _load_checkpoint() -> tuple[set[str], int]:
    if _CHECKPOINT_FILE.exists():
        data = json.loads(_CHECKPOINT_FILE.read_text())
        return set(data["imported_ids"]), data["imported"]
    return set(), 0


def _save_checkpoint(imported_ids: set[str], count: int) -> None:
    _CHECKPOINT_DIR.mkdir(exist_ok=True)
    _CHECKPOINT_FILE.write_text(
        json.dumps({"imported_ids": sorted(imported_ids), "imported": count}, indent=2)
    )


# ── Fetch helpers ─────────────────────────────────────────────────────────────

async def _fetch_all_items(client: httpx.AsyncClient) -> list[dict]:
    """Fetch all rulings from the public API in one request."""
    r = await client.post(
        _API_URL,
        json={"pageSize": 10000, "page": 0},
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["data"]


# ── Extraction helpers ────────────────────────────────────────────────────────

def _extract_date(text: str) -> date | None:
    m = _DATE_RE.search(text)
    if m:
        return date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
    return None


def _extract_case_number(body: str) -> str | None:
    m = _CASE_NUM_RE.search(body)
    return m.group(1) if m else None


def _extract_keywords(subject: str) -> list[str]:
    return [k.strip() for k in subject.split(".") if k.strip()]


# ── Document builder ──────────────────────────────────────────────────────────

def _build_document(
    raw: dict,
    body_text: str,
    source_id: uuid.UUID,
    config: SourceConfig,
) -> Document:
    doc_date = _extract_date(body_text)
    case_number = _extract_case_number(raw["body"])
    keywords = _extract_keywords(raw["subject"])
    pdf_url = f"{_PDF_BASE}/{raw['uniqueId']}"

    doc = Document(
        id=uuid.uuid4(),
        source_id=source_id,
        external_id=raw["uniqueId"],
        url=pdf_url,
        raw_api_data={
            "id": raw["id"],
            "uniqueId": raw["uniqueId"],
            "subject": raw["subject"],
            "body": raw["body"],
            "created": raw["created"],
            "attachments": raw.get("attachments"),
        },
        case_number=case_number,
        document_date=doc_date,
        court="KVÞ.",
        verdict_type="Úrskurður",
        instance_tier=config.instance_tier,
        plaintiffs=None,
        defendants=None,
        keywords=keywords,
        summary=raw["subject"],
        body_text=body_text or None,
        lower_body_text=None,
    )
    errors = validate(doc, config)
    doc.validation_errors = errors or None
    return doc


# ── DB helpers ────────────────────────────────────────────────────────────────

async def _ensure_source(session: AsyncSession, config: SourceConfig) -> uuid.UUID:
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
            base_url=_PAGE_URL,
        ))
        await session.commit()
        return source_id
    return source.id


async def _upsert_doc(session: AsyncSession, doc: Document) -> None:
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
    update_cols = {k: v for k, v in values.items() if k not in ("id", "source_id", "external_id")}
    update_cols["updated_at"] = func.now()
    await session.execute(
        pg_insert(Document)
        .values(**values)
        .on_conflict_do_update(constraint="uq_doc_source_external", set_=update_cols)
    )


def _render_and_save(doc: Document, config: SourceConfig, taken: set[str]) -> str | None:
    base = verdict_filename(doc, config)
    vf = unique_verdict_filename(base, taken)
    taken.add(vf)
    try:
        write_markdown(doc, config, vf=vf)
    except Exception as exc:
        log.warning("write_markdown failed for %s: %s", doc.external_id, exc)
        return None
    return vf


# ── Main ──────────────────────────────────────────────────────────────────────

async def main(limit: int | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    config = get_config("kaeruna_voruthjonusta")
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

    imported_ids, imported_count = _load_checkpoint()
    total_errors = 0

    log.info("Fetching ruling list…")
    async with make_client() as client:
        all_items = await _fetch_all_items(client)

    pending = [it for it in all_items if it["uniqueId"] not in imported_ids]
    log.info("Total: %d, to import: %d", len(all_items), len(pending))

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
        task_id = progress.add_task("Importing kaeruna_voruthjonusta…", total=len(pending))

        async with make_client() as client:
            for raw in pending:
                if limit is not None and imported_count >= limit:
                    break

                pdf_url = f"{_PDF_BASE}/{raw['uniqueId']}"
                try:
                    pdf_resp = await client.get(pdf_url, follow_redirects=True)
                    pdf_resp.raise_for_status()
                    body_text = parse_pdf(pdf_resp.content) or ""
                except Exception as exc:
                    log.warning("PDF fetch failed for %s: %s", raw["uniqueId"], exc)
                    imported_ids.add(raw["uniqueId"])
                    _save_checkpoint(imported_ids, imported_count)
                    progress.advance(task_id)
                    await asyncio.sleep(_REQUEST_DELAY)
                    continue

                doc = _build_document(raw, body_text, source_id, config)

                async with _db_conn.AsyncSessionLocal() as session:
                    try:
                        await _upsert_doc(session, doc)
                        await session.commit()
                    except Exception as exc:
                        log.error("Upsert failed for %s: %s", raw["uniqueId"], exc)
                        await session.rollback()
                        progress.advance(task_id)
                        await asyncio.sleep(_REQUEST_DELAY)
                        continue

                async with _db_conn.AsyncSessionLocal() as session:
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

                if doc.validation_errors:
                    total_errors += 1
                    log.warning("WARN %s: %s", doc.case_number,
                                [e["field"] for e in doc.validation_errors])
                else:
                    log.info("OK   %s", doc.case_number)

                imported_ids.add(raw["uniqueId"])
                imported_count += 1
                _save_checkpoint(imported_ids, imported_count)

                progress.update(
                    task_id,
                    advance=1,
                    description=(
                        f"Importing KVÞ.  "
                        f"[{doc.case_number}  Imported: {imported_count}  Errors: {total_errors}]"
                    ),
                )

                await asyncio.sleep(_REQUEST_DELAY)

    log.info("Done. Imported: %d, checkpoint: %d", imported_count, len(imported_ids))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Stop after N documents")
    args = parser.parse_args()
    asyncio.run(main(limit=args.limit))
