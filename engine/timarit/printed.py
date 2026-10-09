"""The printed page decides (Task 22b, ruling R40): where an article of an issue PDF starts, which registry entries the
file holds at all, and the title, authors and type an article prints on its own pages.

analyze locates every registry entry by its page number first (locate_articles); relocate_by_title then moves a start
to the page that prints the title, drop_beyond_document leaves out entries the file does not contain, end_ranges ends
each article on the last page of its own text (pagekind.py, Task 22c), and read_printed_page reads the article's first
and last page (firstpage.py) for the metadata.

Significant deviations between the registry and the printed pages are flagged for the user's review (R42): an analysis
note that starts with FRAVIK, which sends the issue to review. Task 23b took out the flags audit 4 found false: an
interview's page names the person interviewed, name forms ("Yoran, Aaron" / "Aharon Yoran", "Róbert R." / "Róbert
Ragnar") are one person, a title search keeps the registry's page when it prints the title, a title found on a page
whose running head prints the registry's number is a note, and a signature after the next item's heading ends the range
there (flagged as such).
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Callable, NamedTuple, Sequence

from engine.timarit import config as C
from engine.timarit import firstpage as FP
from engine.timarit import pagekind as PK
from engine.timarit.article_type import infer_article_type
from engine.timarit.authors import looks_like_person
from engine.timarit.firstpage import Line, Page, PageReader
from engine.timarit.manifest import ArticleEntry
from engine.timarit.pagemap import PageMap, read_page_numbers
from engine.timarit.toc import _nominative, is_label, label_type, split_label
from engine.timarit.ulfljotur_xlsx import _fold

# R42: a significant deviation between the registry and the printed page, for the user to review later. analyze
# exports it and counts it among the notes that force review; the pilot report lists every such note.
FRAVIK = "FRÁVIK: "


class RegistryValues(NamedTuple):
    """What the registry (TOC, spreadsheet, leitir.is, Claude, file name) said before the printed page replaced it
    (R41): the verification checks compare these with the page, not the page with itself."""
    title: str | None              # without a section label; None when the registry had only a label or no title
    authors: list[str]
    page_start: int | None
    title_sources: frozenset[str] = frozenset()     # the title's provenance labels


def registry_values(a: ArticleEntry) -> RegistryValues:
    title = None
    if a.title:
        _, rest = split_label(a.title)
        title = rest if rest and not FP.is_label_line(rest) else None
    sources = frozenset(a.provenance.get("title", "").split("+")) - {""}
    return RegistryValues(title, list(a.authors), a.page_start, sources)


def add_note(a: ArticleEntry, note: str) -> None:
    """Machine findings go to analysis_notes; `note` belongs to the user (R33)."""
    if note and note not in a.analysis_notes:
        a.analysis_notes.append(note)


def flag(a: ArticleEntry, text: str) -> None:
    """A significant deviation (R42): the note starts with FRAVIK and names both values."""
    add_note(a, FRAVIK + text)


def assign_ranges(located: list[tuple[int, ArticleEntry]], page_map: PageMap, last_text: int) -> None:
    """Each located article runs from its start page to the page before the next one (the last to `last_text`); a
    page_end still unknown is the printed number of its range's last numbered page, not past the last page whose
    number was read (the map only extrapolates over trailing adverts: TL 1955 1. hefti)."""
    located.sort(key=lambda t: t[0])
    last_read = max((i for i, r in enumerate(page_map.read) if r), default=None)
    for k, (idx, a) in enumerate(located):
        end = located[k + 1][0] - 1 if k + 1 < len(located) else last_text
        a.pdf_pages = [idx, max(idx, end)]
        if a.page_end is None:
            hi = a.pdf_pages[1] if last_read is None or last_read < idx else min(a.pdf_pages[1], last_read)
            a.page_end = next((p for p in reversed(page_map.printed[idx:hi + 1]) if p is not None), None)


_NEAR = 2                    # pages around a registry's start page where the printed title is looked for
_REGISTRY_NAMES = {"xlsx": "taflan", "toc": "efnisyfirlitið", "llm": "Claude", "leitir": "leitir.is"}


def _registry_name(a: ArticleEntry) -> str:
    srcs = a.provenance.get("pages", "").split("+")
    return next((_REGISTRY_NAMES[x] for x in srcs if x in _REGISTRY_NAMES), "skráin")


def _heading_at(reader: PageReader, i: int, title: str, *, loose: bool, min_share: float = 0.3) -> bool:
    """The page's top prints `title` in a heading set at least a little larger than its body (firstpage.top_lines
    leaves out the running head, which may repeat the title)."""
    page = reader(i)
    h = FP.printed_title(page, title, loose=loose, min_share=min_share, contain=True)
    return h is not None and h.size >= FP.EMPHASIS * FP.body_size(page)


def relocate_by_title(entries: list[ArticleEntry], registry_end: list[int | None], pm: PageMap,
                      page_texts: list[str], reader: PageReader, skip: set[int],
                      number: Callable[[int], int | None] | None = None) -> None:
    """After locate_articles: the registry's page number locates an article, the page that prints its title is
    where it starts (R40). For a located article:
    - an unnumbered page just before it that prints the title in a heading is its title page (fix 4);
    - otherwise, when its page does not print the title, the nearest page within _NEAR that does is its start
      (registry numbers off by one or two, fix 3);
    - a start that the page map only extrapolated beyond the last number read, with the title nowhere near, is no
      start (TL 1955: the TOC lists pages the file lacks).
    An article without a start page (Claude read no number, a TOC number that went backwards) is looked for first
    between its neighbours in registry order, then anywhere (a jumbled TOC lists articles out of order) (fix 6). The
    printed number of the new start page becomes page_start, and a registry number that disagrees is noted; ranges
    and page_end are recomputed. Only the whole registry title in a heading counts (`contain`): part of it may head a
    different article. `skip`: TOC pages, the pages before them and the cover. `number(i)`: the printed number of PDF
    page i (default: the page map's); an unnumbered title page takes one less than the next page's.
    R42: a printed start that disagrees with the registry's, and an article found by its title alone (no registry page,
    or one more than _NEAR pages off), are flagged. R43: not when the contents page lists the entry and its page number
    is unreadable or missing, and the title is found between the starts of the entries around it (contents order):
    that is a plain note (Lögfræðingur 2008, 2011, 2012: OCR-garbled numbers); a page that breaks the order, and an
    entry with no contents line, stay deviations."""
    starts = {id(a): a.pdf_pages[0] for a in entries if a.pdf_pages}
    last_read = max((i for i, r in enumerate(pm.read) if r), default=-1)
    top_read = pm.printed[last_read] if last_read >= 0 else None
    moved = False
    unnumbered: list[tuple[int, ArticleEntry, str, str]] = []
    for k, a in enumerate(entries):
        if not a.title or is_label(a.title):
            continue
        loose = a.provenance.get("title") == "filename"
        taken = set(starts.values())
        s = starts.get(id(a))
        new, title_page = None, False
        if s is not None:
            before = s - 1
            if before >= 0 and before not in skip and before not in taken and not pm.read[before] \
                    and _heading_at(reader, before, a.title, loose=loose):
                new, title_page = before, True
            elif not _heading_at(reader, s, a.title, loose=loose):
                # Earlier pages only, unless the number came from the Úlfljótur spreadsheet, whose Bls is often a page
                # or two off: a magazine prints a heading as an image or over a photo spread on the TOC's own page.
                ahead = _NEAR if "xlsx" in a.provenance.get("pages", "") else 0
                near = [i for i in range(max(0, s - _NEAR), min(len(page_texts), s + ahead + 1))
                        if i != s and i not in skip and i not in taken]
                new = next((i for i in near if _heading_at(reader, i, a.title, loose=loose)), None)
                if new is None and top_read is not None and a.page_start is not None and a.page_start > top_read:
                    del starts[id(a)]
                    a.pdf_pages, moved = None, True
                    add_note(a, f"upphafsbls. {a.page_start} er hvergi prentuð í skjalinu og titillinn "
                                "fannst ekki á síðunum")
                    continue
        else:
            # Task 23b: the page that prints the registry's number first, when it prints the title (a map too ambiguous
            # to locate by: TL 1971's "Um örorkumöt" on p. 51, not Páll Sigurðsson's "Örorkumöt" on p. 37)
            at_registry = [i for i in range(len(page_texts)) if number is not None and a.page_start is not None
                           and i not in skip and i not in taken and number(i) == a.page_start]
            new = next((i for i in at_registry if _heading_at(reader, i, a.title, loose=loose, min_share=0.6)), None)
            lo = max((starts[id(b)] for b in entries[:k] if id(b) in starts), default=-1)
            hi = min((starts[id(b)] for b in entries[k + 1:] if id(b) in starts), default=len(page_texts))
            if new is None and a.page_start in pm.contested and len(between := [i for i in at_registry if lo < i < hi]) == 1:
                new = between[0]          # a number two pages print: the one in registry order (TL 2000, p. 261)
            if new is None:
                order = list(range(lo + 1, hi)) + [i for i in range(len(page_texts)) if not lo < i < hi]
                new = next((i for i in order if i not in skip and i not in taken          # registry order first: a
                            and _heading_at(reader, i, a.title, loose=loose, min_share=0.6)), None)   # jumbled TOC may err
        if new is None:
            continue
        starts[id(a)], moved = new, True
        at_page = number if number is not None else (lambda i: pm.printed[i] if 0 <= i < len(pm.printed) else None)
        printed = at_page(new)
        if printed is None and (following := at_page(new + 1)) is not None:
            printed = following - 1                   # an unnumbered title page
        registry, at = _registry_name(a), f"bls. {printed}" if printed is not None else f"pdf-síðu {new + 1}"
        # Not R42 deviations but notes: an entry of Claude's list found by its title (the list itself sends the issue
        # to review) and an unnumbered title page just before the registry's page (the title-page rule, fix 4).
        sources = a.provenance.get("title", "").split("+")
        claude = "llm" in sources
        if s is None and a.page_start is None:
            text = f"{registry} gaf enga upphafsblaðsíðu; greinin fannst eftir titli sínum á {at}"
            if claude:
                add_note(a, text)
            elif "toc" in sources:
                unnumbered.append((k, a, text, at))         # R43: a note or a deviation once every start is known
            else:
                flag(a, text)
        elif s is None and printed is None and a.page_start is not None and (
                _margin_prints(page_texts[new], a.page_start) or number is not None
                and number(new, max(0, new - 3), min(len(page_texts) - 1, new + 3)) == a.page_start):
            add_note(a, f"{registry} segir bls. {a.page_start}; greinin fannst eftir titli sínum á {at}, sem ber það "
                        "blaðsíðutal þótt það yrði ekki lesið í blaðsíðukortið")   # (Task 23b: no deviation)
        elif s is None and (printed is None or abs(printed - a.page_start) > _NEAR):
            (add_note if claude else flag)(a, f"{registry} segir bls. {a.page_start}; greinin fannst eftir titli sínum "
                                              f"á {at}")
        elif printed is not None and printed != a.page_start:
            (add_note if title_page and printed == a.page_start - 1 else flag)(
                a, f"{registry} segir bls. {a.page_start}, prentað bls. {printed}")
        if printed is not None and printed != a.page_start:
            a.page_start, a.provenance["pages"] = printed, "page"
    for k, a, text, at in unnumbered:
        if _in_contents_order(entries, starts, k, len(page_texts)):
            add_note(a, f"blaðsíðutal greinarinnar í efnisyfirlitinu var ólæsilegt; greinin fannst eftir titli sínum á {at}")
        else:
            flag(a, text)
    if not moved:
        return
    last_text = max((i for i, t in enumerate(page_texts) if t.strip()), default=len(page_texts) - 1)
    located = []
    for a, end in zip(entries, registry_end):
        if id(a) in starts:
            a.page_end = end if a.provenance.get("pages") != "page" else None
            located.append((starts[id(a)], a))
    assign_ranges(located, pm, last_text)


def _margin_prints(text: str, page: int) -> bool:
    """The page's running head or foot (its first or last two lines) is the folio `page`: the number alone, or with the
    journal's name or a running word before or after it ("Afmælisrit  379" on Úlfljótur 2017-3's spreads, which the page
    map cannot read: two printed pages to a PDF page); "12. árg." is no folio."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    side = r"[^\W\d_][^\W\d_.]*(?:\s+[^\W\d_][^\W\d_.]*){0,2}"      # the journal's name, up to three words
    folio = re.compile(rf"{page}|{side}\s*[/|•–-]?\s*{page}|{page}\s*[/|•–-]?\s*{side}")
    return any(folio.fullmatch(ln) for ln in lines[:2] + lines[-2:])


