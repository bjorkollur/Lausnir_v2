"""Sync all Stjórnarráðið rulings against the database.

Uses two complementary strategies for complete coverage:

  1. Committee sweep — for each registered source (SOURCE_REGISTRY, h1_use_display_name),
     uses the paginated Committee filter to get every ruling from that committee.
     This gives 100% coverage for all known committees.

  2. Year sweep — iterates all years 1966–present via the year-based search API
     to catch rulings from committees not yet registered.  Capped at ~200/year by
     the API, but sufficient for detecting new/rare committees.

Completed years/committees are checkpointed so re-running is fast.  Unknown
committees are created automatically as new Source rows.

Usage:
    uv run python scripts/sync_stjornarradid.py                   # full sync
    uv run python scripts/sync_stjornarradid.py --from-year 2024  # recent only
    uv run python scripts/sync_stjornarradid.py --year 2025       # single year
    uv run python scripts/sync_stjornarradid.py --dry-run         # enumerate, no import
"""
from __future__ import annotations

import argparse
import asyncio
import html
import json
import logging
import re
import urllib.parse
import uuid
from datetime import date
from pathlib import Path

import httpx
from sqlalchemy import select, update

import engine.database.connection as _db_conn
from engine.config.sources import SOURCE_REGISTRY, SourceConfig
from engine.database.connection import init_db
from engine.database.models import Document, Source
from engine.processors.http_utils import get_with_retry, make_client
from engine.processors.renderer import unique_verdict_filename, verdict_filename, write_markdown

import sys
sys.path.insert(0, str(Path(__file__).parent))
from import_stjornarradid import (
    _build_document,
    _parse_case_list,
    _parse_case_page,
    _render_and_save,
    _upsert_doc,
)
from engine.processors import extractor as _ext_mod

log = logging.getLogger(__name__)

_SEARCH_URL = (
    "https://www.stjornarradid.is/gogn/urskurdir-og-alit-/$LisasticSearch/Search/"
)
_DETAIL_BASE = "https://www.stjornarradid.is"
_REQUEST_DELAY = 0.8
_CHECKPOINT_DIR = Path("checkpoints")
_CURRENT_YEAR = date.today().year
_ALL_YEARS = list(range(1966, _CURRENT_YEAR + 1))

# Stjórnarráðið newsids are bare UUIDs; court sources prefix with "s-", "g-", etc.
_UUID_RE = re.compile(
    r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$",
    re.IGNORECASE,
)


# ── cname / source resolution ─────────────────────────────────────────────────

def _build_cname_map() -> dict[str, tuple[str, SourceConfig]]:
    return {
        cfg.display_name: (sn, cfg)
        for sn, cfg in SOURCE_REGISTRY.items()
        if cfg.stjornarradid_source
    }


def _cname_to_short_name(cname: str) -> str:
    s = html.unescape(cname).lower()
    s = re.sub(r"^úrskurðir\s+(á\s+)?", "", s)
    s = re.sub(r"^álit\s+", "", s)
    for src, dst in [
        ("á","a"),("é","e"),("í","i"),("ó","o"),("ú","u"),("ý","y"),
        ("ð","d"),("þ","th"),("æ","ae"),("ö","o"),
    ]:
        s = s.replace(src, dst)
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", "_", s.strip())
    return s[:50]


def _fallback_config(display_name: str, short_name: str) -> SourceConfig:
    return SourceConfig(
        short_name=short_name,
        display_name=display_name,
        abbreviation="Ú.Sr.",
        instance_tier=1,
        has_lower_court=False,
        parse_parties="none",
        verdict_type_default="Úrskurður",
        verdict_types_allowed=["Úrskurður", "Álit", "Ákvörðun", "Dómur"],
        case_number_prefix="",
        h1_use_display_name=True,
    )


def _resolve_source(
    cname: str, cname_map: dict[str, tuple[str, SourceConfig]]
) -> tuple[str, SourceConfig]:
    clean = html.unescape(cname).strip()
    if clean in cname_map:
        return cname_map[clean]
    sn = _cname_to_short_name(clean) if clean else "stjornarradid_unknown"
    return sn, _fallback_config(clean or "Óþekkt", sn)


# ── DB helpers ────────────────────────────────────────────────────────────────

