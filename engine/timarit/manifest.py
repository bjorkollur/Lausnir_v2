"""The per-issue review file (spec §7.2) and the rules around it: confidence,
status, identity, and the merge that keeps the user's corrections."""
from __future__ import annotations

import datetime
import hashlib
import math
import os
import re
import tempfile
import unicodedata
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

import yaml

from engine.config.sources import SourceConfig, get_config
from engine.processors.renderer import citable_issue
from engine.timarit import config as C

USER = "user"
STATUSES = ("pending", "review", "approved", "imported", "failed")
# "pdf_metadata": a date that is only the year, taken from the PDF's metadata (Task 22d).
PROVENANCE_LABELS = frozenset({"xlsx", "leitir", "toc", "filename", "page", "llm", "pdf_metadata", USER})


@dataclass
class ArticleEntry:
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    author_titles: list[str] = field(default_factory=list)
    article_type: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    issue: str | None = None            # None → the manifest's issue; set when this article's own issue differs
    pdf_pages: list[int] | None = None
    lang: str | None = None
    date: str | None = None
    summary: str | None = None
    keywords: list[str] = field(default_factory=list)
    provenance: dict[str, str] = field(default_factory=dict)
    checks: dict[str, bool] = field(default_factory=dict)
    confidence: float = 0.0
    leitir_record_id: str | None = None
    xlsx_row: int | None = None
    source_file: str | None = None
    note: str = ""                      # the user's; analyze never writes it
    analysis_notes: list[str] = field(default_factory=list)   # analyze's findings; replaced on every run (R33)


@dataclass
class IssueManifest:
    source: str
    year: int | None
    volume: int | None
    issue: str | None
    label: str
    file: str | None = None
    sha256: str | None = None
    pages: int | None = None
    year_end: int | None = None
    # Task 23e: the PDF pages of `file` that are this issue's (0-based, inclusive), when the file holds more than one
    # volume (config.SPLIT_FILES); None: the whole file. Analysis reads only these pages; every PDF page index the
    # manifest stores (pdf_pages, page_map) is still the whole file's, so import cuts from the file as before.
    pdf_window: list[int] | None = None
    page_map: dict = field(default_factory=dict)
    status: str = "pending"
    confidence: float = 0.0
    error: str | None = None
    files: list[str] = field(default_factory=list)
    articles: list[ArticleEntry] = field(default_factory=list)


def _x(v) -> str:
    return "x" if v is None else str(v)


def manifest_path(root: Path, source: str, year, volume, issue) -> Path:
    return root / source / f"{_x(year)}_{_x(volume)}_{_x(issue)}.yaml"


def issue_label(cfg: SourceConfig, year, volume, issue) -> str:
    """'Name, 60. árg. 1. hefti (2010)'; any part without a value is left out
    (the year too: some sources have none after discovery), and so is an issue key that is not a number
    ("afmaelisrit" → 'Úlfljótur (2007)'; see renderer.citable_issue)."""
    name = cfg.citation_name or cfg.display_name
    bib = []
    if volume is not None:
        bib.append(f"{volume}. árg.")
    if citable_issue(issue) and cfg.issue_term:
        bib.append(f"{issue}. {cfg.issue_term}")
    label = f"{name}, {' '.join(bib)}" if bib else name
    return f"{label} ({year})" if year is not None else label


def _iso_date(v):
    """yaml.safe_load turns an unquoted `date: 2022-01-21` into datetime.date;
    the rest of the pipeline wants the ISO string."""
    if isinstance(v, datetime.datetime):
        return v.date().isoformat()
    if isinstance(v, datetime.date):
        return v.isoformat()
    return v


# --- loading: the file is hand-edited, so load_manifest is the validation boundary --------------------
# Each coercer takes the raw YAML value and returns the clean one, or raises ValueError(reason);
# the caller adds the file, article index and key.

def _opt_str(v):
    if v is None:
        return None
    if isinstance(v, bool):
        # `title: No` is False to YAML, and str(False) would silently store "False"
        raise ValueError(f"{v!r} is a YAML boolean, not text; quote the value (e.g. 'No') if it is meant as text")
    if isinstance(v, datetime.date):
        return _iso_date(v)
    if isinstance(v, str):
        return v
    if isinstance(v, (int, float)):
        return str(v)
    raise ValueError(f"expected text, got {v!r}")


def _req_str(v):
    if v is None:
        raise ValueError("must not be empty")
    return _opt_str(v)


