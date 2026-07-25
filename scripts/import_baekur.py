"""Import law books from a local dropfolder into lausnir_v2.

Unlike every other source, this one has no external API — a human drops a
PDF into {DATA_DIR}/dropfolder/ and this script:
  1. Extracts full text (parse_pdf -> docling_ocr_pdf fallback, with a longer
     OCR timeout than court rulings since books run hundreds of pages)
  2. Resolves title/author/ISBN via the tiered metadata chain
     (engine/processors/book_metadata.py)
  3. Builds + validates + upserts the Document, writes .md, moves the PDF
     to {DATA_DIR}/raw/logfraedibaekur/{external_id}.pdf

Chunking (document_chunks + fts_is) is NOT done here — run
    uv run python scripts/backfill_chunks.py --source logfraedibaekur
afterwards, same as for logfraediritgerdir.

Usage:
    set -a; . ./.env; set +a
    uv run python scripts/import_baekur.py --dry-run
    uv run python scripts/import_baekur.py
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import unicodedata
import uuid
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, null as sa_null, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from engine.config.sources import DROPFOLDER_DIR, SourceConfig, get_config
import engine.database.connection as _db_conn
from engine.database.connection import init_db
from engine.database.models import Document, Source
from engine.processors.book_metadata import resolve_book_metadata
from engine.processors.extractor import Extractor
from engine.processors.http_utils import make_client
from engine.processors.pdf_parser import docling_ocr_pdf, parse_pdf
from engine.processors.renderer import unique_verdict_filename, write_markdown
from engine.processors.validator import validate

log = logging.getLogger(__name__)

_OCR_TIMEOUT = 1800  # 30 min — books run hundreds of pages, unlike single rulings

_TRANSLIT = str.maketrans({
    "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ý": "y", "ð": "d",
    "þ": "th", "æ": "ae", "ö": "o", "Á": "A", "É": "E", "Í": "I", "Ó": "O",
    "Ú": "U", "Ý": "Y", "Ð": "D", "Þ": "Th", "Æ": "Ae", "Ö": "O",
})


def extract_text(pdf_bytes: bytes) -> str:
    """Extract full text; falls back to OCR (longer timeout) if the text layer is empty."""
    text = parse_pdf(pdf_bytes)
    if not text:
        text = docling_ocr_pdf(pdf_bytes, timeout=_OCR_TIMEOUT) or ""
    return text


def book_stem(title: str, max_len: int = 40) -> str:
    """Return the filename stem (no extension) derived from the book title."""
    t = title.translate(_TRANSLIT)
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode("ascii")
    t = re.sub(r"[^A-Za-z0-9]+", "_", t).strip("_")
    return t[:max_len].rstrip("_") or "book"


def build_document(
    meta: dict[str, Any],
    body_text: str,
    source_id: uuid.UUID,
    config: SourceConfig,
) -> Document:
    """Build a Document from resolved metadata + extracted text. Pure — no I/O."""
    raw = {
        "title": meta["title"],
        "author": meta["author"],
        "isbn": meta["isbn"],
        "document_date": meta["document_date"],
        "source_filename": None,
        "pdf_text": body_text or None,
    }
    fields = Extractor(config).extract(raw)
    return Document(
        id=uuid.uuid4(),
        source_id=source_id,
        external_id=meta["external_id"],
        url=None,
        **fields,
    )


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
        "case_type": _v(doc.case_type),
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


async def process_dropfolder(dropfolder: Path, *, dry_run: bool) -> dict:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    config = get_config("logfraedibaekur")
    stats = {"total": 0, "imported": 0, "errors": 0}

    pdfs = sorted(dropfolder.glob("*.pdf"))
    if not pdfs:
        log.info("No PDFs found in %s", dropfolder)
        return stats

    source_id = None
    taken: set[str] = set()
    if not dry_run:
        await init_db()
        async with _db_conn.AsyncSessionLocal() as session:
            source_id = await _ensure_source(session, config)
            existing = (await session.execute(
                select(Document.verdict_filename)
                .where(Document.source_id == source_id)
                .where(Document.verdict_filename.isnot(None))
            )).scalars().all()
        taken = set(existing)

    async with make_client() as client:
        for pdf_path in pdfs:
            stats["total"] += 1

            try:
                pdf_bytes = pdf_path.read_bytes()
                body_text = extract_text(pdf_bytes)
                meta = await resolve_book_metadata(client, body_text[:4000], pdf_path)
                doc = build_document(meta, body_text, source_id or uuid.uuid4(), config)
            except Exception as exc:  # noqa: BLE001
                log.error("Failed to process %s: %s", pdf_path.name, exc)
                stats["errors"] += 1
                continue

            errors = validate(doc, config)
            doc.validation_errors = errors or None
            if errors:
                stats["errors"] += 1

            if dry_run:
                plf = doc.plaintiffs[0] if doc.plaintiffs else {}
                print(f"{pdf_path.name}:")
                print(f"  title      : {doc.case_number}")
                print(f"  author     : {plf.get('name')}")
                print(f"  external_id: {doc.external_id}")
                print(f"  body_text  : {len(doc.body_text or '')} chars")
                if errors:
                    print(f"  ⚠ errors   : {[e['field'] + ':' + e['message'] for e in errors]}")
                stats["imported"] += 1
                continue

            async with _db_conn.AsyncSessionLocal() as session:
                try:
                    await _upsert_doc(session, doc)
                    await session.commit()
                except Exception as exc:  # noqa: BLE001
                    log.error("Upsert failed for %s: %s", pdf_path.name, exc)
                    await session.rollback()
                    stats["errors"] += 1
                    continue

            vf = None
            if doc.body_text:
                vf = unique_verdict_filename(book_stem(doc.case_number or "book"), taken)
                taken.add(vf)
                try:
                    write_markdown(doc, config, vf=vf)
                except Exception as exc:  # noqa: BLE001
                    log.warning("write_markdown failed for %s: %s", pdf_path.name, exc)
                    vf = None
                    stats["errors"] += 1

            if vf:
                try:
                    async with _db_conn.AsyncSessionLocal() as session:
                        doc_row = (await session.execute(
                            select(Document).where(Document.id == doc.id)
                        )).scalar_one()
                        doc_row.verdict_filename = vf
                        await session.commit()
                except Exception as exc:  # noqa: BLE001
                    log.warning("Failed to set verdict_filename for %s: %s", pdf_path.name, exc)
                    stats["errors"] += 1

            raw_pdf_path = config.pdf_path(doc.external_id)
            raw_pdf_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                pdf_path.rename(raw_pdf_path)
            except Exception as exc:  # noqa: BLE001
                log.warning("Failed to move %s to %s: %s", pdf_path.name, raw_pdf_path, exc)
                stats["errors"] += 1

            stats["imported"] += 1

    return stats


async def main(dry_run: bool) -> None:
    dropfolder = Path(DROPFOLDER_DIR)
    dropfolder.mkdir(parents=True, exist_ok=True)
    stats = await process_dropfolder(dropfolder, dry_run=dry_run)
    print(f"DONE {stats}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Extract+resolve+validate+report, no DB/disk writes")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
