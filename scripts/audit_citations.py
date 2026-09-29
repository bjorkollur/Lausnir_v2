"""Independent precision audit of resolved case citations (spec §9).

Samples 200 `status='resolved'` citations from the `summary`/`body` layers,
stratified by `method` × `target_court`, and runs a verifier that was written
**from the spec, not from the extractor**: this module deliberately does not
import `engine.processors.citations` or `engine.processors.citation_resolver`
and carries its own court table, number regex and case-number normaliser, so a
bug shared by extractor and verifier cannot hide behind itself. The only engine
import is `engine.search.queries._citation`, which is display code (it turns a
document into its `urlausn` label) and takes no part in extraction.

Mechanical checks per row (spec §9 a–f):
    number   the LAST case-number token in `raw_text` == target.case_number
    court    the LAST court word in `raw_text` maps to target.court
    date     a full date in `raw_text`, if any, == target.document_date
    verdict  the word before the court word, if any, == target.verdict_type
    order    target.document_date <= citing.document_date
    span     `raw_text` ends with the stored character span

A row is `mech_ok` when every applicable check passes; checks that cannot be
evaluated are `n/a` and do not count against it. `verdict` is reported but not
scored: it measures the source's labelling of `verdict_type`, not the link (see
the note at the end of `verify`), and is counted separately as `vmis` in the
table. The machine cannot see whether
the sentence is a reference to a ruling at all, nor whether the court word
belongs to *this* number — a human/LLM reviewer reads the ±250-char context in
`/tmp/lausnir-dev/citations_audit.jsonl` for that.

Usage:
    set -a; . ./.env; set +a
    uv run python scripts/audit_citations.py > /tmp/lausnir-dev/citations_audit.txt
    uv run python scripts/audit_citations.py --n 50 --seed 1 --out /tmp/x.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

DEFAULT_SEED = 20260929
DEFAULT_N = 200
FLOOR = 3          # sampled rows per non-empty stratum, where the budget allows
CONTEXT = 250      # characters of context on each side of the stored span
OUT_PATH = "/tmp/lausnir-dev/citations_audit.jsonl"

# --------------------------------------------------------------------------
# The verifier's own tables and regexes (spec §5.2, §5.3, §5.5 — rewritten here
# on purpose; do not refactor these into a shared module with the extractor).
# --------------------------------------------------------------------------

MONTHS = {m: i + 1 for i, m in enumerate(
    "janúar febrúar mars apríl maí júní júlí ágúst september október nóvember desember".split())}

# Case-number token, spec §5.2. Guarded so a number inside a longer token or a
# second slash-group ('12/2020/3') is not mistaken for a case number.
NUM_RX = re.compile(
    r"(?<![\w/-])(?:[A-ZÞÆÖ]{1,2}-\d{1,5}/\d{4}|\d{1,4}/\d{4}|\d{4}-\d{1,3})(?![\d/])")

# Court words → documents.court, longest/most specific first (spec §5.3), plus
# the abbreviated forms of §5.4 that appear in `raw_text` for abbrev citations.
COURTS: list[tuple[str, str]] = [
    (r"[Hh]éraðsdóm\w*\s+Reykjavíkur", "Hérd. Rvk."),
    (r"[Hh]éraðsdóm\w*\s+Reykjaness", "Hérd. Reykn."),
    (r"[Hh]éraðsdóm\w*\s+Suðurlands", "Hérd. Suðl."),
    (r"[Hh]éraðsdóm\w*\s+Norðurlands\s+eystra", "Hérd. Norðeyst."),
    (r"[Hh]éraðsdóm\w*\s+Norðurlands\s+vestra", "Hérd. Norðvest."),
    (r"[Hh]éraðsdóm\w*\s+Vesturlands", "Hérd. Vestl."),
    (r"[Hh]éraðsdóm\w*\s+Austurlands", "Hérd. Austl."),
    (r"[Hh]éraðsdóm\w*\s+Vestfjarða", "Hérd. Vestfj."),
    (r"Hérd\.\s*Rvk\.", "Hérd. Rvk."),
    (r"Hérd\.\s*Reykn\.", "Hérd. Reykn."),
    (r"Hérd\.\s*Suðl\.", "Hérd. Suðl."),
    (r"Hérd\.\s*Norðeyst\.", "Hérd. Norðeyst."),
    (r"Hérd\.\s*Norðvest\.", "Hérd. Norðvest."),
    (r"Hérd\.\s*Vestl\.", "Hérd. Vestl."),
    (r"Hérd\.\s*Austl\.", "Hérd. Austl."),
    (r"Hérd\.\s*Vestfj\.", "Hérd. Vestfj."),
    (r"[Hh]éraðsdóm\w*", "Hérd."),
    (r"Hérd\.", "Hérd."),
    (r"Hæstaréttar|Hæstarétti|Hæstiréttur|Hæstarétt\b", "Hrd."),
    (r"Hrd\.", "Hrd."),
    (r"Landsréttar|Landsrétti|Landsréttur|Landsrétt\b", "Lrd."),
    (r"Lrd\.", "Lrd."),
    (r"Félagsdóm\w*", "Féld."),
    (r"Féld\.", "Féld."),
    (r"Landsdóm\w*", "Ld."),
    (r"Endurupptökudóm\w*", "Eud."),
]
# One alternation with a numbered group per entry, so the winning branch is known.
_COURT_RX = re.compile("|".join(f"(?P<k{i}>{pat})" for i, (pat, _) in enumerate(COURTS)))
_COURT_ABBR = {f"k{i}": abbr for i, (_, abbr) in enumerate(COURTS)}

# A pronoun standing in for a court named earlier in the sentence: the court
# cannot be verified from `raw_text` alone (spec §5.3) → court check is n/a.
INHERIT_RX = re.compile(r"réttarins|réttinum|dómstólsins|dómstóllinn|sama\s+dómstóls|sama\s+réttar")
# Court word or inherited pronoun, whichever comes last.
_COURT_OR_INHERIT_RX = re.compile(f"(?P<inh>{INHERIT_RX.pattern})|{_COURT_RX.pattern}")

FULL_DATE_RX = re.compile(
    r"(\d{1,2})\.\s+(" + "|".join(MONTHS) + r")\s+(\d{4})")

# The noun that introduces the citation, taken from directly before the court word.
VERDICT_WORDS = [("úrskurð", "Úrskurður"), ("ákvörð", "Ákvörðun"), ("dóm", "Dómur")]
_LAST_WORD_RX = re.compile(r"([A-Za-zÁÉÍÓÚÝÞÆÖÐáéíóúýþæöð]+)\W*$")


def norm_num(s: str | None) -> str | None:
    """Independent re-implementation of the case-number normaliser (spec §6.1).

    Spaces dropped, upper-cased, leading zeros stripped from the numeric part
    before the slash: '055/2001' → '55/2001', 'E-0012/2020' → 'E-12/2020',
    '243 /2002' → '243/2002'.
    """
    if not s:
        return None
    s = re.sub(r"\s+", "", s).upper()
    if "/" in s:
        head, _, tail = s.rpartition("/")
        m = re.fullmatch(r"([A-ZÞÆÖ]{1,2}-)?0*(\d+)", head)
        if m:
            head = (m.group(1) or "") + m.group(2)
        return f"{head}/{tail}"
    return s


def _strip_letter_prefix(n: str) -> str:
    return re.sub(r"^[A-ZÞÆÖ]{1,2}-", "", n)


def _court_from_number(num: str | None) -> str | None:
    """The court a case number's shape implies on its own (spec §5.3), or None.

    'F-…' is always Félagsdómur, any other letter prefix a district court (bare
    'Hérd.', since the number does not say which one), 'YYYY-N' a
    málskotsbeiðni. A bare 'N/YYYY' implies nothing.
    """
    if not num:
        return None
    if num.startswith("F-"):
        return "Féld."
    if re.match(r"^[A-ZÞÆÖ]{1,2}-", num):
        return "Hérd."
    if re.fullmatch(r"\d{4}-\d{1,3}", num):
        return "Hrd. málsk."
    return None


def _last_court(raw_text: str):
    """Return (kind, abbr, start) for the last court word or inherited pronoun.

    kind is 'court', 'inherit' or 'none'.
    """
    last = None
    for m in _COURT_OR_INHERIT_RX.finditer(raw_text):
        last = m
    if last is None:
        return "none", None, -1
    if last.group("inh"):
        return "inherit", None, last.start()
    abbr = next(a for g, a in _COURT_ABBR.items() if last.group(g) is not None)
    return "court", abbr, last.start()


def verify(raw_text: str, span_text: str | None, target: dict, citing: dict,
           prefix: str = "") -> dict:
    """Run the six mechanical checks. Pure — no DB, no engine extraction code.

    `target` carries court / case_number / document_date / verdict_type,
    `citing` carries document_date. Values may be None. `prefix` is the text
    standing immediately before `raw_text` in the document (up to 40 chars):
    `raw_text` starts *at* the court word, so the noun that introduces the
    citation ('dómi Hæstaréttar') lies just outside it and check (d) would be
    vacuous without it.
    Returns {'checks': {name: 'pass'|'fail'|'n/a'}, 'failed': [...],
             'verdict': 'mech_ok'|'mech_fail', 'notes': [...]}.
    """
    checks: dict[str, str] = {}
    notes: list[str] = []
    raw_text = raw_text or ""

    nums = NUM_RX.findall(raw_text)
    found_num = norm_num(nums[-1]) if nums else None
    want_num = norm_num(target.get("case_number"))

    # (a) the last case number in raw_text is the target's case number.
    if want_num is None:
        checks["number"] = "n/a"
    elif found_num is None:
        checks["number"] = "fail"
        notes.append("no case-number token in raw_text")
    elif found_num == want_num:
        checks["number"] = "pass"
    elif target.get("court") == "Féld." and \
            _strip_letter_prefix(found_num) == _strip_letter_prefix(want_num):
        # Félagsdómur stores both 'F-1/2010' and '1/2000' (spec §3).
        checks["number"] = "pass"
        notes.append("Féld. F- prefix difference accepted")
    else:
        checks["number"] = "fail"

    # (b) the last court word maps to the target's court.
    # The number's own shape decides the court whenever it carries one, and it
    # overrides a court word that says otherwise (spec §5.3): 'F-' is always
    # Félagsdómur, any other letter prefix a district court, 'YYYY-N' a
    # málskotsbeiðni — which is why 'ákvörðun nr. 2022-1' needs no court word
    # at all. Only when the number is a bare 'N/YYYY' does the word decide.
    kind, abbr, cpos = _last_court(raw_text)
    tcourt = target.get("court")
    expected = _court_from_number(found_num) or (abbr if kind == "court" else None)
    if expected is None:
        if kind == "inherit":
            checks["court"] = "n/a"
            notes.append("court inherited from an earlier sentence: cannot verify")
        else:
            checks["court"] = "fail"
            notes.append("no court word in raw_text and no court-bearing number shape")
    elif expected == tcourt:
        checks["court"] = "pass"
    elif expected == "Hérd." and (tcourt or "").startswith("Hérd. "):
        # A district court with no place named accepts any district.
        checks["court"] = "pass"
    else:
        checks["court"] = "fail"
    if kind == "court" and expected != abbr:
        notes.append(f"letter-prefix rule (§5.3): number makes it {expected}, "
                     f"the word says {abbr}")
    elif kind != "court" and expected is not None:
        notes.append(f"court taken from the number shape (§5.3): {expected}")

    # (c) a full date in raw_text, if any, is the target's date.
    tdate = target.get("document_date")
    dates = set()
    for d, mon, y in FULL_DATE_RX.findall(raw_text):
        try:
            dates.add(date(int(y), MONTHS[mon], int(d)))
        except ValueError:
            notes.append(f"unparseable date '{d}. {mon} {y}'")
    if not dates:
        checks["date"] = "n/a"
    elif tdate is None:
        checks["date"] = "n/a"
        notes.append("target has no document_date")
    elif tdate in dates:
        checks["date"] = "pass"
    else:
        checks["date"] = "fail"

    # (d) the word immediately before the court word is the target's verdict type.
    tverdict = target.get("verdict_type")
    word = None
    if cpos >= 0:
        before = (prefix or "") + raw_text[:cpos]
        m = _LAST_WORD_RX.search(before)
        if m:
            low = m.group(1).lower()
            for stem, label in VERDICT_WORDS:
                if low.startswith(stem):
                    word = label
                    break
    if word is None or tverdict is None:
        checks["verdict"] = "n/a"
    elif word == tverdict:
        checks["verdict"] = "pass"
    else:
        checks["verdict"] = "fail"

    # (e) a ruling cannot cite a later one.
    cdate = citing.get("document_date")
    if tdate is None or cdate is None:
        checks["order"] = "n/a"
    else:
        checks["order"] = "pass" if tdate <= cdate else "fail"

    # (f) raw_text ends at the stored character span.
    if span_text is None:
        checks["span"] = "n/a"
        notes.append("layer text missing: span not checked")
    else:
        checks["span"] = "pass" if raw_text.endswith(span_text) else "fail"

    # The verdict check is reported but not scored. `documents.verdict_type`
    # follows the source's labelling (Hæstiréttur kærumál are stored as
    # 'Úrskurður' though the document is headed 'Dómur Hæstaréttar'), and the
    # resolver narrows by verdict only when more than one candidate is left —
    # so a mismatch is a labelling signal, never evidence of a wrong link.
    failed = sorted(k for k, v in checks.items() if v == "fail" and k != "verdict")
    return {"checks": checks, "failed": failed,
            "verdict": "mech_fail" if failed else "mech_ok",
            "verdict_mismatch": checks["verdict"] == "fail",
            "notes": notes}


# --------------------------------------------------------------------------
# Sampling
# --------------------------------------------------------------------------

def allocate(sizes: dict, n_total: int) -> dict:
    """Proportional allocation with a floor of FLOOR per non-empty stratum.

    Deterministic: strata are visited largest-first, ties broken by key. The
    floor is honoured while the budget lasts; whatever is left is handed out in
    proportion to stratum size, never beyond a stratum's own size.
    """
    keys = sorted(sizes, key=lambda k: (-sizes[k], k))
    if sum(sizes.values()) <= n_total:
        return dict(sizes)
    alloc = {}
    budget = n_total
    for k in keys:
        take = max(0, min(FLOOR, sizes[k], budget))
        alloc[k] = take
        budget -= take
    while budget > 0:
        live = [k for k in keys if sizes[k] > alloc[k]]
        if not live:
            break
        tot = sum(sizes[k] for k in live)
        given = 0
        for k in sorted(live, key=lambda k: (-sizes[k], k)):
            if budget == 0:
                break
            want = max(1, (budget * sizes[k]) // tot)
            give = min(want, sizes[k] - alloc[k], budget)
            alloc[k] += give
            budget -= give
            given += give
        if given == 0:
            break
    return {k: v for k, v in alloc.items() if v}


POOL_SQL = """
SELECT c.id::text AS id, coalesce(c.method, '—') AS method, c.target_court
FROM citations c
WHERE c.status = 'resolved' AND c.layer IN ('summary', 'body')
ORDER BY c.id
"""

DETAIL_SQL = """
WITH sel AS (
    SELECT c.id, c.from_doc_id, c.to_doc_id, c.layer, c.char_start, c.char_end,
           c.raw_text, coalesce(c.method, '—') AS method, c.target_court,
           c.confidence,
           f.court AS citing_court, f.case_number AS citing_case,
           f.document_date AS citing_date,
           CASE c.layer WHEN 'summary' THEN f.summary
                        WHEN 'body' THEN f.body_text END AS lt,
           t.court AS tcourt, t.case_number AS tcase,
           t.document_date AS tdate, t.verdict_type AS tverdict,
           s.short_name AS tsource
    FROM citations c
    JOIN documents f ON f.id = c.from_doc_id
    JOIN documents t ON t.id = c.to_doc_id
    JOIN sources s ON s.id = t.source_id
    WHERE c.id = ANY(CAST(:ids AS uuid[]))
)
SELECT id::text AS id, from_doc_id::text AS from_doc_id, to_doc_id::text AS to_doc_id,
       layer, char_start, char_end, raw_text, method, target_court, confidence,
       citing_court, citing_case, citing_date,
       tcourt, tcase, tdate, tverdict, tsource,
       substr(lt, char_start + 1, char_end - char_start) AS span_text,
       substr(lt, greatest(char_start - :ctx, 0) + 1,
              (char_end + :ctx) - greatest(char_start - :ctx, 0)) AS context,
       -- the 40 characters standing before the court word raw_text starts at
       substr(lt, greatest(char_end - length(raw_text) - 40, 0) + 1,
              (char_end - length(raw_text))
              - greatest(char_end - length(raw_text) - 40, 0)) AS pre_text
