"""Import Samgöngustofa rulings from island.is into lausnir_v2.

The source page is a Contentful rich-text article with ~524 PDF links embedded
directly in the HTML. There are no individual detail pages. Each PDF is fetched,
parsed with parse_pdf(), and the decision date is extracted from the closing line
("Reykjavík, DD. mánuður YYYY").

External ID: Contentful asset ID extracted from the ctfassets.net URL.
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

_PAGE_URL = "https://island.is/s/samgongustofa/akvardanir-domar-og-urskurdir"
_CHECKPOINT_DIR = Path("checkpoints")
_CHECKPOINT_FILE = _CHECKPOINT_DIR / "samgongustofa.json"
_REQUEST_DELAY = 1.0

_MONTHS = {
    "janúar": 1, "febrúar": 2, "mars": 3, "apríl": 4, "maí": 5, "júní": 6,
    "júlí": 7, "ágúst": 8, "september": 9, "október": 10, "nóvember": 11, "desember": 12,
}
_DATE_RE = re.compile(
    r'Reykjav[íi]k,?\s+(\d{1,2})\.\s+(' + '|'.join(_MONTHS) + r')\s+(\d{4})',
    re.IGNORECASE,
)
_ASSET_ID_RE = re.compile(r'ctfassets\.net/[^/]+/([^/]+)/')
_YEAR_H3_RE = re.compile(r'^\d{4}( og eldra)?$')


# ── Checkpoint ────────────────────────────────────────────────────────────────

def _load_checkpoint() -> tuple[set[str], int]:
    """Return (imported_ids, imported_count)."""
    if _CHECKPOINT_FILE.exists():
        data = json.loads(_CHECKPOINT_FILE.read_text())
        return set(data["imported_ids"]), data["imported"]
    return set(), 0


def _save_checkpoint(imported_ids: set[str], count: int) -> None:
    _CHECKPOINT_DIR.mkdir(exist_ok=True)
    _CHECKPOINT_FILE.write_text(
        json.dumps({"imported_ids": sorted(imported_ids), "imported": count}, indent=2)
    )


# ── Page parser ───────────────────────────────────────────────────────────────

def _parse_page(html: str) -> list[dict]:
    """Extract all PDF items from the Contentful rich-text page.

    Each item includes a `year` field taken from the nearest preceding year h3
    (e.g. "2024", "2023 og eldra"). Used by --new-only to filter by year window.
    """
    h3_pattern = re.compile(r'<h3[^>]*>(.*?)</h3>', re.DOTALL)
    h3_matches = [
        (m.start(), re.sub(r'<[^>]+>', '', m.group(1)).strip())
        for m in h3_pattern.finditer(html)
    ]

    pdf_pattern = re.compile(
        r'<a\s+href="(https://assets\.ctfassets\.net/[^"]+\.pdf)"[^>]*>([^<]+)</a>([^<]*)'
    )

    items = []
    for m in pdf_pattern.finditer(html):
        pdf_url = m.group(1)
        title = m.group(2).strip()
        summary = m.group(3).strip()
        pos = m.start()

        asset_m = _ASSET_ID_RE.search(pdf_url)
        if not asset_m:
            continue
        asset_id = asset_m.group(1)

        prev_sections = [
            t for p, t in h3_matches if p < pos and not _YEAR_H3_RE.match(t)
        ]
        section = prev_sections[-1] if prev_sections else None

        year_sections = [t for p, t in h3_matches if p < pos and _YEAR_H3_RE.match(t)]
        item_year = int(year_sections[-1].split()[0]) if year_sections else None

        items.append({
            "asset_id": asset_id,
            "pdf_url": pdf_url,
            "title": title,
            "summary": summary,
            "section": section,
            "year": item_year,
        })

    return items


# ── Extraction helpers ────────────────────────────────────────────────────────

def _extract_date(text: str) -> date | None:
    m = _DATE_RE.search(text)
    if m:
        return date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
    return None


def _extract_case_number(title: str) -> str | None:
    m = re.search(r'nr\.\s*(\d+/\d{4})', title)
    return m.group(1) if m else None


def _extract_verdict_type(title: str) -> str:
    m = re.match(r'(Úrskurður|Ákvörðun|Dómur)', title)
    return m.group(1) if m else "Úrskurður"


# ── Document builder ──────────────────────────────────────────────────────────

def _build_document(
    raw: dict,
    body_text: str,
    source_id: uuid.UUID,
    config: SourceConfig,
) -> Document:
    doc_date = _extract_date(body_text)
    case_number = _extract_case_number(raw["title"])
    verdict_type = _extract_verdict_type(raw["title"])
    section = raw.get("section")

    doc = Document(
        id=uuid.uuid4(),
        source_id=source_id,
        external_id=raw["asset_id"],
        url=raw["pdf_url"],
        raw_api_data={
            "title": raw["title"],
            "pdf_url": raw["pdf_url"],
            "summary": raw["summary"],
            "section": section,
        },
        case_number=case_number,
        document_date=doc_date,
        court="SGS.",
        verdict_type=verdict_type,
        instance_tier=config.instance_tier,
        plaintiffs=None,
        defendants=None,
        keywords=[section] if section else [],
        summary=raw["summary"] or None,
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

async def main(limit: int | None = None, new_only: bool = False) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")

    config = get_config("samgongustofa")
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

    log.info("Fetching SGS page…")
    async with make_client() as client:
        resp = await client.get(_PAGE_URL, follow_redirects=True)
        resp.raise_for_status()
    html = resp.text

    all_items = _parse_page(html)
    if new_only:
        from datetime import date as _date
        cutoff_year = _date.today().year - 1
        pending = [
            it for it in all_items
            if it["asset_id"] not in imported_ids and (it["year"] or 0) >= cutoff_year
        ]
        log.info("new-only: year ≥ %d, %d to import", cutoff_year, len(pending))
    else:
        pending = [it for it in all_items if it["asset_id"] not in imported_ids]
    log.info("Total items: %d, to import: %d", len(all_items), len(pending))

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
        task_id = progress.add_task("Importing samgongustofa…", total=len(pending))

        async with make_client() as client:
            for raw in pending:
                if limit is not None and imported_count >= limit:
                    break

                try:
                    pdf_resp = await client.get(raw["pdf_url"], follow_redirects=True)
                    pdf_resp.raise_for_status()
                    body_text = parse_pdf(pdf_resp.content) or ""
                except Exception as exc:
                    log.warning("PDF fetch failed for %s: %s", raw["asset_id"], exc)
                    imported_ids.add(raw["asset_id"])
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
                        log.error("Upsert failed for %s: %s", raw["asset_id"], exc)
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

                imported_ids.add(raw["asset_id"])
                imported_count += 1
                _save_checkpoint(imported_ids, imported_count)

                progress.update(
                    task_id,
                    advance=1,
                    description=(
                        f"Importing samgongustofa  "
                        f"[{doc.case_number}  Imported: {imported_count}  Errors: {total_errors}]"
                    ),
                )

                await asyncio.sleep(_REQUEST_DELAY)

    log.info("Done. Imported: %d, checkpoint: %d", imported_count, len(imported_ids))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Stop after N documents")
    parser.add_argument("--new-only", action="store_true", help="Only check items from last 1 year (sequential order)")
    args = parser.parse_args()
    asyncio.run(main(limit=args.limit, new_only=args.new_only))
