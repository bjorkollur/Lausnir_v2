"""Distributions and what-if tables from analysed journal manifests, for choosing the pipeline's thresholds
(spec §9, plan Task 22).

    uv run --env-file .env python scripts/timarit_pilot_report.py
    uv run --env-file .env python scripts/timarit_pilot_report.py --only pilot-set.txt

Read-only: it loads the YAML manifests (engine.timarit.manifest.load_manifest) and data/timarit/_unmatched.yaml and
prints to stdout; nothing is saved, no database, no network. Per source and for all sources together it shows
(1) issues, statuses and articles located, (2) histograms of article confidence, page-map quality, bin_ratio and
runner_up_share, (3) how the confidence signals are composed, (4) what-if tables for APPROVE_THRESHOLD (with the
current weights and with weights where the external record is not required), PAGE_MAP_MIN_QUALITY,
OCR_BIN_RATIO_THRESHOLD, COVERAGE_MAX_GAP_PAGES and PAGE_MAP_RESTART_RATIO, (5) the lowest-confidence articles and
(6) the issues in review or failed with their error. Then (7) "Frávik til yfirferðar": every significant deviation
analysis flagged (an analysis note starting with analyze.FRAVIK, R42) with its issue and article, and the count per
source, and (8) "Efnisyfirlit á síðunum" (Task 23b): for every issue whose contents list the parser read, the share of
its titles found on their start pages (page_map["toc_check"]), a what-if table for TOC_MIN_TITLE_SHARE and one line per
issue, lowest share first, the issues listed in --merkja (audited failures) marked with *. Task 23c adds the pages no
entry claims ("síður utan lista", page_map["toc_check"]["unclaimed"]): their distribution with the issues sent to Claude
marked, and a what-if table for TOC_MAX_UNCLAIMED_PAGES and TOC_MAX_UNCLAIMED_SHARE. Exit status 1 when a manifest is
unreadable or listed in --only but missing, or when nothing has been analysed.
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, NamedTuple

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.timarit import config as C  # noqa: E402
from engine.timarit.analyze import (  # noqa: E402
    _BIN_SAMPLE_PAGES, FORCED_REVIEW_NOTES, FRAVIK, TOC_GATE_NOTE, count_word,
)
from engine.timarit.manifest import ArticleEntry, IssueManifest, issue_status, load_manifest  # noqa: E402

APPROVE_STEPS = tuple(round(0.50 + 0.05 * i, 2) for i in range(10))     # 0.50 … 0.95
QUALITY_STEPS = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8)
BIN_STEPS = (0.5, 0.6, 0.7, 0.8)
GAP_STEPS = (0, 1, 2, 3, 4, 6, 8, 10, 15, 20)
RESTART_STEPS = (0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50)
NO_LIMIT = 10 ** 6          # "any gap": what remains is blocked by articles whose pages are unknown
LOWEST_N = 10
ERROR_CHARS = 100
TITLE_CHARS = 60
_CHECK_SHORT = {"title_on_page": "title", "printed_page_match": "page", "author_on_page": "author", "external_record": "ext"}


class Loaded(NamedTuple):
    key: str                    # the manifest's file stem (YEAR_VOLUME_ISSUE), what `import_timarit.py analyze --issue` takes
    manifest: IssueManifest
    path: Path | None = None    # the manifest's file


# --- pure computations ------------------------------------------------------------------------------------

def histogram(values: Iterable[float], lo: float = 0.0, hi: float = 1.0, bins: int = 10) -> list[int]:
    """Counts per equal-width bin of [lo, hi]; hi itself and anything above falls in the last bin, anything
    below lo in the first."""
    counts = [0] * bins
    for v in values:
        pos = int(round((v - lo) / (hi - lo) * bins, 9))
        counts[max(0, min(bins - 1, pos))] += 1
    return counts


def format_histogram(values: Iterable[float], lo: float = 0.0, hi: float = 1.0, bins: int = 10, *, missing: int = 0) -> str:
    """'n=12, vantar 4  0.0:0 0.1:2 …' — each label is the bin's lower edge; `missing` is how many of the population
    had no value (left out of the bins). Values above hi are counted in the last bin and named."""
    vals = list(values)
    lacking = f", vantar {missing}" if missing else ""
    if not vals:
        return "engin gögn" + (f" (vantar {missing})" if missing else "")
    counts = histogram(vals, lo, hi, bins)
    line = f"n={len(vals)}{lacking}  " + " ".join(f"{lo + i * (hi - lo) / bins:.1f}:{c}" for i, c in enumerate(counts))
    above = sum(v > hi for v in vals)
    return line + (f"  (yfir {hi:.1f}: {above})" if above else "")


def signal_composition(articles: list[ArticleEntry]) -> dict[str, tuple[int, int]]:
    """check name -> (articles where it is True, all articles). A check that is missing counts as not true, as in
    article_confidence. The weighted checks come first, in WEIGHTS order, then any other check that occurs."""
    names = list(C.WEIGHTS) + sorted({k for a in articles for k in a.checks} - set(C.WEIGHTS))
    return {k: (sum(1 for a in articles if a.checks.get(k)), len(articles)) for k in names}


def title_sources(articles: list[ArticleEntry]) -> dict[str, int]:
    return dict(Counter(a.provenance.get("title") or "(óskráð)" for a in articles))


@contextlib.contextmanager
def _override(**values):
    """Temporarily set tunables in engine.timarit.config (manifest.issue_status reads them at call time)."""
    old = {k: getattr(C, k) for k in values}
    try:
        for k, v in values.items():
            setattr(C, k, v)
        yield
    finally:
        for k, v in old.items():
            setattr(C, k, v)


def _number(v) -> float | None:
    """A finite number, else None: text, booleans, NaN and infinity in a hand-edited page_map count as missing."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if math.isfinite(v) else None


