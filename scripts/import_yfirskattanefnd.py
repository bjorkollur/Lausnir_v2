"""Import Yfirskattanefnd rulings into lausnir_v2.

Source: https://yskn.is/urskurdir/ — HTML pages, no API.
4,155+ rulings from 1973–2026.

Scraping strategy:
  1. Read year links from main listing page.
  2. Per year: scrape ?year=Y to collect {nr_id, case_number} pairs.
  3. Per nr_id: fetch detail page, extract date/keywords/summary/body_text.

External ID: nr_id (integer string) from URL parameter.

Date availability: "Ár YYYY, vikudagur D. mánuður" format present from ~2016.
Older cases get document_date=None (validation warning, not error).
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
from engine.processors.renderer import unique_verdict_filename, verdict_filename, write_markdown
from engine.processors.validator import validate

log = logging.getLogger(__name__)

_BASE_URL = "https://yskn.is"
_LISTING_URL = f"{_BASE_URL}/urskurdir/"
_DETAIL_URL = f"{_BASE_URL}/urskurdir/skoda-urskurd/"
_CHECKPOINT_DIR = Path("checkpoints")
_CHECKPOINT_FILE = _CHECKPOINT_DIR / "yfirskattanefnd.json"
_REQUEST_DELAY = 0.5

_MONTHS: dict[str, int] = {
    "janúar": 1, "febrúar": 2, "mars": 3, "apríl": 4, "maí": 5, "júní": 6,
    "júlí": 7, "ágúst": 8, "september": 9, "október": 10, "nóvember": 11, "desember": 12,
}
_MONTH_PAT = "|".join(_MONTHS)
_DATE_RE = re.compile(
    r"Ár\s+(\d{4}),\s+\w+\s+(\d{1,2})\.\s+(" + _MONTH_PAT + r")",
    re.IGNORECASE,
)
_YEAR_LINK_RE = re.compile(r'href="[^"]*\?year=(\d{4})"')
_LISTING_ITEM_RE = re.compile(
    r"href='/urskurdir/skoda-urskurd/\?nr=(\d+)'[^>]+>.*?UrskurdurSplitText[^>]*>\s*([^<]+)\s*<",
    re.DOTALL,
)
_CASE_NUM_RE = re.compile(r"nr\.\s*(\d+/\d{4})")


def _strip_html(html: str) -> str:
    html = re.sub(r"<[^>]+>", " ", html)
    html = html.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", html).strip()


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


# ── Fetch / scrape helpers ────────────────────────────────────────────────────

async def _fetch_years(client: httpx.AsyncClient) -> list[int]:
    """Return all years available on the listing page, newest first."""
    r = await client.get(_LISTING_URL, timeout=30)
    r.raise_for_status()
    years = [int(m.group(1)) for m in _YEAR_LINK_RE.finditer(r.text)]
    return sorted(set(years), reverse=True)


async def _fetch_listing_for_year(client: httpx.AsyncClient, year: int) -> list[dict]:
    """Return [{nr_id, case_number}] for all rulings in a given year."""
    r = await client.get(_LISTING_URL, params={"year": year}, timeout=30)
    r.raise_for_status()
    items = []
    for m in _LISTING_ITEM_RE.finditer(r.text):
        nr_id = m.group(1)
        raw = m.group(2).strip()
        cn_m = _CASE_NUM_RE.search(raw)
        items.append({"nr_id": nr_id, "case_number": cn_m.group(1) if cn_m else None})
    return items


async def _fetch_detail(client: httpx.AsyncClient, nr_id: str) -> str:
    r = await client.get(_DETAIL_URL, params={"nr": nr_id}, timeout=30)
    r.raise_for_status()
    return r.text


# ── Extraction ────────────────────────────────────────────────────────────────

def _extract_date(html: str) -> date | None:
    m = _DATE_RE.search(html)
    if m:
        return date(int(m.group(1)), _MONTHS[m.group(3).lower()], int(m.group(2)))
    return None


def _extract_keywords(html: str) -> list[str]:
    m = re.search(r"atridicontainer[^>]*>(.*?)</ul>", html, re.DOTALL)
    if not m:
        return []
    return [k.strip() for k in re.findall(r"<li>([^<]+)</li>", m.group(1)) if k.strip()]


def _extract_summary(html: str) -> str | None:
    m = re.search(r"resultparagraphBold[^>]*>(.*?)</p>", html, re.DOTALL)
    return _strip_html(m.group(1)) if m else None


def _extract_body(html: str) -> str:
    m = re.search(r"resultcontainer[^>]*>(.*?)</div>\s*</div>", html, re.DOTALL)
    return _strip_html(m.group(1)) if m else _strip_html(html)


# ── Document builder ──────────────────────────────────────────────────────────

def _build_document(
    nr_id: str,
    case_number: str | None,
    html: str,
    source_id: uuid.UUID,
    config: SourceConfig,
) -> Document:
    doc_date = _extract_date(html)
    keywords = _extract_keywords(html)
    summary = _extract_summary(html)
    body_text = _extract_body(html)

    doc = Document(
        id=uuid.uuid4(),
        source_id=source_id,
        external_id=nr_id,
        url=f"{_DETAIL_URL}?nr={nr_id}",
        raw_api_data={"nr_id": nr_id, "case_number": case_number},
        case_number=case_number,
        document_date=doc_date,
        court=config.abbreviation,
        verdict_type=config.verdict_type_default,
        instance_tier=config.instance_tier,
        plaintiffs=None,
        defendants=None,
        keywords=keywords or None,
        summary=summary,
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

    config = get_config("yfirskattanefnd")
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
        log.info("Fetching available years…")
        years = await _fetch_years(client)
        log.info("Years found: %s", years)

        # Collect all pending items across all years
        log.info("Collecting listing pages…")
        all_items: list[dict] = []
        for year in years:
            items = await _fetch_listing_for_year(client, year)
            all_items.extend(items)
            await asyncio.sleep(0.2)

        log.info("Total items: %d", len(all_items))
        pending = [it for it in all_items if it["nr_id"] not in imported_ids]
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
            task_id = progress.add_task("Importing yfirskattanefnd…", total=len(pending))

            for item in pending:
                if limit is not None and imported_count >= limit:
                    break

                nr_id = item["nr_id"]
                case_number = item["case_number"]

                try:
                    html = await _fetch_detail(client, nr_id)
                except Exception as exc:
                    log.warning("Detail fetch failed for nr=%s: %s", nr_id, exc)
                    imported_ids.add(nr_id)
                    _save_checkpoint(imported_ids, imported_count)
                    progress.advance(task_id)
                    await asyncio.sleep(_REQUEST_DELAY)
                    continue

                doc = _build_document(nr_id, case_number, html, source_id, config)

                async with _db_conn.AsyncSessionLocal() as session:
                    try:
                        await _upsert_doc(session, doc)
                        await session.commit()
                    except Exception as exc:
                        log.error("Upsert failed for nr=%s: %s", nr_id, exc)
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
                    log.warning("WARN %s: %s", case_number, [e["field"] for e in doc.validation_errors])
                else:
                    log.info("OK   %s", case_number)

                imported_ids.add(nr_id)
                imported_count += 1
                _save_checkpoint(imported_ids, imported_count)

                progress.update(
                    task_id,
                    advance=1,
                    description=(
                        f"Importing YSKN.  "
                        f"[{case_number}  Imported: {imported_count}  Errors: {total_errors}]"
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
