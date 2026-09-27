"""Merge drafted golden-set slices into tests/golden/queries.yaml (Task 10b).

    set -a; . ./.env; set +a
    uv run python scripts/golden_merge.py \\
        --core tests/golden/queries.yaml \\
        --slices "/tmp/lausnir-dev/golden500/slice_*.done.yaml" \\
        --candidates /tmp/lausnir-dev/golden500/candidates.yaml \\
        --out tests/golden/queries.yaml --target 450

Reads the current core queries.yaml (tagged `set: core, style: medium`) and
every drafted `slice_NN.done.yaml` matching --slices (a shell glob), validates
each drafted entry against --candidates, balances the style ratio
(short 0.40 / medium 0.45 / term 0.15) up to --target auto entries, and
writes core + auto (without the `note` scratch field) to --out with a header
describing the method and date.

Robust to whatever a drafter agent produced:
  - `question` is stripped; empty questions are dropped
  - duplicate questions (case/whitespace-insensitive) are dropped, keeping
    the first occurrence
  - entries whose `id` isn't a known candidate are dropped
  - entries whose question copies a run of more than --max-run content-word
    lemmas from the candidate's summary (scripts/golden_audit.py) are dropped

Prints a summary of what was dropped and why, and how many entries made it
into each style bucket.
"""
from __future__ import annotations

import argparse
import glob
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml

from scripts.golden_audit import extract_summary, longest_shared_run

STYLE_RATIOS = [("short", 0.40), ("medium", 0.45), ("term", 0.15)]

MERGE_HEADER = """\
# Gullsett — {n_core} handvalin kjarnadæmi (set: core) + {n_auto} sjálfvirkt
# samsett dæmi (set: auto), sameinuð {date} með scripts/golden_merge.py.
#
# Kjarninn (core) er upprunalegu 50 spurningarnar úr Task 10, allar
# style: medium. Auto-mengið er dregið úr scripts/golden_candidates.py
# frambjóðendapottinum (Hæstiréttur/Landsréttur, reifun 200-2000 stafir),
# spurningarnar samdar af Claude Code undiragentum í þremur stílum
# (short/medium/term), og hver spurning yfirfarin með
# scripts/golden_audit.py (afritunarpróf: engin þriggja+ orða runa
# sameiginleg með reifuninni). Frambjóðendur sem lentu í árekstri við
# afritunarprófið, voru auðar, tvíteknar eða vísuðu í óþekkt `id` voru
# felldar úr -- sjá keyrsluúttak scripts/golden_merge.py fyrir ástæður.
#
# `note` (reifunartextinn) er fjarlægt hér -- annars yrði afritunarprófið
# marklaust á þessa skrá sjálfa.
"""

FINAL_FIELDS = ["id", "question", "style", "set", "expected", "scope"]


def _norm_question(q: str) -> str:
    return " ".join((q or "").strip().lower().split())


def validate_and_score(drafted: list[dict], candidates: dict[str, dict],
                        max_run: int = 2) -> tuple[list[dict], list[dict]]:
    """Split drafted entries into (accepted, dropped).

    Each dropped entry is the original dict plus a `reason` string. Accepted
    entries carry `id`, `question` (stripped), `style`, `expected`, `scope`
    (the candidate's, not the drafted copy, since only `question` should
    have been filled in).
    """
    accepted: list[dict] = []
    dropped: list[dict] = []
    seen_questions: set[str] = set()

    for e in drafted:
        eid = e.get("id")
        question = (e.get("question") or "").strip()

        if not question:
            dropped.append({**e, "reason": "empty question"})
            continue

        cand = candidates.get(eid)
        if cand is None:
            dropped.append({**e, "reason": f"unknown id {eid!r} (not in candidates)"})
            continue

        norm = _norm_question(question)
        if norm in seen_questions:
            dropped.append({**e, "reason": "duplicate question"})
            continue

        summary = extract_summary(cand)
        run = longest_shared_run(question, summary)
        if run > max_run:
            dropped.append({**e, "reason": f"copies summary (shared run={run})"})
            continue

        seen_questions.add(norm)
        accepted.append({
            "id": eid,
            "question": question,
            "style": cand.get("style", e.get("style", "medium")),
            "expected": cand["expected"],
            "scope": cand.get("scope", ["domstolar"]),
        })

    return accepted, dropped


