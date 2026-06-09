"""Import Fjölmiðlanefnd rulings from fjolmidlanefnd.is into lausnir_v2.

Fetches all posts in category 6 (urlausnir) via WordPress REST API,
extracts fields from title, uses content.rendered as summary, and
downloads the linked PDF for body_text.

Usage:
    uv run python scripts/import_fjolmidlanefnd.py
    uv run python scripts/import_fjolmidlanefnd.py --limit 5
"""
from __future__ import annotations

import asyncio
import html as _html
import json
import logging
import re
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup
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
from engine.processors.pdf_parser import parse_pdf
from engine.processors.renderer import unique_verdict_filename, verdict_filename, write_markdown
from engine.processors.validator import validate

log = logging.getLogger(__name__)

_BASE_URL = "https://fjolmidlanefnd.is"
_WP_API = f"{_BASE_URL}/wp-json/wp/v2"
_CATEGORY_ID = 6
_REQUEST_DELAY = 1.0
_CHECKPOINT_DIR = Path("checkpoints")
_CHECKPOINT_FILE = _CHECKPOINT_DIR / "fjolmidlanefnd.json"

_MONTH_MAP = {
    "janúar": 1, "febrúar": 2, "mars": 3, "apríl": 4, "maí": 5, "júní": 6,
    "júlí": 7, "ágúst": 8, "september": 9, "október": 10, "nóvember": 11, "desember": 12,
}
_CASE_RE = re.compile(r"(\d+/\d{4})")
_DATE_AFTER_DASH = re.compile(r"–\s*(\d{1,2})\.\s+(\w+)\s+(\d{4})")
_DATE_ANY = re.compile(r"(\d{1,2})\.\s+(\w+)\s+(\d{4})")
_TYPE_RE = re.compile(r"^(Ákvörðun|Álit)\b")

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; Lausnir/2.0; +https://lausnir.is)",
    "Accept": "application/json",
}


# ── Checkpoint ────────────────────────────────────────────────────────────────

def _load_checkpoint() -> set[int]:
    if _CHECKPOINT_FILE.exists():
        data = json.loads(_CHECKPOINT_FILE.read_text())
        return set(data.get("imported_ids", []))
    return set()


def _save_checkpoint(imported_ids: set[int]) -> None:
    _CHECKPOINT_DIR.mkdir(exist_ok=True)
    _CHECKPOINT_FILE.write_text(
        json.dumps({"imported_ids": sorted(imported_ids), "count": len(imported_ids)}, indent=2)
    )


# ── WP REST fetch ──────────────────────────────────────────────────────────────

def _fetch_all_posts(client: httpx.Client) -> list[dict]:
    """Fetch all urlausnir posts (category 6). Fits in one page (≤100)."""
    r = client.get(
        f"{_WP_API}/posts",
        params={
            "categories": _CATEGORY_ID,
            "per_page": 100,
            "_fields": "id,title,link,date,content,excerpt",
        },
        timeout=20,
    )
    r.raise_for_status()
    posts = r.json()
    total_pages = int(r.headers.get("X-WP-TotalPages", 1))
    if total_pages > 1:
        for page in range(2, total_pages + 1):
            r2 = client.get(
                f"{_WP_API}/posts",
                params={
                    "categories": _CATEGORY_ID,
                    "per_page": 100,
                    "page": page,
                    "_fields": "id,title,link,date,content,excerpt",
                },
                timeout=20,
            )
            r2.raise_for_status()
            posts.extend(r2.json())
    return posts


# ── Extraction ─────────────────────────────────────────────────────────────────

def _parse_date(title: str) -> date | None:
    m = _DATE_AFTER_DASH.search(title)
    if not m:
        all_m = list(_DATE_ANY.finditer(title))
        m = all_m[-1] if all_m else None
    if not m:
        return None
    mon = _MONTH_MAP.get(m.group(2).lower())
    return date(int(m.group(3)), mon, int(m.group(1))) if mon else None


