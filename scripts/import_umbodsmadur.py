"""Import all Umboðsmaður Alþingis cases from umbodsmadur.is into lausnir_v2.

Cases are fetched by sequential URL ID (1 → current max). One HTTP GET per case.
The import stops after MAX_CONSECUTIVE_404 misses in a row.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
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

from engine.config.sources import SourceConfig, get_config
import engine.database.connection as _db_conn
from engine.database.connection import init_db
from engine.database.models import Document, Source
from engine.processors.extractor import Extractor
from engine.processors.http_utils import get_with_retry, make_client
from engine.processors.renderer import unique_verdict_filename, verdict_filename, write_markdown
from engine.processors.validator import validate

log = logging.getLogger(__name__)

_BASE_URL = "https://umbodsmadur.is/alit-og-bref/mal/nr/{id}/skoda/mal/"
_CHECKPOINT_DIR = Path("checkpoints")
_CHECKPOINT_FILE = _CHECKPOINT_DIR / "umbodsmadur.json"
_REQUEST_DELAY = 0.5  # seconds between requests


# ── Checkpoint ────────────────────────────────────────────────────────────────

def _load_checkpoint() -> tuple[int, int]:
    """Return (start_id, imported_count). Defaults to (1, 0)."""
    if _CHECKPOINT_FILE.exists():
        data = json.loads(_CHECKPOINT_FILE.read_text())
        return data["last_completed_id"] + 1, data["imported"]
    return 1, 0


def _save_checkpoint(case_id: int, imported: int) -> None:
    _CHECKPOINT_DIR.mkdir(exist_ok=True)
    _CHECKPOINT_FILE.write_text(
        json.dumps({"last_completed_id": case_id, "imported": imported}, indent=2)
    )


# ── HTML parser ───────────────────────────────────────────────────────────────

_CASE_NUMBER_RE = re.compile(r'M[aá]l\s+nr\.\s*([^\)]+)')


def _parse_case_page(html: str, url_id: int, url: str) -> dict | None:
    """Parse a case HTML page into a raw dict. Returns None if page is not a valid case."""
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.select_one(".page-header h1")
    if not h1:
        return None
    verdict_type = h1.get_text(strip=True)
    if verdict_type not in ("Álit", "Bréf"):
        return None

    case = soup.select_one("section.case")
    if not case:
        return None

    h3 = case.select_one("h3")
    title = h3.get_text(strip=True) if h3 else ""

    h4 = case.select_one("h4")
    case_number_str = ""
    if h4:
        m = _CASE_NUMBER_RE.search(h4.get_text())
        if m:
            case_number_str = m.group(1).strip()

    reifun_div = case.select_one("div.reifun")
    reifun_text = reifun_div.get_text(separator="\n", strip=True) if reifun_div else ""

    alit_div = case.select_one("div.alit")
    body_text = alit_div.get_text(separator="\n", strip=True) if alit_div else ""

    alit_sec = case.select_one("div.alit-sec")
    if alit_sec:
        sec = alit_sec.get_text(separator="\n", strip=True)
        if sec:
            body_text = f"{body_text}\n\n{sec}".strip()

    return {
        "url_id": url_id,
        "url": url,
        "title": title,
        "case_number_str": case_number_str,
        "verdict_type": verdict_type,
        "reifun_text": reifun_text,
        "body_text": body_text,
    }


# ── Document builder ──────────────────────────────────────────────────────────

def _build_document(
    raw: dict,
    source_id: uuid.UUID,
    config: SourceConfig,
) -> Document:
    fields = Extractor(config).extract(raw)
    ext_id = str(raw["url_id"])
    doc = Document(
        id=uuid.uuid4(),
        source_id=source_id,
        external_id=ext_id,
        url=raw["url"],
        **fields,
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
            base_url="https://umbodsmadur.is/alit-og-bref",
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
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    config = get_config("umbodsmadur")
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

    start_id, imported_count = _load_checkpoint()
    total_errors = 0

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
        task_id = progress.add_task("Importing umbodsmadur…", total=None)

        async with make_client() as client:
            case_id = start_id

            while True:
                if limit is not None and imported_count >= limit:
                    break

                url = _BASE_URL.format(id=case_id)

                try:
                    resp = await get_with_retry(client, url)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 404:
                        # Retry once (x2 total), then move to next ID — never stop
                        await asyncio.sleep(_REQUEST_DELAY)
                        try:
                            resp2 = await client.get(url)
                            resp2.raise_for_status()
                            resp = resp2
                        except Exception:
                            case_id += 1
                            await asyncio.sleep(_REQUEST_DELAY)
                            continue
                    else:
                        log.warning("Fetch error for ID %d: %s", case_id, exc)
                        case_id += 1
                        await asyncio.sleep(_REQUEST_DELAY)
                        continue
                except Exception as exc:
                    log.warning("Fetch error for ID %d: %s", case_id, exc)
                    case_id += 1
                    await asyncio.sleep(_REQUEST_DELAY)
                    continue

                raw = _parse_case_page(resp.text, case_id, url)
                if raw is None:
                    log.warning("Could not parse case ID %d — skipping", case_id)
                    _save_checkpoint(case_id, imported_count)
                    case_id += 1
                    await asyncio.sleep(_REQUEST_DELAY)
                    continue

                doc = _build_document(raw, source_id, config)

                async with _db_conn.AsyncSessionLocal() as session:
                    try:
                        await _upsert_doc(session, doc)
                        await session.commit()
                    except Exception as exc:
                        log.error("Upsert failed for ID %d: %s", case_id, exc)
                        await session.rollback()

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

                imported_count += 1
                _save_checkpoint(case_id, imported_count)

                progress.update(
                    task_id,
                    advance=1,
                    description=(
                        f"Importing umbodsmadur  "
                        f"[ID {case_id}  Imported: {imported_count}  Errors: {total_errors}]"
                    ),
                )

                case_id += 1
                await asyncio.sleep(_REQUEST_DELAY)

    log.info("Done. %d docs imported, %d with validation errors.", imported_count, total_errors)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Stop after N documents")
    args = parser.parse_args()
    asyncio.run(main(limit=args.limit))