def _page_map_values(ms: list[IssueManifest], key: str, *, containers_only: bool = False) -> list[float]:
    out = []
    for m in ms:
        if containers_only and m.file is None:
            continue
        v = _number(m.page_map.get(key))
        if v is not None:
            out.append(v)
    return out


def _page_map_missing(ms: list[IssueManifest], key: str, *, containers_only: bool = False) -> int:
    """How many issues of the population (containers only, or all) have no usable value for the page-map key."""
    population = [m for m in ms if not (containers_only and m.file is None)]
    return len(population) - len(_page_map_values(ms, key, containers_only=containers_only))


def _has_llm(a: ArticleEntry) -> bool:
    return any("llm" in str(p).split("+") for p in a.provenance.values())


def forced_review(m: IssueManifest) -> bool:
    """Whether analysis sends this issue to review whatever its scores say (analyze_issue: a fallback was used),
    read back from the manifest: an error (no text layer, no article list), a page map below PAGE_MAP_MIN_QUALITY,
    an article list that came from Claude, or an analysis note saying a volume or year was computed by formula,
    an article file had no text layer, registry entries were not found in the file, or a deviation the user is to
    review ("FRÁVIK: ", R42) (analyze.FORCED_REVIEW_NOTES). An unreadable file in an article folder
    is not detected, but its article has confidence 0 and fails any threshold anyway."""
    q = _number(m.page_map.get("quality"))
    return (bool(m.error) or (q is not None and q < C.PAGE_MAP_MIN_QUALITY)
            or any(_has_llm(a) or any(n.startswith(FORCED_REVIEW_NOTES) for n in a.analysis_notes) for a in m.articles))


def _confidence(a: ArticleEntry, weights: dict[str, float], assume_passed: frozenset[str] = frozenset()) -> float:
    """article_confidence under other weights, with the checks in `assume_passed` counted as passed: same rounding,
    same cap for an LLM-made list."""
    score = round(sum(w for k, w in weights.items() if a.checks.get(k) or k in assume_passed), 6)
    return min(score, C.LLM_CONFIDENCE_CAP) if _has_llm(a) else score


def reweighted(m: IssueManifest, weights: dict[str, float], assume_passed: Iterable[str] = ()) -> IssueManifest:
    """A copy of the manifest whose article confidences are recomputed from their `checks` under `weights`."""
    assumed = frozenset(assume_passed)
    return dataclasses.replace(m, articles=[dataclasses.replace(a, confidence=_confidence(a, weights, assumed)) for a in m.articles])


def without_external_record(weights: dict[str, float]) -> dict[str, float]:
    """The weights with the external-record check dropped and the others scaled up to the same total, so that an
    article that passes every remaining check still scores what a perfect article scores now."""
    rest = {k: w for k, w in weights.items() if k != "external_record"}
    scale = sum(weights.values()) / sum(rest.values())
    return {k: round(w * scale, 6) for k, w in rest.items()}


class ApproveRow(NamedTuple):
    threshold: float
    articles_ok: int        # articles at or above the threshold
    articles: int
    by_analysis: int        # issues analysis would approve: issue_status says approved AND no forced review (forced_review)
    status_only: int        # issues manifest.issue_status alone calls approved (all articles >= threshold, coverage OK)
    issues: int


