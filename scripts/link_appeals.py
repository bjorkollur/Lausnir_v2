"""Link appeals-chain documents: lower court → higher court.

A higher court's ``lower_body_text`` IS the embedded body of the lower court
ruling it reviewed. We parse that embedded text's header for the lower court +
date, gather candidate lower-court docs from the DB, and pick the one whose
text overlaps best. Overlap is measured on numbers/dates and surrounding words
(both anonymisation-robust) via an overlap coefficient (intersection ÷ smaller
set) — robust to the embedded excerpt being shorter than the lower court's full
body_text.

Edges are written to document_links as (from=lower, to=higher, relation
'appealed_to'). Each run is authoritative for its own tier pair: it clears the
edges it owns (scoped by higher + lower source) before re-inserting, so a
re-pick never leaves a stale edge.

The chain Hérd → Lrd → Hrd is built from three modes:
    lrd_herd : Landsréttur  → Héraðsdómur   (embedded héraðs header)
    hrd_herd : Hæstiréttur  → Héraðsdómur   (pre-2018 + post-2018 héraðs-header)
    hrd_lrd  : Hæstiréttur  → Landsréttur   (post-2018 Landsréttur header)

Usage:
    uv run python scripts/link_appeals.py --mode lrd_herd --dry-run --limit 50
    uv run python scripts/link_appeals.py --mode lrd_herd
    uv run python scripts/link_appeals.py --mode all      # run all three in order
    uv run python scripts/link_appeals.py --mode hrd_lrd --threshold 0.5 --window 14
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

from sqlalchemy import and_, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

import engine.database.connection as _db_conn
from engine.database.connection import init_db
from engine.database.models import Document, DocumentLink, Source

log = logging.getLogger(__name__)

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
_NUM_TOKEN_RE = re.compile(r'\d[\d.,/]*\d|\d')


# ── Parsing / similarity (validated in prototype_match_lrd_herd.py) ────────────

def _parse_date(head: str) -> date | None:
    dm = _DATE_RE.search(head)
    if not dm:
        return None
    try:
        return date(int(dm.group(3)), _MONTHS[dm.group(2)], int(dm.group(1)))
    except ValueError:
        return None


def parse_herd_header(lower: str) -> tuple[str | None, date | None]:
    """Héraðsdómur abbreviation + date from an embedded héraðs header."""
    head = lower[:220]
    cm = _COURT_RE.search(head)
    court = _GEN_TO_ABBR.get(cm.group(1)) if cm else None
    return court, _parse_date(head)


def parse_lrd_header(lower: str) -> tuple[str | None, date | None]:
    """Landsréttur (single court) + date from an embedded Landsréttur header,
    e.g. 'Dómur Landsréttar 8. mars 2019.' / 'Úrskurður Landsréttar ...'."""
    head = lower[:120]
    if not re.search(r'Landsrétt', head):
        return None, None
    d = _parse_date(head)
    return ("Lrd.", d) if d else (None, None)


def extract_herd_casenum(text: str) -> set[str]:
    return {f"{m.group(1)}-{m.group(2)}/{m.group(3)}"
            for m in _CASENUM_RE.finditer(text or "")}


def num_tokens(text: str) -> Counter:
    toks = _NUM_TOKEN_RE.findall(text or "")
    return Counter(t for t in toks if len(t) >= 2)


def word_tokens(text: str) -> set[str]:
    return set(re.findall(r'\w{4,}', (text or "").lower()))


def sim_numbers(a_nums: Counter, b_nums: Counter) -> float:
    """Overlap coefficient over numeric token multisets."""
    if not a_nums or not b_nums:
        return 0.0
    inter = sum((a_nums & b_nums).values())
    smaller = min(sum(a_nums.values()), sum(b_nums.values()))
    return inter / smaller if smaller else 0.0


def sim_words(a_words: set[str], b_words: set[str]) -> float:
    """Overlap coefficient over word sets."""
    if not a_words or not b_words:
        return 0.0
    return len(a_words & b_words) / min(len(a_words), len(b_words))


# ── Modes ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Mode:
    name: str
    higher: str      # higher-court source short_name (its lower_body embeds `lower`)
    lower: str       # lower-court source short_name (link target)
    parser: Callable[[str], tuple[str | None, date | None]]
    use_casenum: bool
    label: str


MODES = {
    "lrd_herd": Mode("lrd_herd", "landsrettur", "heradsdomstolar",
                     parse_herd_header, True, "Lrd → Hérd"),
    "hrd_herd": Mode("hrd_herd", "haestirettur", "heradsdomstolar",
                     parse_herd_header, True, "Hrd → Hérd"),
    "hrd_lrd":  Mode("hrd_lrd", "haestirettur", "landsrettur",
                     parse_lrd_header, False, "Hrd → Lrd"),
}


# ── Matcher ───────────────────────────────────────────────────────────────────

async def _source_id(session, short_name: str):
    return (await session.execute(
        select(Source.id).where(Source.short_name == short_name))).scalar()


async def run_mode(
    mode: Mode, *, threshold: float, window: int, limit: int | None, dry_run: bool
) -> None:
    stats = Counter()
    conf_buckets = Counter()
    method_counts = Counter()

    async with _db_conn.AsyncSessionLocal() as session:
        higher_id = await _source_id(session, mode.higher)
        lower_id = await _source_id(session, mode.lower)
        if not higher_id or not lower_id:
            log.error("[%s] missing source: %s=%s %s=%s", mode.name,
                      mode.higher, higher_id, mode.lower, lower_id)
            return

        q = (select(Document.id, Document.body_text, Document.lower_body_text)
             .where(Document.source_id == higher_id)
             .where(Document.lower_body_text.isnot(None)))
        if limit:
            q = q.limit(limit)
        higher_rows = (await session.execute(q)).all()
        log.info("[%s] processing %d %s docs (threshold=%.2f window=±%dd)",
                 mode.name, len(higher_rows), mode.higher, threshold, window)

        # Authoritative re-run: clear only the edges THIS mode owns — those whose
        # target is one of these higher docs AND whose source is `lower`. Scoping by
        # both ends keeps hrd_herd and hrd_lrd from wiping each other's links.
        if not dry_run and not limit:
            higher_ids = [r[0] for r in higher_rows]
            lower_doc_ids = select(Document.id).where(Document.source_id == lower_id)
            deleted = (await session.execute(
                delete(DocumentLink).where(and_(
                    DocumentLink.relation == "appealed_to",
                    DocumentLink.to_doc_id.in_(higher_ids),
                    DocumentLink.from_doc_id.in_(lower_doc_ids))))).rowcount
            await session.commit()
            log.info("[%s] cleared %d existing links", mode.name, deleted)

        for higher_doc_id, body, lower in higher_rows:
            stats["total"] += 1
            court, d = mode.parser(lower or "")

            lower_nums = num_tokens(lower)
            lower_words = word_tokens(lower)
            cns = set()
            if mode.use_casenum:
                cns = extract_herd_casenum(body or "") | extract_herd_casenum(lower or "")

            # The date-window tier needs court+date; the casenum tier needs casenums.
            # When the embedded header is the generic "Dómur Héraðsdóms …" form (no
            # city), court is None — we can still recover via casenum across all courts.
            if not cns and not (court and d):
                stats["header_fail"] += 1
                continue

            # Tier A: case number (+ court if we parsed one; otherwise search every
            #   héraðs court and let text-sim disambiguate same-number collisions).
            # Tier B: court + date ± window. The embedded header's date is sometimes
            #   the *hearing* date, so we never pin to date == d — gather the window
            #   and let text-sim pick the best.
            cand_rows, method = [], None
            if cns:
                conds = [Document.source_id == lower_id, Document.case_number.in_(cns)]
                if court:
                    conds.append(Document.court == court)
                cand_rows = (await session.execute(
                    select(Document.id, Document.body_text, Document.document_date)
                    .where(and_(*conds)))).all()
                if cand_rows:
                    method = "casenum"
            if not cand_rows and court and d:
                cand_rows = (await session.execute(
                    select(Document.id, Document.body_text, Document.document_date)
                    .where(and_(Document.source_id == lower_id,
                                Document.court == court,
                                Document.document_date >= d - timedelta(days=window),
                                Document.document_date <= d + timedelta(days=window))))).all()

            if not cand_rows:
                stats["no_candidate"] += 1
                continue

            best_id, best_combined, best_date = None, -1.0, None
            for cid, cbody, cdate in cand_rows:
                sn = sim_numbers(lower_nums, num_tokens(cbody or ""))
                sw = sim_words(lower_words, word_tokens(cbody or ""))
                combined = max(sn, sw)
                if combined > best_combined:
                    best_combined, best_id, best_date = combined, cid, cdate
            if method != "casenum":
                method = "court_date" if best_date == d else "court_window"

            # casenum is its own confirmation: the extracted case number + court
            # matched a real doc. Don't reject it on text-sim — only a low floor to
            # guard against a spurious cited-precedent number. The weaker date/window
            # methods are fully gated by the chosen threshold.
            floor = 0.3 if method == "casenum" else threshold
            if best_combined < floor:
                stats["below_threshold"] += 1
                stats[f"below_{method}"] += 1
                continue

            stats["linked"] += 1
            method_counts[method] += 1
            conf_buckets[min(int(best_combined * 10), 9)] += 1

            if not dry_run:
                await session.execute(
                    pg_insert(DocumentLink)
                    .values(from_doc_id=best_id, to_doc_id=higher_doc_id,
                            relation="appealed_to", confidence=round(best_combined, 4),
                            method=method)
                    .on_conflict_do_update(
                        constraint="uq_link_from_to_rel",
                        set_={"confidence": round(best_combined, 4), "method": method}))
                if stats["linked"] % 200 == 0:
                    await session.commit()
                    log.info("[%s]   …%d linked", mode.name, stats["linked"])

        if not dry_run:
            await session.commit()

    # ── Report ────────────────────────────────────────────────────────────────
    n = stats["total"]
    print(f"\n{'DRY RUN — ' if dry_run else ''}{mode.label} linking")
    print(f"  processed:        {n}")
    print(f"  linked:           {stats['linked']}  ({100*stats['linked']//max(1,n)}%)")
    print(f"  below threshold:  {stats['below_threshold']}"
          f"  (casenum={stats['below_casenum']} date={stats['below_court_date']}"
          f" window={stats['below_court_window']})")
    print(f"  no candidate:     {stats['no_candidate']}")
    print(f"  header parse fail:{stats['header_fail']}")
    print(f"\n  by method:")
    for m, c in method_counts.most_common():
        print(f"    {m:14s} {c}")
    print(f"\n  confidence of linked:")
    linked = max(1, stats["linked"])
    for b in range(9, -1, -1):
        bar = "█" * (conf_buckets[b] * 40 // linked)
        print(f"    {b/10:.1f}-{(b+1)/10:.1f}  {conf_buckets[b]:4d}  {bar}")


async def main(mode_name: str, **kw) -> None:
    await init_db()
    names = list(MODES) if mode_name == "all" else [mode_name]
    for nm in names:
        await run_mode(MODES[nm], **kw)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=[*MODES, "all"], default="lrd_herd")
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--window", type=int, default=14)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.mode, threshold=args.threshold, window=args.window,
                     limit=args.limit, dry_run=args.dry_run))
