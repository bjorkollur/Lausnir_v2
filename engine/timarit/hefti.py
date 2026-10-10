"""The hefti of each article in a bound volume (Task 23f, the user's decision of 2026-10-10).

Tímarit lögfræðinga's bound volumes (one PDF a year, manifests `{year}_x_x.yaml`) hold the year's hefti one after the
other. A citation names the hefti as printed ("30. árg. 4. hefti (1980), bls. 248"), so every article needs its own
`issue`. Where the volume's contents, leitir.is or the user gave none, the pages tell: each hefti opens on its cover
("4. HEFTI 30. ÁRGANGUR DESEMBER 1980", from 1999 "4. hefti • 49. árgangur"; TL 1971/1972 "1. hefti 1972") and,
in most volumes, an inner cover two pages on with the issue line alone ("4. HEFTI", over its "EFNI:"). The first
of those pages starts the hefti; an article belongs to the hefti its first PDF page falls in. That is also what tells
apart two articles on the same printed page in a volume that restarts its numbering in every hefti (TL 1973, 1993).

Not a hefti's start: the volume's own contents, which list its hefti as lowercase headings ("1. hefti", "2. hefti"),
a reference or a footnote ("2. hefti, maí 1998.", "1. tbl. 1997."), a citation ("Tímarit lögfræðinga, 2. hefti 1993"),
an old cover pictured small inside an article, and anything in the margin band (a running head's place) or in the lower
half of a page. A cover that names another volume or year (TL 1974 binds 1975's "3. HEFTI 25. ÁRGANGUR") starts a
hefti that is not the volume's own: its articles are given no hefti.

An issue that is set is never changed, whatever its provenance. When the covers cannot be trusted (none found, or
their hefti out of order) nothing is assigned and each article without a hefti says so in a plain note.
"""
from __future__ import annotations

import re
from typing import Callable, NamedTuple

from engine.timarit import config as C
from engine.timarit import firstpage as FP
from engine.timarit.firstpage import Page
from engine.timarit.manifest import ArticleEntry, IssueManifest

# "4. HEFTI …", "1.HEFTI …", "3.-4. HEFTI …", "1. hefti • 49. árgangur", "2. HEFTl …" (OCR: l for I)
_LINE = re.compile(r"\s*(\d{1,2}(?:\s*\.?\s*[-–—/]\s*\d{1,2})?)\s*\.\s*(heft[il1])\b\s*[•·,.]?\s*(.*?)\s*$", re.I)
_VOLUME = re.compile(r"(\d{1,3})\s*\.\s*[áa]r", re.I)        # "30. ÁRGANGUR", "49. árgangur", OCR "árqanqur"
_YEAR = re.compile(r"(?:19|20)\d\d")
_MAX_LEN = 50               # an issue line is short; running text that names a hefti is longer
_MIN_SIZE = 0.75            # set at least this share of the page's body size (an old cover pictured small is not)

NOTE = "hefti greinarinnar var ekki skráð: "     # every note this module writes starts so (and a new run replaces it)


class IssueLine(NamedTuple):
    issue: str                  # as printed: "4", "3-4"
    volume: int | None          # the volume the line names ("30. ÁRGANGUR"), if any
    year: int | None            # the year it names instead ("1. hefti 1972"), if any


class Hefti(NamedTuple):
    issue: str                  # as printed
    first: int                  # PDF index of its first page: its cover, else its inner cover
    foreign: str | None = None  # the volume or year its cover names when that is not the volume's ("25. árg.")


class Result(NamedTuple):
    hefti: list[Hefti]
    doubt: str | None                           # why the boundaries are not trusted (then nothing was assigned)
    assigned: list[int]                         # indexes of the articles given a hefti now
    unset: dict[int, str]                       # articles left without one, with the reason (their note)
    disagree: list[tuple[int, str, str]]        # (index, the hefti set, the hefti its pages are in): kept as set


def bound_volume(m: IssueManifest) -> bool:
    """A bound volume of a source whose volumes hold several hefti: a container PDF, with no issue of its own."""
    return m.source in C.BOUND_VOLUME_SOURCES and bool(m.file) and m.issue is None