def approve_what_if(ms: list[IssueManifest], thresholds: Iterable[float] = APPROVE_STEPS,
                    weights: dict[str, float] | None = None, assume_passed: Iterable[str] = ()) -> list[ApproveRow]:
    """APPROVE_THRESHOLD what-if by manifest.issue_status itself, the threshold overridden in turn; with `weights`
    and/or `assume_passed` the article confidences are first recomputed from the `checks` under them."""
    assumed = tuple(assume_passed)
    if weights is not None or assumed:
        ms = [reweighted(m, C.WEIGHTS if weights is None else weights, assumed) for m in ms]
    arts = [a for m in ms for a in m.articles]
    forced = [forced_review(m) for m in ms]
    rows = []
    for t in thresholds:
        with _override(APPROVE_THRESHOLD=t):
            ok = [issue_status(m) == "approved" for m in ms]
        rows.append(ApproveRow(t, sum(a.confidence >= t for a in arts), len(arts),
                               sum(o and not f for o, f in zip(ok, forced)), sum(ok), len(ms)))
    return rows


def map_quality_what_if(ms: list[IssueManifest], thresholds: Iterable[float] = QUALITY_STEPS) -> list[tuple[float, int, int]]:
    """(threshold, containers whose page-map quality is below it -> forced review, containers with a quality)."""
    q = _page_map_values(ms, "quality", containers_only=True)
    return [(t, sum(v < t for v in q), len(q)) for t in thresholds]


def bin_ratio_what_if(ms: list[IssueManifest], thresholds: Iterable[float] = BIN_STEPS) -> list[tuple[float, int, int, int]]:
    """(threshold, issues whose bin_ratio is below it -> re-OCR at import, of those with ratio 0.0 (no words at all:
    a scan, OCR'd anyway), issues with a bin_ratio)."""
    r = _page_map_values(ms, "bin_ratio")
    return [(t, sum(v < t for v in r), sum(v == 0.0 for v in r if v < t), len(r)) for t in thresholds]


def coverage_what_if(ms: list[IssueManifest], gaps: Iterable[int] = GAP_STEPS) -> list[tuple[int, int, int]]:
    """(gap, issue PDFs whose articles cover the file with at most `gap` loose pages in each hole, issue PDFs with
    articles). The coverage rule is issue_status's own: with the confidence threshold at 0 an issue is 'approved'
    exactly when coverage holds."""
    containers = [m for m in ms if m.file is not None and m.articles]
    rows = []
    for g in gaps:
        with _override(APPROVE_THRESHOLD=0.0, COVERAGE_MAX_GAP_PAGES=g):
            rows.append((g, sum(issue_status(m) == "approved" for m in containers), len(containers)))
    return rows


def restart_ratio_what_if(ms: list[IssueManifest], thresholds: Iterable[float] = RESTART_STEPS) -> list[tuple[float, int, int]]:
    """(ratio, containers whose runner_up_share reaches it -> map ambiguous, containers with a share). The map
    also needs MIN_AGREEING runner-up votes to be ambiguous, so this is an upper bound."""
    s = _page_map_values(ms, "runner_up_share", containers_only=True)
    return [(t, sum(v >= t for v in s), len(s)) for t in thresholds]


def lowest_articles(loaded: list[Loaded], n: int = LOWEST_N) -> list[tuple[str, ArticleEntry]]:
    pairs = [(ld.key, a) for ld in loaded for a in ld.manifest.articles]
    return sorted(pairs, key=lambda p: p[1].confidence)[:n]      # stable: ties keep path, then article order


# --- loading ----------------------------------------------------------------------------------------------

def _abs(p: str | Path) -> Path:
    p = Path(p)
    return p if p.is_absolute() else ROOT / p


def read_paths_file(path: Path) -> list[Path]:
    """One manifest path per line, relative to the repo (or absolute); blank lines and #-comments are skipped.
    Raises ValueError (Icelandic message) when the file cannot be read."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as e:
        raise ValueError(f"tókst ekki að lesa {path}: {e}") from None
    out = []
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(_abs(line))
    return out


def load_manifests(root: Path, only: list[Path] | None = None) -> tuple[list[Loaded], list[str]]:
    """Every manifest under root/*/*.yaml (only those in `only` when given). Returns the loaded ones and a message
    per problem: an unreadable manifest, a listed path that is not under root."""
    paths = sorted(Path(root).glob("*/*.yaml"))
    problems: list[str] = [] if Path(root).is_dir() else [f"mappa fannst ekki: {root}"]
    if only is not None:
        found = {p.resolve() for p in paths}
        wanted = {p.resolve() for p in only}
        problems += [f"ekki fundið: {p}" for p in only if p.resolve() not in found]
        paths = [p for p in paths if p.resolve() in wanted]
    loaded = []
    for p in paths:
        try:
            loaded.append(Loaded(p.stem, load_manifest(p), p))
        except (ValueError, OSError) as e:
            problems.append(f"ólæsilegt: {e}")
    return loaded, problems


