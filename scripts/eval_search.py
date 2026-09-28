"""Measure keyword search against the golden set.

    set -a; . ./.env; set +a
    uv run python scripts/eval_search.py                # k=10, full golden set
    uv run python scripts/eval_search.py --k 20
    uv run python scripts/eval_search.py --rank-sweep --k 10

Prints recall@k, MRR, hit@1, latency p50/p95, zero-result count, and a
per-style breakdown.

--rank-sweep evaluates the passages impl for every (PASSAGE_RANK_FN,
PASSAGE_RANK_STRATEGY) combination in engine.search.passage_search, printing
one table sorted by recall@k then MRR. It restores the module's rank defaults
afterwards and never touches them when the flag isn't given.

--relax-sweep evaluates the golden set for every RELAX_BELOW in (0, 5, 10, 20,
50), setting engine.search.relaxation.RELAX_BELOW for each run (restored
afterwards), and prints one table with recall@k, MRR, hit@1, zero-hit count,
the number of queries that relaxed, and p50/p95 latency.

--relax-cand-limit N overrides engine.search.relaxation.RELAX_CAND_LIMIT (the
candidate cap used by relaxed search only) for the duration of the run,
restoring it afterwards. Works with --relax-sweep, --rank-sweep and plain runs.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import statistics
import sys
import time
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml

GOLDEN = Path(__file__).parent.parent / "tests" / "golden" / "queries.yaml"

# RELAX_BELOW values swept by --relax-sweep (0 = relaxation disabled).
RELAX_SWEEP_VALUES = (0, 5, 10, 20, 50)


@contextlib.contextmanager
def relax_cand_limit(n: int | None):
    """Set engine.search.relaxation.RELAX_CAND_LIMIT to `n` for the duration.

    search_by_passages reads the attribute at call time, so this is enough to
    change the relaxed candidate cap for a whole evaluation run. `n=None` is a
    no-op (the flag wasn't given). Always restored, including on exceptions.
    """
    import engine.search.relaxation as rx
    if n is None:
        yield
        return
    orig = rx.RELAX_CAND_LIMIT
    rx.RELAX_CAND_LIMIT = n
    try:
        yield
    finally:
        rx.RELAX_CAND_LIMIT = orig


def _key(d: dict) -> tuple:
    return (d["source"], d["case_number"], str(d["document_date"]))


def score(expected: list[dict], results: list[dict], k: int) -> tuple[bool, float]:
    """(hit@k, reciprocal rank of the first expected doc in the top k)."""
    want = {_key(e) for e in expected}
    for i, r in enumerate(results[:k]):
        if _key(r) in want:
            return True, 1.0 / (i + 1)
    return False, 0.0


def filter_set(golden: list[dict], name: str) -> list[dict]:
    """Filter golden entries by their `set` field (default "core" when absent).

    name="all" returns everything unfiltered.
    """
    if name == "all":
        return golden
    return [g for g in golden if g.get("set", "core") == name]


def by_style(per_query: dict, golden: list[dict]) -> dict[str, dict]:
    """Group an evaluate() result's per_query dict by each entry's `style`
    field (default "medium" when absent), returning {style: {n, recall, mrr}}.
    """
    style_of = {g["id"]: g.get("style", "medium") for g in golden}
    groups: dict[str, list[dict]] = {}
    for qid, p in per_query.items():
        groups.setdefault(style_of.get(qid, "medium"), []).append(p)
    out = {}
    for style, ps in groups.items():
        n = len(ps)
        out[style] = {
            "n": n,
            "recall": sum(p["hit"] for p in ps) / n,
            "mrr": sum(p["rr"] for p in ps) / n,
        }
    return out


async def evaluate(golden: list[dict], k: int, pre_run: Callable[[], None] | None = None) -> dict:
    import engine.search.queries as q
    # pre_run (rank-sweep only) sets PASSAGE_RANK_FN / PASSAGE_RANK_STRATEGY on
    # engine.search.passage_search, which search_by_passages reads at call time.
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
            per.append({"id": g["id"], "hit": hit, "rr": rr, "total": res.total,
                        "relaxed": res.relaxed})
    n = len(per)
    return {
        "n": n,
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

    rows = []
    orig_fn, orig_strategy = ps.PASSAGE_RANK_FN, ps.PASSAGE_RANK_STRATEGY
    try:
        for rank_fn in ("ts_rank", "ts_rank_cd"):
            for strategy in ps.RANK_STRATEGIES:
                def _set(rank_fn=rank_fn, strategy=strategy) -> None:
                    import engine.search.passage_search as ps2
                    ps2.PASSAGE_RANK_FN = rank_fn
                    ps2.PASSAGE_RANK_STRATEGY = strategy
                r = await evaluate(golden, k, pre_run=_set)
                rows.append({"label": f"{rank_fn} / {strategy}", **r})
    finally:
        ps.PASSAGE_RANK_FN, ps.PASSAGE_RANK_STRATEGY = orig_fn, orig_strategy

    rows.sort(key=lambda r: (-r["recall"], -r["mrr"]))
    print(f"{'rank_fn / strategy':30s} {'recall@'+str(k):>10s} {'MRR':>6s} {'hit@1':>6s} {'p50 ms':>7s} {'0-hit':>5s}")
    for r in rows:
        print(f"{r['label']:30s} {r['recall']:10.3f} {r['mrr']:6.3f} {r['hit1']:6.3f} "
              f"{r['p50_ms']:7.0f} {r['zero']:5d}")


async def relax_sweep(golden: list[dict], k: int) -> None:
    import engine.search.relaxation as rx

    rows = []
    orig = rx.RELAX_BELOW
    try:
        for threshold in RELAX_SWEEP_VALUES:
            def _set(threshold=threshold) -> None:
                import engine.search.relaxation as rx2
                rx2.RELAX_BELOW = threshold
            r = await evaluate(golden, k, pre_run=_set)
            relaxed_n = sum(p["relaxed"] for p in r["per_query"].values())
            rows.append({"threshold": threshold, "relaxed_n": relaxed_n, **r})
    finally:
        rx.RELAX_BELOW = orig

    print(f"RELAX_CAND_LIMIT = {rx.RELAX_CAND_LIMIT}")
    print(f"{'K':>4s} {'recall@'+str(k):>10s} {'MRR':>6s} {'hit@1':>6s} {'0-hit':>5s} "
          f"{'relaxed':>7s} {'p50 ms':>7s} {'p95 ms':>7s}")
    for r in rows:
        print(f"{r['threshold']:4d} {r['recall']:10.3f} {r['mrr']:6.3f} {r['hit1']:6.3f} "
              f"{r['zero']:5d} {r['relaxed_n']:7d} {r['p50_ms']:7.0f} {r['p95_ms']:7.0f}")


async def main(k: int, set_name: str = "all") -> None:
    all_golden = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))
    golden = filter_set(all_golden, set_name)
    r = await evaluate(golden, k)
    print(f"{'n':>3s} {'recall@'+str(k):>10s} {'MRR':>6s} {'hit@1':>6s} {'p50 ms':>7s} {'p95 ms':>7s} {'0-hit':>5s}")
    print(f"{r['n']:3d} {r['recall']:10.3f} {r['mrr']:6.3f} {r['hit1']:6.3f} "
          f"{r['p50_ms']:7.0f} {r['p95_ms']:7.0f} {r['zero']:5d}")
    print()
    for style, s in sorted(by_style(r["per_query"], golden).items()):
        print(f"{style:16s} {s['n']:3d} {s['recall']:10.3f} {s['mrr']:6.3f}")
    wt, wp = await coverage()
    print(f"\nCoverage: {wp:,}/{wt:,} documents with text have passages ({100*wp/wt:.1f}%)")


async def _run_sweep_entrypoint(k: int, set_name: str = "all") -> None:
    golden = filter_set(yaml.safe_load(GOLDEN.read_text(encoding="utf-8")), set_name)
    await rank_sweep(golden, k)


async def _run_relax_sweep_entrypoint(k: int, set_name: str = "all") -> None:
    golden = filter_set(yaml.safe_load(GOLDEN.read_text(encoding="utf-8")), set_name)
    await relax_sweep(golden, k)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--set", choices=["core", "auto", "all"], default="all",
                     help="Restrict the golden set to entries with this `set` field "
                          "(default all). Applies to --rank-sweep and --relax-sweep too.")
    ap.add_argument("--rank-sweep", action="store_true",
                     help="Sweep PASSAGE_RANK_FN x PASSAGE_RANK_STRATEGY and print one "
                          "table sorted by recall@k then MRR.")
    ap.add_argument("--relax-sweep", action="store_true",
                     help="Sweep engine.search.relaxation.RELAX_BELOW over "
                          f"{RELAX_SWEEP_VALUES} and print one table.")
    ap.add_argument("--relax-cand-limit", type=int, default=None, metavar="N",
                     help="Override engine.search.relaxation.RELAX_CAND_LIMIT (the "
                          "candidate cap for relaxed search) for this run only.")
    return ap


if __name__ == "__main__":
    a = build_parser().parse_args()
    with relax_cand_limit(a.relax_cand_limit):
        if a.rank_sweep:
            asyncio.run(_run_sweep_entrypoint(a.k, a.set))
        elif a.relax_sweep:
            asyncio.run(_run_relax_sweep_entrypoint(a.k, a.set))
        else:
            asyncio.run(main(a.k, a.set))
