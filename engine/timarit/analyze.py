"""Per-issue analysis (spec §5): the article list from the best registry for the source, each article
located in the PDF, verified against its first page and scored.

Pure orchestration: the archive, leitir.is and Claude are reached only through AnalyzeDeps, and
nothing is written. The caller's manifest is not modified; a new one is returned.

Registry per source (spec §5.1):
- ulfljotur: the user's spreadsheet. Article PDFs (one per article) are matched one by one on their
  first page (R23); issue PDFs (2002–) take the sheet's rows for their volume and issue, and read
  their own TOC when the sheet has none (vol 76 issues 3–4, vol 77).
- stjornmal_stjornsysla: one PDF per article; page range from the file name, checked against the
  printed numbers (two names carry typos); title, authors, issue and year from the first page (R5).
- ulfljotur_vefrit, fraedigreinar_ymsar: one PDF per work; title from the file name, publication date
  and authors from the first page, no printed pages.
- every other issue or volume PDF: its own TOC (page-first or page-last by source and file kind, R22),
  enriched with leitir.is records for timarit_logfraedinga (R26); Claude reads the TOC when the
  parser finds nothing (provenance llm, confidence capped, issue in review).

Every machine finding goes to the articles' `analysis_notes` (R33; `note` is the user's). Issue-level findings
the reviewer must see (a year or volume taken from a formula, a folder name that disagrees with the printed
masthead) go on every article, since the manifest has no note of its own; a fallback of that kind also sends
the issue to review.
"""
from __future__ import annotations

import copy
import datetime
import difflib
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, NamedTuple

import fitz

from engine.config.sources import SourceConfig
from engine.timarit import config as C
from engine.timarit.article_type import infer_article_type
from engine.timarit.authors import _is_initial, _is_name_token, clean_author, looks_like_person
from engine.timarit.extract import bin_known_ratio, bin_known_share, has_text_layer
from engine.timarit.lang import detect_lang
from engine.timarit.leitir_articles import LeitirArticle, best_match
from engine.timarit.llm_toc import ARTICLE_TYPES
from engine.timarit.manifest import ArticleEntry, IssueManifest, article_confidence, issue_label, issue_status
from engine.timarit.naming import FileIdentity, identify, nfc
from engine.timarit.pagemap import PATTERNS, PageMap, build_page_map, read_page_numbers
from engine.timarit.splitter import sha256_bytes
from engine.timarit.toc import TocEntry, _nominative, find_toc_pages, parse_toc, toc_style
from engine.timarit.ulfljotur_xlsx import (
    XlsxArticle, _fold, articles_for, masthead_issue, masthead_year, match_first_page, volume_for_year,
    year_for_volume,
)

_FROZEN = ("approved", "imported")           # statuses only the user or import moves
_LEITIR_SOURCES = frozenset({"timarit_logfraedinga"})
NO_TEXT_LAYER = "ekkert textalag — þarf OCR"
NO_ARTICLE_LIST = "enginn greinalisti: efnisyfirlit fannst ekki eða var ólæsilegt"
# Notes that analysis writes when it fell back to a formula (and forced the issue to review); the pilot report reads
# them back from analysis_notes, so they are named here, not repeated there.
FORMULA_VOLUME_NOTE = "árgangur reiknaður út frá ári"
FORMULA_YEAR_NOTE = "ár reiknað út frá"
# A note starting with one of these means analysis sent the issue to review whatever the scores say.
FORCED_REVIEW_NOTES = (NO_TEXT_LAYER, FORMULA_VOLUME_NOTE, FORMULA_YEAR_NOTE)
_LLM_TOC_PAGES = 4                            # pages sent to Claude when the TOC parser finds nothing
_BIN_SAMPLE_PAGES = 20                        # pages (container) or files (article folder) behind page_map["bin_ratio"]


@dataclass
class AnalyzeDeps:
    """The outside world, injected so tests need no archive, network or LLM."""
    read_bytes: Callable[[str], bytes]                                          # archive-relative path → PDF bytes
    leitir: Callable[[str, str | None], Awaitable[list[LeitirArticle]]] | None  # (query, journal) → records
    llm_toc: Callable[[str], Awaitable[list[TocEntry]]] | None                  # TOC text → entries (Claude)
    xlsx_rows: list[XlsxArticle]                                                # the Úlfljótur spreadsheet


@dataclass
class _Run:
    """Issue-level findings of one analysis."""
    notes: list[str] = field(default_factory=list)   # appended to every article's note
    review: bool = False                             # a fallback was used: review whatever the scores say
    llm: bool = False                                # the article list came from Claude

    def flag(self, note: str, *, review: bool = False) -> None:
        if note not in self.notes:
            self.notes.append(note)
        self.review = self.review or review


def _add_analysis_note(a: ArticleEntry, note: str) -> None:
    """Machine findings go to analysis_notes; `note` belongs to the user (R33)."""
    if note and note not in a.analysis_notes:
        a.analysis_notes.append(note)