def allocate_style_quota(counts: dict[str, int], target: int,
                          ratios: list[tuple[str, float]] = STYLE_RATIOS) -> dict[str, int]:
    """How many entries of each style to take, given how many are available.

    Starts from the target ratio, caps each style at what's actually
    available, and hands any shortfall to the styles that still have room
    (largest ratio first), so the total stays as close to `target` as the
    available pool allows.
    """
    # Largest-remainder (Hamilton) apportionment: floor each exact share,
    # then hand the leftover seats to the largest fractional remainders
    # (ties broken by ratio order) -- avoids round()'s banker's-rounding bias.
    exact = {name: target * ratio for name, ratio in ratios}
    quotas = {name: int(v) for name, v in exact.items()}
    remainder = target - sum(quotas.values())
    by_frac = sorted(ratios, key=lambda kv: -(exact[kv[0]] - quotas[kv[0]]))
    for name, _ in by_frac[:remainder]:
        quotas[name] += 1

    shortfall = 0
    for name in quotas:
        avail = counts.get(name, 0)
        if quotas[name] > avail:
            shortfall += quotas[name] - avail
            quotas[name] = avail

    order = [name for name, _ in sorted(ratios, key=lambda kv: -kv[1])]
    while shortfall > 0:
        gave = False
        for name in order:
            if shortfall <= 0:
                break
            room = counts.get(name, 0) - quotas[name]
            if room > 0:
                quotas[name] += 1
                shortfall -= 1
                gave = True
        if not gave:
            break  # no style has any more room; can't reach target
    return quotas


def balance_styles(accepted: list[dict], target: int,
                    ratios: list[tuple[str, float]] = STYLE_RATIOS) -> list[dict]:
    """Pick up to `target` accepted entries matching the style ratio as closely
    as the available per-style counts allow. Preserves each style group's
    original order (first-come)."""
    by_style: dict[str, list[dict]] = {}
    for e in accepted:
        by_style.setdefault(e.get("style", "medium"), []).append(e)
    counts = {s: len(v) for s, v in by_style.items()}
    quotas = allocate_style_quota(counts, min(target, sum(counts.values())), ratios)

    out: list[dict] = []
    for style, _ in ratios:
        out.extend(by_style.get(style, [])[:quotas.get(style, 0)])
    return out


def merge(core: list[dict], drafted: list[dict], candidates: dict[str, dict],
          target: int, max_run: int = 2) -> tuple[list[dict], list[dict]]:
    """Return (merged_entries, dropped_entries)."""
    core_tagged = [{**{k: e[k] for k in ("id", "question", "expected", "scope") if k in e},
                     "style": e.get("style", "medium"), "set": "core"} for e in core]
    accepted, dropped = validate_and_score(drafted, candidates, max_run)
    balanced = balance_styles(accepted, target)
    auto_tagged = [{**e, "set": "auto"} for e in balanced]
    merged = core_tagged + auto_tagged
    return [{k: e[k] for k in FINAL_FIELDS if k in e} for e in merged], dropped


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--core", type=Path, required=True)
    ap.add_argument("--slices", required=True, help="shell glob for drafted slice_*.done.yaml files")
    ap.add_argument("--candidates", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--target", type=int, default=450)
    ap.add_argument("--max-run", type=int, default=2)
    args = ap.parse_args(argv)

    core = yaml.safe_load(args.core.read_text(encoding="utf-8")) or []
    candidates = {c["id"]: c for c in yaml.safe_load(args.candidates.read_text(encoding="utf-8")) or []}

    slice_paths = sorted(Path(p) for p in glob.glob(args.slices))
    drafted: list[dict] = []
    for p in slice_paths:
        drafted.extend(yaml.safe_load(p.read_text(encoding="utf-8")) or [])

    merged, dropped = merge(core, drafted, candidates, args.target, args.max_run)
    n_auto = len(merged) - len(core)

    header = MERGE_HEADER.format(n_core=len(core), n_auto=n_auto, date=date.today().isoformat())
    args.out.write_text(header + "\n" + yaml.safe_dump(merged, allow_unicode=True, sort_keys=False, width=120),
                         encoding="utf-8")

    print(f"Read {len(drafted)} drafted entries from {len(slice_paths)} slice file(s).")
    print(f"Merged {len(merged)} entries ({len(core)} core + {n_auto} auto) into {args.out}")
    reasons: dict[str, int] = {}
    for d in dropped:
        reasons[d["reason"].split(" (")[0].split(": ")[0]] = reasons.get(d["reason"].split(" (")[0].split(": ")[0], 0) + 1
    print(f"Dropped {len(dropped)} drafted entries:")
    for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
        print(f"  {reason}: {count}")
    by_style = {}
    for e in merged:
        by_style[e.get("style", "medium")] = by_style.get(e.get("style", "medium"), 0) + 1
    print(f"Style shares (final): {by_style}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