def contained_in(path: Path) -> list[tuple[str, str]]:
    """(file, the larger volume file that contains it) for the `contained_in` rejections of discover. A missing file
    is an empty list; a file that cannot be read or is not a list raises ValueError with an Icelandic message."""
    path = Path(path)
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError, UnicodeDecodeError) as e:
        raise ValueError(f"{path} ólæsileg: {e}") from None
    if data is None:
        return []
    if not isinstance(data, list):
        raise ValueError(f"{path} er ekki listi af færslum")
    return [(str(e.get("path")), str(e.get("duplicate_of") or "")) for e in data
            if isinstance(e, dict) and e.get("reason") == "contained_in"]


def is_analysed(m: IssueManifest) -> bool:
    return bool(m.articles) or m.status != "pending"


# --- rendering ------------------------------------------------------------------------------------------

def _table(headers: list[str], rows: list[list[str]], indent: str = "  ") -> list[str]:
    cols = list(zip(headers, *rows))
    widths = [max(len(c) for c in col) for col in cols]
    def fmt(cells):
        return indent + "  ".join(c.ljust(w) if i == 0 else c.rjust(w) for i, (c, w) in enumerate(zip(cells, widths))).rstrip()
    return [fmt(headers)] + [fmt(r) for r in rows]


def _mark(t: float, current: float | None) -> str:
    return f"{t:.2f}" + (" *" if current is not None and abs(t - current) < 1e-9 else "")


def _one_line(s: str | None, n: int) -> str:
    return " ".join((s or "").split())[:n]


def _weights_text(weights: dict[str, float]) -> str:
    return ", ".join(f"{k} {w:g}" for k, w in weights.items())


def _missing_note(n: int) -> str:
    return f"; vantar gildi í {n} hefti (ekki talin með)" if n else ""


def _approve_table(rows: list[ApproveRow]) -> list[str]:
    cells = [[_mark(r.threshold, C.APPROVE_THRESHOLD), f"{r.articles_ok}/{r.articles}", f"{r.by_analysis}/{r.issues}",
              f"{r.status_only}/{r.issues}"] for r in rows]
    return _table(["þröskuldur", "greinar ≥", "samþykkt af greiningu", "issue_status eingöngu"], cells, indent="    ")


def _approve_lines(ms: list[IssueManifest]) -> list[str]:
    alt_w = without_external_record(C.WEIGHTS)
    forced = sum(forced_review(m) for m in ms)
    return [
        f"  APPROVE_THRESHOLD (núna {C.APPROVE_THRESHOLD}, * merkir núverandi gildi): hefti sem yrðu „approved“ ef þröskuldurinn væri annar",
        "  „samþykkt af greiningu“ = allar greinar ≥ þröskuldur og þekja í lagi (manifest.issue_status) OG engin þvinguð yfirferð;",
        "  „issue_status eingöngu“ sleppir síðara skilyrðinu og ofmetur því samþykkt hefti.",
        "  Þvinguð yfirferð (greining sendir hefti í yfirferð hvað sem stigum líður) = villa í skránni, ekkert textalag,",
        f"  blaðsíðukort undir PAGE_MAP_MIN_QUALITY (núna {C.PAGE_MAP_MIN_QUALITY}), greinalisti frá Claude (llm), árgangur eða ár reiknað út frá formúlu,",
        "  greinar sem fundust ekki í skránni, eða frávik til yfirferðar (FRÁVIK, sjá neðst).",
        f"  Þvinguð yfirferð í úrtakinu: {forced}/{len(ms)} hefti.",
        "",
        f"  1) núverandi vogir (öryggi eins og greining geymdi það): {_weights_text(C.WEIGHTS)}",
        *_approve_table(approve_what_if(ms)),
        "",
        f"  2) TILGÁTA: án external_record — öryggi endurreiknað úr checks með endurkvörðuðum vogum: {_weights_text(alt_w)} (sama heildarsumma)",
        *_approve_table(approve_what_if(ms, weights=alt_w)),
        "",
        "  3) TILGÁTA: external_record talið með — öryggi endurreiknað úr checks með núverandi vogum og external_record",
        "     talið staðist hjá öllum greinum (+0.20 þar sem ytri skrá vantaði)",
        *_approve_table(approve_what_if(ms, assume_passed=("external_record",))),
    ]


def _quality_lines(ms: list[IssueManifest]) -> list[str]:
    rows = [[_mark(t, C.PAGE_MAP_MIN_QUALITY), f"{below}/{total}"] for t, below, total in map_quality_what_if(ms)]
    missing = _page_map_missing(ms, "quality", containers_only=True)
    return [f"  PAGE_MAP_MIN_QUALITY (núna {C.PAGE_MAP_MIN_QUALITY}): bindi með blaðsíðukort undir mörkum fara í yfirferð{_missing_note(missing)}",
            *_table(["lágmark", "undir"], rows)]


