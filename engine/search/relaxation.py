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
# Provisional; the final value is chosen with `scripts/eval_search.py --relax-sweep`.
RELAX_BELOW = 10

# Candidate cap for relaxed search only (unrelaxed keeps
# passage_search.PASSAGE_CANDIDATE_DOCS = 2000). Relaxed queries aggregate passages
# per candidate through a LATERAL index walk, so the cost is linear in this number;
# 2000 candidates x an any-lemma tsquery was the remaining latency problem
# (p95 8.7 s on the golden set). Read as a module attribute at call time so
# `scripts/eval_search.py --relax-cand-limit N` can flip it for a measurement.
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