def _page_texts(pdf: bytes) -> list[str]:
    with fitz.open(stream=pdf, filetype="pdf") as d:
        return [p.get_text() for p in d]


# --- verification --------------------------------------------------------------------------------------

_HEAD_CHARS = 1500       # titles and bylines stand at the top of an article's first page
_SHORT_NEEDLE = 12       # folded needles up to this length must occur as whole words, never fuzzily
_HYPHEN_BREAK = re.compile(r"(\w)-[ \t]*\n[ \t]*(\w)")


def _on_page(needle: str | None, page_text: str, threshold: float) -> bool:
    """Is `needle` printed near the top of the page? Folded text (case, accents, ð/þ/æ, punctuation);
    an exact whole-word hit, or for a needle longer than _SHORT_NEEDLE a needle-sized window starting at a
    word that is at least `threshold` similar (OCR errors, line breaks inside the title)."""
    n = _fold(needle or "")
    if not n:
        return False
    head = _fold(_HYPHEN_BREAK.sub(r"\1\2", page_text[:_HEAD_CHARS].replace("\xad", "")))
    if f" {n} " in f" {head} ":
        return True
    if len(n) <= _SHORT_NEEDLE:
        return False
    sm = difflib.SequenceMatcher(None, autojunk=False)
    sm.set_seq2(n)
    for i in [0] + [k + 1 for k, ch in enumerate(head) if ch == " "]:
        sm.set_seq1(head[i:i + len(n)])
        if sm.real_quick_ratio() >= threshold and sm.quick_ratio() >= threshold and sm.ratio() >= threshold:
            return True
    return False


def verify_article(a: ArticleEntry, first_page_text: str, printed_page: int | None) -> dict[str, bool]:
    """The four signals of spec §5.4 for one article. `printed_page` is the page number the located first
    page carries (read there, or inferred from a trustworthy page map), None when unknown."""
    title_sources = set(a.provenance.get("title", "").split("+"))
    return {
        "title_on_page": _on_page(a.title, first_page_text, C.TITLE_ON_PAGE_MIN),
        "printed_page_match": a.page_start is not None and printed_page == a.page_start,
        "author_on_page": any(_on_page(x, first_page_text, C.AUTHOR_ON_PAGE_MIN) for x in a.authors),
        "external_record": bool(title_sources & {"leitir", "xlsx"}) or a.leitir_record_id is not None
                           or a.xlsx_row is not None,
    }


# --- location in an issue PDF ----------------------------------------------------------------------------

def locate_articles(entries: list[ArticleEntry], page_map: PageMap, page_texts: list[str]) -> None:
    """Fill pdf_pages in place from each entry's printed start page: from the PDF page carrying it to the
    page before the next located article; the last one runs to the last page with any text (spec §5.2).
    An entry whose start is unknown or not in the document gets no range, and a note when it had a start,
    instead of raising: beyond the document's numbers, a number printed on two pages, a gap between two segments
    of a piecewise map, or an ambiguous map. A page_end that is still unknown becomes the printed number of the
    range's last numbered page (the last article of a hefti in an annual volume runs over the next hefti's
    unnumbered cover pages)."""
    last_text = max((i for i, t in enumerate(page_texts) if t.strip()), default=len(page_texts) - 1)
    numbered = [p for p in page_map.printed if p is not None]
    located: list[tuple[int, ArticleEntry]] = []
    for a in entries:
        idx = page_map.pdf_index_for(a.page_start) if a.page_start is not None else None
        if idx is None:
            a.pdf_pages = None
            if a.page_start is None:
                continue
            if page_map.offset is None:
                _add_analysis_note(a, f"upphafsbls. {a.page_start} fannst ekki: blaðsíðutal skjalsins óljóst")
            elif a.page_start in page_map.contested:                         # printed on two pages (pagemap._contested)
                _add_analysis_note(a, f"upphafsbls. {a.page_start} fannst ekki: blaðsíðutalið óljóst "
                                      "(talan stendur á fleiri en einni síðu)")
            elif numbered and numbered[0] < a.page_start < numbered[-1]:   # between two segments of the map
                _add_analysis_note(a, f"upphafsbls. {a.page_start} er á engri síðu skjalsins (eyða í blaðsíðutali)")
            else:
                _add_analysis_note(a, f"upphafsbls. {a.page_start} er utan skjals")
            continue
        located.append((idx, a))
    located.sort(key=lambda t: t[0])
    for k, (idx, a) in enumerate(located):
        end = located[k + 1][0] - 1 if k + 1 < len(located) else last_text
        a.pdf_pages = [idx, max(idx, end)]
        if a.page_end is None:
            a.page_end = next((p for p in reversed(page_map.printed[idx:a.pdf_pages[1] + 1]) if p is not None), None)


def _printed_at(pm: PageMap, idx: int) -> int | None:
    """Evidence for the printed number of PDF page idx: read on that very page, or inferred from a map
    good enough to trust. (Location itself uses the map, so an untrusted map proves nothing.)"""
    return pm.printed[idx] if pm.read[idx] or pm.quality >= C.PAGE_MAP_MIN_QUALITY else None