def _in_contents_order(entries: list[ArticleEntry], starts: dict[int, int], k: int, n_pages: int) -> bool:
    """Does entry k start after every earlier entry's start and before every later entry's (those located)?"""
    here = starts.get(id(entries[k]))
    if here is None:
        return False
    lo = max((starts[id(b)] for b in entries[:k] if id(b) in starts), default=-1)
    hi = min((starts[id(b)] for b in entries[k + 1:] if id(b) in starts), default=n_pages)
    return lo < here < hi


PLACED_NOTE = "staðsett eftir nágrönnum sínum í listanum"


def place_by_neighbours(entries: list[ArticleEntry], registry_ends: dict[int, int | None], pm: PageMap,
                        page_texts: list[str], reader: PageReader) -> dict[int, float | None]:
    """Task 23d, rule 2 (R48a): an entry still without a page after relocate_by_title (the map could not locate its
    printed start, title search missed it: a short section item, a heading in body type, a page another entry starts
    on too) is placed at its printed start plus the offset (PDF index − printed start) of the nearest located entries
    before and after it in list order, where that page or one either side prints its title or opens with its section
    label (_prints_entry). Neighbours that disagree have a restart between them (TL 1993: each hefti numbered from 1;
    an unnumbered page): each offset is tried and only a page that prints the title counts (two such pages: none). The
    place stays between the neighbours' starts (list order is page order within a hefti: never in another hefti). A
    placed entry gets a plain note (no deviation: the page prints its title), is a neighbour for the entries after it,
    and the ranges are recomputed, so that the entries around it end before it (TL 1993 idx 3 no longer runs on through
    pp. 43–65). `registry_ends`: the end page each entry's registry gave (by id), as relocate_by_title uses them.
    Returns the entries placed (by id), each with the top of its heading on a start page it shares with the item above
    it (_SHARED lines of text or more over the heading; None when it heads its page, a byline or a name over it): the
    contents gate counts them as the list's failures (tocgate.check), and analysis reads a shared start page from that
    heading down, not the item above it (pagekind.below)."""
    n = len(page_texts)
    starts = {id(a): a.pdf_pages[0] for a in entries if a.pdf_pages}
    placed: dict[int, float | None] = {}
    for k, a in enumerate(entries):
        if a.pdf_pages or a.page_start is None or not a.title:
            continue
        before = next((b for b in reversed(entries[:k]) if id(b) in starts and b.page_start is not None), None)
        after = next((b for b in entries[k + 1:] if id(b) in starts and b.page_start is not None), None)
        lo = starts[id(before)] if before is not None else 0
        hi = starts[id(after)] if after is not None else n - 1
        # (a page where another entry of the same title starts prints that entry's heading: TL 1973's two memorials
        # of Jóhann J. Ragnarsson, the second without a heading of its own)
        same = {starts[id(b)] for b in entries if id(b) in starts and _fold(b.title or "") == _fold(a.title)}
        hits: dict[int, float] = {}
        for off in dict.fromkeys(starts[id(b)] - b.page_start for b in (before, after) if b is not None):
            at = a.page_start + off
            for i in (at, at - 1, at + 1):
                if lo <= i <= hi and i not in same and (y := _prints_entry(reader(i), a)) is not None:
                    hits[i] = y
                    break
        if len(hits) == 1:
            (i, y), = hits.items()
            over = [ln for ln in PK.content(reader(i)) if ln.y < y and len(re.findall(r"[^\W\d_]", ln.text)) >= 2]
            starts[id(a)], placed[id(a)] = i, y if len(over) >= _SHARED else None
            add_note(a, f"{PLACED_NOTE} (pdf-síða {i + 1})")
    if placed:
        last_text = max((i for i, t in enumerate(page_texts) if t.strip()), default=n - 1)
        located = []
        for a in entries:
            if id(a) in starts:
                a.page_end = registry_ends.get(id(a)) if a.provenance.get("pages") != "page" else None
                located.append((starts[id(a)], a))
        assign_ranges(located, pm, last_text)
    return placed


