"""Measure keyword search against the golden set, for one or both implementations.

    set -a; . ./.env; set +a
    uv run python scripts/eval_search.py                # both impls, k=10
    uv run python scripts/eval_search.py --impl passages --k 20
    uv run python scripts/eval_search.py --rank-sweep --k 10

Prints recall@k, MRR, hit@1, latency p50/p95, zero-result count, and the
queries where the two implementations disagree.

--rank-sweep evaluates the documents baseline once, then the passages impl for
every (PASSAGE_RANK_FN, PASSAGE_RANK_STRATEGY) combination in
engine.search.passage_search, printing one table sorted by recall@k then MRR.
It restores the module's rank defaults afterwards and never touches them when
the flag isn't given.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml

GOLDEN = Path(__file__).parent.parent / "tests" / "golden" / "queries.yaml"


def _key(d: dict) -> tuple:
    return (d["source"], d["case_number"], str(d["document_date"]))


def score(expected: list[dict], results: list[dict], k: int) -> tuple[bool, float]:
    """(hit@k, reciprocal rank of the first expected doc in the top k)."""
    want = {_key(e) for e in expected}
    for i, r in enumerate(results[:k]):
        if _key(r) in want:
            return True, 1.0 / (i + 1)
    return False, 0.0


async def evaluate(impl: str, golden: list[dict], k: int, pre_run: Callable[[], None] | None = None) -> dict:
    os.environ["LAUSNIR_SEARCH_IMPL"] = impl
    import engine.search.queries as q
    importlib.reload(q)
    # engine.search.passage_search is imported by queries.py at module scope, so
    # reloading queries above just re-fetches the same (cached, unreloaded)
    # passage_search module object from sys.modules. pre_run runs after the
    # reload and imports passage_search itself to set PASSAGE_RANK_FN /
    # PASSAGE_RANK_STRATEGY on that same module object, which is what
    # search_by_passages actually reads at call time.
    if pre_run is not None:
        pre_run()
    import engine.database.connection as c
    await c.init_db()
    per, lat = [], []
    async with c.AsyncSessionLocal() as s:
        for g in golden:
            t0 = time.perf_counter()
            res = await q.search_documents(s, q=g["question"], mode="keyword",
                                           scope=g.get("scope"), page_size=k)
            lat.append((time.perf_counter() - t0) * 1000)
            hit, rr = score(g["expected"], res.results, k)
            per.append({"id": g["id"], "hit": hit, "rr": rr, "total": res.total})
    n = len(per)
    return {
        "impl": impl, "n": n,
        "recall": sum(p["hit"] for p in per) / n,
        "mrr": sum(p["rr"] for p in per) / n,
        "hit1": sum(p["rr"] == 1.0 for p in per) / n,
        "p50_ms": statistics.median(lat), "p95_ms": sorted(lat)[int(0.95 * (n - 1))],
        "zero": sum(p["total"] == 0 for p in per),
        "per_query": {p["id"]: p for p in per},
    }


async def coverage() -> tuple[int, int]:
    from sqlalchemy import text
    import engine.database.connection as c
    async with c.AsyncSessionLocal() as s:
        row = (await s.execute(text("""
            SELECT count(*) FILTER (WHERE body_text IS NOT NULL OR summary IS NOT NULL),
                   count(*) FILTER (WHERE EXISTS (SELECT 1 FROM passages p WHERE p.document_id = d.id))
            FROM documents d"""))).first()
    return int(row[0]), int(row[1])


async def rank_sweep(golden: list[dict], k: int) -> None:
    import engine.search.passage_search as ps

    rows = [{"label": "documents / - / -", **(await evaluate("documents", golden, k))}]

    orig_fn, orig_strategy = ps.PASSAGE_RANK_FN, ps.PASSAGE_RANK_STRATEGY
    try:
        for rank_fn in ("ts_rank", "ts_rank_cd"):
            for strategy in ps.RANK_STRATEGIES:
                def _set(rank_fn=rank_fn, strategy=strategy) -> None:
                    import engine.search.passage_search as ps2
                    ps2.PASSAGE_RANK_FN = rank_fn
                    ps2.PASSAGE_RANK_STRATEGY = strategy
                r = await evaluate("passages", golden, k, pre_run=_set)
                rows.append({"label": f"passages / {rank_fn} / {strategy}", **r})
    finally:
        ps.PASSAGE_RANK_FN, ps.PASSAGE_RANK_STRATEGY = orig_fn, orig_strategy

    rows.sort(key=lambda r: (-r["recall"], -r["mrr"]))
    print(f"{'impl/rank_fn/strategy':30s} {'recall@'+str(k):>10s} {'MRR':>6s} {'hit@1':>6s} {'p50 ms':>7s} {'0-hit':>5s}")
    for r in rows:
        print(f"{r['label']:30s} {r['recall']:10.3f} {r['mrr']:6.3f} {r['hit1']:6.3f} "
              f"{r['p50_ms']:7.0f} {r['zero']:5d}")


async def main(impls: list[str], k: int) -> None:
    golden = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))
    reports = [await evaluate(i, golden, k) for i in impls]
    print(f"{'impl':10s} {'n':>3s} {'recall@'+str(k):>10s} {'MRR':>6s} {'hit@1':>6s} {'p50 ms':>7s} {'p95 ms':>7s} {'0-hit':>5s}")
    for r in reports:
        print(f"{r['impl']:10s} {r['n']:3d} {r['recall']:10.3f} {r['mrr']:6.3f} {r['hit1']:6.3f} "
              f"{r['p50_ms']:7.0f} {r['p95_ms']:7.0f} {r['zero']:5d}")
    if len(reports) == 2:
        a, b = reports
        diff = [(qid, a["per_query"][qid]["hit"], b["per_query"][qid]["hit"])
                for qid in a["per_query"] if a["per_query"][qid]["hit"] != b["per_query"][qid]["hit"]]
        print(f"\nDisagreements ({len(diff)}): id  {a['impl']}  {b['impl']}")
        for qid, ha, hb in diff:
            print(f"  {qid}  {'hit ' if ha else 'miss'}  {'hit ' if hb else 'miss'}")
    wt, wp = await coverage()
    print(f"\nCoverage: {wp:,}/{wt:,} documents with text have passages ({100*wp/wt:.1f}%)")


async def _run_sweep_entrypoint(k: int) -> None:
    golden = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))
    await rank_sweep(golden, k)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--impl", choices=["documents", "passages", "both"], default="both")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--rank-sweep", action="store_true",
                     help="Sweep PASSAGE_RANK_FN x PASSAGE_RANK_STRATEGY against the "
                          "documents baseline; implies --impl passages and ignores --impl.")
    a = ap.parse_args()
    if a.rank_sweep:
        asyncio.run(_run_sweep_entrypoint(a.k))
    else:
        asyncio.run(main(["documents", "passages"] if a.impl == "both" else [a.impl], a.k))