async def _load_db_newsids() -> set[str]:
    """All UUID-format external_ids already in DB (= all stjórnarráðið docs)."""
    async with _db_conn.AsyncSessionLocal() as session:
        rows = (await session.execute(select(Document.external_id))).scalars().all()
    return {r for r in rows if _UUID_RE.match(r)}


async def _ensure_source(
    display_name: str, short_name: str, config: SourceConfig
) -> uuid.UUID:
    async with _db_conn.AsyncSessionLocal() as session:
        row = (
            await session.execute(select(Source).where(Source.short_name == short_name))
        ).scalar_one_or_none()
        if row:
            return row.id
        sid = uuid.uuid4()
        session.add(Source(
            id=sid,
            short_name=short_name,
            display_name=display_name,
            base_url=f"{_DETAIL_BASE}/gogn/urskurdir-og-alit-/",
        ))
        await session.commit()
        log.info("New source created: %s (%s)", short_name, display_name)
        return sid


# ── Fetch helpers ─────────────────────────────────────────────────────────────

def _extract_newsids(text: str) -> set[str]:
    return set(re.findall(r"newsid=([a-f0-9-]{36})", text))


async def _fetch_committee_all(
    client: httpx.AsyncClient, display_name: str
) -> set[str]:
    """Fetch all newsids for a committee using PageIndex pagination."""
    ids: set[str] = set()
    base = {"SearchQuery": "", "Committee": display_name, "SortByDate": "True"}

    # First pass without pageSize
    try:
        r = await client.get(_SEARCH_URL, params=base, timeout=15)
        first_batch = _parse_case_list(r.text)
        for newsid, _, _ in first_batch:
            ids.add(newsid)

        if len(first_batch) < 200:
            return ids  # got everything in one shot

        # Possibly truncated — try paginated
        page = 1
        while True:
            r = await client.get(
                _SEARCH_URL,
                params={**base, "pageSize": "200", "PageIndex": str(page)},
                timeout=15,
            )
            batch = _parse_case_list(r.text)
            if not batch:
                break
            new = sum(1 for n, _, _ in batch if n not in ids)
            for newsid, _, _ in batch:
                ids.add(newsid)
            log.debug("  %s page %d: %d items (%d new)", display_name[:30], page, len(batch), new)
            if len(batch) < 200:
                break
            page += 1
            await asyncio.sleep(0.3)
    except Exception as exc:
        log.warning("Committee fetch failed (%s): %s", display_name[:40], exc)

    return ids


async def _fetch_year(client: httpx.AsyncClient, year: int) -> set[str]:
    """Fetch up to ~200 newsids for a year (year-search is capped by the API)."""
    ids: set[str] = set()
    base = {"SearchQuery": "", "SortByDate": "True", "Year": str(year)}
    for params in [base, {**base, "pageSize": "200"}]:
        try:
            r = await client.get(_SEARCH_URL, params=params, timeout=15)
            ids |= _extract_newsids(r.text)
        except Exception as exc:
            log.warning("Year %d fetch failed: %s", year, exc)
    return ids


# ── Checkpointing ─────────────────────────────────────────────────────────────

def _committee_checkpoint(short_name: str) -> Path:
    return _CHECKPOINT_DIR / f"sync_stjrr_committee_{short_name}.json"


def _year_checkpoint(year: int) -> Path:
    return _CHECKPOINT_DIR / f"sync_stjrr_year_{year}.json"


def _committee_done(short_name: str) -> bool:
    p = _committee_checkpoint(short_name)
    return p.exists() and json.loads(p.read_text()).get("status") == "done"


def _year_done(year: int) -> bool:
    if year == _CURRENT_YEAR:
        return False
    p = _year_checkpoint(year)
    return p.exists() and json.loads(p.read_text()).get("status") == "done"


def _save_committee_checkpoint(short_name: str, found: int, imported: int) -> None:
    _CHECKPOINT_DIR.mkdir(exist_ok=True)
    _committee_checkpoint(short_name).write_text(
        json.dumps({"status": "done", "short_name": short_name, "found": found, "imported": imported})
    )


def _save_year_checkpoint(year: int, found: int, imported: int) -> None:
    _CHECKPOINT_DIR.mkdir(exist_ok=True)
    _year_checkpoint(year).write_text(
        json.dumps({"status": "done", "year": year, "found": found, "imported": imported})
    )


# ── Per-document import ───────────────────────────────────────────────────────

