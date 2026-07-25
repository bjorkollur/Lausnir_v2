"""Import Lagasafn Alþingis — full initial import from ZIP.

Downloads https://www.althingi.is/lagasafn/zip/nuna/allt.zip and imports
all law HTML files. Each law becomes one Document, linked to its chapter source.

Usage:
    uv run python scripts/import_lagasafn.py
    uv run python scripts/import_lagasafn.py --limit 20   # test run
    uv run python scripts/import_lagasafn.py --kafli 5    # one chapter only
"""
from __future__ import annotations

import argparse
import asyncio
import io
import logging
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
from sqlalchemy import select

from engine.config.sources import SOURCE_REGISTRY
import engine.database.connection as _db
from engine.database.connection import init_db, get_engine
from engine.database.models import Document, Source
from engine.processors.lagasafn_parser import build_chapter_map, parse_law_html

ZIP_URL = "https://www.althingi.is/lagasafn/zip/nuna/allt.zip"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)


async def ensure_sources(session) -> dict[str, str]:
    """Ensure all 48 lagasafn source rows exist. Returns {short_name: source_id}."""
    name_to_id: dict[str, str] = {}
    for short_name, cfg in SOURCE_REGISTRY.items():
        if not short_name.startswith("lagasafn_"):
            continue
        row = (await session.execute(
            select(Source).where(Source.short_name == short_name)
        )).scalar_one_or_none()
        if row is None:
            row = Source(
                short_name=short_name,
                display_name=cfg.display_name,
                base_url=ZIP_URL,
            )
            session.add(row)
            await session.flush()
            log.info(f"Created source: {short_name}")
        name_to_id[short_name] = str(row.id)
    await session.commit()
    return name_to_id


async def import_law(session, parsed: dict, source_id: str, zip_last_modified: str) -> bool:
    """Upsert one law. Returns True if inserted/updated."""
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
        existing.court = parsed["court"]
        existing.verdict_type = parsed["verdict_type"]
        existing.body_text = parsed["body_text"]
        existing.provisions = parsed["provisions"]
        existing.summary = parsed["law_name"]
    return True


async def main(limit: int | None = None, kafli: int | None = None, zip_file: str | None = None) -> None:
    await init_db()

    if zip_file:
        log.info(f"Using local ZIP file: {zip_file}")
        zip_bytes = Path(zip_file).read_bytes()
        zip_last_modified = ""
    else:
        log.info(f"Downloading {ZIP_URL} …")
        headers = {"User-Agent": "Mozilla/5.0 (compatible; Lausnir/2.0; +https://lausnir.is)"}
        async with httpx.AsyncClient(timeout=120, headers=headers, follow_redirects=True) as client:
            resp = await client.get(ZIP_URL)
            resp.raise_for_status()
            zip_bytes = resp.content
            zip_last_modified = resp.headers.get("last-modified", "")

    log.info(f"ZIP: {len(zip_bytes):,} bytes, Last-Modified: {zip_last_modified}")

    log.info("Building chapter map …")
    chapter_map = build_chapter_map(zip_bytes)
    log.info(f"Chapter map: {len(chapter_map)} law files")

    async with _db.AsyncSessionLocal() as session:
        source_map = await ensure_sources(session)

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        all_names = [
            n for n in zf.namelist()
            if n.endswith('.html') and n in chapter_map
        ]
        if kafli is not None:
            all_names = [n for n in all_names if chapter_map[n] == kafli]
        if limit:
            all_names = all_names[:limit]

        log.info(f"Processing {len(all_names)} law files …")
        t0 = time.time()
        ok = 0
        errors = 0

        async with _db.AsyncSessionLocal() as session:
            for i, fname in enumerate(all_names, 1):
                kafli_num = chapter_map[fname]
                short_name = f"lagasafn_{kafli_num:02d}"
                source_id = source_map.get(short_name)
                if not source_id:
                    log.warning(f"No source_id for {short_name}, skipping {fname}")
                    continue
                try:
                    raw_bytes = zf.read(fname)
                    parsed = parse_law_html(raw_bytes, fname)
                    await import_law(session, parsed, source_id, zip_last_modified)
                    ok += 1
                except Exception as e:
                    log.error(f"Error processing {fname}: {e}")
                    errors += 1

                if i % 100 == 0:
                    await session.commit()
                    elapsed = time.time() - t0
                    rate = i / elapsed
                    eta = (len(all_names) - i) / rate
                    log.info(f"{i}/{len(all_names)} — {rate:.0f}/s — ETA {eta:.0f}s")

            await session.commit()

    elapsed = time.time() - t0
    log.info(f"Done. {ok} laws imported, {errors} errors, {elapsed:.0f}s")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--kafli", type=int, default=None)
    parser.add_argument("--zip-file", default=None, help="Use local ZIP file instead of downloading")
    args = parser.parse_args()
    asyncio.run(main(limit=args.limit, kafli=args.kafli, zip_file=args.zip_file))
