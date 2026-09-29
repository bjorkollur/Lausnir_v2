"""Fill case_type from the court's own classification.

For Hæstiréttur and Landsréttur that classification exists only as the
``caseTypes`` filter on the island.is listing query — the verdict items
themselves carry no such field (probed 2026-09-29: caseType, caseTypes,
caseCategories, caseCategory, type, category are all rejected on
WebVerdictItem, and introspection is disabled).  So the only way to read it is
to list each category and keep the ids.  For héraðsdómstólar it is the court's
own case-number prefix (E-, S-, X- …), which needs no network at all.

Two modes:

``--missing-only`` (cheap, for every import run) fills rows where case_type IS
NULL and never touches a row that already has a value.  The listing is
newest-first, so a gap left by a recent import is found in the first page or
two; the walk stops once the listing is older than the oldest gap, and in any
case after ``--max-pages`` pages per category.  A verdict the site classifies
in no category stays NULL however long we look — 3 Landsréttarmál were in that
state on 2026-09-29 — so the cap is what keeps the cost flat.

Full run (no flag) re-reads every category and writes what the site says now.
Use it when the site's own classification has changed — but read
docs/snapshots/README.md first: island.is classifies Hæstaréttar verdicts from
2016-01-05 on only, so a full run cannot restore the ~10.000 older rows, and
those values exist nowhere else.

Usage:
    uv run python scripts/backfill_case_type.py --missing-only
    uv run python scripts/backfill_case_type.py --missing-only --dry-run
    uv run python scripts/backfill_case_type.py --source landsrettur
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import logging
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text

import engine.database.connection as _db_conn
from engine.database.connection import init_db
from engine.processors.extractor import _heradsdomur_case_type

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
      verdictDate
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
                            UPDATE documents d
                            SET case_type = :case_type
                            WHERE d.external_id = ANY(:ids)
                              AND d.source_id = (
                                  SELECT id FROM sources WHERE short_name = :short_name
                              )
                            RETURNING d.id
                        """),
                        {"case_type": normalised, "ids": ids, "short_name": short_name},
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


# ── Incremental mode ──────────────────────────────────────────────────────────

# Héraðsdómstólar need no network: the court puts the case kind in the case
# number itself (E- einkamál, S- sakamál, X-/Y-/Z- ágreiningsmál …).
_LOCAL_PREFIX_SOURCES = ("heradsdomstolar",)
_DEFAULT_MAX_PAGES = 10


async def _missing(short_name: str) -> tuple[set[str], str | None]:
    """(external_ids whose case_type is NULL, oldest document_date among them)."""
    async with _db_conn.AsyncSessionLocal() as session:
        rows = (await session.execute(
            text("""
                SELECT d.external_id, d.document_date
                FROM documents d JOIN sources s ON s.id = d.source_id
                WHERE s.short_name = :sn AND d.case_type IS NULL
            """),
            {"sn": short_name},
        )).all()
    dates = [str(r[1]) for r in rows if r[1] is not None]
    return {r[0] for r in rows}, (min(dates) if dates else None)