# Per-run caches so we don't re-query DB on every document
_source_cache: dict[str, uuid.UUID] = {}
_taken_cache: dict[str, set[str]] = {}


async def _init_source_cache(short_name: str, config: SourceConfig) -> uuid.UUID:
    if short_name in _source_cache:
        return _source_cache[short_name]

    source_id = await _ensure_source(config.display_name, short_name, config)
    _source_cache[short_name] = source_id

    async with _db_conn.AsyncSessionLocal() as session:
        existing = (
            await session.execute(
                select(Document.verdict_filename)
                .where(Document.source_id == source_id)
                .where(Document.verdict_filename.isnot(None))
            )
        ).scalars().all()
    _taken_cache[short_name] = set(existing)

    if short_name not in _ext_mod._EXTRACTORS:
        fn = _ext_mod._EXTRACTORS.get("innvida")
        if fn:
            _ext_mod._EXTRACTORS[short_name] = fn

    return source_id


async def _import_newsid(
    client: httpx.AsyncClient,
    newsid: str,
    cname: str,
    config: SourceConfig,
    source_id: uuid.UUID,
    dry_run: bool,
) -> bool:
    """Fetch detail page and import one document.  Returns True on success."""
    detail_url = (
        f"{_DETAIL_BASE}/gogn/urskurdir-og-alit-/stakur-urskurdur/"
        f"?newsid={newsid}&cname={urllib.parse.quote(cname)}"
    )
    try:
        resp = await get_with_retry(client, detail_url)
    except Exception as exc:
        log.warning("Fetch failed %s: %s", newsid[:8], exc)
        return False

    if resp.status_code != 200:
        log.warning("HTTP %d for %s", resp.status_code, newsid)
        return False

    if dry_run:
        log.info("[DRY] %s  source=%s", newsid[:8], config.short_name)
        return True

    short_name = config.short_name
    taken = _taken_cache[short_name]

    raw = await _parse_case_page(
        resp.text, newsid, str(resp.url), cname, "", client=client
    )
    if raw is None:
        log.warning("Parse failed %s", newsid[:8])
        return False

    log.info("Importing %s → %s  case=%s", newsid[:8], short_name, raw.get("case_number_str") or "?")

    doc = _build_document(raw, source_id, config)
    async with _db_conn.AsyncSessionLocal() as session:
        try:
            await _upsert_doc(session, doc)
            await session.commit()
        except Exception as exc:
            log.error("Upsert failed %s: %s", newsid[:8], exc)
            await session.rollback()
            return False

    vf = _render_and_save(doc, config, taken)
    if vf:
        async with _db_conn.AsyncSessionLocal() as session:
            await session.execute(
                update(Document)
                .where(Document.source_id == source_id, Document.external_id == newsid)
                .values(verdict_filename=vf)
            )
            await session.commit()
    return True


async def _import_batch(
    client: httpx.AsyncClient,
    new_ids: set[str],
    cname: str,
    cname_map: dict[str, tuple[str, SourceConfig]],
    db_ids: set[str],
    dry_run: bool,
) -> int:
    """Import a batch of new newsids.  Returns count of successfully imported docs."""
    if not new_ids:
        return 0

    short_name, config = _resolve_source(cname, cname_map)
    source_id = await _init_source_cache(short_name, config)

    imported = 0
    for newsid in sorted(new_ids):
        await asyncio.sleep(_REQUEST_DELAY)
        ok = await _import_newsid(client, newsid, cname, config, source_id, dry_run)
        if ok:
            imported += 1
            db_ids.add(newsid)
    return imported


# ── Main sync ─────────────────────────────────────────────────────────────────