def _prints_entry(page: Page, a: ArticleEntry) -> float | None:
    """The top of the entry's heading on the page (_entry_heading), else None."""
    line = _entry_heading(page, a)
    return line.y if line is not None else None


def _entry_heading(page: Page, a: ArticleEntry) -> Line | None:
    """Where the page prints the entry's title as a heading, bold or set a little larger than the lines just above and
    below it, wherever it stands (a short item starts where it may on a page it shares: TL 1973's "BÓKAÞÁTTUR" near
    the foot of p. 68), or opens with the entry's section label: the heading's (or label's) first line, else None. A
    copy of the title that is no heading (a running head, a name over a memorial's heading: TL 1993 p. 75; running
    text) is passed over for the next one. (Not the page's body size: a title in 30 pt may make most of its page,
    Lögrétta 2011-1 p. 53, and a bibliography in small type most of an article's last page.)"""
    label, rest = split_label(a.title or "")
    if label and (at := next((ln for ln in FP.top_lines(page)[:3] if FP._match_label(ln.text) == label.lower()), None)):
        return at
    lines = FP._content_lines(page, 1.0)
    for _ in range(_COPIES):
        h = FP.printed_title(Page(lines, page.height, page.width), rest, contain=True, bottom=1.0,
                             loose=a.provenance.get("title") == "filename") if rest else None
        if h is None:
            return None
        around = [lines[i] for i in (h.first - 1, h.last + 1) if 0 <= i < len(lines)]
        if all(ln.bold for ln in lines[h.first:h.last + 1]) or around and all(h.size >= FP.EMPHASIS * ln.size
                                                                              for ln in around):
            return lines[h.first]
        lines = lines[:h.first] + lines[h.last + 1:]
    return None


