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

The printed page decides (Task 22b, R40; printed.py and firstpage.py): the registry's page number locates an article,
the page that prints its title is where it starts (a title page before it, a number one or two off, an entry without
a number); entries past the last printed page are not in the file; the title, authors and section label are read off
the article's first page, an editorial's signature off its last. TOC titles are cleaned of OCR'd leaders and front
matter is dropped; with no readable TOC Claude gets a digest of every page's head (fix 8).

An article ends on the last page of its own text, not on the advert, blank page or unlisted item after it (Task 22c,
pagekind.py); those pages are recorded in page_map["non_article"], which coverage allows. Where the page map is unusable
the numbers printed on the located pages are read directly. Significant deviations between registry and print are
flagged with a note starting FRAVIK ("FRÁVIK: ", R42), which sends the issue to review.

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
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, NamedTuple

import fitz

from engine.config.sources import SourceConfig
from engine.timarit import config as C
from engine.timarit import firstpage as FP
from engine.timarit import pagekind as PK
from engine.timarit.article_type import infer_article_type
from engine.timarit.authors import clean_author
from engine.timarit.extract import bin_known_ratio, bin_known_share, has_text_layer
from engine.timarit.firstpage import Page, PageReader, eftir_authors, publication_date, read_page, sogs_heading
from engine.timarit.lang import detect_lang
from engine.timarit.leitir_articles import LeitirArticle, best_match
from engine.timarit.llm_toc import ARTICLE_TYPES, TEXT_LIMIT
from engine.timarit.manifest import ArticleEntry, IssueManifest, article_confidence, issue_label, issue_status
from engine.timarit.naming import FileIdentity, identify, nfc
from engine.timarit.pagemap import PATTERNS, PageMap, build_page_map, read_page_numbers
from engine.timarit.printed import (
    FRAVIK, NOT_FOUND_NOTE, RegistryValues, add_note, assign_ranges, drop_beyond_document, end_in_file, end_ranges,
    fill_printed_pages, flag, last_body_page, page_numbers, read_printed_page, registry_values, relocate_by_title,
    signatures,
)
from engine.timarit.splitter import sha256_bytes
from engine.timarit.toc import TocEntry, clean_title, find_toc_pages, is_front_matter, parse_toc, \
    style_of_text, toc_style
