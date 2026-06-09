"""Import Fjarskiptastofa (FST / PFS) rulings into lausnir_v2.

Scrapes the Blazor Server app at fjarskiptastofa.is using Playwright (Chromium)
to navigate year-filtered lists, visits each detail page, downloads the PDF,
and stores the result in the DB.

PDF body_text strategy:
  - Regular FST/PFS ákvarðanir: parse_pdf() (text layer is intact)
  - ÚFP úrskurðir (ÚRSK / Ursk_ prefix): docling_ocr_pdf() via Tesseract/isl
    because those PDFs are image-scanned with broken font encoding.

Usage:
    uv run python scripts/import_fjarskiptastofa.py
    uv run python scripts/import_fjarskiptastofa.py --limit 5
    uv run python scripts/import_fjarskiptastofa.py --year 2024
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx
from playwright.sync_api import sync_playwright
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

import engine.database.connection as _db_conn
from engine.config.sources import SourceConfig, get_config
from engine.database.connection import init_db
from engine.database.models import Document, Source
from engine.processors.pdf_parser import docling_ocr_pdf, parse_pdf
from engine.processors.renderer import unique_verdict_filename, verdict_filename, write_markdown
from engine.processors.validator import validate

log = logging.getLogger(__name__)

_BASE_URL = "https://www.fjarskiptastofa.is"
_LIST_URL = f"{_BASE_URL}/fjarskiptastofa/stjornsysla/akvardanir-og-urskurdir/"
_PFS_CUTOFF = date(2021, 7, 1)   # FST renamed from PFS on 1 July 2021

_CHECKPOINT_DIR = Path("checkpoints")
_CHECKPOINT_FILE = _CHECKPOINT_DIR / "fjarskiptastofa.json"

_REQUEST_DELAY = 1.5

_MONTH_MAP = {
    "janúar": 1, "febrúar": 2, "mars": 3, "apríl": 4, "maí": 5, "júní": 6,
    "júlí": 7, "ágúst": 8, "september": 9, "október": 10, "nóvember": 11, "desember": 12,
}

# ÚRSK 1/2025  or  Ursk_3_2024  or  Ursk_06_2024
_URSK_RE = re.compile(r"^(?:ÚRSK\s+|Ursk_)", re.IGNORECASE)
# Detect date in DD.MM.YYYY format (detail page)
_DATE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")
# Extract case number from Númer field (strip ÚRSK prefix, normalise)
_CASE_CLEAN_RE = re.compile(r"Ursk_(\d+)_(\d{4})", re.IGNORECASE)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; Lausnir/2.0; +https://lausnir.is)",
}


# ── Checkpoint ─────────────────────────────────────────────────────────────────

def _load_checkpoint() -> set[str]:
    if _CHECKPOINT_FILE.exists():
        data = json.loads(_CHECKPOINT_FILE.read_text())
        return set(data.get("imported_slugs", []))
    return set()


def _save_checkpoint(imported_slugs: set[str]) -> None:
    _CHECKPOINT_DIR.mkdir(exist_ok=True)
    _CHECKPOINT_FILE.write_text(
        json.dumps({"imported_slugs": sorted(imported_slugs), "count": len(imported_slugs)}, indent=2)
    )


# ── Playwright scraping ────────────────────────────────────────────────────────

def _collect_all_slugs(year_filter: str | None = None) -> list[dict]:
    """Return list of {slug, list_date_str, list_case_number} for all decisions."""
    all_items: list[dict] = []

    all_years = [
        "2026","2025","2024","2023","2022","2021","2020","2019","2018",
        "2017","2016","2015","2014","2013","2012","2011","2010","2009",
        "2008","2007","2006","2005","2004","2003","2002","2001","2000","1999",
    ]
    years = [year_filter] if year_filter else all_years

    detail_pattern = re.compile(r"/akvardanir-og-urskurdir/akvordun/[^?]+/(.+)$")
    index_pattern = re.compile(r"\?index=(\d+)")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        for yr in years:
            visited_indices: set[int] = set()
            pending_indices: set[int] = {0}

            while pending_indices:
                idx = min(pending_indices)
                pending_indices.discard(idx)
                if idx in visited_indices:
                    continue
                visited_indices.add(idx)

                url = f"{_LIST_URL}?index={idx}"
                page.goto(url, wait_until="networkidle", timeout=20000)
                time.sleep(1.5)

                year_sel = page.query_selector("select")
                if year_sel:
                    year_sel.select_option(yr)
                    time.sleep(2)

                # Collect detail links
                for a in page.query_selector_all("a[href]"):
                    href = a.get_attribute("href") or ""
                    text = a.inner_text().strip()
                    m = detail_pattern.search(href)
                    if not m:
                        continue
                    slug = m.group(1)
                    if any(d["slug"] == slug for d in all_items):
                        continue
                    # Extract date and case number from link text
                    lines = [l.strip() for l in text.split("\n") if l.strip()]
                    list_date = lines[0] if lines else ""
                    list_case = lines[1] if len(lines) > 1 else ""
                    all_items.append({
                        "slug": slug,
                        "year": yr,
                        "list_date": list_date,
                        "list_case": list_case,
                    })

                # Find additional pages for this year
                for a in page.query_selector_all("a[href]"):
                    href = a.get_attribute("href") or ""
                    m = index_pattern.search(href)
                    if m:
                        i = int(m.group(1))
                        if i not in visited_indices:
                            pending_indices.add(i)

        browser.close()

    return all_items


def _scrape_detail_standalone(slug: str) -> dict:
    """Open a fresh Playwright browser, scrape one detail page, close."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            return _scrape_detail(page, slug)
        finally:
            browser.close()


