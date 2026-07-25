"""Import Úrskurðarnefnd umhverfis- og auðlindamála rulings into lausnir_v2.

Source: https://uua.is/listi-yfir-urskurdi/ — HTML, no API, no PDFs.
~2,960 rulings, 1998–2026.

Listing page loads all links at once (no pagination).
Case number formats:
  - Old (pre-2025): NN/YYYY  — H1: '178/2024 Álfaskeið'
  - New (2025+):    UUAYYMMnnn — H1: 'UUA2603004 Vetrarmýri og Smalaholt'

Keyword: text after case number in H1 (e.g. 'Vetrarmýri og Smalaholt').
Summary: Úrskurðarorð section.
Date: 'Ár(ið) YYYY, (weekday) D. mánuður' — present in all eras.
External ID: URL slug (e.g. 'uua2603004-vetrarmyri-og-smalaholt-2').
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
from engine.processors.renderer import unique_verdict_filename, verdict_filename, write_markdown
from engine.processors.validator import validate

log = logging.getLogger(__name__)

_BASE_URL = "https://uua.is"
_LISTING_URL = f"{_BASE_URL}/listi-yfir-urskurdi/"
_CHECKPOINT_DIR = Path("checkpoints")
_CHECKPOINT_FILE = _CHECKPOINT_DIR / "uua.json"
_REQUEST_DELAY = 0.5

_MONTHS: dict[str, int] = {
    "janúar": 1, "febrúar": 2, "mars": 3, "apríl": 4, "maí": 5, "júní": 6,
    "júlí": 7, "ágúst": 8, "september": 9, "október": 10, "nóvember": 11, "desember": 12,
}
_MONTH_PAT = "|".join(_MONTHS)
_DATE_RE = re.compile(
    r"Ár(?:ið)?\s+(\d{4}),\s+(?:\w+\s+)?(\d{1,2})\.\s+(" + _MONTH_PAT + r")",
    re.IGNORECASE,
)
_LISTING_LINK_RE = re.compile(r'href="(https://uua\.is/urleits/[^"]+)"')


def _strip(html: str) -> str:
    html = unescape(html)
    html = re.sub(r"<[^>]+>", " ", html)
    html = html.replace("\xa0", " ")
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


# ── Fetch helpers ─────────────────────────────────────────────────────────────

async def _fetch_all_urls(client: httpx.AsyncClient) -> list[str]:
    """Fetch listing page and return all unique detail-page URLs."""
    r = await client.get(_LISTING_URL, timeout=30)
    r.raise_for_status()
    urls = list(dict.fromkeys(m.group(1) for m in _LISTING_LINK_RE.finditer(r.text)))
    return urls


# ── Extraction ────────────────────────────────────────────────────────────────

def _extract_case_and_keyword(html: str) -> tuple[str | None, str | None]:
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.DOTALL)
    if not h1:
        return None, None
    h1t = _strip(h1.group(1))
    m = re.match(r"^(\S+)\s+(.*)", h1t)
    if m:
        return m.group(1), m.group(2).strip() or None
    return h1t or None, None


def _extract_date(text: str) -> date | None:
    m = _DATE_RE.search(text)
    if m:
        return date(int(m.group(1)), _MONTHS[m.group(3).lower()], int(m.group(2)))
    return None


def _extract_summary(text: str) -> str | None:
    m = re.search(r"[ÚU]rskurðarorð\s*:?\s*(.*?)(?:\s*_{5,}|\s*$)", text, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip()[:500] if m else None


# ── Document builder ──────────────────────────────────────────────────────────

def _build_document(
    url: str,
    html: str,
    source_id: uuid.UUID,
    config: SourceConfig,
) -> Document:
    slug = url.rstrip("/").split("/")[-1]
    art_m = re.search(r"<article[^>]*>(.*?)</article>", html, re.DOTALL)
    text = _strip(art_m.group(1)) if art_m else _strip(html)

    case_number, keyword = _extract_case_and_keyword(html)
    doc_date = _extract_date(text)
    summary = _extract_summary(text)

    doc = Document(
        id=uuid.uuid4(),
        source_id=source_id,
        external_id=slug,
        url=url,
        raw_api_data={"slug": slug, "url": url},
        case_number=case_number,
        document_date=doc_date,
        court=config.abbreviation,
        verdict_type=config.verdict_type_default,
        instance_tier=config.instance_tier,
        plaintiffs=None,
        defendants=None,
        keywords=[keyword] if keyword else None,
        summary=summary,
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

async def main(limit: int | None = None, new_only: bool = False) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    config = get_config("uua")
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
        all_urls = await _fetch_all_urls(client)
        log.info("Total rulings: %d", len(all_urls))

        if new_only:
            pending = []
            for u in all_urls:
                if u.rstrip("/").split("/")[-1] in imported_ids:
                    log.info("Stopping at first known: %s", u.rstrip("/").split("/")[-1])
                    break
                pending.append(u)
        else:
            pending = [u for u in all_urls if u.rstrip("/").split("/")[-1] not in imported_ids]
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
            task_id = progress.add_task("Importing uua…", total=len(pending))

            for url in pending:
                slug = url.rstrip("/").split("/")[-1]
                if limit is not None and imported_count >= limit:
                    break

                try:
                    r = await client.get(url, timeout=30)
                    r.raise_for_status()
                    html = r.text
                except Exception as exc:
                    log.warning("Fetch failed for %s: %s", slug, exc)
                    imported_ids.add(slug)
                    _save_checkpoint(imported_ids, imported_count)
                    progress.advance(task_id)
                    await asyncio.sleep(_REQUEST_DELAY)
                    continue

                doc = _build_document(url, html, source_id, config)

                async with _db_conn.AsyncSessionLocal() as session:
                    try:
                        await _upsert_doc(session, doc)
                        await session.commit()
                    except Exception as exc:
                        log.error("Upsert failed for %s: %s", slug, exc)
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
                    log.info("OK   %s", doc.case_number)

                imported_ids.add(slug)
                imported_count += 1
                _save_checkpoint(imported_ids, imported_count)

                progress.update(
                    task_id,
                    advance=1,
                    description=(
                        f"Importing UUA.  "
                        f"[{doc.case_number}  Imported: {imported_count}  Errors: {total_errors}]"
                    ),
                )

                await asyncio.sleep(_REQUEST_DELAY)

    log.info("Done. Imported: %d, checkpoint: %d", imported_count, len(imported_ids))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Stop after N documents")
    parser.add_argument("--new-only", action="store_true", help="Stop at first known doc (newest-first listing)")
    args = parser.parse_args()
    asyncio.run(main(limit=args.limit, new_only=args.new_only))