class _Doc(NamedTuple):
    """One article PDF: its page texts and the printed numbers of its first and last page (None when unknown)."""
    rel: str
    texts: list[str]
    first_printed: int | None
    last_printed: int | None

    @property
    def first_page(self) -> str:
        return self.texts[0] if self.texts else ""


def _article_doc(rel: str, texts: list[str], source: str) -> _Doc:
    """R7: the number read on the first page, else the article's own page map when it fits well enough (one- and
    two-page articles cannot fit one). The last page's number comes from that map only. The read number comes
    first because it is on the very page: some Stjórnmál og stjórnsýsla PDFs lack a page after the first, so
    their map puts the first page one too high."""
    if not texts:
        return _Doc(rel, texts, None, None)
    pm = build_page_map(texts, source)
    trusted = pm.offset is not None and pm.quality >= C.PAGE_MAP_MIN_QUALITY
    read0 = read_page_numbers(texts[:1], PATTERNS.get(source, PATTERNS["default"]))[0]
    first = read0 if read0 is not None else (pm.printed[0] if trusted else None)
    return _Doc(rel, texts, first, pm.printed[-1] if trusted else None)


# --- volume and year ---------------------------------------------------------------------------------------

_ARG = re.compile(r"\b(\d{1,3})\.\s*árg(?:angur|\.|\b)", re.I)
# "1. tbl. 1. árg. 2005", "1. tbl.,7. árg. 2011", "1. tölublað – 4. árgangur – 2010" (spec §5.1)
_TBL_ARG_YEAR = re.compile(
    r"(?:\b(\d{1,2})\.\s*(?:tbl|tölublað)\.?\s*[,–-]?\s*)?\b(\d{1,3})\.\s*árg(?:angur)?\.?\s*[,–-]?\s*((?:19|20)\d\d)\b",
    re.I)
_VOLUME_SLACK = 2        # a printed volume this far from the base-year formula is still believed


def _printed_volume(pages: list[str], cfg: SourceConfig, year: int | None) -> int | None:
    """The first "N. árg." / "N. árgangur" on the given pages that fits the year (when the source has a base year)."""
    for text in pages:
        for mt in _ARG.finditer(text):
            v = int(mt.group(1))
            if v >= 1 and (cfg.volume_base_year is None or year is None
                           or abs(year - cfg.volume_base_year + 1 - v) <= _VOLUME_SLACK):
                return v
    return None


def _fallback_volume(m: IssueManifest, cfg: SourceConfig, run: _Run) -> None:
    """Volume from the year when nothing printed or external gave it: flagged for review (spec §3)."""
    if m.volume is not None or m.year is None:
        return
    if cfg.short_name == "ulfljotur":
        if m.issue is None or not re.fullmatch(r"\d+(?:-\d+)?", m.issue):
            return                                   # the anniversary issue has no numbered volume
        try:
            v = volume_for_year(m.year)
        except ValueError:
            return
    elif cfg.volume_base_year is not None:
        v = m.year - cfg.volume_base_year + 1
    else:
        return
    m.volume = v
    run.flag(f"{FORMULA_VOLUME_NOTE}: {v}. árg. ({m.year}); ekkert prentað árgangsnúmer fannst", review=True)


def _printed_issue_volume_year(pages: list[str]) -> tuple[str | None, int | None, int | None]:
    for text in pages:
        if mt := _TBL_ARG_YEAR.search(text):
            return mt.group(1), int(mt.group(2)), int(mt.group(3))
    return None, None, None


# --- entries from a registry ------------------------------------------------------------------------------

def _row_entry(r: XlsxArticle, m: IssueManifest) -> ArticleEntry:
    a = ArticleEntry(title=r.title, authors=list(r.authors), page_start=r.page_start, xlsx_row=r.row,
                     provenance={"title": "xlsx", "authors": "xlsx", "pages": "xlsx"})
    if r.issue != m.issue:                      # R31: a volume file, or a double issue the file names singly
        a.issue, a.provenance["issue"] = r.issue, "xlsx"
    return a


def _toc_entries(toc: list[TocEntry], m: IssueManifest, cfg: SourceConfig, prov: str) -> list[ArticleEntry]:
    out = []
    for t in toc:
        names, titles = [], []
        for raw in t.authors:
            name, tt = clean_author(raw)
            if name:
                names.append(name)
            if tt:
                titles.append(tt)
        kind = t.section if prov == "llm" and t.section in ARTICLE_TYPES else \
            infer_article_type(t.section, t.title, cfg.verdict_type_default)
        a = ArticleEntry(title=t.title, authors=names, author_titles=titles, article_type=kind,
                         page_start=t.page_start, provenance={"title": prov, "article_type": prov})
        if names:
            a.provenance["authors"] = prov
        if t.page_start is not None:
            a.provenance["pages"] = prov
        if t.issue is not None and m.issue is None:   # an annual volume's TOC is split by "N. hefti"
            a.issue, a.provenance["issue"] = t.issue, prov
        out.append(a)
    return out


