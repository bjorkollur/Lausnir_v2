"""Does a contents list the parser read hold on the issue's own pages? (Task 23b, ruling R47.)

The heuristic contents parser fails on whole issues: rows shifted by one item (every title on the next entry's page),
items merged into one title, a cover blurb read as titles, "Annað efni" blocks, most articles missing, entries on an
advert. Claude's list of the same kind of issue was right 25 times of 27 in audit 4, so a parsed list that fails on its
pages is replaced by Claude's (analyze). After the list is located and every article verified, `check` measures:

- `found`: the entries their start page holds, of `entries`, those whose registry title (R41 snapshot) is more than a
  section label or a column rubric ("Frá ritstjórn", "Ávarp"). The page holds an entry when it prints the title
  (title_on_page), a form of it (without "VIÐTAL VIÐ <NAFN>", a byline in capitals in front, another entry's byline
  run on behind, its words a letter or two apart: "Clausen" / "Claessen"), a person the entry names at a line's start
  (its registry author, an interview's subject), or the label of its kind at the top (an editorial's over the first
  entry, "VIÐTAL" over an interview). Fix round 1: the title alone counted lists that work as failing
  (Lögmannablaðið's interviews and columns, TL's editorials). Fix round 2: a name or a label does not place an entry
  whose title is printed on a neighbour's start page; a row the parser read without a title ("—") is counted and never
  found; the entries found by a name or a label only are returned (`proxies`) for a plain note on each;
- `shifted`: of the others, those whose title is printed on the next or the previous entry's start page instead;
- `on_filler`: the entries whose start page is an advert or a blank page (pagekind) without their title;
- `unclaimed`: pages of article text that no entry's range covers and that are no advert or blank page (non_article),
  from the first page after the cover and the contents (fix round 2: TL 1971's pdf 0-231, before its first located
  entry, in the type of the page itself): articles the list does not have. Task 23c: the first COVERAGE_MAX_GAP_PAGES
  pages after the contents are front matter (masthead, editor's note, adverts) and never counted, and a page whose text
  layer is garbled (replacement characters) is no readable article text: in TL 2009-2012 ranges are cut short in those
  pages, which is no article missing from the list. `pages` is the document's pages from the same first page on.

The list fails when the share found is below config.TOC_MIN_TITLE_SHARE, when nothing could be measured (only rubrics
and section labels: fix round 2, such a list passed at a share of 1.0), when more titles stand one entry off than on
their own page, or (Task 23c) when the unclaimed pages exceed both config.TOC_MAX_UNCLAIMED_PAGES and
config.TOC_MAX_UNCLAIMED_SHARE of `pages`: the titles alone pass a list that covers only the end of a volume (TL 1989:
6 of 7 found, the first entry on pdf 224 of 304). (The shift test implies a share below one half, so at any threshold
from 0.5 up it fails no list the share test does not; it stays to name the kind of failure in the note, and as the test
if the threshold is set lower.) `on_filler` is recorded for the report and named in the note, but decides nothing:
measured on the bulk, a magazine article that opens on a full-page photograph with its title set as a picture looks like
an advert (Lögmannablaðið's interviews: five lists that hold).
"""
from __future__ import annotations

import difflib
import re
from typing import Callable, NamedTuple, Sequence

from engine.timarit import config as C
from engine.timarit import firstpage as FP
from engine.timarit import pagekind as PK
from engine.timarit.firstpage import Page
from engine.timarit.authors import looks_like_person
from engine.timarit.manifest import ArticleEntry
from engine.timarit.printed import interview_subjects
from engine.timarit.toc import label_type
from engine.timarit.ulfljotur_xlsx import _fold


