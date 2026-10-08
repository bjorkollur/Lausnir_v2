"""Repoint Landsréttur → Hæstiréttur appeal edges that cannot be true.

An `appealed_to` edge says the Landsréttur ruling was reviewed by the Hæstiréttur
judgment. When the Landsréttur ruling is dated AFTER the judgment, the edge has
picked the wrong ruling of a case that has several (a dómur, a later úrskurður, the
second dómur after a remand — they all carry one case number, and island.is serves
them under one id). The resolution_link / casenum matchers cannot tell them apart.

For each such edge made by a matcher (never `manual`: those were set by hand) the
fix is the ruling of the same Landsréttur case that is already in the database and
dated on or before the Hæstiréttur judgment — if exactly one exists. With none, or
several, the edge is left alone.

Both rows of a relationship are moved together: `appealed_to` (lower → higher) and
its mirror `appealed_from` (higher → lower). Rows are updated in place, never deleted,
except where the correct pair already exists — then the wrong row is the duplicate.

Usage:
    uv run python scripts/repair_impossible_lrd_hrd_edges.py --dry-run
    uv run python scripts/repair_impossible_lrd_hrd_edges.py
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text

import engine.database.connection as _db_conn

log = logging.getLogger(__name__)

_FIND = """
SELECT l.id AS link_id, l.method, lo.id AS wrong_id, lo.case_number, lo.document_date AS wrong_date,
       hi.id AS hrd_id, hi.case_number AS hrd_case, hi.document_date AS hrd_date,
       (SELECT array_agg(x.id || '|' || x.document_date::text || '|' || x.verdict_type)
          FROM documents x
         WHERE x.source_id = lo.source_id AND x.case_number = lo.case_number
           AND x.id <> lo.id AND x.document_date <= hi.document_date) AS cands
FROM document_links l
JOIN documents lo ON lo.id = l.from_doc_id
JOIN documents hi ON hi.id = l.to_doc_id
WHERE l.relation = 'appealed_to' AND l.method <> 'manual'
  AND lo.court = 'Lrd.' AND hi.instance_tier = 3
  AND lo.document_date > hi.document_date
ORDER BY hi.document_date
"""


async def main(dry_run: bool) -> None:
    await _db_conn.init_db()
    stats = {"repointed": 0, "no_candidate": 0, "ambiguous": 0, "duplicate_removed": 0}
    async with _db_conn.AsyncSessionLocal() as s:
        rows = (await s.execute(text(_FIND))).mappings().all()
        for r in rows:
            cands = [c.split("|") for c in (r["cands"] or [])]
            if not cands:
                stats["no_candidate"] += 1
                continue
            if len(cands) > 1:
                stats["ambiguous"] += 1
                log.warning("ambiguous: Lrd %s → Hrd %s: %s", r["case_number"], r["hrd_case"], cands)
                continue
            new_id, new_date, new_vt = cands[0]
            body = (await s.execute(
                text("SELECT body_text FROM documents WHERE id = :i"), {"i": r["hrd_id"]})).scalar() or ""
            d = f"{int(new_date[8:10])}. "
            confirmed = bool(re.search(rf"Landsréttar {d}", body))
            print(f"Lrd {r['case_number']:>9}  {r['wrong_date']} → {new_date} {new_vt:<9} "
                  f"(Hrd {r['hrd_case']} {r['hrd_date']}, {r['method']}; "
                  f"Hrd text names the day: {'yes' if confirmed else 'not checked/no'})")
            if dry_run:
                stats["repointed"] += 1
                continue
            for rel, lo_col, hi_col in (("appealed_to", "from_doc_id", "to_doc_id"),
                                        ("appealed_from", "to_doc_id", "from_doc_id")):
                row = (await s.execute(text(
                    f"SELECT id FROM document_links WHERE relation = :rel AND {lo_col} = :w AND {hi_col} = :h"),
                    {"rel": rel, "w": r["wrong_id"], "h": r["hrd_id"]})).first()
                if row is None:
                    continue
                exists = (await s.execute(text(
                    f"SELECT 1 FROM document_links WHERE relation = :rel AND {lo_col} = :n AND {hi_col} = :h"),
                    {"rel": rel, "n": new_id, "h": r["hrd_id"]})).first()
                if exists:
                    await s.execute(text("DELETE FROM document_links WHERE id = :i"), {"i": row[0]})
                    stats["duplicate_removed"] += 1
                else:
                    await s.execute(text(f"UPDATE document_links SET {lo_col} = :n WHERE id = :i"),
                                    {"n": new_id, "i": row[0]})
            stats["repointed"] += 1
        if dry_run:
            await s.rollback()
        else:
            await s.commit()
    print("\nDRY RUN — nothing written" if dry_run else "")
    for k, v in stats.items():
        print(f"{k:20s} {v:4d}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    asyncio.run(main(ap.parse_args().dry_run))
