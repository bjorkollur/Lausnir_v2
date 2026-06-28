"""Incremental sync of Lagasafn Alþingis.

Checks Last-Modified header of ZIP. If unchanged → exits immediately.
If changed → downloads ZIP, MD5-hashes each HTML file, compares with
stored raw_api_data["md5"]. Re-imports only changed files.

Usage:
    uv run python scripts/sync_lagasafn.py
    uv run python scripts/sync_lagasafn.py --force   # ignore Last-Modified check
    uv run python scripts/sync_lagasafn.py --zip-file /tmp/lagasafn.zip --force
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import logging
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
from sqlalchemy import select

import engine.database.connection as _db
from engine.database.connection import init_db
from engine.database.models import Document, Source
from engine.processors.lagasafn_parser import build_chapter_map, parse_law_html

ZIP_URL = "https://www.althingi.is/lagasafn/zip/nuna/allt.zip"
LAST_MODIFIED_FILE = Path("/tmp/lagasafn_last_modified.txt")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)


async def get_stored_md5s(session) -> dict[str, tuple[str, str]]:
    """Returns {external_id: (md5, source_id)} for all lagasafn documents."""
    rows = (await session.execute(
        select(Document.external_id, Document.raw_api_data, Document.source_id)
        .join(Source, Source.id == Document.source_id)
        .where(Source.short_name.like("lagasafn_%"))
    )).all()
    return {
        ext_id: (raw.get("md5", ""), str(src_id))
        for ext_id, raw, src_id in rows
        if raw
    }


async def sync_law(session, parsed: dict, source_id: str, zip_last_modified: str) -> None:
    existing = (await session.execute(
        select(Document).where(
            Document.source_id == source_id,
            Document.external_id == parsed["external_id"],
        )
    )).scalar_one_or_none()

    raw = {
        "md5": parsed["md5"],
        "zip_last_modified": zip_last_modified,
        "law_name": parsed["law_name"],
    }

    if existing is None:
        # New law (added since last sync)
        doc = Document(
            source_id=source_id,
            external_id=parsed["external_id"],
            url=f"https://www.althingi.is/lagasafn/html/nuna/{parsed['external_id']}.html",
            raw_api_data=raw,
            case_number=parsed["case_number"],
            document_date=parsed["document_date"],
            court=parsed["court"],
            verdict_type=parsed["verdict_type"],
            body_text=parsed["body_text"],
            provisions=parsed["provisions"],
            summary=parsed["law_name"],
        )
        session.add(doc)
    else:
        existing.raw_api_data = raw
        existing.case_number = parsed["case_number"]
        existing.document_date = parsed["document_date"]
        existing.verdict_type = parsed["verdict_type"]
        existing.body_text = parsed["body_text"]
        existing.provisions = parsed["provisions"]
        existing.summary = parsed["law_name"]
        # Null out fts_is so backfill re-indexes it
        existing.fts_is = None


async def main(force: bool = False, zip_file: str | None = None) -> None:
    await init_db()

    # 1. Check Last-Modified header (skip if using local file)
    zip_last_modified = ""
    if not zip_file:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; Lausnir/2.0; +https://lausnir.is)"}
        async with httpx.AsyncClient(timeout=30, headers=headers) as client:
            resp = await client.head(ZIP_URL)
            zip_last_modified = resp.headers.get("last-modified", "")

        stored_lm = LAST_MODIFIED_FILE.read_text().strip() if LAST_MODIFIED_FILE.exists() else ""

        if not force and zip_last_modified and zip_last_modified == stored_lm:
            log.info(f"ZIP unchanged (Last-Modified: {zip_last_modified}). Nothing to do.")
            return

        log.info(f"ZIP updated: {stored_lm!r} → {zip_last_modified!r}. Downloading …")

        # 2. Download ZIP
        async with httpx.AsyncClient(timeout=120, headers=headers, follow_redirects=True) as client:
            resp = await client.get(ZIP_URL)
            resp.raise_for_status()
            zip_bytes = resp.content
    else:
        log.info(f"Using local ZIP file: {zip_file}")
        zip_bytes = Path(zip_file).read_bytes()

    # 3. Build chapter map
    chapter_map = build_chapter_map(zip_bytes)

    # 4. Load stored MD5s
    async with _db.AsyncSessionLocal() as session:
        stored = await get_stored_md5s(session)
        # Get source_id map
        src_rows = (await session.execute(
            select(Source.short_name, Source.id).where(Source.short_name.like("lagasafn_%"))
        )).all()
        source_map = {sn: str(sid) for sn, sid in src_rows}

    # 5. Compute MD5 for every HTML file and find changes
    changed: list[str] = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        law_names = [n for n in zf.namelist() if n.endswith('.html') and n in chapter_map]
        for fname in law_names:
            raw_bytes = zf.read(fname)
            md5 = hashlib.md5(raw_bytes).hexdigest()
            ext_id = fname.removesuffix('.html')
            stored_md5, _ = stored.get(ext_id, ("", ""))
            if md5 != stored_md5:
                changed.append(fname)

    log.info(f"Changed laws: {len(changed)} / {len(law_names)}")
    if not changed:
        if zip_last_modified:
            LAST_MODIFIED_FILE.write_text(zip_last_modified)
        log.info("No content changes. Done.")
        return

    # 6. Re-process changed files
    updated = 0
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        async with _db.AsyncSessionLocal() as session:
            for i, fname in enumerate(changed, 1):
                kafli_num = chapter_map[fname]
                short_name = f"lagasafn_{kafli_num:02d}"
                source_id = source_map.get(short_name)
                if not source_id:
                    continue
                try:
                    raw_bytes = zf.read(fname)
                    parsed = parse_law_html(raw_bytes, fname)
                    await sync_law(session, parsed, source_id, zip_last_modified)
                    updated += 1
                except Exception as e:
                    log.error(f"Error syncing {fname}: {e}")

                if i % 50 == 0:
                    await session.commit()

            await session.commit()

    if zip_last_modified:
        LAST_MODIFIED_FILE.write_text(zip_last_modified)
    log.info(f"Sync complete. {updated} laws updated.")
    log.info("Run 'uv run python scripts/backfill_fts_is.py' to update FTS index.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Ignore Last-Modified check")
    parser.add_argument("--zip-file", default=None, help="Use local ZIP file instead of downloading")
    args = parser.parse_args()
    asyncio.run(main(force=args.force, zip_file=args.zip_file))
