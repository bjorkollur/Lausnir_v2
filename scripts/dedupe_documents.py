"""Remove documents that are the same verdict imported twice.

island.is sometimes republishes a verdict under a new GUID.  The upsert keys on
(source_id, external_id), so the new GUID becomes a second row: same court, same
case number, same date, same verdict type, and — in 9 of the 10 pairs found on
2026-09-29 — byte-identical body text.  Two rows for one verdict double-count in
the catalogue facets and in `cited_by_count`.

**Which row survives:** the most recently imported one.  That is not a taste
call.  Probing all twenty ids against the source (`--probe`) showed the older row
dead (HTTP 404 on the Next.js data route) and the newer alive in every pair the
source could decide — five of ten; for the other five island.is serves both
GUIDs.  The newer fetch also carries the source's current text, which is where
the pairs differ at all (a few bytes of summary, and 15 more bytes of keywords in
Lrd. 353/2022).

**Before deleting** every doomed row is written out in full, because for a dead
GUID our row is the only copy left anywhere.  Deleting cascades to `passages`,
`citations` and `document_links`; the survivor carries its own copies of all but
two málskotsbeiðni edges, so `link_malskotsbeidnir.py` has to run afterwards —
the script says so when it finishes.

Usage:
    uv run python scripts/dedupe_documents.py --dry-run
    uv run python scripts/dedupe_documents.py --probe --dry-run
    uv run python scripts/dedupe_documents.py --out docs/snapshots/dedupe.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
from sqlalchemy import text

import engine.database.connection as _db_conn
from engine.config.sources import get_config
from engine.database.connection import init_db

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

# What makes two rows the same verdict.  `court` is in the key because
# héraðsdómar reuse case numbers across courts (docs/wiki/09-gildrur.md).
IDENTITY = ("source_id", "court", "case_number", "document_date", "verdict_type")

# The key holds only where it has been checked against the data.  It does NOT
# hold generally: heilbrigdi_raduneyti 008/2020 is four different rulings sharing
# one case number and date (4.192 / 4.935 / 5.348 bytes of different text), and
# hugverkastofa dates everything 01-01-<year>.  Run those with an explicit
# --source once someone has established what identifies a document there.
DEFAULT_SOURCES = ("haestirettur", "landsrettur", "heradsdomstolar")

_FIND_SQL = f"""
WITH scoped AS (
    SELECT d.* FROM documents d
    JOIN sources s ON s.id = d.source_id
    WHERE s.short_name = ANY(:sources)
),
dup AS (
    SELECT {', '.join('d.' + c for c in IDENTITY)}
    FROM scoped d
    GROUP BY {', '.join('d.' + c for c in IDENTITY)}
    HAVING count(*) > 1
)
SELECT s.short_name, d.*, md5(coalesce(d.body_text, '')) AS body_md5,
       row_number() OVER (
           PARTITION BY {', '.join('d.' + c for c in IDENTITY)}
           ORDER BY d.created_at DESC, d.verdict_filename DESC
       ) AS rn
