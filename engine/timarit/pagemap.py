"""Printed page numbers of a PDF (spec §5.2).

Reads the number from the edge lines of each page with per-journal patterns, then fits a piecewise map:
consecutive segments, each with its own offset printed = pdf_index + k, whose printed numbers increase
strictly over the whole document. One segment is the common case (an issue: one offset, as before).
Several segments are an annual volume whose hefti covers and TOC pages carry no number and are not
counted (TL 1971-2002: the offset steps down at each hefti), or an issue PDF that lacks the pages between
two articles (Lögrétta). Reads that fit no segment are misreads (adverts, covers, footnotes, OCR noise).

A block of reads whose numbers would appear a second time (a hefti that starts again at 1) cannot join the
increasing chain. When it is large (R16: at least max(MIN_AGREEING, PAGE_MAP_RESTART_RATIO x the largest
segment)) the document is a genuine restart and gets no map at all: no page numbers, quality 0, review.
Nothing is stored for a document whose numbers cannot be fitted — the anchor then falls back to the section.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from engine.timarit.config import PAGE_MAP_RESTART_RATIO

MIN_AGREEING = 3          # agreeing reads a segment needs
_MAX_PAGE = 1799          # a larger number on an edge line is a year ("2014", "Lögfræðingur 2010"), never a page

_BARE = re.compile(r"^\s*(\d{1,4})\s*$")
_LEADING = re.compile(r"^\s*(\d{1,4})\s+\S")          # "81 Samræmi laga …" (first line only, second pass)


def _running(header: str) -> list[re.Pattern]:
    """A page number next to a journal's running header: on the same line after or before it ("ÚLFLJÓTUR — 13",
    "13 — ÚLFLJÓTUR", "10\\t LÖGMANNABLAÐIÐ TBL 01/20"), or on the line below or above it — the text layer often
    puts the two on lines of their own. Whole lines only."""
    h = f"(?:{header})"
    sep = r"(?:\s*[—–-]+\s*|\s+)"
    return [
        re.compile(rf"^\s*{h}{sep}(\d{{1,4}})\s*$", re.I),
        re.compile(rf"^\s*(\d{{1,4}}){sep}{h}\s*$", re.I),
        re.compile(rf"^\s*{h}\s*\n\s*(\d{{1,4}})\s*$", re.I),
        re.compile(rf"^\s*(\d{{1,4}})\s*\n\s*{h}\s*$", re.I),
    ]


# Headers measured on the archive (Task 17a report). Úlfljótur 1950s-1980s with OCR variants of the name
# (ÚLFUÓTUR, ULFUOTUR, ÚLFLJÓrUR, LJlfljótur, ÚLFLIÓTUR …); Lögmannablaðið "LÖGMANNABLAÐIÐ TBL 01/20" (2012-),
# "< LÖGMANNABLAÐIÐ – 2 / 2006" and "LÖGMANNABLAÐIÐ – 4 / 2008 >" (2006-2011), "Lögmannablaðið" (1999);
# TL 1951-1972 "Tímarit lögfræðinga" with OCR variants (Timarit, Tímcirit, lögfrœöinga, löqfræðinqa).
_ULFLJOTUR = r"(?:[ÚU]|L[IJ])LF(?:L[JI]|U)[ÓOÖQ][TR]UR"
_LOGMANNABLADID = r"<?\s*LÖGMANNABLAÐIÐ(?:\s*[–-]?\s*(?:TBL\s*)?\d{1,2}\s*/\s*\d{2,4})?\s*>?"
_TIMARIT_LOGFRAEDINGA = r"T\S{1,4}rit\s+l\S{3,}"
# Stjórnmál og stjórnsýsla 2008 (Vol04): the number on the line above the running footer "Stjórnmál og stjórnsýsla
# veftímarit (fræðigreinar)" (Task 22f). Whole lines only, so the footer's own text is never read as a number.
_SOGS_FOOTER = [re.compile(r"^\s*(\d{1,4})\s*\n\s*Stjórnmál og stjórnsýsla\b[^\n]*$", re.I),
                re.compile(r"^\s*Stjórnmál og stjórnsýsla\b[^\n]*\n\s*(\d{1,4})\s*$", re.I)]
# Any short line, then the number on a line of its own: the article's running title or its author's name above the
# number (Rannsóknir í félagsvísindum; TL 1951-1953 "Okur og skyld brot." / "67"). Tried on the first two lines.
_TITLE_THEN_NUMBER = re.compile(r"^[^\n]{2,90}\n\s*(\d{1,4})\s*$")

PATTERNS: dict[str, list[re.Pattern]] = {
    "default": [_BARE],
    "ulfljotur": [_BARE, *_running(_ULFLJOTUR)],
    # Lögrétta 2005-2014: the article's letter and the page on two lines ("a" / "9"); 2019: "TÍMARIT LÖGRÉTTU" / "4"
    "logretta": [_BARE, re.compile(r"^\s*[a-z]\s+(\d{1,4})\s*$"), re.compile(r"^\s*[a-z]\s*\n\s*(\d{1,4})\s*$"),
                 *_running("TÍMARIT LÖGRÉTTU")],
    "logbru": [_BARE, re.compile(r"^\s*(\d{1,4})\s*/\s*LÖGBRÚ\s*$", re.I)],
    "logmannabladid": [_BARE, *_running(_LOGMANNABLADID)],
    "logfraedingur": [_BARE, *_running("Lögfræðingur")],          # "Lögfræðingur 21", "22 Lögfræðingur"
    "rannsoknir_lagadeild": [_BARE, _TITLE_THEN_NUMBER],
    "stjornmal_stjornsysla": [_BARE, _LEADING, *_SOGS_FOOTER],
    "timarit_logfraedinga": [_BARE, _LEADING, *_running(_TIMARIT_LOGFRAEDINGA), _TITLE_THEN_NUMBER],
}


def _two_line(pat: re.Pattern) -> bool:
    """A pattern whose source contains a line break is matched against two adjacent edge lines."""
    return "\\n" in pat.pattern


def _number(text: str, patterns: list[re.Pattern]) -> int | None:
    for pat in patterns:
        if (m := pat.match(text)) and int(m.group(1)) <= _MAX_PAGE:
            return int(m.group(1))
    return None


def read_page_numbers(page_texts: list[str], patterns: list[re.Pattern]) -> list[int | None]:
    """The number printed on each page, None when none is read.

    The edge lines are tried in this order: the first line, the first two lines (two-line patterns), the last
    line, the last two lines. _LEADING ("81 Samræmi laga …", a weak header) is tried last and on the first line
    only, so a header cannot preempt a footer. A value above _MAX_PAGE is a year and never a page number.
    """
    single = [p for p in patterns if p is not _LEADING and not _two_line(p)]
    pairs = [p for p in patterns if _two_line(p)]
    out: list[int | None] = []
    for t in page_texts:
        lines = [ln for ln in t.splitlines() if ln.strip()]
        found = None
        if lines:
            candidates = [(lines[0], single)]
            if len(lines) > 1:
                candidates += [(f"{lines[0]}\n{lines[1]}", pairs), (lines[-1], single),
                               (f"{lines[-2]}\n{lines[-1]}", pairs)]
            for text, pats in candidates:
                if pats and (found := _number(text, pats)) is not None:
                    break
            if found is None and _LEADING in patterns:
                found = _number(lines[0], [_LEADING])
        out.append(found)
    return out


@dataclass(frozen=True)
class Segment:
    """Consecutive PDF pages printed as pdf_index + offset; first/last are its first and last agreeing reads."""
    offset: int
    first: int
    last: int
    votes: int


class _MaxTree:
    """Fenwick tree over printed values: the best completed chain (total, read) that ends below a value."""

    def __init__(self, size: int):
        self._t = [(0, -1)] * (size + 2)

    def update(self, value: int, item: tuple[int, int]) -> None:
        i = value + 1
        while i < len(self._t):
            if item[0] > self._t[i][0]:
                self._t[i] = item
            i += i & -i

    def best_below(self, value: int) -> tuple[int, int]:
        best, i = (0, -1), value
        while i > 0:
            if self._t[i][0] > best[0]:
                best = self._t[i]
            i -= i & -i
        return best


def _chain(read: list[int | None]) -> list[int]:
    """PDF indices of the largest set of reads whose printed values increase strictly with the page index and
    which fall into runs of equal offset ("segments") of at least MIN_AGREEING reads each.

    Dynamic programming over the reads in page order; state = (read, reads so far in its segment, capped at
    MIN_AGREEING). A read continues the segment of the latest earlier read with its offset, or opens a new segment
    after any earlier read with a smaller value whose segment is complete (looked up in _MaxTree). On equal totals
    the longer current segment wins, so a later hefti keeps its first pages and the earlier one loses its tail
    (TL 2000: adverts numbered 259-264 at the end of 3. hefti, 4. hefti starting again at 259).
    """
    pts = [(i, n) for i, n in enumerate(read) if n is not None]
    cap = MIN_AGREEING
    best = [[0] * (cap + 1) for _ in pts]            # best[p][c]: reads in the best chain ending at p in state c
    parent: list[list[tuple[int, int] | None]] = [[None] * (cap + 1) for _ in pts]
    tree = _MaxTree(max((n for _, n in pts), default=0) + 1)
    last_with_offset: dict[int, int] = {}
    for p, (i, v) in enumerate(pts):
        best[p][1] = 1
        total, q = tree.best_below(v)
        if total + 1 > best[p][1]:
            best[p][1], parent[p][1] = total + 1, (q, cap)
        if (q := last_with_offset.get(v - i)) is not None:
            for c in range(cap, 0, -1):
                if best[q][c] and best[q][c] + 1 > best[p][min(cap, c + 1)]:
                    best[p][min(cap, c + 1)], parent[p][min(cap, c + 1)] = best[q][c] + 1, (q, c)
        last_with_offset[v - i] = p
        if best[p][cap]:
            tree.update(v, (best[p][cap], p))
    end = max(range(len(pts)), key=lambda p: best[p][cap], default=None)
    if end is None or not best[end][cap]:
        return []
    chain, state = [], (end, cap)
    while state is not None:
        chain.append(pts[state[0]][0])
        state = parent[state[0]][state[1]]
    return chain[::-1]


def _fit(read: list[int | None]) -> tuple[list[Segment], int, bool, set[int]]:
    """(segments, runner-up votes, ambiguous, the chain's reads). The runner-up is the largest group of equal offset
    among the reads the chain could not take: a block whose numbers would appear twice, or scattered misreads.
    R16 decides."""
    chain = _chain(read)
    if not chain:
        return [], 0, False, set()
    runs: list[list[int]] = []                     # [offset, first, last, votes]
    for i in chain:
        k = read[i] - i
        if runs and runs[-1][0] == k:
            runs[-1][2], runs[-1][3] = i, runs[-1][3] + 1
        else:
            runs.append([k, i, i, 1])
    segments = [Segment(*r) for r in runs]
    taken = set(chain)
    excluded = Counter(n - i for i, n in enumerate(read) if n is not None and i not in taken)
    runner_up = max(excluded.values(), default=0)
    winner = max(s.votes for s in segments)
    return segments, runner_up, runner_up >= max(MIN_AGREEING, PAGE_MAP_RESTART_RATIO * winner), taken


def _contested(read: list[int | None], segments: list[Segment], taken: set[int]) -> set[int]:
    """Printed numbers that two blocks of pages carry. The chain keeps one of them; the number must not resolve
    silently to it, since it may be the wrong one (TL 2000: adverts numbered 259-264 at the end of 3. hefti,
    4. hefti starting again at 259; with one footer of 4. hefti unread, the chain keeps the adverts).

    - A block of reads the chain did not take: MIN_AGREEING or more, one offset, on consecutive pages (at most one
      unread page between two of them, no other read): its numbers. Scattered OCR misreads that happen to share an
      offset are not a block (TL 1951_01/02).
    - Between two segments A and B: when the reads there that follow A's offset reach a number that B's offset
      also reaches (from its reads there, or its first read), every number in that overlap, and the one below it
      when the page before B's first such read is unread (an unnumbered first page, R7; with a footer of the next
      hefti's first page unread nothing else says that its number is printed twice). One read is enough here: it
      continues a segment's own numbering into the next one's (TL 1978: the last page of 2. hefti prints 97, and
      3. hefti starts again at 97; TL 1997: adverts 227-228 and 4. hefti from 227).
    Two or more unread footers at the start of the next hefti can still leave its first number with the other copy.
    """
    out: set[int] = set()
    blocks: list[list[int]] = []
    for i, n in enumerate(read):
        if n is None:
            continue
        if i in taken:
            blocks.append([])
            continue
        if not blocks or not blocks[-1] or n - i != read[blocks[-1][-1]] - blocks[-1][-1] or i - blocks[-1][-1] > 2:
            blocks.append([])
        blocks[-1].append(i)
    for b in (b for b in blocks if len(b) >= MIN_AGREEING):
        k = read[b[0]] - b[0]
        out.update(i + k for i in range(b[0], b[-1] + 1) if i + k > 0)
    for a, b in zip(segments, segments[1:]):
        between = [(i, read[i]) for i in range(a.last + 1, b.first) if read[i] is not None]
        top = max([a.last + a.offset] + [n for i, n in between if n - i == a.offset])
        first_i, first_n = min([(i, n) for i, n in between if n - i == b.offset] + [(b.first, b.first + b.offset)])
        if top < first_n:
            continue
        low = first_n - 1 if first_i > 0 and read[first_i - 1] is None else first_n
        out.update(range(max(low, 1), top + 1))
    return out


def fit_offset(read: list[int | None]) -> tuple[int | None, float]:
    """(offset of the largest segment, runner_up_share); (None, share) when the reads are ambiguous (R16), (None, 0.0)
    when no segment has MIN_AGREEING agreeing reads."""
    segments, runner_up, ambiguous, _ = _fit(read)
    if not segments:
        return None, 0.0
    winner = max(segments, key=lambda s: s.votes)
    share = runner_up / winner.votes
    return (None if ambiguous else winner.offset), share


@dataclass
class PageMap:
    offset: int | None              # the largest segment's offset (compatibility; one-segment documents: the offset)
    printed: list[int | None]       # the printed number of each PDF page, None when unknown (unique by construction)
    read: list[bool]                # the page's own read agrees with its segment
    quality: float                  # share of pages whose read agrees with its segment
    runner_up_share: float = 0.0    # runner-up votes (reads no segment could take) / largest segment's votes
    segments: list[Segment] = field(default_factory=list)
    contested: list[int] = field(default_factory=list)   # numbers two blocks print: unmapped (see _contested)

    def pdf_index_for(self, printed_page: int) -> int | None:
        """The PDF page that carries a printed number, None when no page does."""
        if printed_page < 1:
            return None
        try:
            return self.printed.index(printed_page)
        except ValueError:
            return None

    def as_dict(self) -> dict:
        return {"offset": self.offset, "quality": round(self.quality, 3), "runner_up_share": round(self.runner_up_share, 3),
                "pages": len(self.printed), "segments": [[s.first, s.last, s.offset] for s in self.segments],
                "contested": list(self.contested)}


def build_page_map(page_texts: list[str], source: str) -> PageMap:
    """Pages inside a segment (between its first and last agreeing read) get offset + index. Pages before the
    first segment and after the last get the outer segments' numbers, as a one-offset map gives every page.
    Pages between two segments get None: their numbers are unknown (hefti covers carry none; when pages are
    missing from a scan nothing says where). A number that a second block of pages prints as well (_contested)
    is given to no page."""
    read = read_page_numbers(page_texts, PATTERNS.get(source, PATTERNS["default"]))
    n = len(page_texts)
    segments, runner_up, ambiguous, taken = _fit(read)
    if not segments:
        return PageMap(None, [None] * n, [False] * n, 0.0, 0.0)
    winner = max(segments, key=lambda s: s.votes)
    share = runner_up / winner.votes
    if ambiguous:
        return PageMap(None, [None] * n, [False] * n, 0.0, share)
    printed: list[int | None] = [None] * n
    agrees = [False] * n
    for j, s in enumerate(segments):
        lo = 0 if j == 0 else s.first
        hi = n - 1 if j == len(segments) - 1 else s.last
        for i in range(lo, hi + 1):
            printed[i] = i + s.offset if i + s.offset > 0 else None
            agrees[i] = s.first <= i <= s.last and read[i] is not None and read[i] - i == s.offset
    contested = _contested(read, segments, taken)
    unmapped = sorted(p for p in printed if p is not None and p in contested)
    printed = [None if p in contested else p for p in printed]
    return PageMap(winner.offset, printed, agrees, sum(agrees) / n if n else 0.0, share, segments, unmapped)


_MAX_SKIP = 2            # a printed number may skip one value between two pages (an unprinted page in the original)


def numbers_from_start(page_texts: list[str], source: str, start: int) -> tuple[list[int], str | None]:
    """The printed number of every page of an article whose first page prints `start` (R17, R46), and a note (in
    Icelandic) when the print departs from counting on from `start`; None otherwise (Task 22f, fix round 1).

    Page 0 is `start`. A later page takes the number read on it (read_page_numbers, the source's patterns) when that
    number follows the page before by one, or by two (one value not printed: Stjórnmál og stjórnsýsla 2008 prints 5,
    7, 8 …) and the jump is confirmed by the article-local page map or by the next page reading one more; where the
    map has a number for the page, the read must agree with it. A page whose read does not qualify takes the map's
    number when that follows the page before by one or two, else the page before + 1. So an article printed without
    gaps is counted exactly as before, whatever stray numbers (footnote marks, years) its pages show."""
    read = read_page_numbers(page_texts, PATTERNS.get(source, PATTERNS["default"]))
    fitted = build_page_map(page_texts, source).printed if page_texts else []
    out = [start] if page_texts else []
    for i in range(1, len(page_texts)):
        prev, r, mapped = out[-1], read[i], fitted[i]
        nxt = read[i + 1] if i + 1 < len(read) else None
        if r is not None and 1 <= r - prev <= _MAX_SKIP and (
                r == mapped if mapped is not None else (r == prev + 1 or nxt == r + 1)):
            out.append(r)
        elif mapped is not None and 1 <= mapped - prev <= _MAX_SKIP:
            out.append(mapped)
        else:
            out.append(prev + 1)
    skipped = sorted(set(range(start, out[-1] + 1)) - set(out)) if out else []
    note = None
    if skipped:
        listed = ", ".join(str(n) for n in skipped)
        note = f"prentað blaðsíðutal sleppir bls. {listed} (talið frá bls. {start})"
    return out, note
