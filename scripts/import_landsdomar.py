"""Import Landsdómur rulings into lausnir_v2.

Source: https://xn--landsdmur-b7a.is/domar-og-urskurdir/index.html
5 rulings total (2 Dómar + 3 Úrskurðir), 2 cases, 2011–2012.

All data collected from listing page (date, title, verdict_type, parties).
Each detail page provides the PDF link.
4/5 PDFs are scanned → docling_ocr_pdf(); nr/77 is a text PDF → parse_pdf().

Field mapping:
  - external_id  : URL number (e.g. "77")
  - case_number  : nr. N/YYYY from title
  - document_date: <span class='date'> from listing
  - verdict_type : first word of title ("Dómur" | "Úrskurður")
  - plaintiffs   : before "gegn" in title
  - defendants   : after "gegn" in title
  - body_text    : parse_pdf() → docling_ocr_pdf() fallback
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import date
from html import unescape
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

_BASE = "https://xn--landsdmur-b7a.is"
_LISTING_URL = f"{_BASE}/domar-og-urskurdir/index.html"
_CHECKPOINT_DIR = Path("checkpoints")
_CHECKPOINT_FILE = _CHECKPOINT_DIR / "landsdomar.json"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
# Note: make_client() already injects browser User-Agent; _HEADERS used only for direct httpx calls

_IS_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "maí": 5, "jún": 6,
    "júl": 7, "ág": 8, "sep": 9, "okt": 10, "nóv": 11, "des": 12,
}


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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _strip(html: str) -> str:
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', unescape(html))).strip()


def _parse_date(s: str) -> date | None:
    m = re.search(r'(\d{1,2})\.\s*(\w+)\.?\s*(\d{4})', s)
    if m:
        mon = next((v for k, v in _IS_MONTHS.items() if m.group(2).lower().startswith(k)), None)
        if mon:
            return date(int(m.group(3)), mon, int(m.group(1)))
    return None


# ── Fetch / collect ───────────────────────────────────────────────────────────

async def _collect_items(client: httpx.AsyncClient) -> list[dict]:
    """Parse listing page → [{num, date_str, title, href}]."""
    r = await client.get(_LISTING_URL, timeout=15)
    r.raise_for_status()
    html = r.text
    items = []
    pattern = re.compile(
        r"<span\s+class=['\"]date['\"][^>]*>(.*?)</span>.*?"
        r'<a\s+(?:class="atitle"\s+)?href="(nr/(\d+)\.html)"[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    for m in pattern.finditer(html):
        items.append({
            "date_str": _strip(m.group(1)),
            "href":     m.group(2),
            "num":      m.group(3),
            "title":    _strip(m.group(4)),
        })
    return items


async def _get_pdf_url(client: httpx.AsyncClient, num: str) -> str | None:
    r = await client.get(f"{_BASE}/domar-og-urskurdir/nr/{num}.html", timeout=15)
    r.raise_for_status()
    m = re.search(r'href="(\.\./\.\./media/skyrslur/[^"]+\.pdf)"', r.text, re.IGNORECASE)
    if m:
        filename = m.group(1).split("skyrslur/")[-1]
        return f"{_BASE}/media/skyrslur/{filename}"
    return None


# ── Extraction ────────────────────────────────────────────────────────────────

def _extract_from_title(title: str) -> tuple[str | None, str | None, list | None, list | None]:
    vtype_m = re.match(r'^(Dómur|Úrskurður)', title)
    verdict_type = vtype_m.group(1) if vtype_m else None

    cn_m = re.search(r'nr\.\s*(\d+/\d{4})', title)
    case_number = cn_m.group(1) if cn_m else None

    parties_text = re.sub(r'^.*?nr\.\s*\d+/\d{4}[:\s]+', '', title).strip()
    gegn_m = re.search(r'(.+?)\s+gegn\s+(.+)', parties_text)
    if gegn_m:
        plaintiffs = [{"name": gegn_m.group(1).strip()}]
        defendants = [{"name": gegn_m.group(2).strip()}]
    else:
        plaintiffs = defendants = None

    return verdict_type, case_number, plaintiffs, defendants


# ── Document builder ──────────────────────────────────────────────────────────

def _build_document(
    item: dict,
    pdf_bytes: bytes,
    pdf_url: str | None,
    source_id: uuid.UUID,
    config: SourceConfig,
) -> Document:
    body_text = parse_pdf(pdf_bytes) or ""
    if not body_text and pdf_bytes:
        log.info("Running Docling OCR for nr/%s", item["num"])
        body_text = docling_ocr_pdf(pdf_bytes) or ""

    verdict_type, case_number, plaintiffs, defendants = _extract_from_title(item["title"])

    doc = Document(
        id=uuid.uuid4(),
        source_id=source_id,
        external_id=item["num"],
        url=f"{_BASE}/domar-og-urskurdir/{item['href']}",
        raw_api_data={
            "num": item["num"],
            "title": item["title"],
            "date_str": item["date_str"],
            "pdf_url": pdf_url,
        },
        case_number=case_number,
        document_date=_parse_date(item["date_str"]),
        court=config.abbreviation,
        verdict_type=verdict_type,
        instance_tier=config.instance_tier,
        plaintiffs=plaintiffs,
        defendants=defendants,
        keywords=None,
        summary=None,
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
            base_url=_LISTING_URL,
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

    config = get_config("landsdomar")
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
        log.info("Fetching listing page…")
        all_items = await _collect_items(client)
        log.info("Total items: %d", len(all_items))

        pending = [it for it in all_items if it["num"] not in imported_ids]
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
            task_id = progress.add_task("Importing landsdomar…", total=len(pending))

            for item in pending:
                if limit is not None and imported_count >= limit:
                    break

                num = item["num"]

                try:
                    pdf_url = await _get_pdf_url(client, num)
                    if pdf_url:
                        r = await client.get(pdf_url, timeout=120, follow_redirects=True)
                        r.raise_for_status()
                        pdf_bytes = r.content
                    else:
                        log.warning("No PDF found for nr/%s", num)
                        pdf_bytes = b""
                except Exception as exc:
                    log.warning("Fetch failed for nr/%s: %s", num, exc)
                    imported_ids.add(num)
                    _save_checkpoint(imported_ids, imported_count)
                    progress.advance(task_id)
                    continue

                doc = _build_document(item, pdf_bytes, pdf_url, source_id, config)

                async with _db_conn.AsyncSessionLocal() as session:
                    try:
                        await _upsert_doc(session, doc)
                        await session.commit()
                    except Exception as exc:
                        log.error("Upsert failed for nr/%s: %s", num, exc)
                        await session.rollback()
                        progress.advance(task_id)
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

                imported_ids.add(num)
                imported_count += 1
                _save_checkpoint(imported_ids, imported_count)

                progress.update(
                    task_id,
                    advance=1,
                    description=(
                        f"Importing Landsdómur.  "
                        f"[nr/{num}  {doc.verdict_type}  "
                        f"Imported: {imported_count}  Errors: {total_errors}]"
                    ),
                )

    log.info("Done. Imported: %d, checkpoint: %d", imported_count, len(imported_ids))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Stop after N documents")
    args = parser.parse_args()
    asyncio.run(main(limit=args.limit))
