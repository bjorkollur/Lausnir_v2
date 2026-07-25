"""Import Hugverkastofa rulings into lausnir_v2.

Source: Umbraco Delivery API — https://api.hugverk.is/umbraco/delivery/api/v2/content
~1,108 rulings, 1985–2026. All have PDFs.

Three PDF layouts:
  1. Áfrýjunarnefnd (Áfrýjun*): opens with "Ár YYYY, … var haldinn fundur í
     áfrýjunarnefnd …". Parties on separate lines: petitioner / gegn / respondent.
  2. HVS Ákvörðun (Andmæli, Niðurfelling): "Ákvörðun Hugverkastofunnar / í XXXmáli
     / nr. N/YYYY / D. mánuður YYYY". Straight into facts.
  3. HVS Endurveiting/EP: similar header, with "Rétthafi:" and "Umboðsmaður:" lines.

Field mapping:
  - case_number  : first N/YYYY token from item.name; else registration field
  - document_date: item.properties.date (ISO); None if year 0001
  - verdict_type : item.properties.type (9 values, e.g. "Andmæli vörumerki")
  - summary      : item.properties.content (1-sentence description)
  - keywords     : item.properties.tags items
  - parties      : gegn-regex on content; PDF fallback for Áfrýjun items
  - body_text    : PDF text via parse_pdf()
  - external_id  : route.path slug
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import date, datetime
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

_API_URL = "https://api.hugverk.is/umbraco/delivery/api/v2/content"
_MEDIA_BASE = "https://api.hugverk.is"
_WWW_BASE = "https://www.hugverk.is"
_PAGE_SIZE = 100
_CHECKPOINT_DIR = Path("checkpoints")
_CHECKPOINT_FILE = _CHECKPOINT_DIR / "hugverkastofa.json"
_REQUEST_DELAY = 0.5


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


# ── Fetch ─────────────────────────────────────────────────────────────────────

async def _fetch_all_items(client: httpx.AsyncClient) -> list[dict]:
    """Paginate through all rule items and return them."""
    items: list[dict] = []
    skip = 0
    while True:
        r = await client.get(
            _API_URL,
            params={"filter": "contentType:rule", "take": _PAGE_SIZE, "skip": skip},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        batch = data["items"]
        items.extend(batch)
        if len(items) >= data["total"] or not batch:
            break
        skip += _PAGE_SIZE
        await asyncio.sleep(0.2)
    return items


# ── Extraction ────────────────────────────────────────────────────────────────

def _case_number(item: dict) -> str | None:
    m = re.match(r'^(\d+/\d{4})', item["name"])
    if m:
        return m.group(1)
    reg = (item["properties"].get("registration") or "").strip()
    return reg or None


def _document_date(item: dict) -> date | None:
    raw = item["properties"].get("date") or ""
    if not raw or raw.startswith("0001"):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except Exception:
        return None


def _parties_from_content(content: str) -> tuple[list | None, list | None]:
    m = re.search(r'(.{1,120}?)\s+gegn\s+(.{1,120}?)\s+(?:vegna|$)', content)
    if m:
        return [{"name": m.group(1).strip()}], [{"name": m.group(2).strip()}]
    return None, None


def _parties_from_pdf(body_text: str) -> tuple[list | None, list | None]:
    """Extract parties from Layout-1 PDF (Áfrýjunarnefnd).

    Pattern: "Fyrir var tekið mál nr. N/YYYY:\n[name lines]\ngegn\n[name lines]\nvegna"
    Plaintiff/defendant names may span multiple lines (e.g. firm + f.h. + country).
    """
    m = re.search(
        r'Fyrir var tekið mál nr\.[^\n]+\n((?:.+\n)+?)gegn\s*\n((?:.+\n)+?)vegna',
        body_text, re.IGNORECASE,
    )
    if m:
        plaintiff = re.sub(r'\s+', ' ', m.group(1)).strip()
        defendant = re.sub(r'\s+', ' ', m.group(2)).strip()
        if plaintiff and defendant:
            return [{"name": plaintiff}], [{"name": defendant}]
    return None, None


def _keywords(item: dict) -> list[str] | None:
    tags_obj = item["properties"].get("tags") or {}
    tag_items = tags_obj.get("items") or []
    vals = [t["content"]["properties"]["tag"] for t in tag_items if t.get("content")]
    return vals if vals else None


# ── Document builder ──────────────────────────────────────────────────────────

def _build_document(
    item: dict,
    pdf_bytes: bytes,
    source_id: uuid.UUID,
    config: SourceConfig,
) -> Document:
    props = item["properties"]
    body_text = parse_pdf(pdf_bytes) or ""

    content = props.get("content") or ""
    plaintiffs, defendants = _parties_from_content(content)
    if plaintiffs is None:
        plaintiffs, defendants = _parties_from_pdf(body_text)

    doc = Document(
        id=uuid.uuid4(),
        source_id=source_id,
        external_id=item["route"]["path"].rstrip("/").split("/")[-1],
        url=_WWW_BASE + item["route"]["path"],
        raw_api_data=item,
        case_number=_case_number(item),
        document_date=_document_date(item),
        court=config.abbreviation,
        verdict_type=props.get("type"),
        instance_tier=config.instance_tier,
        plaintiffs=plaintiffs,
        defendants=defendants,
        keywords=_keywords(item),
        summary=content[:300] if content else None,
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
            base_url=_WWW_BASE + "/utgafa/urskurdir-og-akvardanir/",
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

    config = get_config("hugverkastofa")
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
        log.info("Fetching item list from API…")
        all_items = await _fetch_all_items(client)
        log.info("Total items: %d", len(all_items))

        if new_only:
            pending = []
            for it in all_items:
                ext_id = it["route"]["path"].rstrip("/").split("/")[-1]
                if ext_id in imported_ids:
                    log.info("Stopping at first known: %s", ext_id)
                    break
                pending.append(it)
        else:
            pending = [it for it in all_items
                       if it["route"]["path"].rstrip("/").split("/")[-1] not in imported_ids]
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
            task_id = progress.add_task("Importing hugverkastofa…", total=len(pending))

            for item in pending:
                if limit is not None and imported_count >= limit:
                    break

                ext_id = item["route"]["path"].rstrip("/").split("/")[-1]
                props = item["properties"]
                files = props.get("file") or []
                pdf_url = _MEDIA_BASE + files[0]["url"] if files else None

                try:
                    if pdf_url:
                        r = await client.get(pdf_url, timeout=60, follow_redirects=True)
                        r.raise_for_status()
                        pdf_bytes = r.content
                    else:
                        pdf_bytes = b""
                except Exception as exc:
                    log.warning("PDF fetch failed for %s: %s", ext_id, exc)
                    imported_ids.add(ext_id)
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
                        log.error("Upsert failed for %s: %s", ext_id, exc)
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

                imported_ids.add(ext_id)
                imported_count += 1
                _save_checkpoint(imported_ids, imported_count)

                progress.update(
                    task_id,
                    advance=1,
                    description=(
                        f"Importing HVS.  "
                        f"[{doc.case_number}  {doc.verdict_type}  "
                        f"Imported: {imported_count}  Errors: {total_errors}]"
                    ),
                )

                await asyncio.sleep(_REQUEST_DELAY)

    log.info("Done. Imported: %d, checkpoint: %d", imported_count, len(imported_ids))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Stop after N documents")
    parser.add_argument("--new-only", action="store_true", help="Stop at first known item (newest-first API)")
    args = parser.parse_args()
    asyncio.run(main(limit=args.limit, new_only=args.new_only))
