"""Dump a diverse candidate pool for the 500-question golden set (Task 10b).

    set -a; . ./.env; set +a
    uv run python scripts/golden_candidates.py --n 600 --min-year 2000 --year-cap 30 \\
        --exclude tests/golden/queries.yaml --slices 9 \\
        --out-dir /tmp/lausnir-dev/golden500

Pool: Hæstiréttur + Landsréttur documents with `length(summary) BETWEEN 200
AND 2000` and `document_date >= --min-year`. Documents already used as the
*expected* answer of an entry in --exclude (matched on source+case_number)
are dropped from the pool before selection.

Diversity caps (applied in Python, in this order, after the pool is fetched
and shuffled with a fixed seed so runs are reproducible):

  1. at most 2 documents per first keyword (`keywords[0]`, lowercased;
     documents with no keywords share the key "-", capped at 20) -- this is
     the rule that matters most: topic diversity for the user
  2. at most --year-cap documents per (source, year) (default 30; this cap
     exists only to spread years within a source, not to gate total volume
     -- raise it freely, it is not a diversity requirement like #1)
  3. case_type mix as close to the pool's own proportions as the above two
     caps allow, but candidates of the four largest case_type values
     (Áfrýjað einkamál, Kært einkamál, Áfrýjað sakamál, Kært sakamál) are
     preferred early so each reaches >= 40 selections whenever caps 1/2
     leave room for it
  4. the appeal chain (document_links, appealed_to/appealed_from) is looked
     up for every selected document and folded into `expected`, as before

Each candidate is also assigned a `style` (short/medium/term, ratio
0.40/0.45/0.15) with a fixed-seed RNG so re-runs are stable.

Caps 1 and 2 are hard ceilings and are never loosened to hit --n; if the
pool can't support --n after them, main() simply returns fewer candidates
and prints the shortfall to stderr instead of silently padding the set.
(Cap 2's ceiling is set by --year-cap -- raise that flag if it's the
binding constraint; cap 1, the keyword cap, is the one the user actually
cares about and is not exposed as a flag.)

Writes `candidates.yaml` (everything selected) and, if --slices > 1,
`slice_01.yaml .. slice_NN.yaml` (contiguous, roughly equal chunks) into
--out-dir. Each slice opens with a YAML comment header carrying the
style rules verbatim, for the drafter agent that fills in `question`.
"""
from __future__ import annotations

import argparse
import asyncio
import random
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
from sqlalchemy import text

import engine.database.connection as c

SEED = 20260927
KEYWORD_CAP = 2
KEYWORD_CAP_NONE = 20  # cap for the synthetic "-" (no keywords) bucket
SOURCE_YEAR_CAP_DEFAULT = 30  # --year-cap default; not a diversity rule, just a volume knob
MAJOR_TYPES = ["Áfrýjað einkamál", "Kært einkamál", "Áfrýjað sakamál", "Kært sakamál"]
TYPE_FLOOR = 40
STYLE_RATIOS = [("short", 0.40), ("medium", 0.45), ("term", 0.15)]

SLICE_HEADER = """\
# Sneið {n}/{total} af frambjóðendapotti fyrir gullsettið (Task 10b).
#
# Fyrir hverja færslu: skrifaðu spurningu í `question` samkvæmt `style`
# færslunnar (sjá reglur að neðan) og vistaðu skrána sem
# `{stem}.done.yaml` í sömu möppu — breyttu engu öðru sviði.
#
# Stílreglur (úr task-10b-brief.md):
#
# - short: 2-3 orð, eins og maður slær inn í leitarreit: kjarnahugtakið og
#   eitt aðgreinandi orð ("nálgunarbann sambýlismaður", "gengistrygging
#   bílalán").
# - medium: 4-8 orð sem lýsa lögfræðilega álitaefninu, umorðað: samheiti,
#   önnur beygingarmynd eða önnur orðaröð en í reifun.
# - term: 3-8 orð með lagafagorði eða lagatilvísun eins og lögfræðingur
#   skrifar hana ("218. gr. hgl. líkamsárás hnífur", "sbr. 2. mgr. 36. gr.
#   samningalaga ósanngjarn samningur"). Lagatilvísun má taka úr reifun; hún
#   er ekki afritun.
# - Aldrei málsnúmer, aldrei nöfn aðila, aldrei ártal. Engin þriggja orða
#   runa (stofnorð, stopporð undanskilin) sameiginleg með reifuninni --
#   scripts/golden_audit.py athugar þetta sjálfvirkt (max shared run 2).
# - Íslenska, lágstafir nema sérnöfn, enginn punktur í lok.
#
# `note` er reifunin í heild + lykilorð + málategund + ártal -- til
# hliðsjónar við að skrifa spurninguna, ekki til að afrita úr.
"""


