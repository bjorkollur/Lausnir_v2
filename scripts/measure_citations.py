"""Measure citation coverage on a random sample (spec §9, "Þekja").

Read-only: connects with DATABASE_URL_READONLY, runs the real extractor and
resolver in-process via `build_rows`, and writes nothing. Nothing here touches
`citations` or `document_links` — the numbers come from the rows that a build
*would* produce, so the script works before (and independently of) any build.

Gate: resolved >= 80 % of (resolved + ambiguous + unresolved), counted over the
`summary` and `body` layers only (`lower_body` never forms an edge, §4.3).

Usage:
    set -a; . ./.env; set +a
    uv run python scripts/measure_citations.py --n 800
"""
from __future__ import annotations

import argparse
import asyncio
import os
import random
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text

from engine.database.connection import get_engine, init_db
from engine.processors.citation_build import build_rows
from engine.processors.citation_resolver import COURT_SOURCES, INDEX_SQL, CitationIndex

EDGE_LAYERS = ("summary", "body")     # the layers the coverage gate is about
GATE = 0.80
CONTEXT = 60                          # chars of context either side of an example
TIERS = (1, 2, 3)

_DIGITS = re.compile(r"\d")
_WS = re.compile(r"\s+")

_SAMPLE_SQL = text("""
    SELECT d.id, d.summary, d.body_text, d.lower_body_text, d.document_date, d.court, s.short_name
    FROM documents d JOIN sources s ON s.id = d.source_id
    WHERE s.short_name = ANY(:sources) AND d.instance_tier = :tier
    ORDER BY random()
    LIMIT :n
""")


def shape(raw: str) -> str:
    """'nr. 700/2017' -> 'nr. ###/####'. Digits collapse to #, whitespace to one space."""
    return _WS.sub(" ", _DIGITS.sub("#", raw)).strip()


def _fmt_counter(c: Counter) -> str:
    return ", ".join(f"{k}={v:,}" for k, v in c.most_common()) or "-"


def _context(layer_text: str | None, start: int, end: int) -> str:
    if not layer_text:
        return ""
    lo, hi = max(0, start - CONTEXT), min(len(layer_text), end + CONTEXT)
    left = _WS.sub(" ", layer_text[lo:start])
    mid = _WS.sub(" ", layer_text[start:end])
    right = _WS.sub(" ", layer_text[end:hi])
    return f"{'…' if lo else ''}{left}[[{mid}]]{right}{'…' if hi < len(layer_text) else ''}"