class TocCheck(NamedTuple):
    entries: int            # entries whose registry title is more than a section label or a column rubric
    found: int              # ... that their own start page holds (found_at)
    shifted: int            # ... printed on the next or previous entry's start page instead
    on_filler: int          # entries that start on an advert or a blank page (no text of their own)
    unclaimed: int          # pages of readable article text no entry covers (not the front matter)
    proxies: tuple[tuple[int, str], ...] = ()   # (index, by what) of the entries found by a name or a label only
    pages: int = 0          # the document's pages after the cover and the contents (what `unclaimed` is a share of)

    @property
    def share(self) -> float:
        """The share found; 0.0 when nothing could be measured (fix round 2: a list of rubrics and section labels
        says nothing about its pages, so it does not pass)."""
        return self.found / self.entries if self.entries else 0.0

    def as_dict(self) -> dict:
        return {"entries": self.entries, "found": self.found, "share": round(self.share, 3), "shifted": self.shifted,
                "on_filler": self.on_filler, "unclaimed": self.unclaimed, "pages": self.pages, "by_proxy": len(self.proxies)}

    def failures(self, min_share: float | None = None, max_unclaimed: int | None = None,
                 max_unclaimed_share: float | None = None) -> list[str]:
        """Why the list fails on its pages, in Icelandic for the analysis note; [] when it holds."""
        min_share = C.TOC_MIN_TITLE_SHARE if min_share is None else min_share
        max_unclaimed = C.TOC_MAX_UNCLAIMED_PAGES if max_unclaimed is None else max_unclaimed
        max_unclaimed_share = C.TOC_MAX_UNCLAIMED_SHARE if max_unclaimed_share is None else max_unclaimed_share
        out = []
        if not self.entries:
            out.append("enginn titill efnisyfirlitsins var mælanlegur á síðunum")
        elif self.share < min_share:
            out.append(f"{self.found} af {self.entries} titlum fundust á upphafssíðum sínum")
        if self.shifted >= 2 and self.shifted > self.found:       # (implies share < 0.5: see the module docstring)
            out.append(f"{self.shifted} titlar standa á upphafssíðu greinarinnar á undan eða eftir")
        if self.unclaimed > max_unclaimed and self.unclaimed > max_unclaimed_share * self.pages:
            n = self.unclaimed
            out.append(f"efnisyfirlitið nær ekki yfir {n} {'síðu' if n % 10 == 1 and n % 100 != 11 else 'síður'} heftisins")
        return out

    def context(self) -> list[str]:
        """What else the measure found, for the note when the list fails (not a reason of its own)."""
        out = []
        if self.on_filler:
            out.append(f"{self.on_filler} {'grein hefst' if self.on_filler == 1 else 'greinar hefjast'} á síðu sem "
                       "virðist auglýsing eða auð")
        return out


# Column rubrics a contents list names instead of the headline its page prints (Lögmannablaðið's "Frá ritstjórn", TL's
# "Ávarp", "Formálsorð", the news column "Frá Lagadeild Háskólans"): no title to look for, like a section label (R41).
_RUBRICS = frozenset({"frá ritstjórn", "ávarp", "formálsorð", "formáli", "frá lagadeild háskólans"})
_HEAD_CHARS = 1500             # as analyze._on_page: titles stand near the top of their first page
_TOP_CHARS = 600               # bylines and an interview's subject stand at the very top
_WORD_MATCH = 0.8              # a word of the title printed with a letter or two different ("Clausen" / "Claessen")
_UNREADABLE = 0.5              # a page this much of whose characters are replacement characters has no readable text


def _rubric(title: str) -> bool:
    return " ".join(re.sub(r"[^\w\s]", " ", title).split()).lower() in _RUBRICS


def _unreadable(page: Page) -> bool:
    """The page's text layer is garbled: most of its characters are replacement characters (TL 2009-2012: a font the
    extraction cannot map, 53 of TL 2010-2's 110 pages). It says nothing about articles the list lacks."""
    text = "".join(ln.text for ln in PK.content(page))
    return text.count("\ufffd") >= _UNREADABLE * max(len(text), 1)