def _note(v):
    return "" if v is None else _opt_str(v)


def _opt_int(v):
    if v is None:
        return None
    if isinstance(v, bool):
        raise ValueError(f"{v!r} is not a whole number")
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        if re.fullmatch(r"[+-]?\d+", s):
            return int(s)
    raise ValueError(f"{v!r} is not a whole number")


def _issue_str(v):
    """`issue` is text as written ("1", "3-4"); an unquoted `issue: 1` arrives as an int."""
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (str, int)):
        raise ValueError(f"{v!r} is not an issue designation")
    s = str(v).strip()
    return s or None


def _float(v):
    if v is None:
        return 0.0
    if isinstance(v, bool):
        raise ValueError(f"{v!r} is not a number")
    try:
        f = float(v.strip() if isinstance(v, str) else v)
    except (TypeError, ValueError):
        raise ValueError(f"{v!r} is not a number") from None
    if not math.isfinite(f):
        raise ValueError(f"{v!r} is not a finite number")
    return f


def _str_list(v):
    """A scalar where a list belongs (`authors: Jón Jónsson`) is a one-element list."""
    if v is None:
        return []
    if isinstance(v, str):
        return [v] if v.strip() else []
    if isinstance(v, (list, tuple)):
        if any(x is None or isinstance(x, (list, tuple, dict, set)) for x in v):
            raise ValueError(f"{v!r} must be a list of text values")
        return [_opt_str(x) for x in v]
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return [str(v)]
    raise ValueError(f"{v!r} must be a list of text values")


def _mapping(v):
    if v is None:
        return {}
    if not isinstance(v, dict):
        raise ValueError(f"{v!r} must be a mapping")
    return dict(v)


def _status(v):
    if v is None:
        return "pending"
    s = v.strip().lower() if isinstance(v, str) else v
    if s not in STATUSES:
        raise ValueError(f"{v!r} is not one of {', '.join(STATUSES)}")
    return s


def _pdf_pages(v):
    if v is None:
        return None
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        raise ValueError(f"{v!r} must be a list of two page indexes [first, last]")
    first, last = (_opt_int(x) for x in v)
    if first is None or last is None or first < 0 or last < first:
        raise ValueError(f"{v!r} must be two page indexes [first, last] with 0 <= first <= last")
    return [first, last]


def _to_bool(b):
    if isinstance(b, bool):
        return b
    if isinstance(b, int) and b in (0, 1):
        return bool(b)
    if isinstance(b, str) and b.strip().lower() in ("true", "yes", "1", "false", "no", "0"):
        return b.strip().lower() in ("true", "yes", "1")
    raise ValueError(f"{b!r} is not true or false")


def _checks(v):
    if v is None:
        return {}
    if not isinstance(v, dict):
        raise ValueError(f"{v!r} must be a mapping of check name to true/false")
    return {str(k): _to_bool(b) for k, b in v.items()}


# A provenance key names the field a value came from; "pages" stands for page_start, page_end and pdf_pages.
# analysis_notes has no provenance: it is analyze's own output and is replaced on every run.
_PROVENANCE_ALIASES = {"pages": ("page_start", "page_end", "pdf_pages")}
_PROVENANCE_KEYS = (frozenset(f.name for f in fields(ArticleEntry)) - {"analysis_notes"}) | frozenset(_PROVENANCE_ALIASES)


def _provenance(v):
    if v is None:
        return {}
    if not isinstance(v, dict):
        raise ValueError(f"{v!r} must be a mapping of field to source")
    out = {}
    for key, src in v.items():
        if key not in _PROVENANCE_KEYS:
            raise ValueError(f"unknown field {key!r}")
        if not isinstance(src, str):
            raise ValueError(f"source of {key!r} must be text, got {src!r}")
        parts = [p.strip().lower() for p in src.split("+")]
        if any(p not in PROVENANCE_LABELS for p in parts):
            raise ValueError(f"unknown source {src!r} for {key!r} (allowed: {', '.join(sorted(PROVENANCE_LABELS))}, combined with '+')")
        out[key] = "+".join(parts)
    return out