_COPIES = 3              # copies of a title on one page that _prints_entry reads before it gives up
_SHARED = 3              # lines of text over a placed entry's heading that are another item's: it shares the page


NOT_FOUND_NOTE = "greinar fundust ekki í skjalinu"


def drop_beyond_document(entries: list[ArticleEntry], pm: PageMap) -> tuple[list[ArticleEntry], str | None]:
    """Registry entries that start past the last page number read in the file and whose title no page prints in a
    heading are left out (TL 1955 1. hefti: the volume's TOC lists pages 65–264, the file ends at 64): (the entries
    without them, the issue-level note, starting with NOT_FOUND_NOTE, or None). The caller sends the issue to review:
    the article may be in the file after all, on pages whose numbers were not read and with a heading not found."""
    last_read = max((i for i, r in enumerate(pm.read) if r), default=None)
    top = pm.printed[last_read] if last_read is not None else None
    beyond = [a for a in entries if a.pdf_pages is None and top is not None and a.page_start is not None
              and a.page_start > top]
    if not beyond:
        return entries, None
    pages = sorted(a.page_start for a in beyond)
    span = f"{pages[0]}–{pages[-1]}" if len(pages) > 1 else f"{pages[0]}"
    note = (f"{NOT_FOUND_NOTE}: {len(beyond)} (bls. {span}; síðasta lesna blaðsíðutal {top}) — þær eru ekki taldar "
            "með; athugaðu hvort þær vanti í skjalið eða hafi ekki fundist")
    return [a for a in entries if a not in beyond], note


class PageNumbers(NamedTuple):
    """Printed page numbers of an issue PDF (Task 22c, point 3). `at(i, lo, hi)`: the page map's number of PDF page i;
    where the map has none (ambiguous, or a gap between its segments) the number read on that very page with the
    source's patterns, else (given the article's pages lo–hi) the one the reads on two or more of its pages near i agree
    on (Lögrétta 2005 binds articles from several issues, numbered 3, 23, 35, 17 and 55: no single map fits, but its
    pages print their numbers). `evidence(i)`: what the checks may take as the page's printed number: the map's where it
    is trusted (analyze._printed_at), else a read on the very page, never a number the map only located by (R34,
    R41)."""
    at: Callable[..., int | None]
    evidence: Callable[[int], int | None]