def _letterless(title: str) -> bool:
    """A row the parser read without a title ("—", "19 6", "79—-"): its failure, counted and never found (fix round 2:
    left out of the count, eight of them let TL 1971's list of 11 rows pass at 2 of 2)."""
    return len(re.findall(r"[^\W\d_]", title)) < 3


def _names_run(tokens: list[str]) -> int:
    """How many tokens at the start of `tokens` are a byline in capitals ("ARNDÍS A.K. GUNNARSDÓTTIR", "RÓBERT R.
    SPANÓ OG HILDUR HJÖRVAR"); 0 when they are not, or when the whole title is set in capitals."""
    k = 0
    while k < len(tokens) and (tokens[k].isupper() or tokens[k] in ("OG", "&")):
        k += 1
    return k if 2 <= k < len(tokens) else 0


def _trailing_names(tokens: list[str]) -> int:
    """How many tokens at the end are other people's names ("… sínum Sindri M. Stephensen og Víðir Smári Petersen:",
    the next entry's byline run into this one): two or more name tokens, initials or "og"."""
    k = len(tokens)
    while k > 0 and (re.fullmatch(r"[A-ZÁÉÍÓÚÝÞÆÖ][^\W\d_]*\.?[,:.]?|[A-ZÁÉÍÓÚÝÞÆÖ]\.(?:[A-ZÁÉÍÓÚÝÞÆÖ]\.)*|og|&", tokens[k - 1])):
        k -= 1
    return len(tokens) - k if len(tokens) - k >= 2 and k > 0 else 0


def variants(title: str) -> list[str]:
    """The registry title and the forms of it the page may print instead (Task 23b fix round 1): without a leading or
    trailing "VIÐTAL VIÐ <NAME>" / "Viðtal við …" / "VIÐTAL", without an author's name in capitals in front, without
    another entry's byline run on at the end. Forms shorter than three words are left out."""
    out = [title]
    t = re.sub(r"^\s*viðtal\s+við\s+", "", title, flags=re.I)                 # "VIÐTAL VIÐ NAFN Titill"
    t = re.sub(r"(?<=\S)\s*\bviðtal\s+við\b.*$", "", t, flags=re.I)             # "Titill: Viðtal við …"
    t = re.sub(r"(?<=\S)\s*\bviðtal\s*$", "", t, flags=re.I)                     # "Titill VIÐTAL"
    tokens = t.split()
    tokens = tokens[_names_run(tokens):]
    if (n := _trailing_names(tokens)):
        tokens = tokens[:-n]
    form = " ".join(tokens).strip(" :;,–—-")
    if form != title and len(form.split()) >= 3:
        out.append(form)
    return out


def words_on(title: str, text: str) -> bool:
    """The title's words of four letters or more (three at least) are printed close together at the top of the page, a
    letter or two apart at most ("Eggert Clausen. In memoriam" on a page that prints "Eggert Claessen,
    hæstaréttarlögmaður / In memoriam"), in any order ("FJÓRÐA IÐNBYLTINGIN / JÓLAHUGVEKJA …"). A title in parts
    ("Könnun meðal lögmanna: Laun, …") may have its parts apart; each part's words stand together."""
    parts = [[w for w in _fold(p).split() if len(w) >= 4] for p in re.split(r"[:–—]", title)]
    parts = [p for p in parts if p]
    if sum(map(len, parts)) < 3:
        return False
    head = _fold(text[:_HEAD_CHARS]).split()

    def like(w: str, h: str) -> bool:
        return w == h or abs(len(h) - len(w)) <= 2 and difflib.SequenceMatcher(None, w, h).ratio() >= _WORD_MATCH

    def together(words: list[str]) -> bool:
        hits = [[i for i, h in enumerate(head) if like(w, h)] for w in words]
        if not all(hits):
            return False
        span = 2 * len(words) + 4
        return any(all(any(start <= i < start + span for i in h) for h in hits) for h in hits for start in h)
    return all(together(p) for p in parts)


