"""The printed page decides (Task 22b, ruling R40): where an article of an issue PDF starts, which registry entries the
file holds at all, and the title, authors and type an article prints on its own pages.

analyze locates every registry entry by its page number first (locate_articles); relocate_by_title then moves a start
to the page that prints the title, drop_beyond_document leaves out entries the file does not contain, and
read_printed_page reads the article's first and last page (firstpage.py) for the metadata.
"""
from __future__ import annotations

from typing import NamedTuple

from engine.timarit import firstpage as FP
from engine.timarit.article_type import infer_article_type
from engine.timarit.firstpage import Page, PageReader
from engine.timarit.manifest import ArticleEntry
from engine.timarit.pagemap import PageMap
from engine.timarit.toc import is_label, label_type, split_label


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
                      page_texts: list[str], reader: PageReader, skip: set[int]) -> None:
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
    different article. `skip`: TOC pages, the pages before them and the cover."""
    starts = {id(a): a.pdf_pages[0] for a in entries if a.pdf_pages}
    last_read = max((i for i, r in enumerate(pm.read) if r), default=-1)
    top_read = pm.printed[last_read] if last_read >= 0 else None
    moved = False
    for k, a in enumerate(entries):
        if not a.title or is_label(a.title):
            continue
        loose = a.provenance.get("title") == "filename"
        taken = set(starts.values())
        s = starts.get(id(a))
        new = None
        if s is not None:
            before = s - 1
            if before >= 0 and before not in skip and before not in taken and not pm.read[before] \
                    and _heading_at(reader, before, a.title, loose=loose):
                new = before
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
            lo = max((starts[id(b)] for b in entries[:k] if id(b) in starts), default=-1)
            hi = min((starts[id(b)] for b in entries[k + 1:] if id(b) in starts), default=len(page_texts))
            order = list(range(lo + 1, hi)) + [i for i in range(len(page_texts)) if not lo < i < hi]
            new = next((i for i in order if i not in skip and i not in taken          # registry order first: a
                        and _heading_at(reader, i, a.title, loose=loose, min_share=0.6)), None)   # jumbled TOC may err
        if new is None:
            continue
        starts[id(a)], moved = new, True
        printed = pm.printed[new] if pm.printed[new] is not None else (
            pm.printed[new + 1] - 1 if new + 1 < len(pm.printed) and pm.printed[new + 1] is not None else None)
        if printed is not None and printed != a.page_start:
            if a.page_start is not None:
                add_note(a, f"{_registry_name(a)} segir bls. {a.page_start}, prentað bls. {printed}")
            a.page_start, a.provenance["pages"] = printed, "page"
    if not moved:
        return
    last_text = max((i for i, t in enumerate(page_texts) if t.strip()), default=len(page_texts) - 1)
    located = []
    for a, end in zip(entries, registry_end):
        if id(a) in starts:
            a.page_end = end if a.provenance.get("pages") != "page" else None
            located.append((starts[id(a)], a))
    assign_ranges(located, pm, last_text)


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


def last_body_page(reader: PageReader, first: int, last: int) -> int:
    """The last page of a range that prints more than a page number or a running head (a blank page ends many)."""
    return next((i for i in range(last, first - 1, -1) if any(not FP.is_furniture(ln) for ln in reader(i).lines)), first)


def read_printed_page(a: ArticleEntry, first: Page, last: Page | None) -> None:
    """Title, section label, authors and (for an editorial) its signature from the article's own pages (R40):
    firstpage.choose_title decides the title; a section label is evidence for the type unless Claude or the user gave
    it; the byline under or above the heading, or a credit, adds or respells authors (merge_authors); an editorial
    with no author named is signed at the end of its last page."""
    loose = a.provenance.get("title") == "filename"
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
    authors = FP.merge_authors(a.authors, FP.byline(first, heading, a.title))
    if authors.from_page:
        a.authors, a.provenance["authors"] = authors.authors, "page"
    if authors.note:
        add_note(a, authors.note)
    editorial = a.article_type == "Ritstjórnargrein" or infer_article_type(None, a.title, "") == "Ritstjórnargrein"
    if not a.authors and editorial and last is not None and (signed := FP.signature(last)):
        a.authors, a.provenance["authors"] = signed, "page"
