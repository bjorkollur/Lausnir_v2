"""Import Félagsdómur verdicts from felagsdomur.is into lausnir_v2.

Fetches the full list via the paginated AJAX endpoint, downloads each PDF,
parses body text, and stores the result.

Usage:
    uv run python scripts/import_felagsdomur.py
    uv run python scripts/import_felagsdomur.py --limit 3
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
from urllib.parse import parse_qs, urlparse

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

_BASE_URL = "https://felagsdomur.is"
_LIST_PAGEID = "f96f953c-06db-11ea-80f1-005056bc217f"
_LIST_URL = (
    f"{_BASE_URL}/default.aspx"
    f"?pageitemid={_LIST_PAGEID}&offset={{offset}}&count={{count}}"
)
_PDF_URL = f"{_BASE_URL}/Cache/Verdicts/{{doc_uuid}}.pdf"
_REQUEST_DELAY = 1.0
_CHECKPOINT_DIR = Path("checkpoints")
_CHECKPOINT_FILE = _CHECKPOINT_DIR / "felagsdomur.json"

_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "maí": 5, "jún": 6,
    "júl": 7, "ág": 8, "sep": 9, "okt": 10, "nóv": 11, "des": 12,
}

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


# ── Checkpoint ────────────────────────────────────────────────────────────────

def _load_checkpoint() -> set[str]:
    """Return set of already-imported external_ids."""
    if _CHECKPOINT_FILE.exists():
        data = json.loads(_CHECKPOINT_FILE.read_text())
        return set(data.get("imported_ids", []))
    return set()


def _save_checkpoint(imported_ids: set[str]) -> None:
    _CHECKPOINT_DIR.mkdir(exist_ok=True)
    _CHECKPOINT_FILE.write_text(
        json.dumps({"imported_ids": sorted(imported_ids), "count": len(imported_ids)}, indent=2)
    )


# ── HTML parsing ───────────────────────────────────────────────────────────────

def _parse_date(box: BeautifulSoup) -> date | None:
    md = box.select_one("div.media-date")
    if not md:
        return None
    try:
        day = int(md.select_one("div.day").get_text(strip=True))
        month_el = md.select_one("div.month")
        month_text = month_el.find(string=True, recursive=False).strip().lower()
        year = int(month_el.select_one("div.year").get_text(strip=True))
        month = _MONTH_MAP.get(month_text)
        if not month:
            return None
        return date(year, month, day)
    except Exception:
        return None


def _parse_parties(box: BeautifulSoup) -> tuple[list[dict], list[dict]]:
    p = box.select_one("p.ellipsis")
    if not p:
        return [], []

    html = str(p)
    if "<strong>gegn</strong>" not in html:
        return [], []

    parts = html.split("<strong>gegn</strong>")
    plaintiff_html, defendant_html = parts[0], parts[1]

    def _extract_names(html_frag: str) -> list[dict]:
        soup = BeautifulSoup(html_frag, "html.parser")
        results = []
        for strong in soup.find_all("strong"):
            name = strong.get_text(strip=True)
            if not name or name.lower() == "gegn":
                continue
            lawyer = ""
            next_sib = strong.next_sibling
            if next_sib and isinstance(next_sib, str):
                m = re.search(r"\(([^)]+)\)", next_sib)
                if m:
                    lawyer = m.group(1).strip()
            entry: dict[str, Any] = {"name": name}
            if lawyer:
                entry["lawyer"] = lawyer
            results.append(entry)
        return results

    return _extract_names(plaintiff_html), _extract_names(defendant_html)


def _parse_list_page(html: str) -> list[dict]:
    """Parse list page HTML into raw verdict dicts."""
    soup = BeautifulSoup(html, "html.parser")
    boxes = soup.select("div.verdict-box")
    results = []
    for box in boxes:
        anchor = box.select_one("a.sentence")
        if not anchor:
            continue
        href = anchor.get("href", "")
        qs = parse_qs(urlparse(href).query)
        ext_id = qs.get("id", [None])[0]
        if not ext_id:
            continue

        h2 = box.select_one("h2")
        case_number = h2.get_text(strip=True) if h2 else None
        doc_date = _parse_date(box)
        keywords_el = box.select_one("p.keywords")
        keywords = [k.strip() for k in keywords_el.get_text().split(",") if k.strip()] if keywords_el else []
        abstract_el = box.select_one("div.case-abstract")
        summary = abstract_el.get_text(separator="\n", strip=True) if abstract_el else None
        plaintiffs, defendants = _parse_parties(box)

        results.append({
            "external_id": ext_id,
            "case_number": case_number,
            "document_date": doc_date,
            "keywords": keywords or None,
            "summary": summary,
            "plaintiffs": plaintiffs or None,
            "defendants": defendants or None,
        })
    return results


def _fetch_all_list_items(client: httpx.Client) -> list[dict]:
    all_items: list[dict] = []
    offset = 0
    batch_size = 22  # server always returns 22 items per page
    while True:
        url = _LIST_URL.format(offset=offset, count=batch_size)
        log.info("Fetching list offset=%d", offset)
        r = client.get(url, timeout=20)
        r.raise_for_status()
        batch = _parse_list_page(r.text)
        if not batch:
            break
        all_items.extend(batch)
        if len(batch) < batch_size:
            break
        offset += batch_size
    return all_items


def _fetch_pdf_text(client: httpx.Client, ext_id: str, config: SourceConfig) -> str | None:
    url = _PDF_URL.format(doc_uuid=ext_id)
    r = client.get(url, timeout=30)
    if r.status_code == 404:
        log.warning("PDF not found: %s", url)
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

def _detect_verdict_type(body_text: str, default: str) -> str:
    for m in re.finditer(r"^##\s+(.+)$", body_text, re.MULTILINE):
        heading = m.group(1).strip().lower()
        if "dómur" in heading:
            return "Dómur"
        if "úrskurður" in heading:
            return "Úrskurður"
    return default


def _build_document(item: dict, body_text: str | None, source_id: uuid.UUID, config: SourceConfig) -> Document:
    verdict_type = (
        _detect_verdict_type(body_text, config.verdict_type_default)
        if body_text else config.verdict_type_default
    )
    doc = Document(
        id=uuid.uuid4(),
        source_id=source_id,
        external_id=item["external_id"],
        url=f"{_BASE_URL}/domar-og-urskurdir/domur-urskurdur/?id={item['external_id']}",
        raw_api_data={
            k: (v.isoformat() if isinstance(v, date) else v)
            for k, v in item.items()
            if k != "body_text" and v is not None
        },
        case_number=item.get("case_number"),
        document_date=item.get("document_date"),
        court=config.abbreviation,
        verdict_type=verdict_type,
        instance_tier=config.instance_tier,
        plaintiffs=item.get("plaintiffs"),
        defendants=item.get("defendants"),
        keywords=item.get("keywords"),
        summary=item.get("summary"),
        body_text=body_text,
        lower_body_text=None,
    )
    doc.validation_errors = validate(doc, config) or None
    return doc


# ── DB helpers ─────────────────────────────────────────────────────────────────

async def _ensure_source(session: AsyncSession, config: SourceConfig) -> uuid.UUID:
    result = await session.execute(
        select(Source).where(Source.short_name == config.short_name)
    )
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
        .on_conflict_do_update(
            constraint="uq_doc_source_external",
            set_=update_cols,
        )
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


# ── Main ───────────────────────────────────────────────────────────────────────

async def main(limit: int | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    config = get_config("felagsdomur")
    imported_ids = _load_checkpoint()

    with httpx.Client(headers=_HEADERS, follow_redirects=True) as client:
        log.info("Fetching document list from felagsdomur.is…")
        all_items = _fetch_all_list_items(client)
        log.info("Found %d documents total", len(all_items))

        to_import = [x for x in all_items if x["external_id"] not in imported_ids]
        if limit is not None:
            to_import = to_import[:limit]
        log.info("%d documents to import", len(to_import))

        if not to_import:
            log.info("Nothing to import.")
            return

        await init_db()

        async with _db_conn.AsyncSessionLocal() as session:
            source_id = await _ensure_source(session, config)
            await session.commit()

        # Load existing verdict_filenames for collision avoidance
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
            task = progress.add_task("Importing felagsdomur…", total=len(to_import))

            for item in to_import:
                ext_id = item["external_id"]
                try:
                    body_text = _fetch_pdf_text(client, ext_id, config)
                    doc = _build_document(item, body_text, source_id, config)

                    async with _db_conn.AsyncSessionLocal() as session:
                        await _upsert_doc(session, doc)
                        await session.commit()

                    vf = _render_and_save(doc, config, taken)
                    if vf:
                        async with _db_conn.AsyncSessionLocal() as session:
                            await session.execute(
                                update(Document)
                                .where(
                                    Document.source_id == doc.source_id,
                                    Document.external_id == doc.external_id,
                                )
                                .values(verdict_filename=vf)
                            )
                            await session.commit()

                    imported_ids.add(ext_id)
                    _save_checkpoint(imported_ids)

                    if doc.validation_errors:
                        log.warning(
                            "%s validation_errors: %s",
                            item.get("case_number", ext_id),
                            doc.validation_errors,
                        )
                    else:
                        log.info("OK: %s", item.get("case_number", ext_id))

                    await asyncio.sleep(_REQUEST_DELAY)

                except Exception as exc:
                    log.error("FAILED %s: %s", ext_id, exc, exc_info=True)
                finally:
                    progress.advance(task)

        log.info("Done. Total in checkpoint: %d", len(imported_ids))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    asyncio.run(main(limit=args.limit))