def _container_kind(m: IssueManifest) -> str:
    """The file kind toc_style needs (R22): "issue" or "volume" from the file name; a path that names no
    archive pattern is a volume when the manifest has no issue."""
    fid = identify(m.file) if m.file else None
    if fid is not None and fid.granularity in ("issue", "volume"):
        return fid.granularity
    return "volume" if m.issue is None else "issue"


async def _toc_entries_or_llm(texts: list[str], m: IssueManifest, cfg: SourceConfig, deps: AnalyzeDeps,
                              run: _Run) -> list[ArticleEntry]:
    pages = find_toc_pages(texts)
    style = toc_style(cfg.short_name, _container_kind(m))
    toc = parse_toc("\n".join(texts[i] for i in pages), style=style) if pages else []
    if toc:
        return _toc_entries(toc, m, cfg, "toc")
    if deps.llm_toc is None:
        return []
    toc = await deps.llm_toc("\n".join(texts[:_LLM_TOC_PAGES]))
    if not toc:
        return []
    run.llm = True
    return _toc_entries(toc, m, cfg, "llm")


def _apply_leitir(a: ArticleEntry, rec: LeitirArticle, m: IssueManifest) -> None:
    """A matched leitir.is record. The printed title stays (Primo lower-cases subtitles); catalogue names replace
    the TOC's unless the record lists fewer people; pages are confirmed, filled, or a conflict is noted."""
    a.leitir_record_id = rec.record_id
    if not a.title and rec.title:
        a.title, a.provenance["title"] = rec.title.replace(" : ", ": "), "leitir"
    if rec.authors and len(rec.authors) >= len(a.authors):
        a.authors, a.provenance["authors"] = list(rec.authors), "leitir"
    if rec.page_start is not None:
        if a.page_start is None:
            a.page_start, a.page_end, a.provenance["pages"] = rec.page_start, rec.page_end, "leitir"
        elif rec.page_start == a.page_start:
            a.page_end = rec.page_end if rec.page_end is not None else a.page_end
            prior = a.provenance.get("pages")
            a.provenance["pages"] = f"leitir+{prior}" if prior and prior != "leitir" else "leitir"
        else:
            span = f"{rec.page_start}–{rec.page_end}" if rec.page_end is not None else f"{rec.page_start}"
            _add_analysis_note(a, f"leitir.is segir bls. {span}")
    if m.issue is None and a.issue is None and rec.issue:
        a.issue, a.provenance["issue"] = rec.issue, "leitir"


async def _enrich_with_leitir(entries: list[ArticleEntry], m: IssueManifest, cfg: SourceConfig,
                              deps: AnalyzeDeps) -> None:
    """One query per titled entry; year and volume gate the records (R26). The volume of every matched record,
    when they agree, becomes the issue's volume if it had none."""
    if deps.leitir is None or cfg.short_name not in _LEITIR_SOURCES:
        return
    journal = cfg.citation_name or cfg.display_name
    year = None if m.year_end else m.year          # a file spanning several years gates on none of them
    volumes: set[int] = set()
    for a in entries:
        if not a.title:
            continue
        query = " ".join(filter(None, [a.title, a.authors[0] if a.authors else None]))
        rec = best_match(await deps.leitir(query, journal), title=a.title, page_start=a.page_start,
                         year=year, volume=m.volume)
        if rec is None:
            continue
        _apply_leitir(a, rec, m)
        if rec.volume is not None:
            volumes.add(rec.volume)
    if m.volume is None and len(volumes) == 1:
        m.volume = volumes.pop()


# --- an issue or volume PDF --------------------------------------------------------------------------------

async def _analyze_container(m: IssueManifest, cfg: SourceConfig, deps: AnalyzeDeps, run: _Run) -> None:
    pdf = deps.read_bytes(m.file)
    texts = _page_texts(pdf)
    m.sha256, m.pages = sha256_bytes(pdf), len(texts)
    pm = build_page_map(texts, cfg.short_name)
    m.page_map = pm.as_dict()
    # Share of words BÍN knows over the first pages: the pilot (Task 22) reads it to choose the OCR threshold.
    # Taken from the texts already in hand, so the PDF is not opened twice. 0.0 for a scan.
    m.page_map["bin_ratio"] = round(bin_known_ratio(" ".join(texts[:_BIN_SAMPLE_PAGES])), 3)
    if not has_text_layer(texts):
        m.error = NO_TEXT_LAYER                  # scanned: no TOC to read, no titles to check; OCR is Task 20's
        run.review = True
        return
    if pm.quality < C.PAGE_MAP_MIN_QUALITY:
        run.review = True
    if m.volume is None:
        m.volume = _printed_volume(texts[:3], cfg, m.year)
    entries: list[ArticleEntry] = []
    if cfg.short_name == "ulfljotur":
        _fallback_volume(m, cfg, run)            # the sheet is keyed by volume
        if m.volume is not None:
            entries = [_row_entry(r, m) for r in articles_for(deps.xlsx_rows, volume=m.volume, issue=m.issue)]
    if not entries:
        entries = await _toc_entries_or_llm(texts, m, cfg, deps, run)
    await _enrich_with_leitir(entries, m, cfg, deps)
    _fallback_volume(m, cfg, run)
    if not entries:
        m.error = NO_ARTICLE_LIST
        return
    locate_articles(entries, pm, texts)
    for a in entries:
        if a.pdf_pages:
            first, last = a.pdf_pages
            page, printed, body = texts[first], _printed_at(pm, first), " ".join(texts[first:last + 1][:3])
        else:
            page, printed, body = "", None, ""
        a.article_type = a.article_type or infer_article_type(None, a.title or "", cfg.verdict_type_default)
        a.lang = detect_lang(body, cfg.lang_default)
        a.checks = verify_article(a, page, printed)
        a.confidence = article_confidence(a.checks, llm=run.llm)
    m.articles = entries