def page_numbers(pm: PageMap, texts: list[str], patterns: Sequence) -> PageNumbers:
    reads: dict[int, int | None] = {}

    def read(i: int) -> int | None:
        if i not in reads:
            reads[i] = read_page_numbers(texts[i:i + 1], patterns)[0] if 0 <= i < len(texts) else None
        return reads[i]

    def mapped(i: int) -> bool:
        return 0 <= i < len(texts) and pm.offset is not None and pm.printed[i] is not None

    def at(i: int, lo: int | None = None, hi: int | None = None) -> int | None:
        if not 0 <= i < len(texts):
            return None
        if mapped(i):
            return pm.printed[i]
        if (own := read(i)) is not None or lo is None or hi is None:
            return own
        offsets = Counter(n - j for j in range(max(lo, i - 3), min(hi, i + 3) + 1) if j != i
                          and (n := read(j)) is not None)
        if offsets and (best := offsets.most_common(1)[0])[1] >= 2:
            return i + best[0]
        return None

    def evidence(i: int) -> int | None:
        if mapped(i):
            return pm.printed[i] if pm.read[i] or pm.quality >= C.PAGE_MAP_MIN_QUALITY else None
        return read(i)
    return PageNumbers(at, evidence)


def fill_printed_pages(a: ArticleEntry, number: Callable[[int], int | None], first: int, last: int) -> None:
    """A located article's printed start and end where the map gave none (Task 22c, point 3); a located article with
    no printed start at all is flagged (R42)."""
    if a.page_start is None and (start := number(first)) is not None:
        a.page_start, a.provenance["pages"] = start, "page"
    if a.page_end is None and a.page_start is not None and (end := number(last)) is not None and end >= a.page_start:
        a.page_end = end
    if a.page_start is None:
        flag(a, f"greinin er á pdf-síðu {first + 1} en blaðsíðutal hennar fannst ekki")


def end_in_file(a: ArticleEntry, page: Callable[[int], Page], n: int, pictures: Sequence[float],
                printed: Sequence[int | None]) -> PK.End:
    """Where an article ends inside its own file of n pages (an article file the user split from a scan may run into
    the next item); numbers from the file's own map (`printed`, when trusted), else counted from its first page."""
    e = PK.article_end(page, 0, n - 1, a.title, pictures.__getitem__ if pictures else None, a.authors)

    def number(i: int) -> int | None:
        if printed and printed[i] is not None:
            return printed[i]
        return a.page_start + i if a.page_start is not None else None
    end_article(a, e, number)
    return e


class Ends(NamedTuple):
    """What end_ranges found: per article (by id) its End, and the pages no article's (adverts, blank pages)."""
    ends: dict[int, PK.End]
    outside: list[int]


def _range_text(first: int, last: int, number: Callable[[int], int | None]) -> str:
    a, b = number(first), number(last)
    if a is not None and b is not None:
        return f"bls. {a}" if a == b else f"bls. {a}–{b}"
    return f"pdf-síðu {first + 1}" if first == last else f"pdf-síðum {first + 1}–{last + 1}"


def end_article(a: ArticleEntry, e: PK.End, number: Callable[[int], int | None],
                registry_end: int | None = None) -> None:
    """Apply an article's End (pagekind.article_end): its range ends on the last page of its own text and page_end is
    that page's printed number (a registry's own end page that disagrees, `registry_end`, is noted); an uncertain end
    and an item the registry does not list are flagged (R42)."""
    first, last = a.pdf_pages
    if e.last != last:
        a.pdf_pages = [first, e.last]
        end = number(e.last)
        if end is not None and registry_end is not None and end != registry_end:
            add_note(a, f"skráin segir að greinin endi á bls. {registry_end}, prentuð lokasíða er bls. {end}")
        a.page_end = end                              # unknown rather than the end of the longer range
    if e.uncertain:
        kept = e.kept or (e.last,)
        flag(a, f"lok greinar óviss: á eftir texta hennar kemur efni á {_range_text(kept[0], kept[-1], number)} sem "
                "er hvorki framhald hennar né upphaf annars efnis (mynd, auglýsing eða texti án textalags?); það er "
                "talið með greininni")
    if e.unlisted:
        page, heading = e.unlisted
        what = f"„{heading}“" if heading else "efni"
        at = f"bls. {number(page)}" if number(page) is not None else f"pdf-síðu {page + 1}"
        flag(a, f"á eftir greininni hefst {what} á {at} sem skráin nefnir ekki; greinin er talin enda þar")