def _bin_lines(ms: list[IssueManifest]) -> list[str]:
    cur = C.OCR_BIN_RATIO_THRESHOLD
    rows = [[_mark(t, cur), f"{below}/{total}", str(zero)] for t, below, zero, total in bin_ratio_what_if(ms)]
    missing = _page_map_missing(ms, "bin_ratio")
    return [f"  OCR_BIN_RATIO_THRESHOLD (núna {cur}): hefti með BÍN-hlutfall undir mörkum{_missing_note(missing)}",
            f"  Vísbending: hlutfallið er sýni úr fyrstu {_BIN_SAMPLE_PAGES} síðum hefti-PDF (eða fyrstu {_BIN_SAMPLE_PAGES} skrám möppu);",
            "  innflutningur ákveður fyrir hverja grein út frá hlutfalli í texta hennar. (Þar af 0.0 = skönnun án orða, sem þarf OCR hvort sem er.)",
            *_table(["mörk", "undir", "þar af 0.0"], rows)]


def _coverage_lines(ms: list[IssueManifest]) -> list[str]:
    rows = [["ótakmörkuð" if g == NO_LIMIT else (f"{g} *" if g == C.COVERAGE_MAX_GAP_PAGES else str(g)), f"{ok}/{total}"]
            for g, ok, total in coverage_what_if(ms)]
    return [f"  COVERAGE_MAX_GAP_PAGES (núna {C.COVERAGE_MAX_GAP_PAGES}): hefti-PDF þar sem greinar þekja skrána með í mesta lagi svo mörgum lausum síðum í hverri eyðu",
            *_table(["síður", "þekja í lagi"], rows)]


def _restart_lines(ms: list[IssueManifest]) -> list[str]:
    rows = [[_mark(t, C.PAGE_MAP_RESTART_RATIO), f"{n}/{total}"] for t, n, total in restart_ratio_what_if(ms)]
    missing = _page_map_missing(ms, "runner_up_share", containers_only=True)
    return [f"  PAGE_MAP_RESTART_RATIO (núna {C.PAGE_MAP_RESTART_RATIO}): bindi þar sem næstbesta lesning nær hlutfallinu (kortið gæti orðið tvírætt; efri mörk){_missing_note(missing)}",
            *_table(["hlutfall", "bindi"], rows)]


def _page_map_histogram(ms: list[IssueManifest], key: str, *, containers_only: bool = False) -> str:
    return format_histogram(_page_map_values(ms, key, containers_only=containers_only),
                            missing=_page_map_missing(ms, key, containers_only=containers_only))


def _check_text(a: ArticleEntry) -> str:
    names = list(_CHECK_SHORT) + sorted(set(a.checks) - set(_CHECK_SHORT))
    return " ".join(f"{_CHECK_SHORT.get(k, k)}{'+' if a.checks.get(k) else '-'}" for k in names)


def group_report(name: str, loaded: list[Loaded], *, detail: bool = True) -> list[str]:
    all_ms = [ld.manifest for ld in loaded]
    analysed = [ld for ld in loaded if is_analysed(ld.manifest)]
    ms = [ld.manifest for ld in analysed]
    arts = [a for m in ms for a in m.articles]
    located = sum(1 for a in arts if a.pdf_pages)
    of_total = f" af {len(all_ms)}" if len(all_ms) != len(ms) else ""
    out = [f"\n== {name} ==",
           f"Hefti: {len(ms)} greind{of_total}; staða {dict(sorted(Counter(m.status for m in ms).items()))}",
           f"Greinar: {len(arts)}; staðsettar (pdf_pages): {located}/{len(arts)}"]
    if not ms:
        return out
    out += ["", "Dreifingar (10 hólf, 0–1; hvert heiti er neðri mörk hólfs)",
            f"  öryggi greina        {format_histogram(a.confidence for a in arts)}",
            f"  gæði blaðsíðukorts   {_page_map_histogram(ms, 'quality', containers_only=True)}   (aðeins hefti-PDF)",
            f"  BÍN-hlutfall         {_page_map_histogram(ms, 'bin_ratio')}",
            f"  næstbesta lesning    {_page_map_histogram(ms, 'runner_up_share', containers_only=True)}   (runner_up_share, aðeins hefti-PDF)"]
    out += ["", "Merki (hlutfall greina þar sem athugunin stenst)"]
    for k, (n, total) in signal_composition(arts).items():
        out.append(f"  {k:<20} {n}/{total}" + (f"  ({100 * n / total:.0f}%)" if total else ""))
    out.append(f"  uppruni titla        {title_sources(arts)}")
    out += ["", "Ef þröskuldarnir væru aðrir"]
    for block in (_approve_lines(ms), _quality_lines(ms), _bin_lines(ms), _coverage_lines(ms), _restart_lines(ms)):
        out += [""] + block
    if detail:
        out += ["", f"Lægstu {LOWEST_N} greinar"]
        for key, a in lowest_articles(analysed):
            title = f"„{_one_line(a.title, TITLE_CHARS)}“" if a.title else "(enginn titill)"
            pages = f"bls. {a.page_start}" if a.page_start is not None else "bls. ?"
            note = f"  — {_one_line(a.analysis_notes[0], ERROR_CHARS)}" if a.analysis_notes else ""
            out.append(f"  {a.confidence:.2f}  {key}  {_check_text(a)}  {pages}  {title}{note}")
        flagged = [ld for ld in analysed if ld.manifest.status in ("review", "failed")]
        out += ["", f"Hefti í yfirferð eða misheppnuð: {len(flagged)}"]
        for ld in flagged:
            m = ld.manifest
            low = f", lægst {min(a.confidence for a in m.articles):.2f}" if m.articles else ""
            out.append(f"  {m.status:<7} {ld.key}  ({len(m.articles)} greinar{low})  villa: {_one_line(m.error, ERROR_CHARS) or '-'}")
    return out