def issue_line(page: Page) -> IssueLine | None:
    """The hefti a page starts, from its issue line: short, in the upper half below the margin band, set at least
    _MIN_SIZE of the page's body, and either naming a volume or a year after the hefti or being the hefti alone in
    capitals (an inner cover). None for every other page."""
    body = FP.body_size(page)
    for ln in page.lines:
        if not FP._MARGIN_BAND * page.height < ln.y < 0.5 * page.height or len(ln.text) > _MAX_LEN \
                or "bls" in ln.text.lower() or ln.size < _MIN_SIZE * body:
            continue
        m = _LINE.fullmatch(ln.text)
        if m is None:
            continue
        number, word, rest = m.groups()
        volume = year = None
        if not rest:
            if not word[:4].isupper():          # "1. hefti" alone: a heading of the volume's own contents
                continue
        elif v := _VOLUME.search(rest):
            volume = int(v.group(1))
        elif _YEAR.fullmatch(rest):
            year = int(rest)
        else:
            continue                            # "2. hefti, maí 1998.": a reference in running text
        return IssueLine(re.sub(r"\s*\.?\s*[-–—/]\s*", "-", number), volume, year)
    return None


def find_hefti(page: Callable[[int], Page], first: int, last: int, volume: int | None,
               years: tuple[int, int]) -> list[Hefti]:
    """The hefti that start on PDF pages first..last, in page order. A cover and the inner cover after it are one
    hefti, which starts on the first of them. `volume` and `years` are the volume's: a cover naming others starts a
    foreign hefti."""
    out: list[Hefti] = []
    for i in range(first, last + 1):
        line = issue_line(page(i))
        if line is None:
            continue
        foreign = (f"{line.volume}. árg." if line.volume is not None and volume is not None and line.volume != volume
                   else str(line.year) if line.year is not None and not years[0] <= line.year <= years[1] else None)
        if out and (out[-1].issue, out[-1].foreign) == (line.issue, foreign):
            continue
        out.append(Hefti(line.issue, i, foreign))
    return out


def _bounds(issue: str) -> tuple[int, int]:
    parts = [int(p) for p in issue.split("-")]
    return parts[0], parts[-1]


def doubt(hefti: list[Hefti]) -> str | None:
    """Why these boundaries cannot be trusted, None when they can: the volume's own hefti must follow each other in
    rising order (a hefti may be missing: TL 1975's 3. hefti is a folder of its own)."""
    own = [h.issue for h in hefti if h.foreign is None]
    if not own:
        return "engin heftisforsíða fannst"
    if any(_bounds(b)[0] <= _bounds(a)[1] for a, b in zip(own, own[1:])):
        return f"heftin á forsíðunum eru ekki í röð ({', '.join(own)})"
    return None


def _in(hefti: list[Hefti], i: int) -> Hefti | None:
    """The hefti PDF page i falls in; None before the first."""
    found = None
    for h in hefti:
        if h.first <= i:
            found = h
    return found


def _note(a: ArticleEntry, why: str | None) -> None:
    """Replace this module's note on the article with `why` (None: no note)."""
    a.analysis_notes = [n for n in a.analysis_notes if not n.startswith(NOTE)] + ([NOTE + why] if why else [])


def assign_hefti(m: IssueManifest, page: Callable[[int], Page], first: int = 0, last: int | None = None) -> Result:
    """Give each article of the bound volume `m` that has no `issue` the hefti its first PDF page falls in (provenance
    `page`), reading the covers on PDF pages first..last (`page(i)`; article pdf_pages are in the same numbering).
    An article whose hefti cannot be told (the boundaries are not trusted, it starts before the first cover or in a
    foreign hefti, it has no pages) keeps none and gets a note. Set issues are left alone; where the pages say
    otherwise that is reported (`disagree`), not changed. Changes `m` in place; running it again changes nothing."""
    last = (m.pages - 1 if m.pages else first) if last is None else last
    hefti = find_hefti(page, first, last, m.volume, (m.year or 0, m.year_end or m.year or 9999))
    why_not = doubt(hefti)
    assigned: list[int] = []
    unset: dict[int, str] = {}
    disagree: list[tuple[int, str, str]] = []
    for k, a in enumerate(m.articles):
        h = _in(hefti, a.pdf_pages[0]) if a.pdf_pages and why_not is None else None
        if a.issue is not None:
            if h is not None and h.foreign is None and h.issue != a.issue:
                disagree.append((k, a.issue, h.issue))
            _note(a, None)
            continue
        if why_not is not None:
            why = why_not
        elif not a.pdf_pages:
            why = "engar síður eru skráðar á hana (pdf_pages)"
        elif h is None:
            why = f"hún hefst á undan fyrstu heftisforsíðu bindisins (pdf-síðu {hefti[0].first + 1})"
        elif h.foreign is not None:
            why = f"hún er í {h.issue}. hefti {h.foreign}, sem er bundið inn í bindið"
        else:
            a.issue, a.provenance["issue"] = h.issue, "page"
            assigned.append(k)
            why = None
        _note(a, why)
        if why is not None:
            unset[k] = why
    return Result(hefti, why_not, assigned, unset, disagree)