# --- one PDF per article ---------------------------------------------------------------------------------

def _issue_variants(issue: str) -> list[str]:
    """A masthead "3-4" may be stored as "3-4", "3" or "4"; articles_for("3") already includes "3-4" rows."""
    parts = issue.split("-")
    return [issue] + parts if len(parts) > 1 else [issue]


def _ulfljotur_entry(first: str, printed0: int | None, m: IssueManifest, rows: list[XlsxArticle],
                     fid: FileIdentity | None) -> ArticleEntry:
    """R23–R25: match the first page against the volume's rows; an ambiguous or missing match is retried
    within the issue the masthead names. The file stem is a scan number, never a page."""
    hit, score = (match_first_page(first, printed0, articles_for(rows, volume=m.volume, issue=m.issue))
                  if m.volume is not None else (None, 0.0))
    printed_issue = masthead_issue(first)
    if hit is None and printed_issue and m.volume is not None:
        for iss in _issue_variants(printed_issue):
            hit, s = match_first_page(first, printed0, articles_for(rows, volume=m.volume, issue=iss))
            score = max(score, s)
            if hit is not None:
                break
    if hit is not None:
        a = _row_entry(hit, m)
        if printed_issue and not set(printed_issue.split("-")) & set(hit.issue.split("-")):
            _add_analysis_note(a, f"forsíðan segir {printed_issue}. tbl., taflan {hit.issue}. tbl.")
        return a
    a = ArticleEntry(analysis_notes=[f"engin röð í töflu passaði (best {score:.2f})"])
    if fid is not None and fid.title_hint:
        a.title, a.provenance["title"] = fid.title_hint, "filename"
    if printed0 is not None:
        a.page_start, a.provenance["pages"] = printed0, "page"
    if printed_issue and printed_issue != m.issue:
        a.issue, a.provenance["issue"] = printed_issue, "page"
    return a


def _ulfljotur_articles(docs: list[_Doc], m: IssueManifest, cfg: SourceConfig, rows: list[XlsxArticle],
                        run: _Run) -> list[ArticleEntry]:
    firsts = [d.first_page for d in docs]
    volume_from_folder = m.volume is not None
    if m.volume is None:                         # later article folders name only year and issue
        m.volume = _printed_volume(firsts, cfg, m.year)
        _fallback_volume(m, cfg, run)
    entries = [_ulfljotur_entry(d.first_page, d.first_printed, m, rows, identify(d.rel)) for d in docs]
    # R25: the printed masthead outranks the folder name and the formula; folders 1952–1957 are a year early.
    years = Counter(y for y in map(masthead_year, firsts) if y is not None)
    if years:
        year, how = years.most_common(1)[0][0], "prentað ár"
    elif volume_from_folder:
        year, how = year_for_volume(m.volume), f"ár {m.volume}. árgangs"
    else:
        year, how = m.year, ""
    if year != m.year:
        if m.year is not None:
            run.flag(f"möppuár {m.year} ≠ {how} {year}")
        m.year = year
    return entries


_FURNITURE = re.compile(r"^(?:\d{1,4}|&|stjórnmál|stjórnsýsla|n|(?:n\s+)?(?:fræðigreinar|greinar|ritrýnd(?:ar)? greinar?))$",
                        re.I)
_HEADING_END = re.compile(r"^(?:útdráttur|abstract|ágrip|summary|inngangur|efnisyfirlit)\b|\b\d{1,2}\.\s*tbl\b|\bárg\b",
                          re.I)
_MAX_TITLE_LINES = 6
_MAX_AUTHOR_LINES = 10
_TRAILING_MARK = re.compile(r"(?<=[^\W\d_])\d{1,2}$|\s*\*+$")      # "bankahruni1", "… 490/2018*"