def _scrape_detail(page: Any, slug: str) -> dict:
    """Visit detail page and extract all fields."""
    url = f"{_LIST_URL}akvordun/Úrlausnir/{slug}"
    page.goto(url, wait_until="networkidle", timeout=20000)
    time.sleep(2)

    body = page.inner_text("body")
    lines = [l.strip() for l in body.split("\n") if l.strip()]

    # Find content area (after breadcrumb)
    try:
        start = next(i for i, l in enumerate(lines) if "Ákvarðanir og úrskurðir" in l)
        lines = lines[start:]
    except StopIteration:
        pass

    def get_field(label: str, lines: list[str]) -> str | None:
        try:
            idx = next(i for i, l in enumerate(lines) if l.strip() == label)
        except StopIteration:
            return None
        # Collect consecutive non-label lines as value
        vals = []
        known_labels = {"Númer", "Heiti", "Dagsetning", "Málsaðilar", "Málaflokkur",
                        "Lagagreinar", "Reifun", "Skjöl", "Tengt efni"}
        for l in lines[idx + 1:]:
            if l in known_labels:
                break
            vals.append(l)
        return "\n".join(vals).strip() or None

    case_number_raw = get_field("Númer", lines)
    dagsetning = get_field("Dagsetning", lines)
    malsa_raw = get_field("Málsaðilar", lines)
    malafl = get_field("Málaflokkur", lines)
    reifun = get_field("Reifun", lines)

    # PDF link
    pdf_href = None
    for a in page.query_selector_all("a[href]"):
        href = a.get_attribute("href") or ""
        if "library" in href:
            pdf_href = _BASE_URL + href
            break

    return {
        "case_number_raw": case_number_raw,
        "dagsetning": dagsetning,
        "malsa_raw": malsa_raw,
        "malaflokk": malafl,
        "reifun": reifun,
        "pdf_url": pdf_href,
    }


# ── Extraction helpers ─────────────────────────────────────────────────────────

def _parse_date(dagsetning: str | None) -> date | None:
    if not dagsetning:
        return None
    m = _DATE_RE.search(dagsetning)
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _normalize_case_number(raw: str | None) -> str | None:
    if not raw:
        return None
    # Ursk_3_2024 → ÚRSK 3/2024
    m = _CASE_CLEAN_RE.match(raw)
    if m:
        return f"ÚRSK {int(m.group(1))}/{m.group(2)}"
    return raw.strip()


def _is_ursk(case_number: str | None) -> bool:
    return bool(case_number and _URSK_RE.match(case_number))


def _parse_defendants(malsa_raw: str | None) -> list[dict] | None:
    if not malsa_raw:
        return None
    names = [
        n.strip() for n in malsa_raw.split("\n")
        if n.strip() and "engir skráðir" not in n.lower()
    ]
    return [{"name": n} for n in names] if names else None


def _court(doc_date: date | None) -> str:
    if doc_date and doc_date < _PFS_CUTOFF:
        return "PFS."
    return "FST."


def _verdict_type(case_number: str | None) -> str:
    if _is_ursk(case_number):
        return "Úrskurður"
    return "Ákvörðun"


# ── PDF fetch ──────────────────────────────────────────────────────────────────

def _fetch_body_text(
    client: httpx.Client,
    pdf_url: str | None,
    case_number: str | None,
    config: SourceConfig,
) -> str | None:
    if not pdf_url:
        return None
    r = client.get(pdf_url, timeout=60)
    if r.status_code == 404:
        log.warning("PDF 404: %s", pdf_url)
        return None
    r.raise_for_status()

    if _is_ursk(case_number):
        # Image PDF — use Docling OCR
        text = docling_ocr_pdf(r.content)
        if not text:
            log.warning("Docling OCR returned nothing for %s", case_number)
        return text

    crop = config.pdf_crop
    return parse_pdf(
        r.content,
        header_pt=int(crop.header_pt) if crop else 0,
        footer_pt=int(crop.footer_pt) if crop else 0,
        skip_header_on_first=crop.skip_header_on_first if crop else False,
        heading_sizes=crop.heading_sizes if crop else {},
        heading_fonts=crop.heading_fonts if crop else {},
    )


# ── Build Document ─────────────────────────────────────────────────────────────

