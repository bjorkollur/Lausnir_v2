"""Store the Hæstiréttur case number of pending appeals on the granted petitions.

island.is/s/haestirettur/afryjud-mal lists the appeals Hæstiréttur has not decided
("Áfrýjuð mál"): case number, parties with counsel and the day the appeal was
issued (`webSupremeCourtAppeals`). A granted petition (`appeal_outcome = 'veitt'`)
that has no Hæstiréttur judgment yet is matched to its row on parties and counsel
and gets `documents.hrd_case_number`, e.g. "51/2025".

Matching: one petition ↔ one listed case, best score first, the case must have
been issued on or after the petition. Score = Jaccard overlap of distinctive party
words and counsel. Ties and zero scores stay unmatched. A number already stored is
only replaced when the list now says something else; nothing is ever cleared (the
number stays right after the judgment arrives and is what the judgment is called).

Usage:
    uv run python scripts/sync_pending_hrd_cases.py --dry-run
    uv run python scripts/sync_pending_hrd_cases.py
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from sqlalchemy import text

import engine.database.connection as _db_conn
from engine.processors.http_utils import make_client, post_with_retry
from link_malskotsbeidnir import counsel_names, party_words

log = logging.getLogger(__name__)

_QUERY = """query Q($input: WebSupremeCourtAppealsInput!) {
  webSupremeCourtAppeals(input: $input) {
    total items { title caseNumber appealPolicyDate registrationDate verdictDate }
  }
}"""


async def fetch_pending() -> list[dict]:
    items: list[dict] = []
    async with make_client() as client:
        page, total = 1, None
        while total is None or len(items) < total:
            data = await post_with_retry(client, "https://island.is/api/graphql",
                                         {"query": _QUERY, "variables": {"input": {"page": page}}})
            w = data["data"]["webSupremeCourtAppeals"]
            total = w["total"]
            if not w["items"]:
                break
            items += w["items"]
            page += 1
    return items


def _sides(title: str) -> tuple[list[dict], list[dict]]:
    left, _, right = title.partition(" gegn ")
    return [{"name": left}], [{"name": right}]


def _tokens(plaintiffs, defendants) -> set[str]:
    return party_words(plaintiffs, defendants) | {f"c:{n}" for n in counsel_names(plaintiffs, defendants)}


def match_pending(petitions: list, cases: list[dict]) -> dict:
    """{petition id: case number}. `petitions` need .id, .document_date, .plaintiffs, .defendants.

    Score = Jaccard overlap of party words and counsel, so a listed case that has
    extra parties scores below one that names exactly the petition's parties (the
    seven WOW air cases share Skúli Mogensen and the same counsel). The best pair
    is fixed first, then the rest again without the cases already taken; a petition
    whose best case is tied with another stays unmatched.
    """
    cand: dict = {}
    for p in petitions:
        mine = _tokens(p.plaintiffs, p.defendants)
        for c in cases:
            issued = (c.get("appealPolicyDate") or c.get("registrationDate") or "")[:10]
            if not issued or date.fromisoformat(issued) < p.document_date:
                continue
            theirs = _tokens(*_sides(c["title"]))
            inter = mine & theirs
            if inter:
                cand.setdefault(p.id, {})[c["caseNumber"]] = len(inter) / len(mine | theirs)
    out: dict = {}
    taken: set[str] = set()
    while True:
        best = None
        for pid, scores in cand.items():
            if pid in out:
                continue
            avail = sorted(((sc, cn) for cn, sc in scores.items() if cn not in taken), reverse=True)
            if not avail or (len(avail) > 1 and avail[0][0] == avail[1][0]):
                continue
            if best is None or avail[0][0] > best[0]:
                best = (avail[0][0], pid, avail[0][1])
        if best is None:
            return out
        _, pid, cn = best
        out[pid] = cn
        taken.add(cn)


async def main(dry_run: bool) -> None:
    await _db_conn.init_db()
    cases = await fetch_pending()
    async with _db_conn.AsyncSessionLocal() as s:
        petitions = (await s.execute(text("""
            SELECT d.id, d.case_number, d.document_date, d.plaintiffs, d.defendants, d.hrd_case_number
            FROM documents d JOIN sources so ON so.id = d.source_id AND so.short_name = 'malskotsbeidnir'
            WHERE d.appeal_outcome = 'veitt'
              AND NOT EXISTS (SELECT 1 FROM document_links x WHERE x.relation = 'leiddi_til_doms'
                              AND x.from_doc_id = d.id)
            ORDER BY d.document_date"""))).all()
        matched = match_pending(petitions, cases)
        changed = 0
        for p in petitions:
            case = matched.get(p.id)
            if case is None or case == p.hrd_case_number:
                continue
            print(f"{p.case_number:>9}  {p.hrd_case_number or '-':>9} -> {case}")
            changed += 1
            if not dry_run:
                await s.execute(text("UPDATE documents SET hrd_case_number = :c WHERE id = :i"),
                                {"c": case, "i": p.id})
        if dry_run:
            await s.rollback()
        else:
            await s.commit()
    print(f"\n{'DRY RUN — nothing written. ' if dry_run else ''}"
          f"pending cases listed: {len(cases)}; petitions without judgment: {len(petitions)}; "
          f"matched: {len(matched)}; changed: {changed}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    asyncio.run(main(ap.parse_args().dry_run))