async def _write_case_type(short_name: str, case_type: str, ids: list[str]) -> int:
    """Set case_type on the given ids — only where it is still NULL."""
    async with _db_conn.AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
                UPDATE documents d
                SET case_type = :case_type
                WHERE d.external_id = ANY(:ids)
                  AND d.case_type IS NULL
                  AND d.source_id = (SELECT id FROM sources WHERE short_name = :sn)
                RETURNING d.id
            """),
            {"case_type": case_type, "ids": ids, "sn": short_name},
        )
        n = len(result.fetchall())
        await session.commit()
    return n


async def _walk_for(
    client: httpx.AsyncClient,
    court: str,
    case_type: str,
    wanted: set[str],
    oldest: str | None,
    max_pages: int,
) -> list[str]:
    """Newest-first walk over one category, returning the wanted ids it holds.

    Stops as soon as nothing is left to look for, the listing has gone past
    `oldest`, or `max_pages` pages have been read.  The four categories are
    disjoint, so an id found here is removed from `wanted` for the rest.
    """
    found: list[str] = []
    page = 1
    while page <= max_pages and wanted:
        result = await _fetch_page(client, court, case_type, page, _PAGE_SIZE)
        items = result.get("items") or []
        if not items:
            break
        for item in items:
            if item["id"] in wanted:
                found.append(item["id"])
                wanted.discard(item["id"])
        last_date = (items[-1].get("verdictDate") or "")[:10]
        if oldest is not None and last_date and last_date < oldest:
            break
        page += 1
    return found


async def _fill_from_prefix(short_name: str, dry_run: bool) -> None:
    filled = collections.Counter()
    unknown: list[str] = []
    async with _db_conn.AsyncSessionLocal() as session:
        rows = (await session.execute(
            text("""
                SELECT d.id, d.case_number
                FROM documents d JOIN sources s ON s.id = d.source_id
                WHERE s.short_name = :sn AND d.case_type IS NULL
            """),
            {"sn": short_name},
        )).all()
    by_type: dict[str, list] = collections.defaultdict(list)
    for doc_id, case_number in rows:
        case_type = _heradsdomur_case_type(case_number)
        if case_type is None:
            unknown.append(case_number or "<ekkert málsnúmer>")
        else:
            by_type[case_type].append(doc_id)
    for case_type, ids in sorted(by_type.items()):
        if dry_run:
            log.info("  [dry-run] %s ← %d skjöl", case_type, len(ids))
            filled[case_type] = len(ids)
            continue
        async with _db_conn.AsyncSessionLocal() as session:
            result = await session.execute(
                text("""UPDATE documents SET case_type = :ct
                        WHERE id = ANY(:ids) AND case_type IS NULL RETURNING id"""),
                {"ct": case_type, "ids": ids},
            )
            filled[case_type] = len(result.fetchall())
            await session.commit()
    log.info("%s: %d fyllt %s%s", short_name, sum(filled.values()), dict(filled),
             f", {len(unknown)} með óþekkt forskeyti" if unknown else "")
    for case_number in unknown[:10]:
        log.warning("  óþekkt forskeyti: %s", case_number)


async def backfill_missing(
    sources: list[str] | None = None,
    dry_run: bool = False,
    max_pages: int = _DEFAULT_MAX_PAGES,
) -> None:
    """Fill only the rows that have no case_type, leaving every other row alone."""
    await init_db()
    if sources is None:
        sources = list(_SOURCE_CASE_TYPES) + list(_LOCAL_PREFIX_SOURCES)

    async with httpx.AsyncClient() as client:
        for short_name in sources:
            if short_name in _LOCAL_PREFIX_SOURCES:
                log.info("=== %s (málsnúmer, engin netköll) ===", short_name)
                await _fill_from_prefix(short_name, dry_run)
                continue
            if short_name not in _SOURCE_CASE_TYPES:
                log.error("Óþekkt heimild: %s", short_name)
                continue

            court, case_types = _SOURCE_CASE_TYPES[short_name]
            wanted, oldest = await _missing(short_name)
            log.info("=== %s (%s): %d án case_type%s ===", short_name, court, len(wanted),
                     f", elsta {oldest}" if oldest else "")
            if not wanted:
                continue

            total_filled = 0
            for case_type in case_types:
                found = await _walk_for(client, court, case_type, wanted, oldest, max_pages)
                if not found:
                    continue
                normalised = _LRD_NORMALISE.get(case_type, case_type)
                if dry_run:
                    log.info("  [dry-run] %s ← %d skjöl", normalised, len(found))
                    total_filled += len(found)
                    continue
                n = await _write_case_type(short_name, normalised, found)
                log.info("  %s ← %d skjöl", normalised, n)
                total_filled += n
            log.info("%s: %d fyllt, %d eftir óflokkuð", short_name, total_filled, len(wanted))
            for external_id in sorted(wanted)[:10]:
                log.warning("  ekki í neinni tegundasíu: %s", external_id)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=str, default=None,
                        help="aðeins þessi heimild (haestirettur, landsrettur, heradsdomstolar)")
    parser.add_argument("--missing-only", action="store_true",
                        help="fylla aðeins raðir með case_type IS NULL; skrifar aldrei yfir")
    parser.add_argument("--max-pages", type=int, default=_DEFAULT_MAX_PAGES,
                        help=f"þak á síðum á tegund í --missing-only (sjálfgefið {_DEFAULT_MAX_PAGES})")
    parser.add_argument("--dry-run", action="store_true",
                        help="sækja en ekki skrifa")
    args = parser.parse_args()

    sources = [args.source] if args.source else None
    if args.missing_only:
        asyncio.run(backfill_missing(sources=sources, dry_run=args.dry_run,
                                     max_pages=args.max_pages))
    else:
        asyncio.run(backfill(sources=sources, dry_run=args.dry_run))
