"""Backfill case_type column from island.is GraphQL API caseTypes filter.

Fetches every caseType category for Hæstiréttur and Landsréttur from the API
and matches documents by external_id to update case_type in the DB.

Usage:
    uv run python scripts/backfill_case_type.py
    uv run python scripts/backfill_case_type.py --dry-run
    uv run python scripts/backfill_case_type.py --source haestirettur
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text

import engine.database.connection as _db_conn
from engine.database.connection import init_db

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

_GQL_ENDPOINT = "https://island.is/api/graphql"
_PAGE_SIZE = 10   # island.is returns 10 per page

# Landsréttur API returns "Einkamál áfrýjað" etc. — normalise to Hæstiréttur word order
_LRD_NORMALISE: dict[str, str] = {
    "Einkamál áfrýjað": "Áfrýjað einkamál",
    "Einkamál kært":    "Kært einkamál",
    "Sakamál áfrýjað":  "Áfrýjað sakamál",
    "Sakamál kært":     "Kært sakamál",
}

# All known caseType values per source (court name as used in API)
_SOURCE_CASE_TYPES: dict[str, tuple[str, list[str]]] = {
    "haestirettur": ("Hæstiréttur", [
        "Áfrýjað einkamál",
        "Kært einkamál",
        "Áfrýjað sakamál",
        "Kært sakamál",
    ]),
    "landsrettur": ("Landsrettur", [
        "Einkamál áfrýjað",
        "Einkamál kært",
        "Sakamál áfrýjað",
        "Sakamál kært",
    ]),
}

_GQL_QUERY = """
query GetVerdicts($input: WebVerdictsInput!) {
  webVerdicts(input: $input) {
    total
    items {
      id
      caseNumber
    }
  }
}
"""


async def _fetch_page(
    client: httpx.AsyncClient,
    court: str,
    case_type: str,
    page: int,
    _page_size: int = _PAGE_SIZE,
) -> dict:
    payload = {
        "query": _GQL_QUERY,
        "variables": {
            "input": {
                "court": court,
                "caseTypes": [case_type],
                "page": page,
            }
        },
    }
    r = await client.post(_GQL_ENDPOINT, json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    if "errors" in data:
        raise ValueError(f"GraphQL errors: {data['errors']}")
    return data["data"]["webVerdicts"]


async def _fetch_all_ids(
    client: httpx.AsyncClient,
    court: str,
    case_type: str,
) -> list[str]:
    """Return all external_ids (API id field) for a given court + caseType."""
    import math
    first = await _fetch_page(client, court, case_type, 1, _PAGE_SIZE)
    total = first["total"]
    ids = [item["id"] for item in first["items"]]
    total_pages = math.ceil(total / _PAGE_SIZE)
    for page in range(2, total_pages + 1):
        result = await _fetch_page(client, court, case_type, page, _PAGE_SIZE)
        ids.extend(item["id"] for item in result["items"])
        if page % 10 == 0:
            log.info("  page %d/%d — %d ids so far", page, total_pages, len(ids))

    log.info("  Fetched %d ids (API reported total=%d)", len(ids), total)
    return ids


async def backfill(
    sources: list[str] | None = None,
    dry_run: bool = False,
) -> None:
    await init_db()

    if sources is None:
        sources = list(_SOURCE_CASE_TYPES.keys())

    async with httpx.AsyncClient() as client:
        for short_name in sources:
            if short_name not in _SOURCE_CASE_TYPES:
                log.error("Unknown source: %s", short_name)
                continue

            court, case_types = _SOURCE_CASE_TYPES[short_name]
            log.info("=== %s (%s) ===", short_name, court)
            total_updated = 0

            for case_type in case_types:
                log.info("Fetching '%s'...", case_type)
                t0 = time.time()
                try:
                    ids = await _fetch_all_ids(client, court, case_type)
                except Exception as e:
                    log.error("Failed to fetch %s / %s: %s", court, case_type, e)
                    continue

                if not ids:
                    log.info("  No results.")
                    continue

                if dry_run:
                    log.info("  [dry-run] Would update %d docs to case_type=%r", len(ids), case_type)
                    continue

                normalised = _LRD_NORMALISE.get(case_type, case_type)
                async with _db_conn.AsyncSessionLocal() as session:
                    result = await session.execute(
                        text("""
                            UPDATE documents
                            SET case_type = :case_type
                            WHERE external_id = ANY(:ids)
                            RETURNING id
                        """),
                        {"case_type": normalised, "ids": ids},
                    )
                    updated = len(result.fetchall())
                    await session.commit()

                elapsed = time.time() - t0
                log.info(
                    "  %d/%d docs updated in %.1fs",
                    updated, len(ids), elapsed,
                )
                if updated < len(ids):
                    log.warning(
                        "  %d API ids not found in DB (possibly not imported yet)",
                        len(ids) - updated,
                    )
                total_updated += updated

            log.info("%s: %d docs updated in total", short_name, total_updated)

    # Summary
    async with _db_conn.AsyncSessionLocal() as session:
        rows = (await session.execute(text("""
            SELECT s.short_name, d.case_type, count(*)
            FROM documents d JOIN sources s ON d.source_id = s.id
            WHERE s.short_name IN ('haestirettur', 'landsrettur')
            GROUP BY 1, 2
            ORDER BY 1, 3 DESC
        """))).all()

    log.info("\n=== Niðurstaða ===")
    for row in rows:
        log.info("  %-16s %-30s %d", row[0], row[1] or "(NULL)", row[2])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=str, default=None,
                        help="Only backfill this source (haestirettur or landsrettur)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch from API but do not write to DB")
    args = parser.parse_args()

    sources = [args.source] if args.source else None
    asyncio.run(backfill(sources=sources, dry_run=args.dry_run))
