"""Import Úrskurðarnefnd lögmanna rulings into lausnir_v2.

Source: https://www.lmfi.is/urskurdarnefnd-logmanna/urskurdir-urskurdarnefndar-logmanna
~453 rulings, 2004–2025. No API, no PDFs — pure HTML scrape.

Listing: paginated at ?page=N (46 pages, 10 items each).
Detail page structure:
  Ár YYYY, (weekday) D. mánuður … Fyrir var tekið málið: A gegn B og kveðinn upp svofelldur
  Ú R S K U R Ð U R :   ← summary (reifun) starts here
  …reifun…
  Málsatvik og málsástæður  ← body_text starts here

External ID: URL slug (e.g. 'mal-612024').
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

_BASE_URL = "https://www.lmfi.is"
_LISTING_PATH = "/urskurdarnefnd-logmanna/urskurdir-urskurdarnefndar-logmanna"
_CHECKPOINT_DIR = Path("checkpoints")
_CHECKPOINT_FILE = _CHECKPOINT_DIR / "urskurdarnefnd_logmanna.json"
_REQUEST_DELAY = 0.5

_MONTHS: dict[str, int] = {
    "janúar": 1, "febrúar": 2, "mars": 3, "apríl": 4, "maí": 5, "júní": 6,
    "júlí": 7, "ágúst": 8, "september": 9, "október": 10, "nóvember": 11, "desember": 12,
}
_MONTH_PAT = "|".join(_MONTHS)
_DATE_RE = re.compile(
    r"Ár\s+(\d{4}),\s+(?:\w+\s+)?(\d{1,2})\.\s+(" + _MONTH_PAT + r")",
    re.IGNORECASE,
)
_UR_RE = re.compile(r"Ú\s*R\s*S\s*K\s*U\s*R\s*[ÐD]\s*U\s*R\s*:", re.IGNORECASE)
_LISTING_LINK_RE = re.compile(
    r'href="(' + re.escape(_LISTING_PATH) + r'/\d{4}/[^"]+)"'
)


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

async def _fetch_listing_paths(client: httpx.AsyncClient) -> list[str]:
    """Return all detail-page paths from all listing pages."""
    # First fetch page 1 to find total page count
    r = await client.get(_BASE_URL + _LISTING_PATH, timeout=30)
    r.raise_for_status()
    pages = re.findall(r"\?page=(\d+)", r.text)
    max_page = max(int(p) for p in pages) if pages else 1

    all_paths: list[str] = []
    seen: set[str] = set()

    def _extract(html: str) -> None:
        for m in _LISTING_LINK_RE.finditer(html):
            p = m.group(1)
            if p not in seen:
                seen.add(p)
                all_paths.append(p)

    _extract(r.text)

    for page in range(2, max_page + 1):
        r = await client.get(_BASE_URL + _LISTING_PATH, params={"page": page}, timeout=30)
        r.raise_for_status()
        _extract(r.text)
        await asyncio.sleep(0.2)

    return all_paths


# ── Extraction ────────────────────────────────────────────────────────────────

def _extract_case_number(html: str, text: str) -> str | None:
    m = re.search(r"[Mm]ál(?:ið)?\s+nr\.\s*(\d+/\d{4})", text)
    if m:
        return m.group(1)
    h1 = re.search(r"<h1[^>]*>([^<]+)</h1>", html)
    if h1:
        h1t = _strip(h1.group(1))
        m2 = re.search(r"(\d+)[/\s]+(\d{4})", h1t)
        if m2:
            return f"{m2.group(1)}/{m2.group(2)}"
    return None


def _extract_date(text: str) -> date | None:
    m = _DATE_RE.search(text)
    if m:
        return date(int(m.group(1)), _MONTHS[m.group(3).lower()], int(m.group(2)))
    return None


def _extract_parties(text: str) -> tuple[str | None, str | None]:
    m = re.search(r":\s*([^:]{1,60}?)\s+gegn\s+(.+?)\s+og kveðinn", text, re.IGNORECASE)
    if m:
        return m.group(1).strip().rstrip(","), m.group(2).strip().rstrip(",")
    return None, None


def _extract_summary_and_body(text: str) -> tuple[str | None, str]:
    ur_m = _UR_RE.search(text)
    mal_idx = text.find("Málsatvik og málsástæður")

    if ur_m and mal_idx > ur_m.start():
        summary = text[ur_m.end():mal_idx].strip()
        body = text[mal_idx:].strip()
    elif ur_m:
        summary = None
        body = text[ur_m.start():].strip()
    elif mal_idx > 0:
        summary = None
        body = text[mal_idx:].strip()
    else:
        summary = None
        body = text

    return summary or None, body


# ── Document builder ──────────────────────────────────────────────────────────

def _build_document(
    path: str,
    html: str,
    source_id: uuid.UUID,
    config: SourceConfig,
) -> Document:
    slug = path.rstrip("/").split("/")[-1]
    art_m = re.search(r"<article[^>]*>(.*?)</article>", html, re.DOTALL)
    text = _strip(art_m.group(1)) if art_m else _strip(html)

    case_number = _extract_case_number(html, text)
    doc_date = _extract_date(text)
    plaintiff, defendant = _extract_parties(text)
    summary, body_text = _extract_summary_and_body(text)

    plaintiffs = [{"name": plaintiff}] if plaintiff else None
    defendants = [{"name": defendant}] if defendant else None

    doc = Document(
        id=uuid.uuid4(),
        source_id=source_id,
        external_id=slug,
        url=_BASE_URL + path,
        raw_api_data={"slug": slug, "path": path},
        case_number=case_number,
        document_date=doc_date,
        court=config.abbreviation,
        verdict_type=config.verdict_type_default,
        instance_tier=config.instance_tier,
        plaintiffs=plaintiffs,
        defendants=defendants,
        keywords=None,
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
            base_url=_BASE_URL + _LISTING_PATH,
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

    config = get_config("urskurdarnefnd_logmanna")
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
        log.info("Collecting listing pages…")
        all_paths = await _fetch_listing_paths(client)
        log.info("Total paths: %d", len(all_paths))

        if new_only:
            pending = []
            for p in all_paths:
                if p.rstrip("/").split("/")[-1] in imported_ids:
                    log.info("Stopping at first known: %s", p.rstrip("/").split("/")[-1])
                    break
                pending.append(p)
        else:
            pending = [p for p in all_paths if p.rstrip("/").split("/")[-1] not in imported_ids]
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
            task_id = progress.add_task("Importing urskurdarnefnd_logmanna…", total=len(pending))

            for path in pending:
                slug = path.rstrip("/").split("/")[-1]
                if limit is not None and imported_count >= limit:
                    break

                try:
                    r = await client.get(_BASE_URL + path, timeout=30)
                    r.raise_for_status()
                    html = r.text
                except Exception as exc:
                    log.warning("Fetch failed for %s: %s", slug, exc)
                    imported_ids.add(slug)
                    _save_checkpoint(imported_ids, imported_count)
                    progress.advance(task_id)
                    await asyncio.sleep(_REQUEST_DELAY)
                    continue

                doc = _build_document(path, html, source_id, config)

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
                        f"Importing ÚLM.  "
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
