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
from engine.processors.book_metadata import _TRANSLIT, resolve_book_metadata
from engine.processors.extractor import Extractor
from engine.processors.http_utils import make_client
from engine.processors.pdf_parser import (
    docling_ocr_pdf,
    downsample_if_high_res,
    is_scanned_pdf,
    parse_pdf,
)
from engine.processors.renderer import unique_verdict_filename, write_markdown
from engine.processors.validator import validate

log = logging.getLogger(__name__)

_OCR_TIMEOUT = 1800  # 30 min — books run hundreds of pages, unlike single rulings

# The footnote pass drops any note line it cannot attach to a number. On a long
# book — hundreds of repeated small-type running headers — that can eat real text,
# so the result is rejected if it comes back materially shorter than a plain pass.
_FOOTNOTE_PASS_MIN_RATIO = 0.97


def extract_text(pdf_bytes: bytes, *, footnotes: bool = False) -> str:
    """Extract full text — scanned pages go straight to OCR, native ones through
    the structured parser; falls back to OCR (longer timeout) for anything that
    slips past the classifier with an empty text layer regardless.

    A scanned page's text layer is not just sometimes empty — it can be full of
    text and still wrong (garbled roman numerals, mis-encoded letters), so
    "text came back" is not enough to trust it. is_scanned_pdf() checks for a
    full-page image instead of checking whether text exists, and settles the
    question before parse_pdf ever runs.

    OCR loses the word positions footnote pairing depends on, so that path
    yields plain text with no footnote markup — the text is still captured.
    """
    if is_scanned_pdf(pdf_bytes):
        return docling_ocr_pdf(pdf_bytes, timeout=_OCR_TIMEOUT) or ""

    text = parse_pdf(pdf_bytes, footnotes=footnotes)
    if footnotes and text:
        plain = parse_pdf(pdf_bytes)
        if plain and len(text) < len(plain) * _FOOTNOTE_PASS_MIN_RATIO:
            log.warning(
                "footnote pass lost text (%d vs %d chars) — keeping plain extraction",
                len(text), len(plain),
            )
            text = plain
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
        "authors": meta["authors"],
        "isbn": meta["isbn"],
        "publisher": meta["publisher"],
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
        "isbn": _v(doc.isbn),
        "publisher": _v(doc.publisher),
        "validation_errors": _v(doc.validation_errors),
    }
    update_cols = {k: v for k, v in values.items() if k not in ("id", "source_id", "external_id")}
    update_cols["updated_at"] = func.now()
    await session.execute(
        pg_insert(Document)
        .values(**values)
        .on_conflict_do_update(constraint="uq_doc_source_external", set_=update_cols)
    )