def _first_keyword(keywords) -> str:
    if not keywords:
        return "-"
    return str(keywords[0]).lower()


async def fetch_pool(min_year: int) -> list[dict]:
    await c.init_db()
    async with c.AsyncSessionLocal() as s:
        rows = (await s.execute(text("""
            SELECT d.id, s.short_name, d.case_number, d.document_date, d.summary,
                   d.keywords, d.case_type
            FROM documents d JOIN sources s ON s.id = d.source_id
            WHERE s.short_name IN ('haestirettur', 'landsrettur')
              AND length(d.summary) BETWEEN 200 AND 2000
              AND d.document_date >= :min_date
        """), {"min_date": date(min_year, 1, 1)})).mappings().all()
    return [dict(r) for r in rows]


async def fetch_appeal_chain(doc_id) -> list[dict]:
    async with c.AsyncSessionLocal() as s:
        linked = (await s.execute(text("""
            SELECT os.short_name, o.case_number, o.document_date
            FROM document_links dl JOIN documents o ON o.id = dl.to_doc_id
            JOIN sources os ON os.id = o.source_id
            WHERE dl.from_doc_id = :id AND dl.relation IN ('appealed_to', 'appealed_from')
        """), {"id": doc_id})).all()
    return [{"source": a, "case_number": b, "document_date": str(d)} for a, b, d in linked]


def load_excluded(path: Path | None) -> set[tuple[str, str]]:
    """(source, case_number) pairs already used as an `expected` answer in --exclude."""
    if path is None or not path.exists():
        return set()
    entries = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    out = set()
    for e in entries:
        for exp in e.get("expected", []) or []:
            out.add((exp["source"], exp["case_number"]))
    return out


def select_diverse(rows: list[dict], n: int, year_cap: int = SOURCE_YEAR_CAP_DEFAULT,
                    seed: int = SEED) -> list[dict]:
    """Greedy diversity selection under the keyword / source-year caps.

    Ordering: candidates of the four major case_type values are interleaved
    first (round-robin, each internally shuffled) until every major type has
    offered TYPE_FLOOR candidates or is exhausted, so the >= 40-per-type goal
    is reached whenever caps 1/2 leave room for it; everything else follows
    in one shuffled remainder. The keyword and source/year caps are enforced
    during the final pass and are never relaxed to reach `n`. The keyword
    cap (2 per first keyword) is the diversity rule that matters; year_cap
    is just a volume knob to keep any one (source, year) from dominating.
    """
    rng = random.Random(seed)
    by_type: dict[str, list[dict]] = {}
    for r in rows:
        by_type.setdefault(r["case_type"] or "-", []).append(r)
    for grp in by_type.values():
        rng.shuffle(grp)

    ordered: list[dict] = []
    majors = [t for t in MAJOR_TYPES if t in by_type]
    idx = {t: 0 for t in by_type}
    floor_taken = {t: 0 for t in majors}
    progressed = True
    while progressed:
        progressed = False
        for t in majors:
            if floor_taken[t] >= TYPE_FLOOR:
                continue
            if idx[t] < len(by_type[t]):
                ordered.append(by_type[t][idx[t]])
                idx[t] += 1
                floor_taken[t] += 1
                progressed = True

    remainder = []
    for t, grp in by_type.items():
        remainder.extend(grp[idx[t]:])
    rng.shuffle(remainder)
    ordered.extend(remainder)

    selected: list[dict] = []
    kw_count: dict[str, int] = {}
    sy_count: dict[tuple[str, int], int] = {}
    for r in ordered:
        if len(selected) >= n:
            break
        kw = _first_keyword(r["keywords"])
        kw_cap = KEYWORD_CAP_NONE if kw == "-" else KEYWORD_CAP
        if kw_count.get(kw, 0) >= kw_cap:
            continue
        sy = (r["short_name"], r["document_date"].year)
        if sy_count.get(sy, 0) >= year_cap:
            continue
        kw_count[kw] = kw_count.get(kw, 0) + 1
        sy_count[sy] = sy_count.get(sy, 0) + 1
        selected.append(r)
    return selected


def assign_styles(count: int, seed: int = SEED) -> list[str]:
    rng = random.Random(seed)
    styles = []
    for _ in range(count):
        x = rng.random()
        acc = 0.0
        for name, ratio in STYLE_RATIOS:
            acc += ratio
            if x < acc:
                styles.append(name)
                break
        else:
            styles.append(STYLE_RATIOS[-1][0])
    return styles


