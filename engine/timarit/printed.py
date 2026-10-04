"""The printed page decides (Task 22b, ruling R40): where an article of an issue PDF starts, which registry entries the
file holds at all, and the title, authors and type an article prints on its own pages.

analyze locates every registry entry by its page number first (locate_articles); relocate_by_title then moves a start
to the page that prints the title, drop_beyond_document leaves out entries the file does not contain, end_ranges ends
each article on the last page of its own text (pagekind.py, Task 22c), and read_printed_page reads the article's first
and last page (firstpage.py) for the metadata.

Significant deviations between the registry and the printed pages are flagged for the user's review (R42): an analysis
note that starts with FRAVIK, which sends the issue to review.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Callable, NamedTuple, Sequence

from engine.timarit import config as C
from engine.timarit import firstpage as FP
from engine.timarit import pagekind as PK
from engine.timarit.article_type import infer_article_type
from engine.timarit.firstpage import Page, PageReader
from engine.timarit.manifest import ArticleEntry
from engine.timarit.pagemap import PageMap, read_page_numbers
from engine.timarit.toc import is_label, label_type, split_label
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
    or one more than _NEAR pages off), are flagged."""
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
            lo = max((starts[id(b)] for b in entries[:k] if id(b) in starts), default=-1)
            hi = min((starts[id(b)] for b in entries[k + 1:] if id(b) in starts), default=len(page_texts))
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
        claude = "llm" in a.provenance.get("title", "").split("+")
        if s is None and a.page_start is None:
            (add_note if claude else flag)(a, f"{registry} gaf enga upphafsblaðsíðu; greinin fannst eftir titli sínum "
                                              f"á {at}")
        elif s is None and (printed is None or abs(printed - a.page_start) > _NEAR):
            (add_note if claude else flag)(a, f"{registry} segir bls. {a.page_start}; greinin fannst eftir titli sínum "
                                              f"á {at}")
        elif printed is not None and printed != a.page_start:
            (add_note if title_page and printed == a.page_start - 1 else flag)(
                a, f"{registry} segir bls. {a.page_start}, prentað bls. {printed}")
        if printed is not None and printed != a.page_start:
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
               registry_ends: dict[int, int | None] | None = None) -> Ends:
    """End every located article of an issue PDF on the last page of its own text (Task 22c; pagekind). `number(i, lo,
    hi)`: the printed number of PDF page i of an article on pages lo–hi; `registry_ends`: the end page the registry
    gave each article (by id), if any."""
    ends: dict[int, PK.End] = {}
    outside: set[int] = set()
    for a in entries:
        if not a.pdf_pages:
            continue
        first, last = a.pdf_pages
        e = PK.article_end(reader, first, last, a.title, reader.picture, a.authors, reader.drawings)
        end_article(a, e, lambda i, f=first, la=e.last: number(i, f, la), (registry_ends or {}).get(id(a)))
        ends[id(a)] = e
        outside |= set(e.outside)
    return Ends(ends, sorted(outside))


def last_body_page(reader: PageReader, first: int, last: int) -> int:
    """The last page of a range that prints more than a page number or a running head (a blank page ends many)."""
    return next((i for i in range(last, first - 1, -1) if any(not FP.is_furniture(ln) for ln in reader(i).lines)), first)


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
        parts = [n for i in range(first, e.last + 1) for n in PK.part_signatures(page(i), t, a.title)] if t.sizes else []
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
    if authors.note and _conflict(registry, printed):
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


def _person(a: str, b: str) -> bool:
    """The same person in two spellings, with a word more ("Arnþór Gunnarsson" / "Arnþór Gunnarsson Sagnfræðingur") or
    a given name cut short ("Ben. Sigurjónsson" / "Benedikt Sigurjónsson")."""
    fa, fb = _fold(a).split(), _fold(b).split()
    ta, tb = set(fa), set(fb)
    if FP._same_person(a, b) or ta and tb and (ta <= tb or tb <= ta):
        return True
    if not (fa and fb and fa[-1] == fb[-1]):
        return False
    a0, b0 = a.split()[0], b.split()[0]                # the cut name ends in a period
    return a0.endswith(".") and fb[0].startswith(fa[0]) or b0.endswith(".") and fa[0].startswith(fb[0])


def _conflict(registry: Sequence[str], printed: Sequence[str]) -> bool:
    """The page names someone the registry does not and the registry someone the page does not: not merely a co-author
    the page adds (R42). An outline heading the byline reader took for a name ("I Introduction") names nobody."""
    printed = [p for p in printed if not re.match(r"(?:[IVX]+|\d+)\.?\s", p)]
    return any(not any(_person(p, r) for r in registry) for p in printed) and \
        any(not any(_person(r, p) for p in printed) for r in registry)


_OTHER_TITLE = 0.5            # a page heading this little like the registry title is another title


def _other_heading(a: ArticleEntry, first: Page, registry_title: str) -> None:
    """The registry title is not printed at the top of the article's first page and the page's main heading is
    another title altogether (R42): the registry title is kept and the pair flagged."""
    h = FP.page_heading(first)
    if h is None or FP.is_label(h.text) or FP.is_label_line(h.text) or PK.part_of_title(h.text, registry_title):
        return                                        # (a double obituary's page heads its first name)
    if FP.match_score(h.text, registry_title).score < _OTHER_TITLE:
        flag(a, f"fyrirsögnin á fyrstu síðu („{h.text}“) er annar titill en skrárinnar („{registry_title}“); "
                "titli skrárinnar haldið")
