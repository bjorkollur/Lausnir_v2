# engine/timarit/ulfljotur_xlsx.py
"""The Úlfljótur table of contents the user keeps (1,258 articles, 1.–76. árg.).

Columns: Grein | Höfundur | Höfundur II | Höfundur III | Árgangur ('76. árg') |
Tölublað ('1. tölublað', '3-4. tölublað') | Bls (int). Row 4 has title and author
swapped. A row is swapped back only when the title cell reads as a person (strict:
-son/-dóttir surname) while the first author cell does not even have the shape of a
name (lenient), so a tribute titled "Ólafur Jóhannesson" by an author with a family
name ("Sigurður Líndal") is left alone.

Years: the date printed in a page's masthead (`masthead_year`) is the evidence; the archive folder
names and `year_for_volume` are fallbacks only (spec §3) — both are a year off for volumes 6–11.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import openpyxl

from engine.timarit.authors import _PARTICLES, _is_initial, _is_name_token, looks_like_person, split_authors
from engine.timarit.config import XLSX_MATCH_MARGIN, XLSX_MATCH_MIN

_ARG = re.compile(r"(\d+)\.?\s*árg", re.I)
_TBL = re.compile(r"(\d+(?:-\d+)?)\.?\s*tölubla", re.I)
_SPACE_BEFORE_DOT = re.compile(r"\s+\.")
_DOTTED_INITIALS = re.compile(r"(?:[^\W\d_]\.){1,4}")     # "G.O.", "W.E.v."


@dataclass(frozen=True)
class XlsxArticle:
    title: str
    authors: list[str]
    volume: int
    issue: str
    page_start: int
    row: int


def _given_token(t: str) -> bool:
    return _is_name_token(t) or _is_initial(t) or bool(_DOTTED_INITIALS.fullmatch(t)) or t in _PARTICLES


def _normalise_author_cell(cell: str) -> str:
    """Typographic repairs on one raw author cell: "dr . Jón" -> "dr. Jón", and a foreign
    "Surname, Given" cell (one comma, a single name token on the left, one to three name or
    initial tokens on the right) -> "Given Surname" so the comma is not read as an author separator."""
    cell = " ".join(_SPACE_BEFORE_DOT.sub(".", cell).split())
    if cell.count(",") == 1:
        left, right = (part.strip() for part in cell.split(","))
        toks = right.split()
        if len(left.split()) == 1 and _is_name_token(left) and 1 <= len(toks) <= 3 and all(_given_token(t) for t in toks):
            return f"{right} {left}"
    return cell


def _author_names(cells: list[str]) -> list[str]:
    """Every cell split into its authors (comma, "og", "&", ";"), flattened in order, deduplicated;
    "Anonymus" is no author."""
    names: list[str] = []
    for cell in cells:
        for name in split_authors(cell):
            if name and name.casefold().rstrip(".") != "anonymus" and name not in names:
                names.append(name)
    return names


def load_xlsx(path: Path) -> list[XlsxArticle]:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0]
        out: list[XlsxArticle] = []
        for n, row in enumerate(ws.iter_rows(values_only=True), start=1):
            if n == 1 or not any(c is not None for c in row):
                continue
            title, a1, a2, a3, arg, tbl, bls = (list(row) + [None] * 7)[:7]
            title = str(title or "").strip()
            cells = [_normalise_author_cell(str(a)) for a in (a1, a2, a3) if a and str(a).strip()]
            if cells and looks_like_person(title) and not looks_like_person(cells[0], strict=False):
                title, cells[0] = cells[0], title
            m_arg, m_tbl = _ARG.search(str(arg or "")), _TBL.search(str(tbl or ""))
            if not (m_arg and m_tbl and bls is not None):
                continue
            out.append(XlsxArticle(title, _author_names(cells), int(m_arg.group(1)), m_tbl.group(1), int(bls), n))
        return out
    finally:
        wb.close()


def articles_for(rows: list[XlsxArticle], *, volume: int | None = None, issue: str | None = None) -> list[XlsxArticle]:
    sel = [r for r in rows if (volume is None or r.volume == volume) and (issue is None or r.issue == issue or issue in r.issue.split("-"))]
    return sorted(sel, key=lambda r: r.page_start)


def _fold(s: str) -> str:
    """Lower-case ASCII skeleton: diacritics and ð/þ/æ folded, punctuation and runs of blanks collapsed."""
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", s.replace("ð", "d").replace("þ", "th").replace("æ", "ae")).split())


def title_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _fold(a), _fold(b)).ratio()


# --- first-page matching -------------------------------------------------------------------
# Only the heading zone of a page counts: the first _HEADING_CHARS folded characters after the
# masthead. A title is scored by the best similarity of a title-sized window inside that zone
# (windows start at every word start, so an exact title is always found).
# Folded titles of at most _SHORT_TITLE_CHARS characters ("Lög", "Gjafsókn", "Flöggun") turn up in
# ordinary prose: they are not fuzzy-matched. They must occur as a whole-word span (>= _SHORT_TITLE_MIN,
# never inside a longer word) that starts within the first _SHORT_TITLE_ZONE characters, where the
# heading is; the same word further down is body text.
_HEADING_CHARS = 600
_SHORT_TITLE_CHARS = 12
_SHORT_TITLE_MIN = 0.95
_SHORT_TITLE_ZONE = 150
_PRINTED_PAGE_BONUS = 0.1
_HYPHEN_BREAK = re.compile(r"(\w)-[ \t]*\n[ \t]*(\w)")
# "ULFLJOTUR Reykjavik, desember 1947, 4. tbl., I. árg." / "REYKJAVIK, 1. TBL. 1962, XV. ÁRG." (folded text)
_MASTHEAD = re.compile(r"^.{0,40}?\b(?:ulfljot\w*|reykjav\w+)\b.{0,160}?\barg\b")
# the issue marker of a masthead: "4. tbl.", "3.-4. TBL.", "3.–4. tbl.", "3.—4. tbl."
_ISSUE_MARK = re.compile(r"(\d{1,2})\s*\.?\s*(?:[-\u2013\u2014\u2212]\s*(\d{1,2})\s*\.?\s*)?tbl\b", re.I)
_MASTHEAD_WORD = re.compile(r"\b(?:ulfljotur|reykjavik)\b")
_MASTHEAD_ZONE = 250


def _heading_zone(first_page_text: str) -> str:
    folded = _fold(_HYPHEN_BREAK.sub(r"\1\2", first_page_text[:3000]))
    m = _MASTHEAD.match(folded)
    if m:
        folded = folded[m.end():].lstrip()
    return folded[:_HEADING_CHARS]


def masthead_issue(first_page_text: str) -> str | None:
    """The issue the page's own masthead names ("4" from "... 1947, 4. tbl., I. árg.", "3-4" from
    "3.–4. tbl."), or None. The marker only counts near the top and after "Úlfljótur" / "Reykjavík",
    so a sentence citing another issue ("í 3. tbl. Úlfljóts 1987") is not read as the masthead."""
    head = first_page_text[:_MASTHEAD_ZONE]
    m = _ISSUE_MARK.search(head)
    if not m or not _MASTHEAD_WORD.search(_fold(head[:m.start()])):
        return None
    return m.group(1) if not m.group(2) else f"{m.group(1)}-{m.group(2)}"


_MASTHEAD_DATE = re.compile(r"\b(?:ulfljotur|reykjavik)\b(.{0,160}?)\barg\b")
_YEAR = re.compile(r"\b(19[4-9]\d|20[0-2]\d)\b")


def masthead_year(first_page_text: str) -> int | None:
    """The year printed in the page's own masthead ("Reykjavik, desember 1947, 4. tbl., I. árg." ->
    1947), or None. It must stand between "Úlfljótur"/"Reykjavík" and "árg." next to an issue marker,
    so a year in running text ("... 1987", "kom út 1947") is not read as the masthead date.
    The printed date outranks the folder name and `year_for_volume`."""
    m = _MASTHEAD_DATE.search(_fold(first_page_text[:_MASTHEAD_ZONE]))
    if not m or not re.search(r"\btbl\b", m.group(1)):
        return None
    y = _YEAR.search(m.group(1))
    return int(y.group(1)) if y else None


def _span_score(title: str, zone: str) -> tuple[float, int]:
    """A short title: (score, position) of the first whole-word span of the zone that equals it
    (>= _SHORT_TITLE_MIN) and starts inside the heading; (0.0, 0) otherwise."""
    n, at, starts = len(title.split()), 0, []
    words = zone.split()
    for w in words:
        at = zone.index(w, at)
        starts.append(at)
        at += len(w)
    for i in range(len(words) - n + 1):
        if starts[i] > _SHORT_TITLE_ZONE:
            break
        span = " ".join(words[i:i + n])
        if abs(len(span) - len(title)) <= 1:
            ratio = difflib.SequenceMatcher(None, title, span).ratio()
            if ratio >= _SHORT_TITLE_MIN:
                return ratio, starts[i]
    return 0.0, 0


def _window_score(title: str, zone: str) -> tuple[float, int]:
    """(best similarity, start of the earliest best window) of title-sized windows of the zone,
    windows starting at every word start."""
    at = zone.find(title)
    while at > 0 and zone[at - 1] != " ":      # an exact hit must start a word ("Ábyrgð lækna" is not in "refsiábyrgð lækna")
        at = zone.find(title, at + 1)
    if at >= 0:
        return 1.0, at
    sm = difflib.SequenceMatcher(None, autojunk=False)
    sm.set_seq2(title)
    width, best, best_at = len(title), 0.0, 0
    for i in [0] + [k + 1 for k, ch in enumerate(zone) if ch == " "]:
        sm.set_seq1(zone[i:i + width])
        if sm.real_quick_ratio() <= best or sm.quick_ratio() <= best:
            continue
        r = sm.ratio()
        if r > best:
            best, best_at = r, i
    return best, best_at


class _Scored(NamedTuple):
    article: XlsxArticle
    folded: str
    score: float        # title evidence only
    key: float          # ranking key: score + printed-page bonus (not capped, so a bonus can split a tie)
    pos: int            # where in the heading zone the title was found


def _score(first_page_text: str, printed_page: int | None, candidates: list[XlsxArticle]) -> list[_Scored]:
    zone = _heading_zone(first_page_text)
    scored: list[_Scored] = []
    for c in candidates:
        t = _fold(c.title)
        if not t:
            continue
        s, pos = _span_score(t, zone) if len(t) <= _SHORT_TITLE_CHARS else _window_score(t, zone)
        if s <= 0.0:
            continue
        bonus = _PRINTED_PAGE_BONUS if printed_page is not None and printed_page == c.page_start and s >= XLSX_MATCH_MIN else 0.0
        scored.append(_Scored(c, t, s, s + bonus, pos))
    scored.sort(key=lambda r: (r.key, len(r.folded)), reverse=True)
    return scored


def rank_first_page(first_page_text: str, printed_page: int | None,
                    candidates: list[XlsxArticle]) -> list[tuple[XlsxArticle, float]]:
    """Every candidate whose title scores above zero on the page's heading zone, best first (equal
    scores: the longer title first). Score = best windowed title similarity, +0.1 when the printed
    page equals the registry's start page and the title already clears XLSX_MATCH_MIN (capped at 1.0)."""
    return [(r.article, min(1.0, r.key)) for r in _score(first_page_text, printed_page, candidates)]