class Deviation(NamedTuple):
    source: str
    issue: str                  # the issue's label
    title: str | None           # the article's title (cut to TITLE_CHARS); None for a deviation of the whole issue
    note: str                   # the note without the FRÁVIK prefix
    articles: int = 1           # how many of the issue's articles carry it


def deviations(loaded: list[Loaded]) -> list[Deviation]:
    """Every significant deviation analysis flagged (an analysis note starting with analyze.FRAVIK), in manifest and
    article order. A deviation of the issue as a whole, which analysis writes on every article (the manifest has no
    note of its own), is listed once, for the issue."""
    out: list[Deviation] = []
    for ld in loaded:
        arts = ld.manifest.articles
        seen: set[str] = set()
        for a in arts:
            for n in a.analysis_notes:
                if not n.startswith(FRAVIK) or n in seen:
                    continue
                many = sum(n in b.analysis_notes for b in arts)
                if many > 1:
                    seen.add(n)
                title = None if many > 1 else _one_line(a.title, TITLE_CHARS) or "(enginn titill)"
                out.append(Deviation(ld.manifest.source, ld.manifest.label or ld.key, title,
                                     " ".join(n[len(FRAVIK):].split()), many))
    return out


def _deviation_lines(loaded: list[Loaded]) -> list[str]:
    devs = deviations(loaded)
    per = Counter(d.source for d in devs)
    out = ["", f"== Frávik til yfirferðar: {len(devs)} ==",
           "Greining merkti þessi frávik milli skrár (efnisyfirlits, töflu, Claude, skráarnafns) og prentaðra síðna;",
           "hvert þeirra sendir heftið í yfirferð.",
           "Eftir heimild: " + (", ".join(f"{src} {n}" for src, n in sorted(per.items())) if per else "engin")]
    for d in devs:
        what = f"„{d.title}“" if d.title is not None else f"[allt heftið, {count_word(d.articles, 'grein', 'greinar')}]"
        out.append(f"  {d.issue}  {what}  — {d.note}")
    return out


class TocRow(NamedTuple):
    key: str                    # the manifest's file stem
    issue: str                  # the issue's label
    check: dict                 # page_map["toc_check"] (tocgate.TocCheck.as_dict)
    to_claude: bool             # analysis replaced the list by Claude's, or waits for it (analyze.TOC_GATE_NOTE)
    marked: bool                # listed in --merkja (an audited failure)


TOC_STEPS = (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8)
# Task 23c: ranges of unclaimed pages (low, high, label) and the what-if steps for the page count and the share of the volume
UNCLAIMED_BINS = ((0, 0, "0"), (1, 4, "1–4"), (5, 9, "5–9"), (10, 19, "10–19"), (20, 49, "20–49"), (50, 99, "50–99"),
                  (100, None, "100+"))
UNCLAIMED_PAGE_STEPS = (10, 20, 30, 50, 100)
UNCLAIMED_SHARE_STEPS = (0.1, 0.15, 0.2, 0.25, 0.3, 0.5)


def toc_rows(loaded: list[Loaded], marked: Iterable[Path] = ()) -> list[TocRow]:
    """Every issue whose contents list the parser read (page_map["toc_check"], Task 23b), the lowest share first."""
    marks = {Path(p).resolve() for p in marked}
    rows = []
    for ld in loaded:
        check = ld.manifest.page_map.get("toc_check")
        if not isinstance(check, dict) or _number(check.get("share")) is None:
            continue
        gated = any(n.startswith(TOC_GATE_NOTE) for a in ld.manifest.articles for n in a.analysis_notes)
        rows.append(TocRow(ld.key, ld.manifest.label or ld.key, check, gated,
                           ld.path is not None and ld.path.resolve() in marks))
    return sorted(rows, key=lambda r: (r.check["share"], r.key))