def end_ranges(entries: Sequence[ArticleEntry], reader: PageReader, number: Callable[..., int | None],
               registry_ends: dict[int, int | None] | None = None, tops: dict[int, float | None] | None = None) -> Ends:
    """End every located article of an issue PDF on the last page of its own text (Task 22c; pagekind). `number(i, lo,
    hi)`: the printed number of PDF page i of an article on pages lo–hi; `registry_ends`: the end page the registry
    gave each article (by id), if any; `tops`: for an entry placed by its neighbours (place_by_neighbours, by id), the
    top of its heading on its start page, which it reads from there down (the item above it signs off there: TL 1971's
    "Páll Líndal." over "LEIÐRÉTTING:")."""
    ends: dict[int, PK.End] = {}
    outside: set[int] = set()
    order = sorted((a for a in entries if a.pdf_pages), key=lambda a: a.pdf_pages[0])     # (stable: list order)
    following = {id(a): b for a, b in zip(order, order[1:])}
    under = {id(b) for a, b in zip(order, order[1:]) if a.pdf_pages[0] == b.pdf_pages[0]}  # starts below another
    for a in entries:
        if not a.pdf_pages:
            continue
        first, last = a.pdf_pages
        page: Callable[[int], Page] = reader
        if (top := (tops or {}).get(id(a))) is not None:
            own = PK.below(reader(first), top)
            page = lambda i, f=first, own=own: own if i == f else reader(i)      # noqa: E731
        # Task 23d: the next item in page order starts on the page after the range; the article may end on that page,
        # above that item's heading (its title printed as one, fix round 1)
        nb = following.get(id(a))
        shared = nb is not None and nb.pdf_pages[0] == last + 1
        e = PK.article_end(page, first, last, a.title, reader.picture, a.authors, reader.drawings, shared=shared,
                           next_heading=(lambda p, nb=nb: _entry_heading(p, nb)) if shared else None,
                           beside=id(a) in under)
        cut = _signed_after_item(a, page, first, e)
        if cut is not None:
            e = PK.article_end(page, first, cut[0] - 1, a.title, reader.picture, a.authors, reader.drawings)
        end_article(a, e, lambda i, f=first, la=e.last: number(i, f, la), (registry_ends or {}).get(id(a)))
        if cut is not None:
            at = number(cut[0], first, last)
            flag(a, f"undirritunin ({', '.join(cut[2])}) stendur á eftir fyrirsögninni „{cut[1]}“ á "
                    f"{f'bls. {at}' if at is not None else f'pdf-síðu {cut[0] + 1}'}; greinin er talin enda á undan "
                    "henni")
        ends[id(a)] = e
        outside |= set(e.outside)
    return Ends(ends, sorted(outside))


def _signed_after_item(a: ArticleEntry, reader: Callable[[int], Page], first: int,
                       e: PK.End) -> tuple[int, str, list[str]] | None:
    """A range that ran on into the next item (Task 23b): the signature where its text ends names other people than
    the registry (TL 1996: "Allan V. Magnússon" under the Dómarafélag report, the registry's "Jón L. Arnalds"), and a
    page between its start and that signature starts another item (pagekind.item_heading). (that page, its heading, the
    signature), else None: the signature belongs to that item, and the article ends before it."""
    if not e.signed or not a.authors or not _conflict(a.authors, list(e.signed)):
        return None
    t = PK.typography([reader(i) for i in range(first, min(first + PK._PROFILE_PAGES, e.last + 1))])
    if not t.sizes:
        return None
    own = {_fold(ln.text) for ln in reader(first).lines[:8]}      # a section's label over all its pages ("UMFJÖLLUN")
    for i in range(first + 1, e.last + 1):
        if (heading := PK.item_heading(reader(i), t, a.title)) and _fold(heading.split(" — ")[0]) not in own:
            return i, heading, list(e.signed)
    return None


def last_body_page(reader: PageReader, first: int, last: int) -> int:
    """The last page of a range that prints more than a page number, a running head or a stamp up its margin (a blank
    page ends many)."""
    def printed(i: int) -> bool:
        return any(not FP.is_furniture(ln) and not PK.margin_stamp(ln, reader(i)) for ln in reader(i).lines)
    return next((i for i in range(last, first - 1, -1) if printed(i)), first)


_COLLECTIVE = re.compile(r"^(?:ritstjórn\w*|ritnefnd\w*|stjórn\b.*|útgefandi|ritstjóri)$", re.I)
_MEMORIAL = re.compile(r"^(?:minning|in memoriam|minningarorð|eftirmæli)", re.I)


def is_memorial(a: ArticleEntry) -> bool:
    """An obituary: a "Minning: …" title, or a title that is only names ("Birgir Ásgeirsson — Páll Pálmason")."""
    title = a.title or ""
    if _MEMORIAL.match(title):
        return True
    parts = [x for x in re.split(r"\s+[—–-]\s+", title) if x.strip()]
    return bool(parts) and all(PK._name_only(x) for x in parts)


def signatures(a: ArticleEntry, page: Callable[[int], Page], first: int, e: PK.End) -> list[str]:
    """Who signed the article: for a memorial every part's signature on its pages, else the end signature (pagekind)."""
    names = list(e.signed)
    if is_memorial(a):
        t = PK.typography([page(i) for i in range(first, min(first + PK._PROFILE_PAGES, e.last + 1))])
        own = [PK.above(page(i), e.cut) if i == e.last else page(i) for i in range(first, e.last + 1)]   # (23d)
        parts = [n for p in own for n in PK.part_signatures(p, t, a.title)] if t.sizes else []
        names = [n for k, n in enumerate(parts + names) if n not in (parts + names)[:k]]
    return names


def _printed_anywhere(word: str, pages: Sequence[Page]) -> bool:
    w = f" {_fold(word)} "
    return any(w in f" {_fold(ln.text)} " for p in pages for ln in p.lines)


