"""Verify that appeal edges are oriented consistently.

The invariant, per the DocumentLink docstring: a relationship between two
instances is stored as a bidirectional pair, oriented by instance_tier —
lower→higher carries 'appealed_to', higher→lower carries 'appealed_from'.

This broke once already: backfill_hrd_lrd_links.py wrote the two relations the
wrong way round, so 659 edges disagreed with the 11 256 written by
link_appeals.py. Nothing failed loudly — the API just rendered "Áfrýjað til:
<a district court>" on Hæstiréttur judgments, and any query that followed only
one direction silently saw half the graph.

Usage:
    uv run python scripts/check_link_orientation.py
Exits non-zero if the invariant is violated.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text

import engine.database.connection as _db_conn

_CHECKS = {
    "wrong orientation for its relation": """
        SELECT count(*) FROM document_links dl
        JOIN documents f ON f.id = dl.from_doc_id
        JOIN documents t ON t.id = dl.to_doc_id
        WHERE dl.relation IN ('appealed_to','appealed_from')
          AND f.instance_tier IS NOT NULL AND t.instance_tier IS NOT NULL
          AND ((dl.relation = 'appealed_to'   AND f.instance_tier > t.instance_tier)
            OR (dl.relation = 'appealed_from' AND f.instance_tier < t.instance_tier))
    """,
    "same pair carrying both relations in one direction": """
        SELECT count(*) FROM (
            SELECT from_doc_id, to_doc_id FROM document_links
            WHERE relation IN ('appealed_to','appealed_from')
            GROUP BY 1,2 HAVING count(DISTINCT relation) > 1
        ) x
    """,
    "missing mirror edge": """
        SELECT count(*) FROM document_links a
        WHERE a.relation IN ('appealed_to','appealed_from')
          AND NOT EXISTS (
            SELECT 1 FROM document_links b
            WHERE b.from_doc_id = a.to_doc_id AND b.to_doc_id = a.from_doc_id
              AND b.relation = CASE a.relation WHEN 'appealed_to' THEN 'appealed_from'
                                               ELSE 'appealed_to' END)
    """,
}


async def main() -> int:
    await _db_conn.init_db()
    failures = 0
    async with _db_conn.AsyncSessionLocal() as session:
        for label, sql in _CHECKS.items():
            n = (await session.execute(text(sql))).scalar_one()
            status = "ok" if n == 0 else "FAIL"
            if n:
                failures += 1
            print(f"  [{status}] {label}: {n}")
    print("\nAppeal-link orientation " + ("is consistent." if not failures
                                          else f"is BROKEN ({failures} check(s) failed)."))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