def _build_document(
    slug: str,
    detail: dict,
    body_text: str | None,
    source_id: uuid.UUID,
    config: SourceConfig,
) -> Document:
    case_number = _normalize_case_number(detail.get("case_number_raw"))
    doc_date = _parse_date(detail.get("dagsetning"))
    defendants = _parse_defendants(detail.get("malsa_raw"))
    malafl = detail.get("malaflokk")
    keywords = [malafl] if malafl else None

    doc = Document(
        id=uuid.uuid4(),
        source_id=source_id,
        external_id=slug,
        url=f"{_LIST_URL}akvordun/Úrlausnir/{slug}",
        raw_api_data={
            "slug": slug,
            "case_number_raw": detail.get("case_number_raw"),
            "dagsetning": detail.get("dagsetning"),
            "malsa_raw": detail.get("malsa_raw"),
            "malaflokk": malafl,
            "pdf_url": detail.get("pdf_url"),
        },
        case_number=case_number,
        document_date=doc_date,
        court=_court(doc_date),
        verdict_type=_verdict_type(case_number),
        instance_tier=config.instance_tier,
        plaintiffs=None,
        defendants=defendants,
        keywords=keywords,
        summary=detail.get("reifun"),
        body_text=body_text,
        lower_body_text=None,
    )
    doc.validation_errors = validate(doc, config) or None
    return doc


# ── DB helpers ─────────────────────────────────────────────────────────────────

async def _ensure_source(session: AsyncSession, config: SourceConfig) -> uuid.UUID:
    result = await session.execute(select(Source).where(Source.short_name == config.short_name))
    src = result.scalar_one_or_none()
    if src is None:
        src_id = uuid.uuid4()
        session.add(Source(
            id=src_id,
            short_name=config.short_name,
            display_name=config.display_name,
            base_url=_BASE_URL,
        ))
        await session.flush()
        return src_id
    return src.id


def _v(val: Any) -> Any:
    return sa_null() if val is None else val


async def _upsert_doc(session: AsyncSession, doc: Document) -> None:
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
        log.warning("write_markdown failed %s: %s", doc.external_id, exc)
        return None
    return vf


# ── Main ───────────────────────────────────────────────────────────────────────

async def main(limit: int | None = None, year: str | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    config = get_config("fjarskiptastofa")
    imported_slugs = _load_checkpoint()

    log.info("Collecting slugs from fjarskiptastofa.is…")
    all_items = await asyncio.to_thread(_collect_all_slugs, year)
    log.info("Total items found: %d", len(all_items))

    to_import = [it for it in all_items if it["slug"] not in imported_slugs]
    if limit is not None:
        to_import = to_import[:limit]
    log.info("%d items to import", len(to_import))

    if not to_import:
        log.info("Nothing to import.")
        return

    await init_db()

    async with _db_conn.AsyncSessionLocal() as session:
        source_id = await _ensure_source(session, config)
        await session.commit()

    async with _db_conn.AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(Document.verdict_filename)
            .where(Document.source_id == source_id)
            .where(Document.verdict_filename.isnot(None))
        )).scalars().all()
    taken: set[str] = set(rows)

    progress = Progress(
        SpinnerColumn(), "[progress.description]{task.description}",
        BarColumn(), TaskProgressColumn(),
        TimeElapsedColumn(), TimeRemainingColumn(),
    )

    with httpx.Client(headers=_HEADERS, follow_redirects=True) as client:
        with progress:
            task = progress.add_task("Importing fjarskiptastofa…", total=len(to_import))

            for item in to_import:
                slug = item["slug"]
                try:
                    detail = await asyncio.to_thread(_scrape_detail_standalone, slug)

                    body_text = await asyncio.to_thread(
                        _fetch_body_text,
                        client,
                        detail.get("pdf_url"),
                        _normalize_case_number(detail.get("case_number_raw")),
                        config,
                    )

                    doc = _build_document(slug, detail, body_text, source_id, config)

                    async with _db_conn.AsyncSessionLocal() as session:
                        await _upsert_doc(session, doc)
                        await session.commit()

                    vf = _render_and_save(doc, config, taken)
                    if vf:
                        async with _db_conn.AsyncSessionLocal() as session:
                            await session.execute(
                                update(Document)
                                .where(Document.source_id == doc.source_id,
                                       Document.external_id == doc.external_id)
                                .values(verdict_filename=vf)
                            )
                            await session.commit()

                    imported_slugs.add(slug)
                    _save_checkpoint(imported_slugs)

                    label = doc.case_number or slug
                    if doc.validation_errors:
                        errs = [e["field"] for e in doc.validation_errors]
                        if errs == ["keywords"]:
                            log.info("OK   %s", label)
                        else:
                            log.warning("WARN %s: %s", label, errs)
                    else:
                        log.info("OK   %s", label)

                    time.sleep(_REQUEST_DELAY)

                except Exception as exc:
                    log.error("FAIL %s: %s", slug, exc, exc_info=True)
                finally:
                    progress.advance(task)

    log.info("Done. Imported: %d, checkpoint: %d", len(to_import), len(imported_slugs))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--year", type=str, default=None, help="Import only this year, e.g. 2024")
    args = parser.parse_args()
    asyncio.run(main(limit=args.limit, year=args.year))
