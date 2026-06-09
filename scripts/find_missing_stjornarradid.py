"""Find and import stjornarradid.is rulings not reachable via committee filter.

Phase 1: Enumerate all newsids by year (1966–2026), merge with DB, save missing list.
Phase 2: Fetch each missing detail page, group by cname, create sources and import.

Usage:
    uv run python scripts/find_missing_stjornarradid.py --phase 1
    uv run python scripts/find_missing_stjornarradid.py --phase 2
    uv run python scripts/find_missing_stjornarradid.py --phase 1 --year 2023  # single year
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import urllib.parse
import uuid
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, null as sa_null, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

import engine.database.connection as _db_conn
from engine.config.sources import SOURCE_REGISTRY, SourceConfig, get_config
from engine.database.connection import init_db
from engine.database.models import Document, Source
from engine.processors.http_utils import get_with_retry, make_client
from engine.processors.renderer import unique_verdict_filename, verdict_filename, write_markdown
from engine.processors.validator import validate

# Re-use parsing logic from import_stjornarradid
import sys
sys.path.insert(0, str(Path(__file__).parent))
from import_stjornarradid import (
    _build_document,
    _parse_case_page,
    _section_to_markdown,
    _upsert_doc,
)

# Allow dynamic short_names to use the generic stjornarradid extractor
from engine.processors import extractor as _ext_mod
_stjornarradid_fn = _ext_mod._EXTRACTORS.get("innvida")  # any registered stjornarradid source

log = logging.getLogger(__name__)

_SEARCH_URL = "https://www.stjornarradid.is/gogn/urskurdir-og-alit-/$LisasticSearch/Search/"
_DETAIL_BASE = "https://www.stjornarradid.is"
_REQUEST_DELAY = 0.8
_MISSING_FILE = Path("checkpoints/missing_stjornarradid.json")
_YEARS = list(range(1966, 2027))


# ── Phase 1: enumerate ────────────────────────────────────────────────────────

def _extract_newsids(html: str) -> set[str]:
    return set(re.findall(r'newsid=([a-f0-9-]{36})', html))


async def _fetch_year_newsids(client: httpx.AsyncClient, year: int) -> set[str]:
    """Fetch all newsids for a given year using both API modes."""
    ids: set[str] = set()
    base = {"SearchQuery": "", "SortByDate": "True", "Year": str(year)}

    try:
        r = await client.get(_SEARCH_URL, params=base, timeout=15)
        ids |= _extract_newsids(r.text)

        r2 = await client.get(_SEARCH_URL, params={**base, "pageSize": "200"}, timeout=15)
        ids |= _extract_newsids(r2.text)
    except Exception as exc:
        log.warning("Year %d fetch failed: %s", year, exc)

    return ids


async def phase1(year_filter: int | None = None) -> None:
    """Collect all newsids by year, diff against DB, save missing list."""
    await init_db()

    # Load existing newsids from DB (all stjornarradid sources)
    async with _db_conn.AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(Document.external_id)
            .join(Source, Document.source_id == Source.id)
        )).scalars().all()
    db_ids: set[str] = set(rows)
    log.info("DB has %d existing document IDs", len(db_ids))

    # Load existing missing file (resume support)
    missing: dict[str, str] = {}  # newsid → cname (empty until phase 2 fills it)
    if _MISSING_FILE.exists():
        missing = json.loads(_MISSING_FILE.read_text())
        log.info("Loaded %d existing missing entries", len(missing))

    years = [year_filter] if year_filter else _YEARS

    async with make_client() as client:
        for year in years:
            year_ids = await _fetch_year_newsids(client, year)
            new = year_ids - db_ids - set(missing.keys())
            if new:
                log.info("Year %d: %d total, %d new missing", year, len(year_ids), len(new))
                for nid in new:
                    missing[nid] = ""  # cname unknown until phase 2
            else:
                log.info("Year %d: %d total, 0 new", year, len(year_ids))
            await asyncio.sleep(0.3)

    _MISSING_FILE.parent.mkdir(exist_ok=True)
    _MISSING_FILE.write_text(json.dumps(missing, indent=2, ensure_ascii=False))
    log.info("Phase 1 done. Missing newsids: %d → %s", len(missing), _MISSING_FILE)


# ── Phase 2: import missing ───────────────────────────────────────────────────

async def _ensure_source_by_name(
    session: AsyncSession, display_name: str, short_name: str
) -> uuid.UUID:
    result = await session.execute(
        select(Source).where(Source.short_name == short_name)
    )
    source = result.scalar_one_or_none()
    if source is None:
        source_id = uuid.uuid4()
        session.add(Source(
            id=source_id,
            short_name=short_name,
            display_name=display_name,
            base_url=f"{_DETAIL_BASE}/gogn/urskurdir-og-alit-/",
        ))
        await session.commit()
        log.info("Created new source: %s (%s)", short_name, display_name)
        return source_id
    return source.id


def _cname_to_short_name(cname: str) -> str:
    """Derive a short_name slug from a cname string."""
    s = cname.lower()
    # Remove common prefixes
    s = re.sub(r'^úrskurðir\s+(á\s+)?', '', s)
    s = re.sub(r'^álit\s+', '', s)
    # Transliterate Icelandic chars
    for src, dst in [('á','a'),('é','e'),('í','i'),('ó','o'),('ú','u'),('ý','y'),
                     ('ð','d'),('þ','th'),('æ','ae'),('ö','o')]:
        s = s.replace(src, dst)
    # Keep only word chars
    s = re.sub(r'[^\w\s]', '', s)
    s = re.sub(r'\s+', '_', s.strip())
    return s[:50]


def _config_for_cname(cname: str, short_name: str) -> SourceConfig:
    """Return a SourceConfig for a historical committee not in registry."""
    from engine.config.sources import SourceConfig as SC
    return SC(
        short_name=short_name,
        display_name=cname,
        abbreviation="Ú.Sr.",
        instance_tier=1,
        has_lower_court=False,
        parse_parties="none",
        verdict_type_default="Úrskurður",
        verdict_types_allowed=["Úrskurður", "Álit", "Ákvörðun", "Dómur"],
        case_number_prefix="",
        h1_use_display_name=True,
    )


async def phase2(dry_run: bool = False) -> None:
    """Fetch each missing newsid, group by cname, import into DB."""
    if not _MISSING_FILE.exists():
        log.error("Missing file not found. Run --phase 1 first.")
        return

    missing: dict[str, str] = json.loads(_MISSING_FILE.read_text())
    pending = {nid: cn for nid, cn in missing.items() if cn != "DONE"}
    log.info("Phase 2: %d missing newsids to process", len(pending))

    await init_db()

    # Source registry (known + dynamic)
    source_id_cache: dict[str, uuid.UUID] = {}
    taken_cache: dict[str, set[str]] = {}  # short_name → taken filenames

    async with make_client() as client:
        for i, (newsid, cname) in enumerate(list(pending.items())):
            detail_url = (
                f"{_DETAIL_BASE}/gogn/urskurdir-og-alit-/stakur-urskurdur/"
                f"?newsid={newsid}&cname={urllib.parse.quote(cname)}"
            )
            await asyncio.sleep(_REQUEST_DELAY)

            try:
                resp = await get_with_retry(client, detail_url)
            except Exception as exc:
                log.warning("Fetch failed for %s: %s", newsid, exc)
                continue

            if resp.status_code != 200:
                log.warning("HTTP %d for %s", resp.status_code, newsid)
                continue

            # Extract cname from response if not yet known.
            # The detail page embeds it in <meta data-category='Ministry,CommitteeName,type:X'>
            if not cname:
                cname = resp.url.params.get("cname", "")
            if not cname:
                cat_m = re.search(r"data-category='([^']*)'", resp.text)
                if cat_m:
                    parts = [p.strip() for p in cat_m.group(1).split(',')
                             if p.strip() and not p.strip().startswith('type:')]
                    # Last non-type part is typically the committee name
                    if parts:
                        cname = parts[-1]
            if cname:
                missing[newsid] = cname

            # Determine source
            short_name: str | None = None
            config: SourceConfig | None = None

            # Check if cname matches a known source display_name
            for sn, cfg in SOURCE_REGISTRY.items():
                if cfg.display_name == cname:
                    short_name = sn
                    config = cfg
                    break

            if not short_name:
                short_name = _cname_to_short_name(cname) if cname else "stjornarradid_unknown"
                config = _config_for_cname(cname or "Óþekkt", short_name)

            # Ensure source exists in DB
            if short_name not in source_id_cache:
                async with _db_conn.AsyncSessionLocal() as session:
                    sid = await _ensure_source_by_name(session, config.display_name, short_name)
                source_id_cache[short_name] = sid

                # Load taken filenames
                async with _db_conn.AsyncSessionLocal() as session:
                    rows = (await session.execute(
                        select(Document.verdict_filename)
                        .where(Document.source_id == sid)
                        .where(Document.verdict_filename.isnot(None))
                    )).scalars().all()
                taken_cache[short_name] = set(rows)

            source_id = source_id_cache[short_name]
            taken = taken_cache[short_name]

            # Parse and import
            raw = await _parse_case_page(resp.text, newsid, str(resp.url), cname, "", client=client)
            if raw is None:
                log.warning("Parse failed for %s", newsid)
                continue

            log.info("[%d/%d] %s  case=%s  cname=%s",
                     i+1, len(pending), newsid[:8],
                     raw.get("case_number_str") or "?", cname[:50])

            if dry_run:
                missing[newsid] = f"DRY:{cname}"
                continue

            # Register dynamic short_name so Extractor can find it
            if short_name not in _ext_mod._EXTRACTORS and _stjornarradid_fn:
                _ext_mod._EXTRACTORS[short_name] = _stjornarradid_fn

            doc = _build_document(raw, source_id, config)

            async with _db_conn.AsyncSessionLocal() as session:
                try:
                    await _upsert_doc(session, doc)
                    await session.commit()
                except Exception as exc:
                    log.error("Upsert failed: %s", exc)
                    await session.rollback()
                    continue

            # Render markdown
            base = verdict_filename(doc, config)
            vf = unique_verdict_filename(base, taken)
            taken.add(vf)
            try:
                write_markdown(doc, config, vf=vf)
            except Exception as exc:
                log.warning("write_markdown failed: %s", exc)
                vf = None

            if vf:
                async with _db_conn.AsyncSessionLocal() as session:
                    await session.execute(
                        update(Document)
                        .where(Document.source_id == source_id,
                               Document.external_id == newsid)
                        .values(verdict_filename=vf)
                    )
                    await session.commit()

            missing[newsid] = f"DONE:{cname}"
            _MISSING_FILE.write_text(json.dumps(missing, indent=2, ensure_ascii=False))

    log.info("Phase 2 done.")
    done = sum(1 for v in missing.values() if v.startswith("DONE"))
    log.info("Imported: %d / %d", done, len(missing))


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", type=int, choices=[1, 2], required=True)
    parser.add_argument("--year", type=int, default=None, help="Single year for phase 1")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.phase == 1:
        asyncio.run(phase1(year_filter=args.year))
    else:
        asyncio.run(phase2(dry_run=args.dry_run))