def _author_in(line: str, *, lenient: bool) -> str | None:
    """The person a byline names ("Jón Jónsson, prófessor …", "Baldur Þórhallsson dósent við", "Sigtryggur
    Pétursson"). Only the first byline may be a family-name author ("Salvör Nordal, …"); after it a lenient
    shape would take affiliation lines ("Political Science, University of Iceland") for people."""
    if "," in line:
        head = line.split(",")[0].strip()
        if looks_like_person(head, strict=not lenient):
            return clean_author(head)[0]
    words = line.split()
    for k in (4, 3, 2):
        if len(words) > k and words[k][:1].islower() and looks_like_person(" ".join(words[:k]), strict=True):
            return clean_author(" ".join(words[:k]))[0]
    return clean_author(line)[0] if looks_like_person(line, strict=True) else None


def _join_title(lines: list[str]) -> str | None:
    out = ""
    for ln in lines:
        if out.endswith("\xad") or (out.endswith("-") and ln[:1].islower()):
            out = out[:-1] + ln                  # "Rök-" / "semdir", or a soft hyphen at the line end
        else:
            out = f"{out} {ln}" if out else ln
    out = _TRAILING_MARK.sub("", out.replace("\xad", "").strip()).strip()
    return out or None


def _first_page_heading(text: str) -> tuple[str | None, list[str]]:
    """Title and authors at the top of a Stjórnmál og stjórnsýsla first page: furniture (page number,
    masthead, "Fræðigreinar"), then title lines, then bylines, then "N. tbl. N. árg. YYYY" or "Útdráttur".
    (None, []) when no byline or end marker follows a short run of title lines."""
    lines = [ln for ln in (" ".join(raw.split()) for raw in text.splitlines()) if ln]
    i = 0
    while i < len(lines) and _FURNITURE.match(lines[i]):
        i += 1
    title: list[str] = []
    while i < len(lines) and not _HEADING_END.search(lines[i]) and _author_in(lines[i], lenient=True) is None:
        if len(title) == _MAX_TITLE_LINES:
            return None, []
        title.append(lines[i])
        i += 1
    authors: list[str] = []
    for k, line in enumerate(lines[i:i + _MAX_AUTHOR_LINES]):
        if _HEADING_END.search(line) or _FURNITURE.match(line):
            break
        name = _author_in(line, lenient=k == 0)
        if name and name not in authors:
            authors.append(name)
    return _join_title(title), authors


def _reconcile_pages(a: ArticleEntry, fid: FileIdentity | None, doc: _Doc) -> None:
    """A file-name page range is a hint (Task 7: two Stjórnmál og stjórnsýsla names carry typos, and some
    ranges disagree with the printed numbers): the printed first page wins; the end is the last page's
    printed number, else the first plus the PDF's page count; any disagreement with the name is noted."""
    fn_start, fn_end = (fid.page_start, fid.page_end) if fid else (None, None)
    printed0 = doc.first_printed
    start = printed0 if printed0 is not None else fn_start
    if start is None:
        return
    if doc.last_printed is not None and doc.last_printed >= start:
        end = doc.last_printed
    else:
        end = start + len(doc.texts) - 1 if doc.texts else fn_end
    if fn_start is None:
        prov = "page"
    elif printed0 is None:
        prov = "filename"
    elif printed0 == fn_start and fn_end in (None, end):
        prov = "filename+page"
    else:
        prov = "page"
    if fn_start is not None and (start != fn_start or fn_end not in (None, end)):
        named = f"{fn_start}–{fn_end}" if fn_end is not None else f"{fn_start}"
        _add_analysis_note(a, f"skráarnafn segir bls. {named}, PDF-skjalið bls. {start}–{end}")
    a.page_start, a.page_end, a.provenance["pages"] = start, end, prov


def _sogs_articles(docs: list[_Doc], m: IssueManifest, cfg: SourceConfig, run: _Run) -> list[ArticleEntry]:
    """R5: the folder gives only the volume; the first page prints "N. tbl. N. árg. YYYY"."""
    entries, years = [], Counter()
    for doc in docs:
        fid = identify(doc.rel)
        title, authors = _first_page_heading(doc.first_page)
        a = ArticleEntry(authors=authors)
        if title:
            a.title, a.provenance["title"] = title, "page"
        elif fid is not None and fid.title_hint:
            a.title, a.provenance["title"] = fid.title_hint, "filename"    # the English title in the file name
        if authors:
            a.provenance["authors"] = "page"
        _reconcile_pages(a, fid, doc)
        issue, volume, year = _printed_issue_volume_year(doc.texts[:2])
        if issue is not None and issue != m.issue:
            a.issue, a.provenance["issue"] = issue, "page"
        if volume is not None and m.volume is not None and volume != m.volume:
            _add_analysis_note(a, f"prentað {volume}. árg., mappan {m.volume}. árg.")
        if year is not None:
            years[year] += 1
        entries.append(a)
    if years:
        year = years.most_common(1)[0][0]
        if m.year is not None and m.year != year:
            run.flag(f"ár {m.year} ≠ prentað ár {year}")
        m.year = year
    elif m.year is None and cfg.volume_base_year is not None and m.volume is not None:
        m.year = cfg.volume_base_year + m.volume - 1
        run.flag(f"{FORMULA_YEAR_NOTE} {m.volume}. árg.: {m.year}; ekkert prentað ár fannst", review=True)
    return entries