from engine.timarit.ulfljotur_xlsx import (
    XlsxArticle, _fold, articles_for, masthead_issue, masthead_year, match_first_page, rank_first_page, volume_for_year,
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
# A note starting with one of these means analysis sent the issue to review whatever the scores say. FRAVIK ("FRÁVIK: ")
# marks a significant deviation between the registry and the printed pages for the user's review (R42).
FORCED_REVIEW_NOTES = (NO_TEXT_LAYER, FORMULA_VOLUME_NOTE, FORMULA_YEAR_NOTE, NOT_FOUND_NOTE, FRAVIK)
_UNPAGED = frozenset({"ulfljotur_vefrit", "fraedigreinar_ymsar"})   # works cited without page numbers
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


_add_analysis_note = add_note


def _page_texts(pdf: bytes) -> list[str]:
    with fitz.open(stream=pdf, filetype="pdf") as d:
        return [p.get_text() for p in d]


# "D:YYYYMMDDHHmmSSOHH'mm'": every part after the year is optional; O is +, - or Z (UTC).
_META_DATE = re.compile(r"^(?:D:)?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?\s*(?:(Z)|([+-])(\d{2})?(?:'?(\d{2}))?)?", re.I)
_META_FIRST_YEAR = 1990       # web journals: nothing in the archive is older than the web
# A PDF made by a web browser ("print to PDF") is dated by the day it was printed, not the day it was published (R45).
_BROWSER = re.compile(r"\b(?:safari|chrome|chromium|firefox|edge|skia/pdf)\b", re.I)


class PdfMeta(NamedTuple):
    """What a PDF's metadata says about when an article was published, and how far that can be trusted (Task 22d)."""
    year: int | None = None                  # the year of the creation date, else of the modification date, if plausible
    when: datetime.datetime | None = None    # that date and time in UTC, naive: the key of a batch (None without an hour)
    browser: bool = False                    # a web browser made the PDF


def _meta_date(raw: str) -> tuple[int, datetime.datetime | None] | None:
    """A PDF date as (year, UTC time or None without an hour), or None when it is not a date (a month 13, a 25-hour
    offset: PyMuPDF hands the string over as the file has it)."""
    mt = _META_DATE.match(raw)
    if not mt:
        return None
    year, month, day, hour, minute, second = (int(g) if g is not None else None for g in mt.groups()[:6])
    month, day, minute, second = (1 if month is None else month), (1 if day is None else day), minute or 0, second or 0
    try:
        datetime.date(year, month, day)                  # a month 13 or a day 40 is no date ("00" is none either)
        if hour is None:
            return year, None
        when = datetime.datetime(year, month, day, hour, minute, second)
        sign, off_h, off_m = mt.group(8), int(mt.group(9) or 0), int(mt.group(10) or 0)
        if off_h > 23 or off_m > 59:
            return None
        offset = datetime.timedelta(hours=off_h, minutes=off_m)
        return year, when - offset if sign == "+" else when + offset if sign == "-" else when
    except (ValueError, OverflowError):                  # year 1 with a + offset, year 9999 with a - offset
        return None


def pdf_meta(meta: dict | None, this_year: int | None = None) -> PdfMeta:
    """Read a PDF's metadata ("D:20201002123456+00'00'"): the creation date's year, else the modification date's,
    whichever is a date and plausible for a web article (from _META_FIRST_YEAR to this year), that date's time in UTC
    (the offset normalised) when it has an hour, and whether the creator or producer is a web browser."""
    meta = meta or {}
    last = this_year or datetime.date.today().year
    year, when = None, None
    for key in ("creationDate", "modDate"):
        found = _meta_date(str(meta.get(key) or ""))
        if found and _META_FIRST_YEAR <= found[0] <= last:
            year, when = found
            break
    return PdfMeta(year, when, bool(_BROWSER.search(f"{meta.get('creator') or ''} {meta.get('producer') or ''}")))


def metadata_year(meta: dict | None, this_year: int | None = None) -> int | None:
    """The plausible year of a PDF's metadata date (pdf_meta(...).year), for callers that need nothing else."""
    return pdf_meta(meta, this_year).year


def _read_article_pdf(pdf: bytes, every_page: bool = False) -> tuple[list[str], list[Page], list[float], PdfMeta]:
    """An article PDF's page texts, its pages as visual lines (firstpage.read_page): every page with the share
    pictures cover of each (`every_page`: its end is looked for, pagekind), else the first and the last only, and what
    its metadata says about the publication date."""
    with fitz.open(stream=pdf, filetype="pdf") as d:
        texts, meta = [p.get_text() for p in d], pdf_meta(d.metadata)
        if not texts:
            return texts, [], [], meta
        if not every_page:
            return texts, [read_page(d[0]), read_page(d[len(texts) - 1])], [], meta
        reader = PageReader(d)
        return texts, [reader(i) for i in range(len(texts))], [reader.picture(i) for i in range(len(texts))], meta


def _visual_text(page: Page) -> str:
    """A page's text top to bottom (the text layer's order may put the title last)."""
    return "\n".join(ln.text for ln in page.lines)


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


def verify_article(a: ArticleEntry, first_page_text: str, printed_page: int | None,
                   registry: RegistryValues | None = None) -> dict[str, bool]:
    """The four signals of spec §5.4 for one article. `printed_page` is the page number the located first
    page carries (read there, or inferred from a trustworthy page map), None when unknown.

    R41: with `registry` (printed.registry_values, taken before the printed page replaced title, authors or start
    page) the checks compare the registry's values with the page: a title or a byline read off the page, or a start
    page found by its title, would otherwise confirm itself. A registry title that was only a section label, or none,
    is not on the page; a registry without a start page does not match."""
    r = registry or RegistryValues(a.title, list(a.authors), a.page_start,
                                   frozenset(a.provenance.get("title", "").split("+")))
    return {
        "title_on_page": bool(r.title) and _on_page(r.title, first_page_text, C.TITLE_ON_PAGE_MIN),
        "printed_page_match": r.page_start is not None and printed_page == r.page_start,
        "author_on_page": any(_on_page(x, first_page_text, C.AUTHOR_ON_PAGE_MIN) for x in r.authors),
        "external_record": bool(r.title_sources & {"leitir", "xlsx"}) or a.leitir_record_id is not None
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
    assign_ranges(located, page_map, last_text)


def _printed_at(pm: PageMap, idx: int) -> int | None:
    """Evidence for the printed number of PDF page idx: read on that very page, or inferred from a map
    good enough to trust. (Location itself uses the map, so an untrusted map proves nothing.)"""
    return pm.printed[idx] if pm.read[idx] or pm.quality >= C.PAGE_MAP_MIN_QUALITY else None


class _Doc(NamedTuple):
    """One article PDF: its page texts, the printed numbers of its first and last page (None when unknown), its pages
    as visual lines (every page, or the first and the last), the share pictures cover of each (every page only) and
    the printed number of each page (its own map, when trusted) and what its metadata says of its date (Task 22d)."""
    rel: str
    texts: list[str]
    first_printed: int | None
    last_printed: int | None
    pages: list[Page] = []
    pictures: list[float] = []
    printed: list[int | None] = []
    meta: PdfMeta = PdfMeta()

    @property
    def first_page(self) -> str:
        return self.texts[0] if self.texts else ""

    @property
    def first(self) -> Page:
        return self.pages[0] if self.pages else FP.EMPTY

    def page(self, i: int) -> Page:
        return self.pages[i] if len(self.pages) == len(self.texts) and 0 <= i < len(self.pages) else FP.EMPTY

    def last_page(self, i: int | None = None) -> Page:
        """Page i (every page read) or the last page."""
        if i is not None and len(self.pages) == len(self.texts):
            return self.pages[i]
        return self.pages[-1] if self.pages else FP.EMPTY


def _article_doc(rel: str, texts: list[str], source: str, pages: list[Page] | None = None,
                 pictures: list[float] | None = None, meta: PdfMeta = PdfMeta()) -> _Doc:
    """R7: the number read on the first page, else the article's own page map when it fits well enough (one- and
    two-page articles cannot fit one). The last page's number comes from that map only. The read number comes
    first because it is on the very page: some Stjórnmál og stjórnsýsla PDFs lack a page after the first, so
    their map puts the first page one too high."""
    pages, pictures = pages or [], pictures or []
    if not texts:
        return _Doc(rel, texts, None, None, pages, pictures, meta=meta)
    pm = build_page_map(texts, source)
    trusted = pm.offset is not None and pm.quality >= C.PAGE_MAP_MIN_QUALITY
    read0 = read_page_numbers(texts[:1], PATTERNS.get(source, PATTERNS["default"]))[0]
    printed0 = read0 if read0 is not None else (pm.printed[0] if trusted else None)
    return _Doc(rel, texts, printed0, pm.printed[-1] if trusted else None, pages, pictures,
                list(pm.printed) if trusted else [], meta)


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
        # A section that names a type is that type: Claude's list, and a contents heading the parser knows (toc.
        # SECTION_LABELS, Task 22f: "FRÉTTABÁLKUR" → Frétt, "MINNINGARBÁLKUR" → Annað)
        kind = t.section if t.section in ARTICLE_TYPES else \
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


async def _toc_entries_or_llm(texts: list[str], toc_pages: list[int], pm: PageMap, reader: PageReader,
                              m: IssueManifest, cfg: SourceConfig, deps: AnalyzeDeps, run: _Run) -> list[ArticleEntry]:
    text = "\n".join(texts[i] for i in toc_pages)
    style = style_of_text(text, toc_style(cfg.short_name, _container_kind(m)))
    toc = parse_toc(text, style=style) if toc_pages else []
    if toc:
        return _toc_entries(toc, m, cfg, "toc")
    if deps.llm_toc is None:
        return []
    toc = await deps.llm_toc(_fallback_text(texts, toc_pages, pm, reader))
    if not toc:
        return []
    run.llm = True
    return _toc_entries(toc, m, cfg, "llm")


def _fallback_text(texts: list[str], toc_pages: list[int], pm: PageMap, reader: PageReader) -> str:
    """What Claude reads when the TOC parser finds nothing (fix 8 of Task 22b): the TOC pages' text, if any, then a
    digest of the whole issue, one line per page with its printed number and its first two lines
    (firstpage.page_heads_digest), so that every article's start page is in view, not only the first article's.
    toc_via_llm cuts it to its length limit; the cache key is over the text sent."""
    toc = "\n".join(texts[i] for i in toc_pages).strip()
    digest = FP.page_heads_digest([reader(i) for i in range(len(texts))],
                                  [_printed_at(pm, i) for i in range(len(texts))],
                                  budget=max(0, TEXT_LIMIT - len(toc) - 2))
    return f"{toc}\n\n{digest}" if toc else digest


def _tidy_registry(entries: list[ArticleEntry]) -> list[ArticleEntry]:
    """Registry titles without TOC leaders (toc.clean_title); front matter that a TOC lists ("Höfundalisti") is no
    article and is dropped (fix 7)."""
    out = []
    for a in entries:
        if a.title:
            a.title = clean_title(a.title) or a.title
        if not is_front_matter(a.title):
            out.append(a)
    return out


def _starts_inside_pagination(pm: PageMap) -> bool:
    """The file's first page is a numbered page past the first (Lögrétta: the first article on page 9, no cover):
    nothing in it is an issue TOC, whatever page numbers its body pages show."""
    return bool(pm.read) and pm.read[0] and (pm.printed[0] or 0) > 1


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
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        texts = [p.get_text() for p in doc]
        await _analyze_container_pages(m, cfg, deps, run, pdf, texts, PageReader(doc))


async def _analyze_container_pages(m: IssueManifest, cfg: SourceConfig, deps: AnalyzeDeps, run: _Run, pdf: bytes,
                                   texts: list[str], reader: PageReader) -> None:
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
    toc_pages = [] if _starts_inside_pagination(pm) else find_toc_pages(texts)
    if not entries:
        entries = await _toc_entries_or_llm(texts, toc_pages, pm, reader, m, cfg, deps, run)
    entries = _tidy_registry(entries)
    await _enrich_with_leitir(entries, m, cfg, deps)
    _fallback_volume(m, cfg, run)
    if not entries:
        m.error = NO_ARTICLE_LIST
        return
    registry_end = [a.page_end for a in entries]
    registry_ends = {id(a): end for a, end in zip(entries, registry_end)}
    registry = {id(a): registry_values(a) for a in entries}          # R41: what the checks compare with the page
    numbers = page_numbers(pm, texts, PATTERNS.get(cfg.short_name, PATTERNS["default"]))
    locate_articles(entries, pm, texts)
    # an unnumbered first page where no registry entry starts is the cover (it may list titles in large type); with an
    # unusable map it is the page itself that tells (Lögrétta 2005's first page prints 3: its first article)
    unnumbered = not pm.read[0] if pm.offset is not None else numbers.at(0) is None
    cover = {0} if texts and unnumbered and not any(a.pdf_pages and a.pdf_pages[0] == 0 for a in entries) else set()
    relocate_by_title(entries, registry_end, pm, texts, reader,
                      skip=set(toc_pages) | set(range(max(toc_pages, default=-1) + 1)) | cover, number=numbers.at)
    entries, beyond = drop_beyond_document(entries, pm)
    if beyond:
        run.flag(beyond, review=True)
    ends = end_ranges(entries, reader, numbers.at, registry_ends)   # Task 22c: articles end on their own text
    m.page_map["non_article"] = ends.outside
    for a in entries:
        if a.pdf_pages:
            first, last = a.pdf_pages
            fill_printed_pages(a, lambda i, f=first, la=last: numbers.at(i, f, la), first, last)
            read_printed_page(a, reader(first), reader(last_body_page(reader, first, last)),
                              sign=lambda x, f=first, e=ends.ends[id(a)]: signatures(x, reader, f, e))
            page, printed, body = _visual_text(reader(first)), numbers.evidence(first), " ".join(texts[first:last + 1][:3])
        else:
            page, printed, body = "", None, ""
        a.article_type = a.article_type or infer_article_type(None, a.title or "", cfg.verdict_type_default)
        a.lang = detect_lang(body, cfg.lang_default)
        a.checks = verify_article(a, page, printed, registry[id(a)])
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
        if printed0 is not None and printed0 != hit.page_start:      # R40: the sheet's Bls locates, print cites
            flag(a, f"taflan segir bls. {hit.page_start}, prentað bls. {printed0}")
            a.page_start, a.provenance["pages"] = printed0, "page"
        return a
    a = ArticleEntry(analysis_notes=[f"engin röð í töflu passaði (best {score:.2f})"])
    if same := _same_title_rows(first, printed0, rows, m.volume):
        a.title, a.authors = same[0].title, list(same[0].authors)
        a.provenance.update({"title": "xlsx", "authors": "xlsx"})
        a.analysis_notes = [f"sami titill í fleiri röðum töflunnar ({', '.join(f'{r.issue}. tbl.' for r in same)}); "
                            "tölublað óvíst"]
    elif fid is not None and fid.title_hint:
        a.title, a.provenance["title"] = fid.title_hint, "filename"
    if printed0 is not None:
        a.page_start, a.provenance["pages"] = printed0, "page"
    if printed_issue and printed_issue != m.issue:
        a.issue, a.provenance["issue"] = printed_issue, "page"
    return a


def _same_title_rows(first: str, printed0: int | None, rows: list[XlsxArticle], volume: int | None) -> list[XlsxArticle]:
    """The rows behind an ambiguous match that are one and the same title and authors in several issues of the volume
    (Úlfljótur 1947: "Á vettvangi lagadeildar" in 3. and 4. tbl.): the title and authors are known even though the
    issue is not. [] otherwise."""
    if volume is None:
        return []
    ranked = rank_first_page(first, printed0, articles_for(rows, volume=volume))
    if not ranked or ranked[0][1] < C.XLSX_MATCH_MIN:
        return []
    top = [r for r, sc in ranked if sc >= ranked[0][1] - C.XLSX_MATCH_MARGIN - 1e-9]
    same = {(_fold(r.title), tuple(r.authors)) for r in top}
    return top if len(top) > 1 and len(same) == 1 else []


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


def count_word(n: int, one: str, many: str) -> str:
    """A number and its noun in Icelandic agreement: the singular after a number ending in 1 but not in 11 ("1 síðu",
    "21 grein"), the plural otherwise ("2 síðum", "11 greinar")."""
    return f"{n} {one if n % 10 == 1 and n % 100 != 11 else many}"


class _Named(NamedTuple):
    """A file name's page range and the printed one, where they disagree (flagged by _flag_file_names)."""
    entry: ArticleEntry
    named: tuple[int, int | None]
    printed: tuple[int, int | None]
    note: str


def _reconcile_pages(a: ArticleEntry, fid: FileIdentity | None, doc: _Doc) -> _Named | None:
    """A file-name page range is a hint (Task 7: two Stjórnmál og stjórnsýsla names carry typos, and some
    ranges disagree with the printed numbers): the printed first page wins; the end is the last page's
    printed number, else the first plus the PDF's page count; a disagreement with the name is returned for
    _flag_file_names."""
    fn_start, fn_end = (fid.page_start, fid.page_end) if fid else (None, None)
    printed0 = doc.first_printed
    start = printed0 if printed0 is not None else fn_start
    if start is None:
        return None
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
    a.page_start, a.page_end, a.provenance["pages"] = start, end, prov
    if fn_start is not None and (start != fn_start or fn_end not in (None, end)):
        named = f"{fn_start}–{fn_end}" if fn_end is not None else f"{fn_start}"
        return _Named(a, (fn_start, fn_end), (start, end), f"skráarnafn segir bls. {named}, PDF-skjalið bls. {start}–{end}")
    return None


def _flag_file_names(named: list[_Named], run: _Run, files: int) -> None:
    """R42 for file-name page ranges: when the names of a folder are all off the print by one offset (Stjórnmál og
    stjórnsýsla 10. árg.: every name two pages ahead), that is one deviation for the issue, flagged once; a file whose
    range is off otherwise, or whose start is off with no common offset, is flagged on its own; the rest are notes."""
    offsets = Counter(n.named[0] - n.printed[0] for n in named)
    common, count = offsets.most_common(1)[0] if offsets else (0, 0)
    common = common if common and count >= 2 else 0
    if common:
        ex = next(n for n in named if n.named[0] - n.printed[0] == common)
        run.flag(f"{FRAVIK}blaðsíðutal skráarnafnanna er {count_word(abs(common), 'síðu', 'síðum')} "
                 f"{'hærra' if common > 0 else 'lægra'} en prentað blaðsíðutal í {count_word(count, 'skrá', 'skrám')} "
                 f"af {files} (t.d. {ex.note})", review=True)

    def off(n: _Named) -> bool:
        if not common:
            return n.named[0] != n.printed[0]
        ends = n.named[1] is not None and n.printed[1] is not None
        return n.named[0] - n.printed[0] != common or ends and n.named[1] - n.printed[1] != common
    for n in named:
        if off(n):
            flag(n.entry, n.note)
        else:
            _add_analysis_note(n.entry, n.note)


def _sogs_articles(docs: list[_Doc], m: IssueManifest, cfg: SourceConfig, run: _Run) -> list[ArticleEntry]:
    """R5: the folder gives only the volume; the first page prints "N. tbl. N. árg. YYYY"."""
    entries, years, named = [], Counter(), []
    for doc in docs:
        fid = identify(doc.rel)
        title, authors = sogs_heading(doc.first_page)
        a = ArticleEntry(authors=authors)
        if title:
            a.title, a.provenance["title"] = title, "page"
        elif fid is not None and fid.title_hint:
            a.title, a.provenance["title"] = fid.title_hint, "filename"    # the English title in the file name
        if authors:
            a.provenance["authors"] = "page"
        if n := _reconcile_pages(a, fid, doc):
            named.append(n)
        issue, volume, year = _printed_issue_volume_year(doc.texts[:2])
        if issue is not None and issue != m.issue:
            a.issue, a.provenance["issue"] = issue, "page"
        if volume is not None and m.volume is not None and volume != m.volume:
            _add_analysis_note(a, f"prentað {volume}. árg., mappan {m.volume}. árg.")
        if year is not None:
            years[year] += 1
        entries.append(a)
    _flag_file_names(named, run, len(docs))
    if years:
        year = years.most_common(1)[0][0]
        if m.year is not None and m.year != year:
            run.flag(f"ár {m.year} ≠ prentað ár {year}")
        m.year = year
    elif m.year is None and cfg.volume_base_year is not None and m.volume is not None:
        m.year = cfg.volume_base_year + m.volume - 1
        run.flag(f"{FORMULA_YEAR_NOTE} {m.volume}. árg.: {m.year}; ekkert prentað ár fannst", review=True)
    return entries


_BATCH_MIN = 3        # this many PDFs of one folder created within _BATCH_WINDOW: a batch (re-exported together, R45a)
_BATCH_WINDOW = datetime.timedelta(minutes=60)


def _reexported(docs: list[_Doc]) -> set[str]:
    """The files whose metadata date is no publication date (R45, R45a): made by a web browser, or one of at least
    _BATCH_MIN files of their folder whose creation times (UTC) fall in one sliding window of _BATCH_WINDOW (a re-export of
    the archive: a clock hour would split it in two). Worked out once per folder from the metadata the files were read with."""
    by_folder: dict[str, list[tuple[datetime.datetime, str]]] = {}
    for d in docs:
        if d.meta.when:
            by_folder.setdefault(str(Path(d.rel).parent), []).append((d.meta.when, d.rel))
    batch: set[str] = set()
    for times in by_folder.values():
        times.sort()
        for i, (start, _) in enumerate(times):
            window = [rel for when, rel in times[i:] if when - start <= _BATCH_WINDOW]
            if len(window) >= _BATCH_MIN:
                batch.update(window)
    return {d.rel for d in docs if d.meta.browser or d.rel in batch}


def _standalone_entry(doc: _Doc, cfg: SourceConfig, reexported: bool = False) -> ArticleEntry:
    """Vefrit and standalone works (spec §5.1): no printed pages; the file name is the title."""
    fid = identify(doc.rel)
    a = ArticleEntry(title=fid.title_hint if fid and fid.title_hint else nfc(Path(doc.rel).stem),
                     provenance={"title": "filename"})
    if authors := eftir_authors(doc.first_page):
        a.authors, a.provenance["authors"] = authors, "page"
    if date := publication_date(doc.first_page):
        a.date, a.provenance["date"] = date, "page"
    elif cfg.cite_by_date:
        # The user's rule (Task 22d): the year alone, from the PDF's metadata; import cites "2020", never a day. Not
        # when the file is a browser print or one of a batch re-exported together: its date is the export's (R45).
        if doc.meta.year and reexported:
            _add_analysis_note(a, "birtingardagur fannst ekki á fyrstu síðu; dagsetning lýsigagnanna er frá "
                                  "endurútflutningi eða vafraprentun og var ekki notuð")
        elif doc.meta.year:
            a.date, a.provenance["date"] = f"{doc.meta.year}-01-01", "pdf_metadata"
            _add_analysis_note(a, f"birtingardagur fannst ekki á fyrstu síðu; ár úr lýsigögnum PDF-skjalsins: {doc.meta.year}")
        else:
            _add_analysis_note(a, "birtingardagur fannst ekki á fyrstu síðu")
    return a


def _filename_entry(doc: _Doc, named: list[_Named]) -> ArticleEntry:
    """Any other article PDF (Tímarit lögfræðinga 1975): what the file name says, checked against the page."""
    fid = identify(doc.rel)
    a = ArticleEntry()
    if fid is not None and fid.title_hint:
        a.title, a.provenance["title"] = fid.title_hint, "filename"
    if n := _reconcile_pages(a, fid, doc):
        named.append(n)
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


def _printed_start_confirmed(a: ArticleEntry, doc: _Doc, rows: dict[int, XlsxArticle]) -> bool:
    """printed_page_match for an article PDF (R34): the number printed on its first page equals a start page
    from an independent source, the registry row or else the file name. When they disagree page_start is
    the printed number, so comparing page_start with the page would always agree; a page_start taken from
    the page alone confirms nothing."""
    fid = identify(doc.rel)
    if a.xlsx_row is not None:
        start = rows[a.xlsx_row].page_start if a.xlsx_row in rows else None
    else:
        start = fid.page_start if fid is not None else None
    return start is not None and start == doc.first_printed


async def _analyze_article_files(m: IssueManifest, cfg: SourceConfig, deps: AnalyzeDeps, run: _Run) -> None:
    m.sha256, m.pages, m.page_map = None, None, {}
    docs: list[_Doc] = []
    unreadable: list[ArticleEntry] = []
    for rel in m.files:
        try:
            texts, pages, pictures, meta = _read_article_pdf(deps.read_bytes(rel), every_page=cfg.short_name in _OWN_ENDS)
        except _UNREADABLE as exc:             # one bad file must not fail a folder of 28
            unreadable.append(_unreadable_entry(rel, exc))
            run.review = True
            continue
        docs.append(_article_doc(rel, texts, cfg.short_name, pages, pictures, meta))
    if cfg.short_name == "ulfljotur":
        entries = _ulfljotur_articles(docs, m, cfg, deps.xlsx_rows, run)
    elif cfg.short_name == "stjornmal_stjornsysla":
        entries = _sogs_articles(docs, m, cfg, run)
    elif cfg.short_name in ("ulfljotur_vefrit", "fraedigreinar_ymsar"):
        reexported = _reexported(docs)
        entries = [_standalone_entry(d, cfg, d.rel in reexported) for d in docs]
    else:
        named: list[_Named] = []
        entries = [_filename_entry(d, named) for d in docs]
        _flag_file_names(named, run, len(docs))
    for d, a in zip(docs, entries):
        a.source_file = d.rel
        a.pdf_pages = [0, len(d.texts) - 1] if d.texts else None
        if a.page_start is not None and a.page_end is None and d.texts:
            last = d.last_printed if d.last_printed is not None and d.last_printed >= a.page_start else None
            a.page_end = last or a.page_start + len(d.texts) - 1          # the file is the whole article
        a.lang = detect_lang(" ".join(d.texts[:3]), cfg.lang_default)
        if not has_text_layer(d.texts):
            _add_analysis_note(a, NO_TEXT_LAYER)
            run.review = True
    ends = {id(a): _end_in_file(a, d) for d, a in zip(docs, entries) if cfg.short_name in _OWN_ENDS and d.texts}
    await _enrich_with_leitir(entries, m, cfg, deps)
    rows = {r.row: r for r in deps.xlsx_rows}
    for d, a in zip(docs, entries):
        registry = registry_values(a)                              # R41
        e = ends.get(id(a))
        read_printed_page(a, d.first, d.last_page(e.last if e else None) if d.texts else None,
                          sign=(lambda x, d=d, e=e: signatures(x, d.page, 0, e)) if e else None)
        if a.page_start is None and d.texts and cfg.short_name not in _UNPAGED:
            flag(a, "blaðsíðutal greinarinnar fannst hvorki á síðunni né í skránni")
        a.article_type = a.article_type or infer_article_type(None, a.title or "", cfg.verdict_type_default)
        a.checks = verify_article(a, _visual_text(d.first) if d.first.lines else d.first_page, d.first_printed,
                                  registry)
        a.checks["printed_page_match"] = _printed_start_confirmed(a, d, rows)
        a.confidence = article_confidence(a.checks)
    m.articles = sorted(entries + unreadable, key=_reading_order)
    m.page_map = {"bin_ratio": _folder_bin_ratio(docs)}


_OWN_ENDS = frozenset({"ulfljotur"})     # article files the user split from scans: they may run into the next item


def _end_in_file(a: ArticleEntry, d: _Doc) -> PK.End:
    """Where an article ends inside its own file (Task 22c): an Úlfljótur scan may go on into the next item (1957
    187.pdf prints "Juristforeningen 50 ára" on its last two pages)."""
    return end_in_file(a, d.page, len(d.texts), d.pictures, d.printed)


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
    if any(n.startswith(FORCED_REVIEW_NOTES) for a in m.articles for n in a.analysis_notes):
        run.review = True                    # a deviation (FRÁVIK) or a missing article: the user reviews the issue
    m.label = issue_label(cfg, m.year, m.volume, m.issue)
    m.confidence = min((a.confidence for a in m.articles), default=0.0)
    if not frozen:
        m.status = "review" if run.review or run.llm else issue_status(m)
    return m