FROM sel
"""


def clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", s).strip() if s else ""


async def run(n_total: int, seed: int, out_path: str) -> int:
    from sqlalchemy import text as sql
    from engine.database import connection as conn_mod
    from engine.database.connection import init_db
    from engine.search.queries import _citation

    # Read-only role only: the audit must never be able to write, so there is
    # no fallback to DATABASE_URL.
    url = os.environ.get("DATABASE_URL_READONLY")
    if not url:
        print("DATABASE_URL_READONLY is not set — run: set -a; . ./.env; set +a",
              file=sys.stderr)
        return 2
    await init_db(url, create_tables=False)

    async with conn_mod.AsyncSessionLocal() as session:
        pool = (await session.execute(sql(POOL_SQL))).mappings().all()
        strata: dict[tuple[str, str], list[str]] = defaultdict(list)
        for r in pool:
            strata[(r["method"], r["target_court"])].append(r["id"])
        sizes = {k: len(v) for k, v in strata.items()}
        alloc = allocate(sizes, n_total)

        rng = random.Random(seed)
        chosen: list[str] = []
        for k in sorted(alloc):
            chosen.extend(rng.sample(sorted(strata[k]), alloc[k]))

        rows = (await session.execute(
            sql(DETAIL_SQL), {"ids": chosen, "ctx": CONTEXT})).mappings().all()

    by_id = {r["id"]: r for r in rows}
    results = []
    for cid in chosen:
        r = by_id.get(cid)
        if r is None:                     # to_doc_id vanished between the two queries
            continue
        target = {"court": r["tcourt"], "case_number": r["tcase"],
                  "document_date": r["tdate"], "verdict_type": r["tverdict"]}
        citing = {"document_date": r["citing_date"]}
        v = verify(r["raw_text"], r["span_text"], target, citing, r["pre_text"] or "")
        results.append((r, v))

    # ---- JSONL for the human/LLM reviewer -------------------------------
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        for r, v in results:
            urlausn = _citation(r["tsource"], r["tcourt"], r["tcase"],
                                r["tdate"], r["tverdict"])
            fh.write(json.dumps({
                "citation_id": r["id"],
                "from_doc_id": r["from_doc_id"],
                "to_doc_id": r["to_doc_id"],
                "method": r["method"],
                "target_court": r["target_court"],
                "layer": r["layer"],
                "raw_text": clean(r["raw_text"]),
                "context": clean(r["context"]),
                "target_urlausn": urlausn,
                "target_date": r["tdate"].isoformat() if r["tdate"] else None,
                "citing_date": r["citing_date"].isoformat() if r["citing_date"] else None,
                "citing_urlausn": _citation(None, r["citing_court"], r["citing_case"],
                                            r["citing_date"], None)
                or f'{r["citing_court"]} {r["citing_case"]}',
                "checks": v["checks"],
                "mech_verdict": v["verdict"],
                "verdict_mismatch": v["verdict_mismatch"],
                "notes": v["notes"],
            }, ensure_ascii=False) + "\n")

    # ---- report ---------------------------------------------------------
    print(f"# Nákvæmniúttekt tilvitnana — seed {seed}, markúrtak {n_total}")
    print(f"# pool: {len(pool):,} resolved citations in summary/body, "
          f"{len(sizes)} strata; sampled {len(results)}")
    print(f"# jsonl: {out_path}\n")

    per = defaultdict(lambda: [0, 0, 0, 0])
    for r, v in results:
        cell = per[(r["method"], r["target_court"])]
        cell[0] += 1
        cell[1 if v["verdict"] == "mech_ok" else 2] += 1
        cell[3] += 1 if v["verdict_mismatch"] else 0

    print(f"{'method':<16} {'target_court':<18} {'pool':>7} {'n':>4} {'ok':>4} "
          f"{'fail':>5} {'vmis':>5}")
    print("-" * 66)
    for k in sorted(per, key=lambda k: (k[0], k[1])):
        n, ok, bad, vmis = per[k]
        print(f"{k[0]:<16} {k[1]:<18} {sizes.get(k, 0):>7,} {n:>4} {ok:>4} "
              f"{bad:>5} {vmis:>5}")
    print("-" * 66)
    tot = len(results)
    ok = sum(1 for _, v in results if v["verdict"] == "mech_ok")
    vmis = sum(1 for _, v in results if v["verdict_mismatch"])
    pct = 100.0 * ok / tot if tot else 0.0
    print(f"{'ALLS':<35} {len(pool):>7,} {tot:>4} {ok:>4} {tot - ok:>5} {vmis:>5}"
          f"   ({pct:.1f} % mech_ok)")
    print(f"\nverdict word vs stored verdict_type: {vmis}/{tot} mismatches "
          f"(labelling, not link errors — not scored)")

    fails = defaultdict(int)
    for _, v in results:
        for f in v["failed"]:
            fails[f] += 1
    if fails:
        print("failing checks: " + ", ".join(f"{k}={v}" for k, v in sorted(fails.items())))

    print(f"\n## mech_fail rows ({tot - ok})")
    for r, v in results:
        if v["verdict"] == "mech_ok":
            continue
        urlausn = _citation(r["tsource"], r["tcourt"], r["tcase"], r["tdate"], r["tverdict"])
        print(f"\n- id {r['id']}  [{r['method']} / {r['target_court']} / {r['layer']}]")
        print(f"  checks : {v['failed']}  ({v['checks']})")
        print(f"  raw    : {clean(r['raw_text'])}")
        print(f"  target : {urlausn}   ({r['tcourt']} {r['tcase']} "
              f"{r['tdate']} {r['tverdict']})")
        print(f"  citing : {r['citing_court']} {r['citing_case']} {r['citing_date']}")
        if v["notes"]:
            print(f"  notes  : {'; '.join(v['notes'])}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=DEFAULT_N, help="sample size (default 200)")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--out", default=OUT_PATH)
    a = ap.parse_args()
    return asyncio.run(run(a.n, a.seed, a.out))


if __name__ == "__main__":
    raise SystemExit(main())