_MONTHS = {mo: i for i, mo in enumerate(["janúar", "febrúar", "mars", "apríl", "maí", "júní", "júlí", "ágúst",
                                         "september", "október", "nóvember", "desember"], start=1)}
_DATE_DMY = re.compile(r"\b(\d{1,2})\.\s*([^\W\d_]+)\s+(\d{4})\b")     # "Birt 21. janúar 2022"
_DATE_MDY = re.compile(r"([^\W\d_]+)\s+(\d{1,2}),\s*(\d{4})")            # "JANÚAR 29, 2018" (the blog's own date)
_EFTIR = re.compile(r"^\s*Eftir\s", re.M)


def _publication_date(text: str) -> str | None:
    """The first date above the "Eftir …" byline, ISO; the body's dates (judgments, statutes) are ignored."""
    cut = _EFTIR.search(text)
    head = text[:cut.start()] if cut else text[:400]
    found = []
    for rx, d, mo, y in ((_DATE_DMY, 1, 2, 3), (_DATE_MDY, 2, 1, 3)):
        for mt in rx.finditer(head):
            month = _MONTHS.get(unicodedata.normalize("NFC", mt.group(mo)).lower())
            try:
                date = datetime.date(int(mt.group(y)), month, int(mt.group(d))) if month else None
            except ValueError:
                date = None
            if date is not None:
                found.append((mt.start(), date))
    return min(found)[1].isoformat() if found else None


def _leading_name(part: str) -> str | None:
    """The name a byline fragment starts with, in the nominative ("dr. Víði Smára Petersen" → "Víðir Smári Petersen")."""
    toks = []
    for t in clean_author(part)[0].split():
        if not (_is_name_token(t) or _is_initial(t)):
            break
        toks.append(t)
    return _nominative(" ".join(toks)) if 2 <= len(toks) <= 4 else None


def _eftir_authors(text: str) -> list[str]:
    """Authors of an "Eftir Nafni, starfsheiti" byline and its "og Nafni" continuation lines."""
    lines = [ln for ln in (" ".join(raw.split()) for raw in text.splitlines()) if ln]
    for i, line in enumerate(lines):
        if not line.startswith("Eftir "):
            continue
        chunks = [line[len("Eftir "):]] + [ln[3:] for ln in lines[i + 1:i + 6] if ln.lower().startswith("og ")]
        names: list[str] = []
        for chunk in chunks:
            for part in re.split(r",|\s+og\s+", chunk):
                if (name := _leading_name(part)) and name not in names:
                    names.append(name)
        return names
    return []


def _standalone_entry(doc: _Doc, cfg: SourceConfig) -> ArticleEntry:
    """Vefrit and standalone works (spec §5.1): no printed pages; the file name is the title."""
    fid = identify(doc.rel)
    a = ArticleEntry(title=fid.title_hint if fid and fid.title_hint else nfc(Path(doc.rel).stem),
                     provenance={"title": "filename"})
    if authors := _eftir_authors(doc.first_page):
        a.authors, a.provenance["authors"] = authors, "page"
    if date := _publication_date(doc.first_page):
        a.date, a.provenance["date"] = date, "page"
    elif cfg.cite_by_date:
        _add_analysis_note(a, "birtingardagur fannst ekki á fyrstu síðu")
    return a


def _filename_entry(doc: _Doc) -> ArticleEntry:
    """Any other article PDF (Tímarit lögfræðinga 1975): what the file name says, checked against the page."""
    fid = identify(doc.rel)
    a = ArticleEntry()
    if fid is not None and fid.title_hint:
        a.title, a.provenance["title"] = fid.title_hint, "filename"
    _reconcile_pages(a, fid, doc)
    return a


# A PDF that cannot be read or parsed (EmptyFileError is a FileDataError; both are RuntimeErrors in PyMuPDF).
_UNREADABLE = (fitz.FileDataError, RuntimeError, OSError, ValueError)


def _unreadable_entry(rel: str, exc: Exception) -> ArticleEntry:
    """One article PDF of a folder that cannot be opened: listed for review, nothing claimed."""
    fid = identify(rel)
    a = ArticleEntry(source_file=rel, analysis_notes=[f"skjal ólæsilegt: {type(exc).__name__}"])
    if fid is not None and fid.title_hint:
        a.title, a.provenance["title"] = fid.title_hint, "filename"
    a.checks = verify_article(a, "", None)
    return a