def _extract(post: dict, config: SourceConfig) -> dict:
    title = _html.unescape(post["title"]["rendered"])

    case_m = _CASE_RE.search(title)
    case_number = case_m.group(1) if case_m else None

    doc_date = _parse_date(title)

    type_m = _TYPE_RE.search(title)
    verdict_type = type_m.group(1) if type_m else config.verdict_type_default

    soup = BeautifulSoup(post["content"]["rendered"], "html.parser")
    summary = soup.get_text(separator=" ", strip=True) or None

    pdf_links = [a["href"] for a in soup.find_all("a", href=True) if ".pdf" in a["href"].lower()]
    pdf_url = pdf_links[0] if pdf_links else None

    return {
        "case_number": case_number,
        "document_date": doc_date,
        "verdict_type": verdict_type,
        "summary": summary,
        "pdf_url": pdf_url,
    }


def _fetch_pdf_text(client: httpx.Client, pdf_url: str, config: SourceConfig) -> str | None:
    r = client.get(pdf_url, timeout=30)
    if r.status_code == 404:
        log.warning("PDF 404: %s", pdf_url)
        return None
    r.raise_for_status()
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
    post: dict,
    extracted: dict,
    body_text: str | None,
    source_id: uuid.UUID,
    config: SourceConfig,
) -> Document:
    doc = Document(
        id=uuid.uuid4(),
        source_id=source_id,
        external_id=str(post["id"]),
        url=post["link"],
        raw_api_data={
            "wp_id": post["id"],
            "title": _html.unescape(post["title"]["rendered"]),
            "date": post["date"],
            "pdf_url": extracted.get("pdf_url"),
        },
        case_number=extracted.get("case_number"),
        document_date=extracted.get("document_date"),
        court=config.abbreviation,
        verdict_type=extracted.get("verdict_type", config.verdict_type_default),
        instance_tier=config.instance_tier,
        plaintiffs=None,
        defendants=None,
        keywords=None,
        summary=extracted.get("summary"),
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

async def main(limit: int | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    config = get_config("fjolmidlanefnd")
    imported_ids = _load_checkpoint()

    with httpx.Client(headers=_HEADERS, follow_redirects=True) as client:
        log.info("Fetching post list from WP REST API…")
        all_posts = _fetch_all_posts(client)
        log.info("Total posts: %d", len(all_posts))

        to_import = [p for p in all_posts if int(p["id"]) not in imported_ids]
        if limit is not None:
            to_import = to_import[:limit]
        log.info("%d posts to import", len(to_import))

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
        with progress:
            task = progress.add_task("Importing fjolmidlanefnd…", total=len(to_import))

            for post in to_import:
                wp_id = int(post["id"])
                try:
                    extracted = _extract(post, config)

                    body_text = None
                    if extracted["pdf_url"]:
                        body_text = _fetch_pdf_text(client, extracted["pdf_url"], config)
                    else:
                        log.warning("NO PDF for wp_id=%d %s", wp_id, extracted.get("case_number"))

                    doc = _build_document(post, extracted, body_text, source_id, config)

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

                    imported_ids.add(wp_id)
                    _save_checkpoint(imported_ids)

                    label = extracted.get("case_number") or str(wp_id)
                    if doc.validation_errors:
                        errs = [e["field"] for e in doc.validation_errors]
                        if errs != ["keywords"]:
                            log.warning("WARN %s: %s", label, errs)
                        else:
                            log.info("OK   %s", label)
                    else:
                        log.info("OK   %s", label)

                    await asyncio.sleep(_REQUEST_DELAY)

                except Exception as exc:
                    log.error("FAIL wp_id=%d: %s", wp_id, exc, exc_info=True)
                finally:
                    progress.advance(task)

        log.info("Done. Imported: %d, checkpoint: %d", len(to_import), len(imported_ids))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    asyncio.run(main(limit=args.limit))