_CREDIT = r"(?:texti|hofundur|eftir|vidtal|umsjon)"     # "Texti: Kristján Örvar Sveinsson - Myndir: …"


def named_on(names: Sequence[str], text: str) -> str | None:
    """A person the entry names (its registry author, the one an interview's title names) begins a line at the top of
    the page, as a byline or an interview's opening does ("ARNÞÓR GUNNARSSON SAGNFRÆÐINGUR", "JÓNAS HARALDSSON
    FÆDDIST …", "Gunnar G. Schram:"), or follows a credit ("Texti: …"). A body ("Lögmannafélag Íslands"), a name inside
    running text ("Dr. Gunnar G. Schram var skipaður …") or a name only like a word on the page ("Högni Jónsson" /
    "Vogi Jónsson") does not count. The name found, or None."""
    lines = [f" {_fold(ln)} " for ln in text[:_TOP_CHARS].splitlines() if ln.strip()]
    for n in names:
        if len(_fold(n).split()) < 2 or not looks_like_person(n, strict=False) or FP._plain_words(n):
            continue
        at = re.compile(rf"^ {re.escape(_fold(n))} |\b{_CREDIT} {re.escape(_fold(n))} ")
        if any(at.search(ln) for ln in lines):
            return n
    return None


def _labelled(title: str, text: str, first: bool) -> bool:
    """The page opens with the label of the entry's kind, alone or before its title: an editorial's ("RITSTJÓRNARGREIN:",
    "LEIÐARI") over the list's first entry, which TL prints over an editorial whose text layer may be unreadable (2010-2,
    2012-3, 2012-4); "VIÐTAL" over an entry the registry calls an interview ("Viðtöl við bifrestinga", Lögbrú 2013-1)."""
    kinds = {"Ritstjórnargrein"} if first else set()
    if re.search(r"\bviðt[aö]l", title, re.I):
        kinds.add("Viðtal")
    lines = [ln.split(":")[0].strip() for ln in text.splitlines() if ln.strip()][:3]
    return any(label_type(ln) in kinds for ln in lines)


def printed_at(title: str, i: int, on_page: Callable[[str, int], bool], text_of: Callable[[int], str]) -> bool:
    """The page prints the title, in one of its forms (variants) or word by word (words_on)."""
    return any(on_page(v, i) for v in variants(title)) or words_on(title, text_of(i))


def found_at(title: str, authors: Sequence[str], first: bool, i: int, on_page: Callable[[str, int], bool],
             text_of: Callable[[int], str]) -> str | None:
    """How page i holds the entry, or None: "titli" when it prints a form of the title (printed_at); "höfundi" or
    "viðmælanda" when a person the entry names begins a line (named_on: its registry author, an interview's subject);
    "merki" when the page opens with the label of the entry's kind (_labelled). The last three leave the registry title
    unverified."""
    text = text_of(i)
    if printed_at(title, i, on_page, text_of):
        return "titli"
    if (name := named_on([*authors, *interview_subjects(title)], text)) is not None:
        return "höfundi" if name in authors else "viðmælanda"
    return "merki" if _labelled(title, text, first) else None