async def measure(*, n: int, seed: int | None) -> int:
    url = os.environ.get("DATABASE_URL_READONLY")
    if not url:
        raise SystemExit("DATABASE_URL_READONLY is not set (set -a; . ./.env; set +a)")
    rng = random.Random(seed)
    # create_tables=False: the read-only role may not run DDL.
    await init_db(url, create_tables=False)
    engine = await get_engine()

    t0 = time.monotonic()
    status: Counter = Counter()                      # every layer
    status_edge: Counter = Counter()                 # summary + body only
    by_layer: Counter = Counter()
    by_court: Counter = Counter()                    # resolved, by target_court
    by_tier: dict[int, Counter] = defaultdict(Counter)
    unresolved_shapes: Counter = Counter()
    ambiguous_shapes: Counter = Counter()
    targets_per_doc: list[int] = []
    docs_with_any = 0
    docs = 0
    examples: dict[str, list[str]] = defaultdict(list)
    seen_per_status: Counter = Counter()             # reservoir counters

    async with engine.connect() as conn:
        index_rows = (await conn.execute(text(INDEX_SQL), {"sources": list(COURT_SOURCES)})).all()
        index = CitationIndex(index_rows)
        print(f"Index: {len(index_rows):,} ruling(s) from {len(COURT_SOURCES)} court source(s)")
        for tier in TIERS:
            rows = (await conn.execute(_SAMPLE_SQL,
                                       {"sources": list(COURT_SOURCES), "tier": tier, "n": n})).all()
            print(f"tier {tier}: sampled {len(rows):,} document(s)")
            for r in rows:
                docs += 1
                layers = {"summary": r.summary, "body": r.body_text, "lower_body": r.lower_body_text}
                cits = build_rows(r.id, summary=r.summary, body=r.body_text, lower=r.lower_body_text,
                                  doc_date=r.document_date, index=index)
                resolved_targets = set()
                for c in cits:
                    st = c["status"]
                    status[st] += 1
                    by_layer[c["layer"]] += 1
                    by_tier[tier][st] += 1
                    if c["layer"] in EDGE_LAYERS:
                        status_edge[st] += 1
                        if st == "resolved":
                            resolved_targets.add(c["to_doc_id"])
                    if st == "resolved":
                        by_court[c["target_court"] or "?"] += 1
                    elif st == "unresolved":
                        unresolved_shapes[shape(c["raw_text"])] += 1
                    elif st == "ambiguous":
                        ambiguous_shapes[shape(c["raw_text"])] += 1
                    # Reservoir sampling: 5 uniformly random examples per status
                    # without holding every row in memory.
                    seen_per_status[st] += 1
                    k = seen_per_status[st]
                    ex = (f"[{r.short_name} tier{tier} {r.court} {r.document_date}] "
                          f"{c['layer']} {c['target_court']} {c['target_case_number']} "
                          f"date={c['target_date']} verdict={c['target_verdict']}\n"
                          f"      {_context(layers[c['layer']], c['char_start'], c['char_end'])}")
                    if len(examples[st]) < 5:
                        examples[st].append(ex)
                    else:
                        j = rng.randrange(k)
                        if j < 5:
                            examples[st][j] = ex
                if resolved_targets:
                    docs_with_any += 1
                targets_per_doc.append(len(resolved_targets))
        await conn.rollback()

    total = sum(status.values())
    denom = status_edge["resolved"] + status_edge["ambiguous"] + status_edge["unresolved"]
    coverage = status_edge["resolved"] / denom if denom else 0.0

    print()
    print("=" * 78)
    print(f"SAMPLE: {docs:,} documents ({n} per tier x {len(TIERS)} tiers), "
          f"{total:,} citations, {time.monotonic() - t0:.0f}s")
    print("=" * 78)
    print()
    print("counts by status (all layers):")
    for k, v in status.most_common():
        print(f"  {k:<14} {v:>8,}  {100 * v / total if total else 0:5.1f}%")
    print(f"  {'TOTAL':<14} {total:>8,}")
    print()
    print("counts by status (layer in summary, body):")
    tot_edge = sum(status_edge.values())
    for k, v in status_edge.most_common():
        print(f"  {k:<14} {v:>8,}  {100 * v / tot_edge if tot_edge else 0:5.1f}%")
    print(f"  {'TOTAL':<14} {tot_edge:>8,}")
    print()
    print(f"COVERAGE = resolved / (resolved+ambiguous+unresolved), summary+body")
    print(f"         = {status_edge['resolved']:,} / {denom:,} = {100 * coverage:.2f}%   "
          f"gate {100 * GATE:.0f}%  -> {'PASS' if coverage >= GATE else 'FAIL'}")
    print()
    print(f"counts by layer: {_fmt_counter(by_layer)}")
    print()
    print("counts by status and tier:")
    keys = [k for k, _ in status.most_common()]
    print(f"  {'tier':<6}" + "".join(f"{k:>14}" for k in keys))
    for tier in TIERS:
        print(f"  {tier:<6}" + "".join(f"{by_tier[tier][k]:>14,}" for k in keys))
    print()
    print("resolved citations by target_court:")
    for k, v in by_court.most_common():
        print(f"  {k:<16} {v:>8,}")
    print()
    nz = [t for t in targets_per_doc if t]
    print("distinct resolved targets per document (summary+body):")
    print(f"  documents sampled              {docs:,}")
    print(f"  documents with >= 1 resolved   {docs_with_any:,}  ({100 * docs_with_any / docs if docs else 0:.1f}%)")
    print(f"  mean over all documents        {sum(targets_per_doc) / docs if docs else 0:.2f}")
    print(f"  mean over documents with any   {sum(nz) / len(nz) if nz else 0:.2f}")
    print(f"  max                            {max(targets_per_doc) if targets_per_doc else 0}")
    hist = Counter(min(t, 10) for t in targets_per_doc)
    print("  histogram (0..9, 10+):  " + "  ".join(f"{k}:{hist[k]:,}" for k in sorted(hist)))
    print()
    for title, shapes in (("UNRESOLVED", unresolved_shapes), ("AMBIGUOUS", ambiguous_shapes)):
        tot = sum(shapes.values())
        print(f"ten most common raw_text shapes among {title} ({tot:,} rows, "
              f"{len(shapes):,} distinct shapes):")
        for s, v in shapes.most_common(10):
            print(f"  {v:>6,}  {100 * v / tot if tot else 0:5.1f}%  {s[:110]}")
        print()
    for st in [k for k, _ in status.most_common()]:
        print(f"five random {st} examples (+-{CONTEXT} chars of context):")
        for e in examples[st]:
            print(f"  - {e}")
        print()
    return 0 if coverage >= GATE else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=800, help="documents sampled per instance_tier (default 800)")
    ap.add_argument("--seed", type=int, default=None, help="seed for the example reservoir")
    a = ap.parse_args()
    sys.exit(asyncio.run(measure(n=a.n, seed=a.seed)))