_ISSUE_COERCE = {
    "source": _req_str, "year": _opt_int, "volume": _opt_int, "issue": _issue_str, "label": _req_str,
    "file": _opt_str, "sha256": _opt_str, "pages": _opt_int, "year_end": _opt_int, "pdf_window": _pdf_pages,
    "page_map": _mapping,
    "status": _status, "confidence": _float, "error": _opt_str, "files": _str_list,
}
_ARTICLE_COERCE = {
    "title": _opt_str, "authors": _str_list, "author_titles": _str_list, "article_type": _opt_str,
    "page_start": _opt_int, "page_end": _opt_int, "issue": _issue_str, "pdf_pages": _pdf_pages,
    "lang": _opt_str, "date": _opt_str,
    "summary": _opt_str, "keywords": _str_list, "provenance": _provenance, "checks": _checks, "confidence": _float,
    "leitir_record_id": _opt_str, "xlsx_row": _opt_int, "source_file": _opt_str, "note": _note,
    "analysis_notes": _str_list,
}


def _check_keys(cls, raw: dict, path: Path, where: str) -> None:
    known = {f.name for f in fields(cls)}
    unknown = sorted(str(k) for k in raw if k not in known)
    if unknown:
        # Dropping an unknown key silently could lose a correction the user typed.
        raise ValueError(f"{path}: unknown key(s) {', '.join(repr(k) for k in unknown)} in {where}")


def _coerce(table: dict, raw: dict, path: Path, where: str) -> dict:
    out = {}
    for name, value in raw.items():
        try:
            out[name] = table[name](value)
        except ValueError as e:
            prefix = f"{where}: " if where else ""
            raise ValueError(f"{path}: {prefix}{name}: {e}") from None
    return out


def _article_from_dict(raw, path: Path, idx: int) -> ArticleEntry:
    where = f"article #{idx + 1}"
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: {where} is not a mapping")
    _check_keys(ArticleEntry, raw, path, where)
    return ArticleEntry(**_coerce(_ARTICLE_COERCE, raw, path, where))


def load_manifest(path: Path) -> IssueManifest:
    """Read a review file. Hand edits are normalised here (numbers, text, lists, status, provenance) or
    refused with a ValueError naming the file, the article and the key, so nothing downstream sees
    a '7', a bare author string or a status spelled 'Approved'."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ValueError(f"{path}: not valid YAML: {e}") from e
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping at the top level")
    data = dict(data)
    raw_articles = data.pop("articles", None) or []
    if not isinstance(raw_articles, list):
        raise ValueError(f"{path}: 'articles' must be a list")
    _check_keys(IssueManifest, data, path, "the issue")
    missing = [k for k in ("source", "year", "volume", "issue", "label") if k not in data]
    if missing:
        raise ValueError(f"{path}: missing required key(s) {', '.join(repr(k) for k in missing)}")
    fields_ = _coerce(_ISSUE_COERCE, data, path, "")
    arts = [_article_from_dict(a, path, i) for i, a in enumerate(raw_articles)]
    return IssueManifest(**fields_, articles=arts)


def _write_atomic(path: Path, text: str) -> None:
    """Write `text` via a temp file in the same directory, fsync it and os.replace it over `path`: a crash or a
    full disk leaves the old file whole (never a truncated YAML), and the temp file is removed on failure. The
    file keeps the mode of the one it replaces (0644 for a new one) instead of mkstemp's private 0600."""
    try:
        mode = path.stat().st_mode & 0o777
    except FileNotFoundError:
        mode = 0o644
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def save_manifest(m: IssueManifest, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(m)
    if data["pdf_window"] is None:          # only a part of a split file has one: any other is saved as before
        del data["pdf_window"]
    for a in data["articles"]:
        a["date"] = _iso_date(a["date"])     # a str is dumped quoted, so it round-trips as a str
    _write_atomic(path, yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120))


def manifest_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def article_confidence(checks: dict[str, bool], *, llm: bool = False) -> float:
    # Rounded: 0.35 + 0.05 is 0.39999999999999997 in floating point, which would miss a threshold it equals.
    score = round(sum(w for k, w in C.WEIGHTS.items() if checks.get(k)), 6)
    return min(score, C.LLM_CONFIDENCE_CAP) if llm else score