async def build_entries(selected: list[dict], start_id: int = 1001) -> list[dict]:
    styles = assign_styles(len(selected))
    out = []
    for i, (r, style) in enumerate(zip(selected, styles)):
        linked = await fetch_appeal_chain(r["id"])
        expected = [{"source": r["short_name"], "case_number": r["case_number"],
                     "document_date": str(r["document_date"])}] + linked
        note = (f"Reifun: {r['summary']} | Lykilorð: {', '.join(r['keywords'] or [])} "
                f"| Málategund: {r['case_type'] or '-'} | Ár: {r['document_date'].year}")
        out.append({
            "id": f"g{start_id + i}",
            "question": "",
            "style": style,
            "expected": expected,
            "scope": ["domstolar"],
            "note": note,
        })
    return out


def diversity_table(rows: list[dict], selected: list[dict], entries: list[dict],
                     year_cap: int = SOURCE_YEAR_CAP_DEFAULT) -> str:
    def counts(items, key):
        c: dict = {}
        for it in items:
            c[key(it)] = c.get(key(it), 0) + 1
        return c

    by_source = counts(selected, lambda r: r["short_name"])
    by_source_year = counts(selected, lambda r: (r["short_name"], r["document_date"].year))
    by_year = counts(selected, lambda r: r["document_date"].year)
    by_type = counts(selected, lambda r: r["case_type"] or "-")
    n_keywords = len({_first_keyword(r["keywords"]) for r in selected})
    by_style = counts(entries, lambda e: e["style"])
    at_cap = sorted(k for k, v in by_source_year.items() if v >= year_cap)

    lines = [
        f"Pool size (before caps): {len(rows)}",
        f"Selected: {len(selected)}",
        f"Year cap: {year_cap}",
        f"By source: {dict(sorted(by_source.items()))}",
        f"By year: {dict(sorted(by_year.items()))}",
        f"(source, year) buckets at the cap ({len(at_cap)}): {at_cap}",
        f"By case_type: {dict(sorted(by_type.items(), key=lambda kv: -kv[1]))}",
        f"Distinct first keywords among selected: {n_keywords}",
        f"Style shares: {dict(sorted(by_style.items()))}",
    ]
    return "\n".join(lines)


def write_slices(entries: list[dict], slices: int, out_dir: Path) -> list[Path]:
    if slices <= 1:
        slices = 1
    chunk = -(-len(entries) // slices)  # ceil division
    paths = []
    for i in range(slices):
        chunk_entries = entries[i * chunk:(i + 1) * chunk]
        if not chunk_entries:
            continue
        stem = f"slice_{i + 1:02d}"
        path = out_dir / f"{stem}.yaml"
        header = SLICE_HEADER.format(n=i + 1, total=slices, stem=stem)
        body = yaml.safe_dump(chunk_entries, allow_unicode=True, sort_keys=False, width=120)
        path.write_text(header + "\n" + body, encoding="utf-8")
        paths.append(path)
    return paths


async def main(n: int, min_year: int, exclude: Path | None, slices: int, out_dir: Path,
               year_cap: int = SOURCE_YEAR_CAP_DEFAULT) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    excluded = load_excluded(exclude)
    rows = await fetch_pool(min_year)
    rows = [r for r in rows if (r["short_name"], r["case_number"]) not in excluded]
    selected = select_diverse(rows, n, year_cap=year_cap)
    entries = await build_entries(selected)

    (out_dir / "candidates.yaml").write_text(
        yaml.safe_dump(entries, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
    slice_paths = write_slices(entries, slices, out_dir)

    print(diversity_table(rows, selected, entries, year_cap=year_cap))
    print(f"\nWrote {out_dir / 'candidates.yaml'} ({len(entries)} entries)")
    for p in slice_paths:
        print(f"Wrote {p}")
    if len(selected) < n:
        print(f"\nWARNING: requested --n {n} but only {len(selected)} candidates "
              f"satisfy the diversity caps (keyword cap {KEYWORD_CAP}/{KEYWORD_CAP_NONE}, "
              f"source-year cap {year_cap}) against a pool of {len(rows)}. "
              "Caps were not loosened; consider a lower --n or a higher --year-cap.",
              file=sys.stderr)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=700)
    ap.add_argument("--min-year", type=int, default=2000)
    ap.add_argument("--year-cap", type=int, default=SOURCE_YEAR_CAP_DEFAULT,
                     help="max documents per (source, year); a volume knob, not a diversity "
                          "rule (default 30)")
    ap.add_argument("--exclude", type=Path, default=None)
    ap.add_argument("--slices", type=int, default=1)
    ap.add_argument("--out-dir", type=Path, required=True)
    a = ap.parse_args()
    asyncio.run(main(a.n, a.min_year, a.exclude, a.slices, a.out_dir, a.year_cap))