def check(entries: Sequence[ArticleEntry], titles: dict[int, str | None], reader: Callable[[int], Page],
          picture: Callable[[int], float], non_article: Sequence[int], on_page: Callable[[str, int], bool],
          n_pages: int, text_of: Callable[[int], str] | None = None,
          authors: dict[int, list[str]] | None = None, front: int | None = None) -> TocCheck:
    """Measure a located, verified list. `titles` and `authors`: each entry's registry title (None for a section label)
    and authors by id (R41); `on_page(title, i)`: is the title printed at the top of PDF page i (analyze._on_page over
    the page's visual text); `text_of(i)`: that text; `non_article`: the pages end_ranges found to be no article's;
    `front`: the first page after the cover and the contents (unclaimed counts from there, or from the first located
    start without it).

    An entry is found when its title is on its start page (title_on_page) or the page holds it otherwise (found_at,
    fix round 1). A name or a label (found_at's "höfundi", "viðmælanda", "merki") does not place an entry whose title
    is printed on a neighbour's start page: it stands one off (fix round 2, LMBL 2016-2). A column rubric ("Frá
    ritstjórn") is left out of the count, like a section label; a row without a title ("—") is counted, never found."""
    text_of = text_of or (lambda i: "")
    authors = authors or {}
    starts = [a.pdf_pages[0] if a.pdf_pages else None for a in entries]
    titled = [k for k, a in enumerate(entries) if titles.get(id(a)) and not _rubric(titles[id(a)])]

    def on_neighbour(k: int) -> bool:
        near = [starts[j] for j in (k - 1, k + 1) if 0 <= j < len(entries) and starts[j] is not None
                and starts[j] != starts[k]]
        return any(printed_at(titles[id(entries[k])], s, on_page, text_of) for s in near)

    how: dict[int, str] = {}
    for k in titled:
        title = titles[id(entries[k])]
        if _letterless(title) or starts[k] is None and not entries[k].checks.get("title_on_page"):
            continue
        if entries[k].checks.get("title_on_page"):
            how[k] = "titli"
        elif (by := found_at(title, authors.get(id(entries[k]), []), k == 0, starts[k], on_page, text_of)) and (
                by == "titli" or not on_neighbour(k)):
            how[k] = by
    shifted = sum(1 for k in titled if k not in how and not _letterless(titles[id(entries[k])]) and starts[k] is not None
                  and on_neighbour(k))
    located = sorted(s for s in starts if s is not None)
    t = _issue_typography(entries, reader)
    on_filler = 0
    if t.sizes:
        for k, (a, s) in enumerate(zip(entries, starts)):
            if s is None or k in how or a.checks.get("title_on_page") or PK._has_text(reader(s), t):
                continue                              # its title or text of its own: no advert (a title page)
            shown = picture(s)
            if PK.classify(reader(s), t, a.title, 0.0 if shown >= PK._SCAN else shown) in (PK.Kind.ADVERT, PK.Kind.BLANK):
                on_filler += 1                        # (a scan's page is one picture: that says nothing)
    claimed = {i for a in entries if a.pdf_pages for i in range(a.pdf_pages[0], a.pdf_pages[1] + 1)}
    skip = claimed | set(non_article)
    unclaimed = pages = 0
    if located:
        start = located[0] if front is None else min(front, located[0])
        pages = max(n_pages - start, 0)
        matter = min(start + C.COVERAGE_MAX_GAP_PAGES, located[0])        # (the front matter: Task 23c)

        def article_text(i: int) -> bool:
            page = reader(i)
            return not _unreadable(page) and (PK._has_text(page, t) or (
                i < located[0] and PK._has_text(page, PK.typography([page]))))     # (before it: any type)
        if t.sizes:
            unclaimed = sum(1 for i in range(start, n_pages) if i not in skip and not start <= i < matter
                            and article_text(i))
    proxies = tuple((k, by) for k, by in sorted(how.items()) if by != "titli")
    return TocCheck(len(titled), len(how), shifted, on_filler, unclaimed, proxies, pages)


def _issue_typography(entries: Sequence[ArticleEntry], reader: Callable[[int], Page]) -> PK.Typography:
    """The type of the issue's running text: measured on the first pages of its longest located article."""
    ranges = [a.pdf_pages for a in entries if a.pdf_pages]
    if not ranges:
        return PK.Typography((), 0.0)
    first, last = max(ranges, key=lambda r: r[1] - r[0])
    return PK.typography([reader(i) for i in range(first, min(first + PK._PROFILE_PAGES, last + 1))])