def find_pdfs(dropfolder: Path) -> list[Path]:
    """List PDFs in `dropfolder`, case-insensitively.

    Path.glob is case-sensitive on every platform (it's Python's own matcher,
    not the filesystem's) — plain "*.pdf" silently drops ".PDF" files, no error.
    """
    return sorted({p for p in dropfolder.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"})


async def process_dropfolder(dropfolder: Path, *, dry_run: bool) -> dict:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    config = get_config("logfraedibaekur")
    stats = {"total": 0, "imported": 0, "errors": 0}

    pdfs = find_pdfs(dropfolder)
    if not pdfs:
        log.info("No PDFs found in %s", dropfolder)
        return stats

    source_id = None
    taken: set[str] = set()
    existing_titles: dict[str, str] = {}  # external_id -> case_number, for the skip message
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
            existing_titles = dict((await session.execute(
                select(Document.external_id, Document.case_number)
                .where(Document.source_id == source_id)
            )).all())

    config.pdf_path("_").parent.mkdir(parents=True, exist_ok=True)
    duplicates_dir = dropfolder / "_duplicates"

    # A book can take minutes of local OCR/extraction between one metadata
    # lookup and the next — long enough for a keep-alive HTTP connection to go
    # stale server-side. httpx doesn't always notice that on reuse (observed:
    # a pooled OpenLibrary connection sat in CLOSE_WAIT with zero CPU activity
    # for 10+ minutes, well past the per-request timeout, wedging the whole
    # batch). A short keepalive_expiry makes the pool drop idle connections
    # before they can go stale; wait_for below is the backstop regardless.
    client_limits = httpx.Limits(keepalive_expiry=5.0)
    async with make_client(limits=client_limits) as client:
        for pdf_path in pdfs:
            stats["total"] += 1

            try:
                pdf_bytes = pdf_path.read_bytes()
                body_text = extract_text(pdf_bytes, footnotes=config.has_footnotes)
                meta = await asyncio.wait_for(
                    resolve_book_metadata(client, body_text[:4000], pdf_path), timeout=60.0,
                )
                doc = build_document(meta, body_text, source_id or uuid.uuid4(), config)
            except Exception as exc:  # noqa: BLE001
                log.error("Failed to process %s: %s", pdf_path.name, exc)
                stats["errors"] += 1
                continue

            # Same external_id (ISBN, or filename-derived id) as a document already
            # imported — almost always a second scan of a book already in the
            # collection, dropped under a different filename. ON CONFLICT DO
            # UPDATE would silently overwrite the existing body_text with this
            # extraction's, discarding whatever the first import produced with no
            # way back — worse, doc.id here is a *new* uuid never actually
            # inserted (the conflict path updates the *existing* row instead),
            # so the later verdict_filename lookup by doc.id finds nothing and
            # fails. Skip and move aside instead; a human can compare and decide.
            if not dry_run and doc.external_id in existing_titles:
                log.warning(
                    "Skipping %s: external_id %r already imported as %r — moved to %s",
                    pdf_path.name, doc.external_id, existing_titles[doc.external_id],
                    duplicates_dir,
                )
                duplicates_dir.mkdir(exist_ok=True)
                try:
                    pdf_path.rename(duplicates_dir / pdf_path.name)
                except Exception as exc:  # noqa: BLE001
                    log.warning("Failed to move duplicate %s aside: %s", pdf_path.name, exc)
                stats["errors"] += 1
                continue

            errors = validate(doc, config)
            doc.validation_errors = errors or None
            if errors:
                stats["errors"] += 1

            if dry_run:
                authors = [p.get("name") for p in (doc.plaintiffs or [])]
                print(f"{pdf_path.name}:")
                print(f"  title      : {doc.case_number}")
                print(f"  authors    : {', '.join(authors) or None}")
                print(f"  isbn       : {doc.isbn}")
                print(f"  publisher  : {doc.publisher}")
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

            # existing_titles is a startup snapshot — without this, two dropfolder
            # files that collide on external_id with *each other* (not with any
            # pre-existing row) both pass the check above, and the second one
            # silently overwrites the first via ON CONFLICT DO UPDATE.
            existing_titles[doc.external_id] = doc.case_number

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
            try:
                # A scanned book's raw copy is a rasterized image per page — worth
                # normalizing an unnecessarily high-DPI scan down before it becomes
                # the permanent artifact (see docs/logfraedibaekur-pdf-extraction-
                # investigation.md). A native-text PDF is never rewritten: it has
                # no scan resolution to normalize, and rasterizing it would throw
                # away its selectable text for nothing — plain rename stays exact.
                if is_scanned_pdf(pdf_bytes):
                    downsampled = downsample_if_high_res(pdf_bytes)
                    if len(downsampled) < len(pdf_bytes):
                        log.info(
                            "Downsampled %s: %.1f MB -> %.1f MB",
                            pdf_path.name, len(pdf_bytes) / 1e6, len(downsampled) / 1e6,
                        )
                    raw_pdf_path.write_bytes(downsampled)
                    pdf_path.unlink()
                else:
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
    if not dry_run and stats["imported"] > 0:
        # Every other source gets this as the final stage of scripts/update_all.py —
        # without it, new documents.fts_is stays NULL and a scope-less "search
        # everything" query never finds them (only a scope narrowed to book
        # sources would, via document_chunks). Run it here so dropfolder imports
        # aren't a silent search gap the operator has to discover separately.
        from scripts.backfill_fts_is import backfill as backfill_fts_is
        await backfill_fts_is(source_name="logfraedibaekur")
        print("Run this to also improve relevance/snippets for book-scoped search:")
        print("  uv run python scripts/backfill_chunks.py --source logfraedibaekur")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Extract+resolve+validate+report, no DB/disk writes")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