def toc_what_if(rows: list[TocRow], steps: Iterable[float] = TOC_STEPS) -> list[tuple[float, int, int]]:
    """(threshold, lists below it, of them marked): what TOC_MIN_TITLE_SHARE would send to Claude by the share alone."""
    return [(t, sum(r.check["share"] < t for r in rows), sum(r.check["share"] < t and r.marked for r in rows))
            for t in steps]


def _unclaimed(row: TocRow) -> int | None:
    n = _number(row.check.get("unclaimed"))
    return None if n is None else int(n)


def unclaimed_histogram(rows: list[TocRow]) -> list[tuple[str, int, int]]:
    """(range of unclaimed pages, lists, of them sent to Claude) over the lists that have the count (Task 23c)."""
    out = []
    for lo, hi, label in UNCLAIMED_BINS:
        inside = [r for r in rows if (n := _unclaimed(r)) is not None and n >= lo and (hi is None or n <= hi)]
        out.append((label, len(inside), sum(r.to_claude for r in inside)))
    return out


def _fails_on_titles(check: dict) -> bool:
    """The list fails on its titles alone (analyze's gate before Task 23c), from the numbers page_map keeps."""
    found, shifted = _number(check.get("found")) or 0, _number(check.get("shifted")) or 0
    return not check.get("entries") or (_number(check.get("share")) or 0.0) < C.TOC_MIN_TITLE_SHARE or (
        shifted >= 2 and shifted > found)


def unclaimed_what_if(rows: list[TocRow], pages: Iterable[int] = UNCLAIMED_PAGE_STEPS,
                      shares: Iterable[float] = UNCLAIMED_SHARE_STEPS) -> list[tuple[int, list[tuple[int, int]]]]:
    """Per page limit, per share limit: (lists whose unclaimed pages exceed both, of them that pass on their titles, so
    that the limits alone would send them to Claude). A list analysed before Task 23c has no page count and is left out."""
    shares = list(shares)
    measured = [(n, _number(r.check.get("pages")), r) for r in rows if (n := _unclaimed(r)) is not None]
    measured = [(n, p, r) for n, p, r in measured if p]
    out = []
    for limit in pages:
        cells = []
        for share in shares:
            over = [r for n, p, r in measured if n > limit and n > share * p]
            cells.append((len(over), sum(not _fails_on_titles(r.check) for r in over)))
        out.append((limit, cells))
    return out


def _unclaimed_lines(rows: list[TocRow]) -> list[str]:
    out = ["",
           "Síður utan lista (Task 23c): síður með greinatexta sem engin grein listans nær yfir, án fyrstu "
           f"{C.COVERAGE_MAX_GAP_PAGES} síðnanna eftir efnisyfirlitið (kápu- og aðfaraefni) og án síðna með ólæsilegt textalag.",
           "Efnisyfirlit víkur fyrir greinalista Claude þegar þær eru fleiri en TOC_MAX_UNCLAIMED_PAGES og fleiri en "
           "TOC_MAX_UNCLAIMED_SHARE af síðum heftisins eftir kápu og efnisyfirlit."]
    hist = unclaimed_histogram(rows)
    out += _table(["síður utan lista", *[label for label, _, _ in hist]],
                  [["öll hefti", *[str(n) for _, n, _ in hist]], ["þar af → Claude", *[str(g) for _, _, g in hist]]])
    lacking = sum(1 for r in rows if not _number(r.check.get("pages")))
    out += ["", f"  TOC_MAX_UNCLAIMED_PAGES (núna {C.TOC_MAX_UNCLAIMED_PAGES}) og TOC_MAX_UNCLAIMED_SHARE "
                f"(núna {C.TOC_MAX_UNCLAIMED_SHARE}): hefti sem fara yfir bæði mörkin (þar af þau sem standast á titlum, "
                f"þ.e. bættust við)" + (f"; vantar síðufjölda í {lacking} hefti (ekki talin með)" if lacking else "")]
    steps = list(UNCLAIMED_SHARE_STEPS)
    out += _table(["síður \\ hlutfall", *[_mark(x, C.TOC_MAX_UNCLAIMED_SHARE) for x in steps]],
                  [[f"{limit}" + (" *" if limit == C.TOC_MAX_UNCLAIMED_PAGES else ""),
                    *[f"{n} ({new})" for n, new in cells]] for limit, cells in unclaimed_what_if(rows)])
    return out