async def sync(years: list[int], dry_run: bool = False) -> None:
    await init_db()

    cname_map = _build_cname_map()
    log.info("%d known committee mappings from SOURCE_REGISTRY", len(cname_map))

    log.info("Loading existing Stjórnarráðið newsids from DB…")
    db_ids = await _load_db_newsids()
    log.info("%d documents already in DB", len(db_ids))

    grand_new = grand_imported = 0

    async with make_client() as client:

        # ── Phase 1: known committees — full paginated sweep ──────────────────
        log.info("Phase 1: sweeping %d known committees", len(cname_map))
        for display_name, (short_name, config) in cname_map.items():
            if _committee_done(short_name):
                log.debug("Committee %s — checkpointed, skipping", short_name)
                continue

            site_ids = await _fetch_committee_all(client, display_name)
            new_ids = site_ids - db_ids
            log.info(
                "Committee %-35s  %4d on site  %4d new",
                short_name, len(site_ids), len(new_ids),
            )
            grand_new += len(new_ids)

            imported = await _import_batch(
                client, new_ids, display_name, cname_map, db_ids, dry_run
            )
            grand_imported += imported

            if not dry_run:
                _save_committee_checkpoint(short_name, found=len(new_ids), imported=imported)
            await asyncio.sleep(0.5)

        # ── Phase 2: year sweep — catches unknown committees ──────────────────
        log.info("Phase 2: year sweep %d–%d for unknown committees", years[0], years[-1])
        for year in years:
            if _year_done(year):
                continue

            site_ids = await _fetch_year(client, year)
            new_ids = site_ids - db_ids  # only IDs missed by committee sweep
            if not new_ids:
                if not dry_run:
                    _save_year_checkpoint(year, found=0, imported=0)
                continue

            log.info("Year %d: %d on site, %d new (not covered by committee sweep)", year, len(site_ids), len(new_ids))
            grand_new += len(new_ids)

            # Resolve cname per-document (fetch detail page)
            imported = 0
            for newsid in sorted(new_ids):
                await asyncio.sleep(_REQUEST_DELAY)
                detail_url = (
                    f"{_DETAIL_BASE}/gogn/urskurdir-og-alit-/stakur-urskurdur/"
                    f"?newsid={newsid}"
                )
                try:
                    resp = await get_with_retry(client, detail_url)
                except Exception as exc:
                    log.warning("Fetch failed %s: %s", newsid[:8], exc)
                    continue

                if resp.status_code != 200:
                    continue

                # Extract cname from redirect URL or page meta
                cname = resp.url.params.get("cname", "")
                if not cname:
                    m = re.search(r"data-category='([^']*)'", resp.text)
                    if m:
                        parts = [
                            p.strip() for p in m.group(1).split(",")
                            if p.strip() and not p.strip().startswith("type:")
                        ]
                        cname = parts[-1] if parts else ""

                short_name, config = _resolve_source(cname, cname_map)
                source_id = await _init_source_cache(short_name, config)

                if dry_run:
                    log.info("[DRY] %s → %s  cname=%s", newsid[:8], short_name, cname[:50])
                    imported += 1
                    db_ids.add(newsid)
                    continue

                taken = _taken_cache[short_name]
                raw = await _parse_case_page(
                    resp.text, newsid, str(resp.url), cname, "", client=client
                )
                if raw is None:
                    continue

                log.info("Importing %s → %s  case=%s", newsid[:8], short_name, raw.get("case_number_str") or "?")
                doc = _build_document(raw, source_id, config)
                async with _db_conn.AsyncSessionLocal() as session:
                    try:
                        await _upsert_doc(session, doc)
                        await session.commit()
                    except Exception as exc:
                        log.error("Upsert %s: %s", newsid[:8], exc)
                        await session.rollback()
                        continue

                vf = _render_and_save(doc, config, taken)
                if vf:
                    async with _db_conn.AsyncSessionLocal() as session:
                        await session.execute(
                            update(Document)
                            .where(Document.source_id == source_id, Document.external_id == newsid)
                            .values(verdict_filename=vf)
                        )
                        await session.commit()
                imported += 1
                db_ids.add(newsid)

            grand_imported += imported
            if not dry_run:
                _save_year_checkpoint(year, found=len(new_ids), imported=imported)

    log.info("Sync complete — new: %d, imported: %d", grand_new, grand_imported)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Sync all Stjórnarráðið rulings against DB.")
    grp = parser.add_mutually_exclusive_group()
    grp.add_argument("--year", type=int, help="Year sweep only (phase 2), single year")
    grp.add_argument("--from-year", type=int, help="Year sweep from this year to present")
    parser.add_argument(
        "--dry-run", action="store_true", help="Enumerate only, do not import"
    )
    args = parser.parse_args()

    if args.year:
        years = [args.year]
    elif args.from_year:
        years = list(range(args.from_year, _CURRENT_YEAR + 1))
    else:
        years = _ALL_YEARS

    asyncio.run(sync(years=years, dry_run=args.dry_run))
