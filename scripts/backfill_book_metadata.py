"""Backfill isbn/publisher/full author list for logfraedibaekur docs imported
before those fields existed (isbn was only ever inside raw_api_data, publisher
and multi-author support didn't exist at all).

Re-runs the same OpenLibrary -> leitir.is lookup chain using each doc's
already-known ISBN (from raw_api_data), so no re-extraction of body_text.

Usage:
    set -a; . ./.env; set +a
    uv run python scripts/backfill_book_metadata.py --dry-run
    uv run python scripts/backfill_book_metadata.py
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import select

import engine.database.connection as _db_conn
from engine.database.connection import init_db
from engine.database.models import Document, Source
from engine.processors.book_metadata import lookup_leitir, lookup_openlibrary
from engine.processors.http_utils import make_client

log = logging.getLogger(__name__)


async def main(dry_run: bool) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    await init_db()

    async with _db_conn.AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(Document)
            .join(Source, Document.source_id == Source.id)
            .where(Source.short_name == "logfraedibaekur")
        )).scalars().all()

    async with make_client() as client:
        for doc in rows:
            isbn = doc.isbn or (doc.raw_api_data or {}).get("isbn")
            if not isbn:
                log.info("Skipping %s (%s) — no ISBN on file", doc.case_number, doc.id)
                continue

            meta = await lookup_openlibrary(client, isbn)
            if not meta or not meta.get("title"):
                meta = await lookup_leitir(client, isbn)
            if not meta:
                log.warning("No metadata found for isbn=%s (%s)", isbn, doc.case_number)
                continue

            authors = meta.get("authors")
            publisher = meta.get("publisher")
            plaintiffs = [{"name": a, "lawyer": None} for a in authors] if authors else doc.plaintiffs

            print(f"{doc.case_number} (isbn={isbn}):")
            print(f"  isbn      : {doc.isbn!r} -> {isbn!r}")
            print(f"  publisher : {doc.publisher!r} -> {publisher!r}")
            print(f"  plaintiffs: {doc.plaintiffs!r} -> {plaintiffs!r}")

            if dry_run:
                continue

            async with _db_conn.AsyncSessionLocal() as session:
                doc_row = (await session.execute(
                    select(Document).where(Document.id == doc.id)
                )).scalar_one()
                doc_row.isbn = isbn
                doc_row.publisher = publisher
                doc_row.plaintiffs = plaintiffs
                await session.commit()

    print("DONE")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