def _coverage_ok(m: IssueManifest) -> bool:
    """For an issue PDF: the articles must account for (nearly) all of it. At most COVERAGE_MAX_GAP_PAGES
    pages may lie before the first article, between two articles and after the last one; an article
    whose pages are unknown cannot vouch for anything. Pages analysis found to be no article's (adverts, blank pages:
    page_map["non_article"], Task 22c) are no gap. Article-granularity issues have no such file. A part of a file that
    holds more than one volume (pdf_window, Task 23e) is measured from the first to the last page of its window."""
    if m.file is None:
        return True
    if any(not a.pdf_pages for a in m.articles):
        return False
    spans = sorted(a.pdf_pages for a in m.articles)
    if not spans:
        return True
    gap = C.COVERAGE_MAX_GAP_PAGES
    outside = m.page_map.get("non_article") or []
    covered = {p for p in outside if isinstance(p, int) and not isinstance(p, bool)}
    for s, e in spans:
        covered.update(range(s, e + 1))
    first, last = min(covered), max(covered)
    bounds = m.pdf_window or ([0, m.pages - 1] if m.pages is not None else None)
    if bounds is not None:
        if first - bounds[0] > gap:                             # pages before the first article
            return False
        if bounds[1] - last > gap:                              # pages after the last article
            return False
    run = 0
    for p in range(first, last + 1):
        run = 0 if p in covered else run + 1
        if run > gap:
            return False
    return True


def issue_status(m: IssueManifest) -> str:
    if not m.articles:
        return "review"
    if all(a.confidence >= C.APPROVE_THRESHOLD for a in m.articles) and _coverage_ok(m):
        return "approved"
    return "review"


_TRANSLIT = str.maketrans({"ð": "d", "Ð": "D", "þ": "th", "Þ": "Th", "æ": "ae", "Æ": "Ae"})


def _slug(s: str, n: int = 40) -> str:
    # NFKD + ASCII fold keeps the base letter of á é í ó ú ý ö; ð þ æ have none, so they are spelled out first.
    s = unicodedata.normalize("NFKD", (s or "").translate(_TRANSLIT)).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:n].rstrip("-") or "x"


def _stem(a: ArticleEntry) -> str | None:
    return Path(a.source_file).stem if a.source_file else None


def _is_standalone(m: IssueManifest) -> bool:
    """A work that belongs to no journal (fraedigreinar_ymsar: no citation_name) and has no issue PDF."""
    try:
        return get_config(m.source).citation_name is None and not m.file
    except ValueError:      # unknown source: treat as an ordinary journal
        return False


def article_issue(m: IssueManifest, a: ArticleEntry) -> str | None:
    """The issue an article belongs to: its own `issue` when set (a volume file whose articles come from
    several issues, R31), else the manifest's."""
    return a.issue if a.issue is not None else m.issue


def date_precision(a: ArticleEntry) -> str:
    """'day' or 'year' (raw_api_data.date_precision, spec 5.1). A date is a whole day when it is printed on the page
    (label `page`, also in a combination such as `page+user`), or when the user set it and it is not the 1st of January;
    the year taken from the PDF's metadata (`pdf_metadata`, kept as YYYY-01-01 because document_date is a date) is a
    year, and so is the YYYY-01-01 a user writes when confirming or correcting an undated article's year (R44). A date
    with no provenance at all is a whole day (manifests from before provenance was kept); anything else is a year."""
    if not a.date:
        return "year"
    if "date" not in a.provenance:
        return "day"
    parts = {p.strip().lower() for p in str(a.provenance["date"]).split("+")}
    if "page" in parts or (USER in parts and str(a.date)[5:10] != "01-01"):
        return "day"
    return "year"


def external_id(m: IssueManifest, a: ArticleEntry) -> str:
    """Identity of an article inside its source (documents.external_id). Page-bearing articles are
    '{year}-{volume}-{issue}-{page_start}' (the article's own issue when it has one), vefrit '{date}-{slug}' ('{year}-{slug}'
    when only the year is known), standalone works the slug of their file stem. Two articles can still share an id
    (same start page, same title); use assign_external_ids for a whole manifest."""
    if _is_standalone(m):
        return _slug(_stem(a) or a.title or "")
    if a.date and a.page_start is None:
        return f"{a.date if date_precision(a) == 'day' else a.date[:4]}-{_slug(a.title or '')}"
    issue = article_issue(m, a)
    if a.page_start is not None:
        return f"{_x(m.year)}-{_x(m.volume)}-{_x(issue)}-{a.page_start}"
    return f"{_x(m.year)}-{_x(m.volume)}-{_x(issue)}-{_slug(a.title or _stem(a) or '')}"


