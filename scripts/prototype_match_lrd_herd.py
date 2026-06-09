"""PROTOTYPE — measure Lrd ↔ Héraðsdómur match quality. Read-only, no writes.

Lrd.lower_body_text IS the embedded héraðsdómur body. We match each Lrd doc to
the héraðsdómur in the DB it embeds.

Signals (measured, see conversation):
  - court  from lower_body header  (~99%)
  - date   from lower_body header  (~98%, but sometimes hearing date not verdict date)
  - héraðs case_number in text     (~29%, exact when present)

Matching tiers:
  A  exact: extracted héraðs case_number + court
  B  court + exact date
  C  court + date ± WINDOW days

Similarity is NUMBER-CENTRIC because one tier may be anonymised and the other not
(name "Jón" on one tier, "X" on the other) — but law refs, amounts, dates, section
numbers are never anonymised. We compare numeric tokens + a few words around them.

Usage:
    uv run python scripts/prototype_match_lrd_herd.py --sample 300
"""
from __future__ import annotations

import argparse
import asyncio
import re
from collections import Counter
from datetime import date, timedelta

import engine.database.connection as _db_conn
from engine.database.connection import init_db
from engine.database.models import Document, Source
from sqlalchemy import and_, select

_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "maí": 5, "jún": 6,
           "júl": 7, "ág": 8, "sep": 9, "okt": 10, "nóv": 11, "des": 12}
_GEN_TO_ABBR = {
    "Reykjavíkur": "Hérd. Rvk.", "Reykjaness": "Hérd. Reykn.",
    "Vesturlands": "Hérd. Vestl.", "Vestfjarða": "Hérd. Vestfj.",
    "Austurlands": "Hérd. Austl.", "Suðurlands": "Hérd. Suðl.",
    "Norðurlands eystra": "Hérd. Norðeyst.", "Norðurlands vestra": "Hérd. Norðvest.",
}
_COURT_RE = re.compile(
    r'Héraðsdóm\w*\s+(Norðurlands\s+(?:eystra|vestra)|Reykjavíkur|Reykjaness'
    r'|Vesturlands|Vestfjarða|Austurlands|Suðurlands)')
_DATE_RE = re.compile(
    r'(\d{1,2})\.\s+(jan|feb|mar|apr|maí|jún|júl|ág|sep|okt|nóv|des)\w*\.?\s+(\d{4})')
_CASENUM_RE = re.compile(r'\b([ES])-?(\d+)/(\d{4})\b')

# Distinctive numeric tokens: law/case refs, amounts, section numbers, plain numbers.
_NUM_TOKEN_RE = re.compile(r'\d[\d.,/]*\d|\d')


def parse_header(lower: str) -> tuple[str | None, date | None]:
    head = lower[:220]
    cm = _COURT_RE.search(head)
    dm = _DATE_RE.search(head)
    court = _GEN_TO_ABBR.get(cm.group(1)) if cm else None
    d = None
    if dm:
        try:
            d = date(int(dm.group(3)), _MONTHS[dm.group(2)], int(dm.group(1)))
        except ValueError:
            d = None
    return court, d


def extract_herd_casenum(text: str) -> set[str]:
    """E-1234/2020 style numbers — normalise to 'E-1234/2020'."""
    return {f"{m.group(1)}-{m.group(2)}/{m.group(3)}" for m in _CASENUM_RE.finditer(text)}


def num_tokens(text: str) -> Counter:
    """Multiset of numeric tokens (anonymisation-robust signal)."""
    toks = _NUM_TOKEN_RE.findall(text or "")
    # drop bare single digits (too common: section numbering noise)
    return Counter(t for t in toks if len(t) >= 2)


def word_tokens(text: str) -> set[str]:
    return set(re.findall(r'\w{4,}', (text or "").lower()))


def sim_numbers(a_nums: Counter, b_nums: Counter) -> float:
    """Overlap coefficient over numeric token multisets.

    Containment, not Jaccard: lower_body_text is the embedded EXCERPT while the
    héraðsdómur DB body_text carries extra boilerplate (party block, keywords,
    preamble). Dividing by the union punishes the longer text; dividing by the
    smaller multiset asks 'how much of the shorter doc's numbers appear in the
    other' — the right question for matching.
    """
    if not a_nums or not b_nums:
        return 0.0
    inter = sum((a_nums & b_nums).values())
    smaller = min(sum(a_nums.values()), sum(b_nums.values()))
    return inter / smaller if smaller else 0.0


def sim_words(a_words: set[str], b_words: set[str]) -> float:
    """Overlap coefficient over word sets (same length-robustness rationale)."""
    if not a_words or not b_words:
        return 0.0
    return len(a_words & b_words) / min(len(a_words), len(b_words))