def _toc_lines(loaded: list[Loaded], marked: Iterable[Path]) -> list[str]:
    rows = toc_rows(loaded, marked)
    out = ["", f"== Efnisyfirlit á síðunum: {len(rows)} hefti með lesið efnisyfirlit ==",
           f"TOC_MIN_TITLE_SHARE (núna {C.TOC_MIN_TITLE_SHARE}): efnisyfirlit þar sem lægra hlutfall titla fannst á "
           "upphafssíðum greinanna víkur fyrir greinalista Claude",
           "(eins líka ef fleiri titlar standa einni grein frá en á sinni síðu, eða ef of margar síður eru utan lista, sjá neðar; "
           "„á auglýsingu“ er aðeins til upplýsingar).",
           "* = merkt í --merkja (endurskoðuð villa); → Claude = greining skipti listanum út eða bíður svars Claude.",
           f"  hlutfall titla   {format_histogram(r.check['share'] for r in rows)}"]
    if not rows:
        return out
    cells = [[_mark(t, C.TOC_MIN_TITLE_SHARE), str(n), str(m)] for t, n, m in toc_what_if(rows)]
    out += _table(["mörk", "undir", "þar af merkt"], cells)
    out += _unclaimed_lines(rows)
    out += ["", "Hefti eitt og eitt, lægsta hlutfall titla fyrst:"]
    for r in rows:
        c = r.check
        parts = [f"{label} {c[k]}" for k, label in (("shifted", "hliðrað"), ("on_filler", "á auglýsingu")) if c.get(k)]
        if c.get("unclaimed"):
            parts.append(f"síður utan lista {c['unclaimed']}" + (f" af {int(c['pages'])}" if _number(c.get("pages")) else ""))
        extra = ", ".join(parts)
        out.append(f"  {'*' if r.marked else ' '} {c['share']:.2f} ({c['found']}/{c['entries']})  {r.issue}  [{r.key}]"
                   f"{f'  {extra}' if extra else ''}{'  → Claude' if r.to_claude else ''}")
    return out


def render_report(loaded: list[Loaded], problems: list[str], contained: list[tuple[str, str]],
                  marked: Iterable[Path] = ()) -> str:
    by_src: dict[str, list[Loaded]] = defaultdict(list)
    for ld in loaded:
        by_src[ld.manifest.source].append(ld)
    out: list[str] = []
    if not any(is_analysed(ld.manifest) for ld in loaded):
        out.append("Engin greind hefti fundust (öll í stöðunni pending); keyrðu analyze fyrst.")
    else:
        for src in sorted(by_src):
            out += group_report(src, by_src[src])
        out += group_report("Allar heimildir", loaded, detail=False)
        out += _deviation_lines(loaded)
        out += _toc_lines(loaded, marked)
    if contained:
        out += ["", f"== Skrár innihaldnar í stærri bindisskrá (contained_in; hlutfall er ekki geymt): {len(contained)} ==",
                *[f"  {p}\n    í: {d}" for p, d in contained]]
    if problems:
        out += ["", f"== Vandamál: {len(problems)} ==", *[f"  {' '.join(p.split())}" for p in problems]]
    return "\n".join(out).lstrip("\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Dreifingar og „ef þröskuldurinn væri“-töflur úr greindum tímaritaskrám (aðeins lestur).")
    ap.add_argument("--manifests", default=str(C.MANIFEST_ROOT), help="mappa með skrám (relative = miðað við rót verkefnisins)")
    ap.add_argument("--only", metavar="SKRÁ", help="textaskrá með einni slóð á manifest í línu (miðað við rót verkefnisins); aðeins þau eru tekin með")
    ap.add_argument("--unmatched", default=str(C.UNMATCHED_PATH), help="_unmatched.yaml (contained_in-línur)")
    ap.add_argument("--merkja", metavar="SKRÁ", help="textaskrá með slóðum á manifest (eins og --only) sem eru merkt með * "
                                                      "í dreifingu efnisyfirlita, t.d. endurskoðaðar villur")
    args = ap.parse_args(argv)
    try:
        only = read_paths_file(_abs(args.only)) if args.only else None
        marked = read_paths_file(_abs(args.merkja)) if args.merkja else []
    except ValueError as e:
        print(f"Villa: {e}", file=sys.stderr)
        return 1
    loaded, problems = load_manifests(_abs(args.manifests), only)
    try:
        contained = contained_in(_abs(args.unmatched))
    except ValueError as e:
        contained = []
        problems.append(str(e))
    print(render_report(loaded, problems, contained, marked))
    return 1 if problems or not any(is_analysed(ld.manifest) for ld in loaded) else 0


if __name__ == "__main__":
    sys.exit(main())
