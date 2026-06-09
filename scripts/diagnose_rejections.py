"""Diagnose court_date rejections in link_appeals: are we losing real matches?

For Lrd docs whose parsed court+date matched a héraðsdómur but best text-sim
< threshold, print the failure signature so we can tell apart:
  (a) true héraðsdómur absent from DB (rejection correct)
  (b) hearing-date != verdict-date (true doc in a nearby date, missed)
  (c) thin/short héraðs body_text inflating low overlap (metric issue)
"""
from __future__ import annotations

import asyncio
from collections import Counter

from sqlalchemy import and_, select

import engine.database.connection as _db_conn
from engine.database.connection import init_db
from engine.database.models import Document, Source
from link_appeals import (
    extract_herd_casenum, num_tokens, parse_herd_header as parse_header,
    sim_numbers, sim_words, word_tokens,
)
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))


async def main(threshold: float = 0.5, show: int = 15) -> None:
    await init_db()
    async with _db_conn.AsyncSessionLocal() as session:
        lrd = (await session.execute(select(Source.id).where(Source.short_name == "landsrettur"))).scalar()
        herd = (await session.execute(select(Source.id).where(Source.short_name == "heradsdomstolar"))).scalar()

        rows = (await session.execute(
            select(Document.id, Document.case_number, Document.body_text, Document.lower_body_text)
            .where(Document.source_id == lrd)
            .where(Document.lower_body_text.isnot(None)))).all()

        cat = Counter()
        shown = 0
        for lid, lcn, body, lower in rows:
            court, d = parse_header(lower or "")
            if not court or not d:
                continue
            herd_cns = extract_herd_casenum(body or "") | extract_herd_casenum(lower or "")
            # only court_date tier: skip if casenum candidates exist
            if herd_cns:
                has = (await session.execute(
                    select(Document.id).where(and_(
                        Document.source_id == herd, Document.court == court,
                        Document.case_number.in_(herd_cns))).limit(1))).first()
                if has:
                    continue
            cands = (await session.execute(
                select(Document.id, Document.case_number, Document.body_text)
                .where(and_(Document.source_id == herd, Document.court == court,
                            Document.document_date == d)))).all()
            if not cands:
                continue
            ln, lw = num_tokens(lower), word_tokens(lower)
            best = max((max(sim_numbers(ln, num_tokens(b or "")),
                            sim_words(lw, word_tokens(b or ""))), cn, b)
                       for _, cn, b in cands)
            best_sim, best_cn, best_body = best
            if best_sim >= threshold:
                continue

            # Classify the rejection
            herd_cn_in_db = False
            if herd_cns:
                any_cn = (await session.execute(
                    select(Document.id).where(and_(
                        Document.source_id == herd,
                        Document.case_number.in_(herd_cns))).limit(1))).first()
                herd_cn_in_db = bool(any_cn)

            if herd_cns and not herd_cn_in_db:
                cat["herd_casenum_NOT_in_db (true doc absent)"] += 1
                label = "ABSENT"
            elif herd_cns and herd_cn_in_db:
                cat["herd_casenum_in_db_but_wrong_date (date mismatch)"] += 1
                label = "DATE?"
            else:
                cat["no_casenum_extracted (cannot verify)"] += 1
                label = "NOCN"

            if shown < show:
                shown += 1
                herd_len = len(best_body or "")
                print(f"[{label}] court={court} date={d} cands={len(cands)} "
                      f"best_sim={best_sim:.2f} lower_len={len(lower)} herd_len={herd_len} "
                      f"herd_cns={sorted(herd_cns)[:2]}")

        print("\nCourt_date rejection breakdown:")
        tot = sum(cat.values())
        for k, c in cat.most_common():
            print(f"  {k:50s} {c:5d}  ({100*c//max(1,tot)}%)")
        print(f"  {'TOTAL':50s} {tot:5d}")


if __name__ == "__main__":
    asyncio.run(main())
