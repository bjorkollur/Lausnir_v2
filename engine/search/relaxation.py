"""Relaxed keyword search: query construction and the relaxation decision.

Strict = all lemmas (AND). When fewer than RELAX_BELOW documents match strictly,
search widens to documents matching all-but-one lemma (tier 1) and then any lemma
(tier 2), always ranked after the strict matches. Pure: no DB access.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

# Above this many lemmas the all-but-one tier is skipped (C(n, n-1) = n subsets,
# each an n-1 way AND — fine up to 8, pointless beyond).
MAX_NMINUS1_LEMMAS = 8

# Relax when the strict document count is below this. 0 disables relaxation.
#
# Fixed 2026-09-28 by user decision, from `scripts/eval_search.py --relax-sweep
# --k 10 --set all` on the 492-question golden set with RELAX_CAND_LIMIT = 300:
#
#   K   recall@10   MRR   hit@1  0-hit  p50 ms  p95 ms
#    0      0.327  0.210  0.161     78      23     216
#    5      0.396  0.243  0.181      0      57     713
#   10      0.402  0.244  0.181      0      91     707
#   20      0.402  0.244  0.181      0     146     702
#   50      0.402  0.244  0.181      0     529     803
#
# Quality saturates at K=10 (identical to K=20 on every metric); K=50 only
# costs more p50 latency for no further gain. RELAX_CAND_LIMIT = 1000 gave the
# same quality at every K, just slower — 300 stays the default (see below).
RELAX_BELOW = 10

# Candidate cap for relaxed search only (unrelaxed keeps
# passage_search.PASSAGE_CANDIDATE_DOCS = 2000). Relaxed queries aggregate passages
# per candidate through a LATERAL index walk, so the cost is linear in this number;
# 2000 candidates x an any-lemma tsquery was the remaining latency problem
# (p95 8.7 s on the golden set). Read as a module attribute at call time so
# `scripts/eval_search.py --relax-cand-limit N` can flip it for a measurement.
#
# 300 vs. 1000 measured 2026-09-28 (same --relax-sweep run as RELAX_BELOW
# above): recall@10/MRR/hit@1/zero-hit are identical at every K for both caps
# — the extra 700 candidates never reach the top 10 — while 1000 is
# consistently slower (e.g. K=10 p95 707 ms at 300 vs. 881 ms at 1000; K=50
# p50 529 ms at 300 vs. 625 ms at 1000). 300 is the better default.
RELAX_CAND_LIMIT = 300


@dataclass(frozen=True)
class KeywordQueries:
    strict: str          # to_tsquery source: 'a & b & c'
    nminus1: str | None  # '(a & b) | (a & c) | (b & c)'; None when n < 2 or n > MAX_NMINUS1_LEMMAS
    any: str             # 'a | b | c'
    n: int


def build_keyword_queries(lemmas: str) -> KeywordQueries:
    toks = lemmas.split()
    if not toks:
        raise ValueError("no lemmas")
    n = len(toks)
    strict = " & ".join(toks)
    any_q = " | ".join(toks)
    if 2 <= n <= MAX_NMINUS1_LEMMAS:
        if n == 2:
            nminus1: str | None = any_q
        else:
            nminus1 = " | ".join("(" + " & ".join(c) + ")" for c in combinations(toks, n - 1))
    else:
        nminus1 = None
    return KeywordQueries(strict=strict, nminus1=nminus1, any=any_q, n=n)


def should_relax(strict_total: int, n: int, threshold: int | None = None) -> bool:
    limit = RELAX_BELOW if threshold is None else threshold
    return n >= 2 and limit > 0 and strict_total < limit