def read_printed_page(a: ArticleEntry, first: Page, last: Page | None, *,
                      sign: Callable[[ArticleEntry], Sequence[str]] | None = None) -> None:
    """Title, section label, authors and signature from the article's own pages (R40): firstpage.choose_title decides
    the title; a section label is evidence for the type unless Claude or the user gave it; the byline under or above
    the heading, or a credit, adds or respells authors (merge_authors; names that conflict with the registry's are
    flagged, R42); with no author at all, the article's signature (`sign(a)`, once its title is known: pagekind's
    reading; failing that, for an editorial, the name at the end of its last page); a signature that names other people
    than the registry is flagged (R42). A registry author that is a body ("Ritstjórn") printed on neither page is
    dropped and flagged. A page heading that is another title than the registry's (kept) is flagged."""
    loose = a.provenance.get("title") == "filename"
    registry_title = a.title
    choice = FP.choose_title(a.title, first, loose=loose)
    if choice.title and choice.title != a.title:
        a.title = choice.title
        if choice.from_page:
            a.provenance["title"] = "page"
    fixed_type = a.provenance.get("article_type") in ("llm", "user")
    if not fixed_type and (kind := label_type(choice.label)):
        a.article_type, a.provenance["article_type"] = kind, "page"
    elif not fixed_type and choice.from_page and a.title:
        kind = infer_article_type(None, a.title, "")
        if kind:
            a.article_type, a.provenance["article_type"] = kind, "page"
    if not a.title:
        return
    heading = FP.printed_title(first, a.title, loose=loose)
    if heading is None and not choice.from_page and registry_title and not is_label(registry_title) and not loose:
        _other_heading(a, first, registry_title)
    pages = [first] + ([last] if last is not None else [])
    for name in [x for x in a.authors if _COLLECTIVE.match(x)]:
        if not _printed_anywhere(name, pages):
            a.authors.remove(name)
            flag(a, f"höfundurinn „{name}“ úr skránni er hvergi prentaður við greinina og var felldur niður")
    printed = [n for n in FP.byline(first, heading, a.title) if not FP.is_label_line(n)]  # not "In Memoriam"
    registry = list(a.authors)
    authors = FP.merge_authors(registry, printed)
    if authors.from_page:
        a.authors, a.provenance["authors"] = authors.authors, "page"
    # the person an interview's title names is the interviewee, not its author (Task 23b), and so are the people its
    # lead introduces (fix round 2): a note, no deviation
    others = [p for p in printed if not any(_person(p, r) for r in registry)]
    introduced = a.article_type == "Viðtal" and bool(others) and all(_in_running_text(p, first) for p in others)
    if authors.note and _conflict(registry, printed) and not _interviewees_only(a.title, registry, printed) \
            and not introduced:
        flag(a, f"höfundar skrárinnar ({', '.join(registry)}) stangast á við síðuna ({', '.join(printed)}); "
                "skránni haldið")
    elif authors.note:
        add_note(a, authors.note)
    signed = list(sign(a)) if sign is not None else []
    if not a.authors and signed:
        a.authors, a.provenance["authors"] = signed, "page"
    elif signed and _conflict(a.authors, signed):
        flag(a, f"greinin er undirrituð ({', '.join(signed)}) en höfundar skrárinnar eru aðrir ({', '.join(a.authors)}); "
                "skránni haldið")
    editorial = a.article_type == "Ritstjórnargrein" or infer_article_type(None, a.title, "") == "Ritstjórnargrein"
    if not a.authors and editorial and last is not None and (names := FP.signature(last)):
        a.authors, a.provenance["authors"] = names, "page"   # (an editorial's name at its end, signed or not)


_VIDTAL_VID = re.compile(r"viðtal\w*\s+við\s+([^,:;–—(]+)", re.I)


def interviewees(title: str | None) -> list[str]:
    """The people an interview's title names ("… Viðtal við Pál Hreinsson, dómara …" → "Páll Hreinsson"), in the
    nominative, and the names the title is otherwise about (pagekind._subjects)."""
    return interview_subjects(title) + PK._subjects(title)


def interview_subjects(title: str | None) -> list[str]:
    """The people "Viðtal við …" in a title names, in the nominative ("VIÐTAL VIÐ JÓNAS HARALDSSON …" → "Jónas
    Haraldsson")."""
    names = []
    for m in _VIDTAL_VID.finditer(title or ""):
        toks: list[str] = []
        for t in m.group(1).split():
            caps = toks and toks[0].isupper()          # a name in capitals ends where the title's words begin
            if not (t[:1].isupper() or t.endswith(".")) or len(toks) == 4 or caps and not t.isupper():
                break
            if t.endswith(".") and len(t) > 2:         # "Viðtal við Markús Sigurbjörnsson. Þægilegt …": the name ends
                toks.append(t.rstrip("."))
                break
            toks.append(t)
        if len(toks) >= 2:
            names.append(_nominative(" ".join(recase_name(t) for t in toks)))
    return names


def recase_name(token: str) -> str:
    """A name token printed in capitals as a name is written ("HELGU" → "Helgu")."""
    return token.capitalize() if token.isupper() and len(token) > 1 else token


def _in_running_text(name: str, page: Page) -> bool:
    """Every line of the page's top part that prints the name runs on after it into a sentence, past any "og <Name>"
    ("Ástríður Gísladóttir er fulltrúi …", "Hjördís E. Harðardóttir og Arnór Halldórsson eru …"): an interview's lead
    introducing its people, no byline. A line that ends with the name, or goes on with a role ("hrl.", "lögmaður"), is
    a byline."""
    key = _fold(name).split()
    seen = False
    for ln in FP.top_lines(page):
        toks = ln.text.split()
        folded = [_fold(t) for t in toks]
        for i in range(len(toks) - len(key) + 1):
            if folded[i:i + len(key)] != key:
                continue
            seen = True
            rest = toks[i + len(key):]
            j = 0
            while j < len(rest) and (rest[j] in ("og", "&") or FP._is_name_token(rest[j]) or FP._is_initial(rest[j])):
                j += 1
            if j == len(rest) or not rest[j][:1].islower() or FP._ROLE_WORD.match(rest[j].strip(".,;:")):
                return False
    return seen