FROM scoped d
JOIN sources s ON s.id = d.source_id
JOIN dup USING ({', '.join(IDENTITY)})
ORDER BY s.short_name, d.case_number, rn
"""

_PROBE_URL = "https://island.is/_next/data/{build}/domar/{external_id}.json"


def _jsonable(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


async def _build_id(client: httpx.AsyncClient) -> str | None:
    """The Next.js build id, needed for the data route the importer also uses."""
    try:
        html = (await client.get("https://island.is/domar", timeout=30)).text
    except httpx.HTTPError as exc:
        log.warning("gat ekki sótt buildId: %s", exc)
        return None
    marker = '"buildId":"'
    i = html.find(marker)
    if i < 0:
        log.warning("buildId fannst ekki í /domar")
        return None
    return html[i + len(marker):html.index('"', i + len(marker))]


async def _alive(client: httpx.AsyncClient, build: str, external_id: str) -> bool | None:
    try:
        r = await client.get(_PROBE_URL.format(build=build, external_id=external_id), timeout=30)
    except httpx.HTTPError as exc:
        log.warning("  könnun mistókst fyrir %s: %s", external_id, exc)
        return None
    return r.status_code == 200


async def run(dry_run: bool, probe: bool, out: Path | None,
              sources: list[str], allow_text_diff: bool) -> None:
    await init_db()
    async with _db_conn.AsyncSessionLocal() as session:
        rows = [dict(r) for r in (
            await session.execute(text(_FIND_SQL), {"sources": sources})
        ).mappings().all()]

    if not rows:
        log.info("engin tvítekin skjöl.")
        return

    keep = [r for r in rows if r["rn"] == 1]
    doomed = [r for r in rows if r["rn"] > 1]
    log.info("%d tvítekin mál, %d raðir, %d til eyðingar", len(keep), len(rows), len(doomed))

    if probe:
        async with httpx.AsyncClient() as client:
            build = await _build_id(client)
            if build:
                log.info("buildId %s — kanna hvort id lifi hjá heimildinni", build)
                for r in rows:
                    alive = await _alive(client, build, r["external_id"])
                    tag = {True: "LIFIR", False: "dautt", None: "?"}[alive]
                    log.info("  %-38s %-6s %s", r["verdict_filename"], tag,
                             "heldur sér" if r["rn"] == 1 else "EYÐIST")
                    if r["rn"] == 1 and alive is False:
                        log.warning("    ATH: röðin sem heldur sér er dauð hjá heimildinni")

    # A row whose text differs from the survivor's may not be the same document
    # at all — that is how the sources outside DEFAULT_SOURCES fail this key.
    survivor_text = {
        tuple(r[c] for c in IDENTITY): r["body_md5"] for r in keep
    }
    differing = [r for r in doomed
                 if survivor_text.get(tuple(r[c] for c in IDENTITY)) != r["body_md5"]]
    if differing and not allow_text_diff:
        log.warning("%d raðir hafa annan texta en sú sem heldur sér — sleppt "
                    "(--allow-text-diff til að taka þær með):", len(differing))
        for r in differing:
            log.warning("  %-16s %-12s %s", r["short_name"], r["case_number"], r["verdict_filename"])
        doomed = [r for r in doomed if r not in differing]
    elif differing:
        log.warning("%d raðir með annan texta teknar með (--allow-text-diff):", len(differing))
        for r in differing:
            log.warning("  %-16s %-12s %s", r["short_name"], r["case_number"], r["verdict_filename"])

    for r in doomed:
        log.info("EYÐA %-16s %-12s %s", r["short_name"], r["case_number"], r["verdict_filename"])
    if not doomed:
        log.info("ekkert til eyðingar eftir varnirnar.")
        return

    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(doomed, ensure_ascii=False, indent=1, default=_jsonable),
            encoding="utf-8",
        )
        log.info("afrit af eyddum röðum: %s (%d raðir)", out, len(doomed))
    elif not dry_run:
        log.error("--out er skylda þegar raunverulega er eytt: fyrir dautt GUID er röðin "
                  "eina eintakið sem eftir er.")
        return

    if dry_run:
        log.info("(þurrkeyrsla — ekkert eytt)")
        return

    doomed_ids = [r["id"] for r in doomed]
    async with _db_conn.AsyncSessionLocal() as session:
        result = await session.execute(
            text("DELETE FROM documents WHERE id = ANY(:ids) RETURNING id"),
            {"ids": doomed_ids},
        )
        deleted = len(result.fetchall())
        await session.commit()

    # Files only after the rows are gone, and only when nothing points at them
    # any more.  Outside the court sources a duplicate pair usually shares ONE
    # verdict_filename — 139 of 161 candidate groups on 2026-09-30 — so removing
    # the doomed row's file would take the survivor's only copy with it.
    removed_files = kept_files = 0
    for r in doomed:
        vf = r["verdict_filename"]
        if not vf:
            continue
        async with _db_conn.AsyncSessionLocal() as session:
            still_used = (await session.execute(
                text("""SELECT 1 FROM documents
                        WHERE source_id = :sid AND verdict_filename = :vf LIMIT 1"""),
                {"sid": r["source_id"], "vf": vf},
            )).first()
        if still_used:
            kept_files += 1
            log.info("  skrá höfð áfram (önnur röð notar hana): %s", vf)
            continue
        config = get_config(r["short_name"])
        for path in (config.markdown_path(vf), config.pdf_path(vf)):
            if path.exists():
                path.unlink()
                removed_files += 1

    log.info("%d raðir eyddar, %d skrár fjarlægðar, %d skráarnöfn höfð áfram",
             deleted, removed_files, kept_files)
    log.info("NEXT: uv run python scripts/link_malskotsbeidnir.py   "
             "# tvær leyfisbeidni_um-tengingar hanga á eyddu röðunum")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="sýna hvað yrði eytt")
    ap.add_argument("--probe", action="store_true",
                    help="kanna hjá island.is hvort hvert id lifi (staðfesting, ekki forsenda)")
    ap.add_argument("--out", type=Path,
                    help="skrá fyrir fullt afrit af eyddu röðunum (skylda við raunverulega eyðingu)")
    ap.add_argument("--source", action="append", dest="sources", metavar="HEIMILD",
                    help=f"heimild til að skoða (má gefa oftar; sjálfgefið {', '.join(DEFAULT_SOURCES)})")
    ap.add_argument("--allow-text-diff", action="store_true",
                    help="eyða líka röðum sem hafa annan texta en sú sem heldur sér")
    args = ap.parse_args()
    asyncio.run(run(args.dry_run, args.probe, args.out,
                    args.sources or list(DEFAULT_SOURCES), args.allow_text_diff))


if __name__ == "__main__":
    main()
