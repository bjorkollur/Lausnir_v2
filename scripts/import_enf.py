"""Import Eftirlitsnefnd fasteignasala rulings into lausnir_v2.

Three listing pages on the same site, all PDF-only:
  - Álit:         https://enf.is/index.php/alit/          (~88 PDFs)
  - Ákvarðanir:   https://enf.is/index.php/akvardanir/    (~11 PDFs)
  - Umburðarbréf: https://enf.is/index.php/umburdarbref/  (~26 PDFs)

Álit and Ákvarðanir: text PDFs → parse_pdf().
Umburðarbréf: scanned PDFs → docling_ocr_pdf().

Case number extraction:
  - Álit/Ákvörðun: from PDF text "Mál nr. K-001-21" / "mál nr. F-003-24"
  - Fallback: from filename pattern (ENF- prefix stripped, _ → /)
  - Umburðarbréf: "N/YYYY" from filename (e.g. Umburdarbref_1_2024 → 1/2024)

Date: "Reykjavík, D. mánuður YYYY" in PDF text.
External ID: PDF filename without extension.
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
from engine.processors.pdf_parser import docling_ocr_pdf, parse_pdf
from engine.processors.renderer import unique_verdict_filename, verdict_filename, write_markdown
from engine.processors.validator import validate

log = logging.getLogger(__name__)

_LISTING_PAGES = [
    ("https://enf.is/index.php/alit/",         "Álit"),
    ("https://enf.is/index.php/akvardanir/",   "Ákvörðun"),
    ("https://enf.is/index.php/umburdarbref/", "Umburðarbréf"),
]
_CHECKPOINT_DIR = Path("checkpoints")
_CHECKPOINT_FILE = _CHECKPOINT_DIR / "enf.json"
_REQUEST_DELAY = 1.0

_MONTHS: dict[str, int] = {
    "janúar": 1, "febrúar": 2, "mars": 3, "apríl": 4, "maí": 5, "júní": 6,
    "júlí": 7, "ágúst": 8, "september": 9, "október": 10, "nóvember": 11, "desember": 12,
}
_MONTH_PAT = "|".join(_MONTHS)
_DATE_RE = re.compile(
    r"Reykjavík,\s+(\d{1,2})\.\s+(" + _MONTH_PAT + r")\s+(\d{4})",
    re.IGNORECASE,
)
_PDF_LINK_RE = re.compile(r'href="(https?://enf\.is[^"]+\.pdf[^"]*)"', re.IGNORECASE)


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


# ── Fetch / collect ───────────────────────────────────────────────────────────

async def _collect_items(
    client: httpx.AsyncClient,
    imported_ids: set[str] | None = None,
    new_only: bool = False,
) -> list[dict]:
    """Return [{pdf_url, filename, external_id, verdict_type}] from all listing pages.

    In new_only mode, stops adding items from each page as soon as a known id is seen
    (each listing page is independently newest-first).
    """
    items: list[dict] = []
    seen: set[str] = set()
    for page_url, verdict_type in _LISTING_PAGES:
        r = await client.get(page_url, timeout=30)
        r.raise_for_status()
        for m in _PDF_LINK_RE.finditer(r.text):
            pdf_url = m.group(1)
            filename = pdf_url.split("/")[-1]
            ext_id = filename.removesuffix(".pdf")
            if ext_id in seen:
                continue
            seen.add(ext_id)
            if new_only and imported_ids is not None and ext_id in imported_ids:
                log.info("new-only: stopping %s at first known: %s", verdict_type, ext_id)
                break
            items.append({
                "pdf_url": pdf_url,
                "filename": filename,
                "external_id": ext_id,
                "verdict_type": verdict_type,
            })
        await asyncio.sleep(0.3)
    return items


# ── Extraction ────────────────────────────────────────────────────────────────

def _extract_case_number(text: str, filename: str, verdict_type: str) -> str | None:
    # From PDF text (most reliable)
    m = re.search(r"[Mm]ál\s+nr\.\s+([A-Z]-\d+-\d+)", text)
    if m:
        return m.group(1)
    # Umburðarbréf: N/YYYY from filename
    if verdict_type == "Umburðarbréf":
        m = re.search(r"(\d+)[_.-](\d{4})", filename)
        if m:
            return f"{m.group(1)}/{m.group(2)}"
    # Ákvörðun fallback: strip ENF- prefix from filename
    m = re.search(r"(?:ENF[-_])?([A-Z]-\d+-\d+)", filename)
    if m:
        return m.group(1)
    return None


def _extract_date(text: str) -> date | None:
    m = _DATE_RE.search(text)
    if m:
        return date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
    return None


# ── Document builder ──────────────────────────────────────────────────────────

def _build_document(
    item: dict,
    pdf_bytes: bytes,
    source_id: uuid.UUID,
    config: SourceConfig,
) -> Document:
    verdict_type = item["verdict_type"]
    filename = item["filename"]

    text = parse_pdf(pdf_bytes) or ""
    if not text and verdict_type == "Umburðarbréf":
        log.info("Running Docling OCR for %s", filename)
        text = docling_ocr_pdf(pdf_bytes) or ""

    case_number = _extract_case_number(text, filename, verdict_type)
    doc_date = _extract_date(text)

    doc = Document(
        id=uuid.uuid4(),
        source_id=source_id,
        external_id=item["external_id"],
        url=item["pdf_url"],
        raw_api_data={"filename": filename, "pdf_url": item["pdf_url"], "verdict_type": verdict_type},
        case_number=case_number,
        document_date=doc_date,
        court=config.abbreviation,
        verdict_type=verdict_type,
        instance_tier=config.instance_tier,
        plaintiffs=None,
        defendants=None,
        keywords=None,
        summary=None,
        body_text=text or None,
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
            base_url="https://enf.is",
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

async def main(limit: int | None = None, new_only: bool = False) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    config = get_config("enf")
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

    async with make_client() as client:
        log.info("Collecting PDF links from listing pages…")
        all_items = await _collect_items(client, imported_ids=imported_ids, new_only=new_only)
        log.info("Total PDFs collected: %d", len(all_items))

        pending = [it for it in all_items if it["external_id"] not in imported_ids]
        log.info("To import: %d", len(pending))

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
            task_id = progress.add_task("Importing enf…", total=len(pending))

            for item in pending:
                if limit is not None and imported_count >= limit:
                    break

                try:
                    r = await client.get(item["pdf_url"], timeout=60, follow_redirects=True)
                    r.raise_for_status()
                    pdf_bytes = r.content
                except Exception as exc:
                    log.warning("PDF fetch failed for %s: %s", item["external_id"], exc)
                    imported_ids.add(item["external_id"])
                    _save_checkpoint(imported_ids, imported_count)
                    progress.advance(task_id)
                    await asyncio.sleep(_REQUEST_DELAY)
                    continue

                doc = _build_document(item, pdf_bytes, source_id, config)

                async with _db_conn.AsyncSessionLocal() as session:
                    try:
                        await _upsert_doc(session, doc)
                        await session.commit()
                    except Exception as exc:
                        log.error("Upsert failed for %s: %s", item["external_id"], exc)
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
                    log.warning("WARN %s: %s", doc.case_number, [e["field"] for e in doc.validation_errors])
                else:
                    log.info("OK   %s  [%s]", doc.case_number, doc.verdict_type)

                imported_ids.add(item["external_id"])
                imported_count += 1
                _save_checkpoint(imported_ids, imported_count)

                progress.update(
                    task_id,
                    advance=1,
                    description=(
                        f"Importing ENF.  "
                        f"[{doc.case_number} {doc.verdict_type}  "
                        f"Imported: {imported_count}  Errors: {total_errors}]"
                    ),
                )

                await asyncio.sleep(_REQUEST_DELAY)

    log.info("Done. Imported: %d, checkpoint: %d", imported_count, len(imported_ids))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Stop after N documents")
    parser.add_argument("--new-only", action="store_true", help="Stop at first known per listing page (newest-first)")
    args = parser.parse_args()
    asyncio.run(main(limit=args.limit, new_only=args.new_only))