async def main(sample: int, window: int) -> None:
    await init_db()
    async with _db_conn.AsyncSessionLocal() as session:
        lrd = (await session.execute(
            select(Source.id).where(Source.short_name == "landsrettur"))).scalar()
        herd = (await session.execute(
            select(Source.id).where(Source.short_name == "heradsdomstolar"))).scalar()

        from sqlalchemy import func as sa_func
        lrd_rows = (await session.execute(
            select(Document.case_number, Document.body_text, Document.lower_body_text)
            .where(Document.source_id == lrd)
            .where(Document.lower_body_text.isnot(None))
            .order_by(sa_func.random())
            .limit(sample)
        )).all()

        results = []  # (lrd_cn, tier, n_cands, best_cn, sim_num, sim_word, confirmed, truth_in_cands)
        for lrd_cn, body, lower in lrd_rows:
            court, d = parse_header(lower or "")
            if not court or not d:
                results.append((lrd_cn, "HEADER_FAIL", 0, None, 0.0, 0.0, False, False))
                continue

            lower_nums = num_tokens(lower)
            lower_words = word_tokens(lower)
            herd_cns = extract_herd_casenum(body or "") | extract_herd_casenum(lower or "")

            # Tier A: exact case number + court
            cand_rows = []
            tier = None
            if herd_cns:
                rows = (await session.execute(
                    select(Document.case_number, Document.body_text)
                    .where(and_(Document.source_id == herd,
                                Document.court == court,
                                Document.case_number.in_(herd_cns)))
                )).all()
                if rows:
                    cand_rows = rows
                    tier = "A_casenum"

            # Tier B: court + exact date
            if not cand_rows:
                rows = (await session.execute(
                    select(Document.case_number, Document.body_text)
                    .where(and_(Document.source_id == herd,
                                Document.court == court,
                                Document.document_date == d))
                )).all()
                if rows:
                    cand_rows = rows
                    tier = "B_court_date"

            # Tier C: court + date ± window
            if not cand_rows:
                rows = (await session.execute(
                    select(Document.case_number, Document.body_text)
                    .where(and_(Document.source_id == herd,
                                Document.court == court,
                                Document.document_date >= d - timedelta(days=window),
                                Document.document_date <= d + timedelta(days=window)))
                )).all()
                if rows:
                    cand_rows = rows
                    tier = "C_window"

            if not cand_rows:
                results.append((lrd_cn, "NO_CAND", 0, None, 0.0, 0.0, False, False))
                continue

            # Pick the candidate maximising combined = max(number-sim, word-sim).
            best = None
            best_combined = -1.0
            best_sn = best_sw = 0.0
            for hcn, hbody in cand_rows:
                sn = sim_numbers(lower_nums, num_tokens(hbody or ""))
                sw = sim_words(lower_words, word_tokens(hbody or ""))
                combined = max(sn, sw)
                if combined > best_combined:
                    best_combined = combined
                    best_sn, best_sw = sn, sw
                    best = hcn
            best_cn = best
            confirmed = best_cn in herd_cns if herd_cns else False
            # Disambiguation ground truth: the true héraðs cn is among the candidates.
            cand_cns = {hcn for hcn, _ in cand_rows}
            truth_in_cands = bool(herd_cns & cand_cns)
            results.append((lrd_cn, tier, len(cand_rows), best_cn,
                            best_sn, best_sw, confirmed, truth_in_cands))

    # ── Report ────────────────────────────────────────────────────────────────
    n = len(results)
    tiers = Counter(r[1] for r in results)
    print(f"\nSample: {n} Lrd docs (window=±{window}d)\n")
    print("Tier distribution:")
    for t, c in tiers.most_common():
        print(f"  {t:16s} {c:4d}  ({100*c//n}%)")

    matched = [r for r in results if r[1] in ("A_casenum", "B_court_date", "C_window")]

    # How often is there exactly ONE candidate? (no disambiguation needed)
    single = [r for r in matched if r[2] == 1]
    multi = [r for r in matched if r[2] > 1]
    print(f"\nCandidate count:")
    print(f"  exactly 1 candidate: {len(single)}  ({100*len(single)//n}%)  → trivial, no text needed")
    print(f"  >1 candidate:        {len(multi)}  ({100*len(multi)//n}%)  → text sim must disambiguate")

    def combined(r):
        return max(r[4], r[5])

    def histogram(title, rows):
        print(f"\n{title} ({len(rows)} docs):")
        buckets = Counter(min(int(combined(r) * 10), 9) for r in rows)
        for b in range(9, -1, -1):
            bar = "█" * (buckets[b] * 50 // max(1, len(rows)))
            print(f"  {b/10:.1f}-{(b+1)/10:.1f}  {buckets[b]:4d}  {bar}")

    # Combined-sim = max(number-sim, word-sim), overlap coefficient.
    histogram("Combined-sim histogram — MULTI-candidate", multi)
    histogram("Combined-sim histogram — SINGLE-candidate", single)

    # Ground truth: case-number-confirmed picks (winner IS the extracted héraðs cn).
    conf = [r for r in matched if r[6]]
    if conf:
        avg_n = sum(r[4] for r in conf) / len(conf)
        avg_w = sum(r[5] for r in conf) / len(conf)
        avg_c = sum(combined(r) for r in conf) / len(conf)
        print(f"\nGround-truth ({len(conf)} case-number-confirmed = definitely correct):")
        print(f"  number-sim avg={avg_n:.2f}  word-sim avg={avg_w:.2f}  combined avg={avg_c:.2f}")
        for thr in (0.3, 0.4, 0.5, 0.6, 0.7):
            kept = sum(1 for r in conf if combined(r) >= thr)
            print(f"  combined >= {thr}: {kept}/{len(conf)}  ({100*kept//len(conf)}% kept)")

    # Disambiguation accuracy: multi-candidate cases where the TRUE héraðs cn is in
    # the candidate set. Did argmax(combined) actually pick it?
    disambig = [r for r in multi if r[7]]
    if disambig:
        correct = sum(1 for r in disambig if r[6])
        print(f"\nDisambiguation accuracy (multi-candidate, truth in candidate set):")
        print(f"  argmax(combined) picked the correct doc: {correct}/{len(disambig)}"
              f"  ({100*correct//len(disambig)}%)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=300)
    parser.add_argument("--window", type=int, default=14)
    args = parser.parse_args()
    asyncio.run(main(args.sample, args.window))