def _interviewees_only(title: str | None, registry: Sequence[str], printed: Sequence[str]) -> bool:
    """Every name the page prints that the registry lacks is a person the interview's title names."""
    others = [p for p in printed if not any(_person(p, r) for r in registry)]
    named = interviewees(title)
    return bool(others) and all(any(_person(p, n) for n in named) for p in others)


def given_first(name: str) -> str:
    """A catalogue's "Surname, Given" as the name is printed, "Given Surname" ("Yoran, Aaron" → "Aaron Yoran"; Task
    23b); any other name as it is."""
    head, comma, rest = name.partition(",")
    if comma and "," not in rest and rest.strip() and len(head.split()) <= 2 and rest.strip()[:1].isupper():
        return f"{rest.strip()} {head.strip()}"
    return name


def _person(a: str, b: str) -> bool:
    """The same person in two spellings, with a word more ("Arnþór Gunnarsson" / "Arnþór Gunnarsson Sagnfræðingur"), a
    given name cut short ("Ben. Sigurjónsson" / "Benedikt Sigurjónsson"), a middle name as its initial ("Róbert R.
    Spanó" / "Róbert Ragnar Spanó") or in a catalogue's order ("Yoran, Aaron" / "Aharon Yoran")."""
    a, b = given_first(a), given_first(b)
    fa, fb = _fold(a).split(), _fold(b).split()
    ta, tb = set(fa), set(fb)
    if FP._same_person(a, b) or ta and tb and (ta <= tb or tb <= ta):
        return True
    if not (fa and fb and fa[-1] == fb[-1]):
        return False
    if fa[0] == fb[0] and len(fa) == len(fb) and all(x.startswith(y) or y.startswith(x) for x, y in zip(fa, fb)):
        return True                                   # "R." for "Ragnar"
    a0, b0 = a.split()[0], b.split()[0]                # the cut name ends in a period
    return a0.endswith(".") and fb[0].startswith(fa[0]) or b0.endswith(".") and fa[0].startswith(fb[0])


def _conflict(registry: Sequence[str], printed: Sequence[str]) -> bool:
    """The page names someone the registry does not and the registry someone the page does not: not merely a co-author
    the page adds (R42). An outline heading the byline reader took for a name ("I Introduction") names nobody."""
    printed = [p for p in printed if not re.match(r"(?:[IVX]+|\d+)\.?\s|[A-Z]\.\s+\S+$", p)]   # "A. Perspectives"
    return any(not any(_person(p, r) for r in registry) for p in printed) and \
        any(not any(_person(r, p) for p in printed) for r in registry)


_OTHER_TITLE = 0.5            # a page heading this little like the registry title is another title


def _other_heading(a: ArticleEntry, first: Page, registry_title: str) -> None:
    """The registry title is not printed at the top of the article's first page and the page's main heading is
    another title altogether (R42): the registry title is kept and the pair flagged. Without a heading set larger than
    the body, the title under the author's "Name:" line (_named_heading) is the heading."""
    text = page_heading_text(first)
    if text is None or FP.is_label(text) or FP.is_label_line(text) or PK.part_of_title(text, registry_title):
        return                                        # (a double obituary's page heads its first name)
    if FP.match_score(text, registry_title).score < _OTHER_TITLE:
        flag(a, f"fyrirsögnin á fyrstu síðu („{text}“) er annar titill en skrárinnar („{registry_title}“); "
                "titli skrárinnar haldið")


def page_heading_text(page: Page) -> str | None:
    """The page's main heading (FP.page_heading), or failing that the title under the author's "Name:" line."""
    h = FP.page_heading(page)
    return h.text if h is not None else _named_heading(page)


_NAME_COLON = re.compile(r"^(?P<name>[^:]{4,60}):$")


def _named_heading(page: Page) -> str | None:
    """TL's heading: the lines in capitals under the author's "Name:" line, in the line's type ("Trausti Fannar
    Valsson:" / "SÉRSTAKT HÆFI Í SVEITARFÉLÖGUM*", TL 2024-4, under the DOI line, where an outline in smaller type makes
    the page's body size the outline's and page_heading finds nothing larger). Fix round 2."""
    lines = FP.top_lines(page)
    for k, ln in enumerate(lines):
        m = _NAME_COLON.match(ln.text.strip())
        if not m or not looks_like_person(m.group("name"), strict=False):
            continue
        title: list[str] = []
        for nxt in lines[k + 1:k + 5]:
            letters = re.sub(r"[^\w]|\d|_", "", nxt.text)
            if abs(nxt.size - ln.size) > 0.05 * ln.size or not letters or not letters.isupper() \
                    or nxt.text.rstrip().endswith(":") or re.match(r"\s*\d", nxt.text) \
                    or FP.is_label_line(nxt.text) or label_type(nxt.text.strip(" :")):
                break                                 # (the outline: "EFNISYFIRLIT:", "1. INNGANGUR")
            title.append(nxt.text)
        return FP.join_title(title) if title else None
    return None