def assign_external_ids(m: IssueManifest) -> list[str]:
    """external_id of every article, in article order, with duplicates inside the manifest numbered
    '-2', '-3', ... (the first keeps the plain id). UNIQUE (source_id, external_id) upserts would otherwise
    overwrite one article with the next."""
    ids: list[str] = []
    used: set[str] = set()
    seen: dict[str, int] = {}
    for a in m.articles:
        base = external_id(m, a)
        n = seen.get(base, 1)
        cand = base
        while cand in used:
            n += 1
            cand = f"{base}-{n}"
        seen[base] = n
        used.add(cand)
        ids.append(cand)
    return ids


# --- merge ----------------------------------------------------------------------------------------

def _fold(s: str | None) -> str:
    return re.sub(r"\W+", " ", unicodedata.normalize("NFC", s or "").casefold()).strip()


def _is_user(prov) -> bool:
    return USER in [p.strip().lower() for p in str(prov).split("+")]


def _same_page(o: ArticleEntry, n: ArticleEntry) -> bool:
    """Same start page in the same issue: page numbers restart per issue in some volumes (Úlfljótur 1–12)."""
    return o.page_start is not None and o.page_start == n.page_start and o.issue == n.issue


# Matching passes, strongest identity first. Each old article is matched at most once and so is each new one.
_MATCH_PASSES = (
    lambda o, n: bool(o.source_file) and o.source_file == n.source_file,
    lambda o, n: o.xlsx_row is not None and o.xlsx_row == n.xlsx_row,
    lambda o, n: bool(o.leitir_record_id) and o.leitir_record_id == n.leitir_record_id,
    lambda o, n: _same_page(o, n) and bool(_fold(o.title)) and _fold(o.title) == _fold(n.title),
    lambda o, n: _same_page(o, n),
    lambda o, n: bool(_fold(o.title)) and _fold(o.title) == _fold(n.title),
)


def _pair_articles(old: list[ArticleEntry], new: list[ArticleEntry]) -> tuple[dict[int, int], list[int]]:
    """({new index: old index}, old indexes left unmatched in old order)"""
    pairs: dict[int, int] = {}
    free = list(range(len(old)))
    for same in _MATCH_PASSES:
        for ni, n in enumerate(new):
            if ni in pairs:
                continue
            for oi in free:
                if same(old[oi], n):
                    pairs[ni] = oi
                    free.remove(oi)
                    break
    return pairs, free


def _carry_user_values(oa: ArticleEntry, na: ArticleEntry) -> None:
    for key, prov in oa.provenance.items():
        if not _is_user(prov):
            continue
        for fld in _PROVENANCE_ALIASES.get(key, (key,)):
            if hasattr(na, fld):
                setattr(na, fld, getattr(oa, fld))
        na.provenance[key] = prov
    if oa.note:                     # the note is the user's (R33); analyze writes analysis_notes, which the new run replaces
        na.note = oa.note


def _precedes(u: ArticleEntry, r: ArticleEntry) -> bool:
    if u.page_start is not None and r.page_start is not None:
        return u.page_start < r.page_start
    if u.pdf_pages and r.pdf_pages:
        return u.pdf_pages[0] < r.pdf_pages[0]
    return False


def merge_preserving_user(old: IssueManifest | None, new: IssueManifest) -> IssueManifest:
    """New analysis never overwrites what the user wrote.

    Old and new articles are paired one-to-one (source_file, xlsx_row, leitir_record_id, then start page with
    title, start page, title; a start page only pairs articles of the same article-level issue). On a pair,
    every field whose provenance carries 'user' keeps the old value ('pages' covers page_start, page_end and
    pdf_pages), the user's note carries over, and analysis_notes are the new analysis's. An old article that no new one matches is kept, in page order, when
    it carries any user provenance, so a hand-added article, or one whose title and start page the user both
    corrected, survives; other unmatched old articles are dropped (the analysis no longer finds them). The flip side: an article the user deleted from the file comes back when
    analyze re-runs on an issue that is not yet approved. An issue the user approved (or that was imported)
    keeps that status."""
    if old is None:
        return new
    pairs, unmatched = _pair_articles(old.articles, new.articles)
    for ni, oi in pairs.items():
        _carry_user_values(old.articles[oi], new.articles[ni])
    articles = list(new.articles)
    for oi in unmatched:
        u = old.articles[oi]
        if any(_is_user(p) for p in u.provenance.values()):
            articles.insert(next((i for i, r in enumerate(articles) if _precedes(u, r)), len(articles)), u)
    new.articles = articles
    if old.status in ("approved", "imported"):
        new.status = old.status
    return new