def _printed_start_confirmed(a: ArticleEntry, doc: _Doc) -> bool:
    """printed_page_match for an article PDF (R34): the number printed on its first page equals a start page
    from an independent source, the registry row or else the file name. When they disagree page_start is
    the printed number, so comparing page_start with the page would always agree; a page_start taken from
    the page alone confirms nothing."""
    fid = identify(doc.rel)
    start = a.page_start if a.xlsx_row is not None else (fid.page_start if fid is not None else None)
    return start is not None and start == doc.first_printed


async def _analyze_article_files(m: IssueManifest, cfg: SourceConfig, deps: AnalyzeDeps, run: _Run) -> None:
    m.sha256, m.pages, m.page_map = None, None, {}
    docs: list[_Doc] = []
    unreadable: list[ArticleEntry] = []
    for rel in m.files:
        try:
            texts = _page_texts(deps.read_bytes(rel))
        except _UNREADABLE as exc:             # one bad file must not fail a folder of 28
            unreadable.append(_unreadable_entry(rel, exc))
            run.review = True
            continue
        docs.append(_article_doc(rel, texts, cfg.short_name))
    if cfg.short_name == "ulfljotur":
        entries = _ulfljotur_articles(docs, m, cfg, deps.xlsx_rows, run)
    elif cfg.short_name == "stjornmal_stjornsysla":
        entries = _sogs_articles(docs, m, cfg, run)
    elif cfg.short_name in ("ulfljotur_vefrit", "fraedigreinar_ymsar"):
        entries = [_standalone_entry(d, cfg) for d in docs]
    else:
        entries = [_filename_entry(d) for d in docs]
    for d, a in zip(docs, entries):
        a.source_file = d.rel
        a.pdf_pages = [0, len(d.texts) - 1] if d.texts else None
        if a.page_start is not None and a.page_end is None and d.texts:
            a.page_end = a.page_start + len(d.texts) - 1          # the file is the whole article
        a.lang = detect_lang(" ".join(d.texts[:3]), cfg.lang_default)
        if not has_text_layer(d.texts):
            _add_analysis_note(a, NO_TEXT_LAYER)
            run.review = True
    await _enrich_with_leitir(entries, m, cfg, deps)
    for d, a in zip(docs, entries):
        a.article_type = a.article_type or infer_article_type(None, a.title or "", cfg.verdict_type_default)
        a.checks = verify_article(a, d.first_page, d.first_printed)
        a.checks["printed_page_match"] = _printed_start_confirmed(a, d)
        a.confidence = article_confidence(a.checks)
    m.articles = sorted(entries + unreadable, key=_reading_order)
    m.page_map = {"bin_ratio": _folder_bin_ratio(docs)}


def _folder_bin_ratio(docs: list[_Doc]) -> float:
    """An article folder has no page map; its one entry is the share of words BÍN knows. It is the mean of the
    per-file ratios of the first pages of at most _BIN_SAMPLE_PAGES files, taken with a stride that spans the
    whole folder. (bin_known_ratio looks at 2000 words only, so pooled text would show just the first files;
    a file without any words, e.g. a scan, says nothing about text quality and is left out.) 0.0 without any."""
    step = -(-len(docs) // _BIN_SAMPLE_PAGES) or 1
    shares = [s for s in (bin_known_share(d.first_page) for d in docs[::step]) if s is not None]
    return round(sum(shares) / len(shares), 3) if shares else 0.0


def _reading_order(a: ArticleEntry) -> tuple:
    """Files come in path order ("119-142_…" before "17-30_…"); the review file lists articles by issue and page."""
    lead = (a.issue or "").split("-")[0]
    return (int(lead) if lead.isdigit() else 0, a.page_start is None, a.page_start or 0, a.date or "", a.source_file or "")


# --- the issue ------------------------------------------------------------------------------------------

async def analyze_issue(m: IssueManifest, cfg: SourceConfig, deps: AnalyzeDeps) -> IssueManifest:
    """Analyse one issue (spec §5) and return a new manifest: article list, page map, checks, confidence,
    status. Year, volume and label may change when the PDF prints better evidence than discovery had.
    `page_map["bin_ratio"]` (share of words BÍN knows, over the first pages; for an article folder the mean over
    a sample of files) is set for every issue, also for article folders, whose page map is otherwise empty; the
    pilot reads it to choose the OCR threshold.
    An approved or imported status is kept; otherwise the issue is approved only when every article
    clears the threshold, the articles cover the file, and no fallback (LLM list, formula year or volume,
    untrusted page map, missing text layer) was needed."""
    m = copy.deepcopy(m)
    frozen = m.status in _FROZEN
    m.articles, m.error, m.confidence = [], None, 0.0
    run = _Run()
    if m.file is None:
        await _analyze_article_files(m, cfg, deps, run)
    else:
        await _analyze_container(m, cfg, deps, run)
    for a in m.articles:
        for note in run.notes:
            _add_analysis_note(a, note)
    m.label = issue_label(cfg, m.year, m.volume, m.issue)
    m.confidence = min((a.confidence for a in m.articles), default=0.0)
    if not frozen:
        m.status = "review" if run.review or run.llm else issue_status(m)
    return m