def _beats(a: _Scored, b: _Scored) -> bool:
    """`a` is plainly the better reading of the heading than `b` (both score within the margin):
    `b` is a whole-word part of `a`'s longer title ("Lagamál" in "Lagamál. Slagorð"), or one of
    them is a short word and the other a long title, and the first of the two to appear in the
    heading is the heading (the long title on a tie)."""
    if a.folded == b.folded:
        return False
    if f" {b.folded} " in f" {a.folded} ":
        return True
    a_short, b_short = len(a.folded) <= _SHORT_TITLE_CHARS, len(b.folded) <= _SHORT_TITLE_CHARS
    if a_short != b_short:
        return a.pos < b.pos if a_short else a.pos <= b.pos
    return False


def match_first_page(first_page_text: str, printed_page: int | None,
                     candidates: list[XlsxArticle]) -> tuple[XlsxArticle | None, float]:
    """The registry row whose title is the page's heading, with its score; (None, best score) when
    nothing reaches XLSX_MATCH_MIN or the answer is ambiguous.

    Ambiguous = another candidate scores within XLSX_MATCH_MARGIN of the best and the best does not
    plainly beat it (see `_beats`): a different title of the same standing, or the very same title
    twice. A printed page equal to exactly one row's start page separates same-title rows.
    Pass the candidates already narrowed by volume and, when the masthead names it, `masthead_issue`.
    """
    scored = _score(first_page_text, printed_page, candidates)
    if not scored:
        return None, 0.0
    top = scored[0]
    shown = min(1.0, top.key)
    contenders = [r for r in scored if r.key >= top.key - XLSX_MATCH_MARGIN - 1e-9]
    winner = next((r for r in contenders if all(r is o or _beats(r, o) for o in contenders)), None)
    if winner is None or winner.key < XLSX_MATCH_MIN:
        return None, shown
    return winner.article, min(1.0, winner.key)


def year_for_volume(volume: int) -> int:
    """The year of a volume when the page itself does not say (`masthead_year` comes first, then
    the folder name; this formula is the last fallback).

    Read off the printed mastheads: 1. árg. = 1947 ... 4. árg. = 1950, 6. árg. = 1953 ... 11. árg. = 1958,
    12. árg. = 1959 ... 16. árg. = 1963; the year that was skipped lies between volume 4 and volume 6.
    Volume 5 is in neither the archive nor the registry, so its 1952 is unverified."""
    return 1946 + volume if volume <= 4 else 1947 + volume


def volume_for_year(year: int) -> int:
    """Inverse of `year_for_volume`, for years that print a masthead (the archive folder name is not
    evidence). 1951 falls in the gap between volume 4 (1950) and volume 6 (1953), and years before
    1947 predate the journal: both raise ValueError. 1952 -> 5 is unverified, as for `year_for_volume`."""
    if year < 1947:
        raise ValueError(f"Úlfljótur began in 1947, not {year}")
    if year == 1951:
        raise ValueError("no volume of Úlfljótur is known for 1951")
    return year - 1946 if year <= 1950 else year - 1947
