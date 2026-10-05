"""What the pages after an article's first page are, where the article ends, and how it is signed (Task 22c).

A located article runs to the page before the next located article (printed.assign_ranges). In an issue that page may
be far off: adverts, blank pages, a colophon or back cover and items the registry does not list follow many articles.
Each page after the first is classified against the article's own type, measured on its first pages (typography):

- TEXT: the article's running text: lines of running length in one of its text sizes or between its footnotes' size
  and its body's, many more of them than an advert's marks; also its last lines at the top of the page after its text;
- NEW: another item starts at the top of the page: the heading the page starts with (_top_heading: its first line set
  clearly larger than the body, after a label or a byline; else firstpage.page_heading), with none of the article's
  running text above it, set at least _NEW_ITEM times the body (_NEW_ITEM_SPARSE on a page without running text), or
  with a byline under or over it. Outline, bibliography and table headings, the journal's masthead, the article's own
  title (a running head; the second name of a double obituary) and a page under the article's own running head start
  nothing;
- ADVERT: no running text to speak of, and an advert's marks (phone numbers, "Sími", www., e-mail, firms), a picture
  over a large part of the page, or only a few lines;
- BLANK: nothing but the running head and the folio;
- OTHER: printed matter that is none of these.

An item may also start half way down a page of the article's text, right under its signature (TL 1985 "Á víð og
dreif"). The article ends on its last TEXT page. OTHER pages right after its text are kept and the end is uncertain
(R42 flags it). Signatures are read where the article's text ends (signature) and, for a memorial with several
contributors, where each part ends (part_signatures).

Task 23b (audit 4) made the uncertain end and the signature more precise: a page whose first line goes on with the
sentence before it is the article's (its chart, its abstract run on); a scan's page is measured in its own scale; a
page without text, or with only an advert's box in its lower half, after text that has ended a sentence is no part of
it; a name set as large as a heading, a caption in the margin or a name over an advert's contact signs nothing; and
item_heading tells where a range that swallowed the next, signed, item should end.
"""
from __future__ import annotations

import difflib
import re
from collections import Counter
from enum import Enum
from typing import Callable, NamedTuple, Sequence

from engine.timarit import firstpage as FP
from engine.timarit.authors import _TITLE_RE
from engine.timarit.firstpage import Line, Page
from engine.timarit.ulfljotur_xlsx import _fold


class Kind(str, Enum):
    TEXT = "text"
    BLANK = "blank"
    ADVERT = "advert"
    NEW = "new"
    OTHER = "other"


class Typography(NamedTuple):
    """An article's type, measured on its first pages."""
    sizes: tuple[float, ...]     # sizes of its running text, the body first (then its footnotes, a lead)
    line: float                  # median length (characters) of its lines in the body size

    @property
    def body(self) -> float:
        return self.sizes[0] if self.sizes else 0.0


_LONG = 30                # a line this long (characters) may be running text
_TOL = 0.07               # a line is set in a size when within 7% of it
_CLUSTER = 0.06           # sizes this close in a row are one type (typography)
_SHARE = 0.15             # a size carrying this share of an article's long-line characters is one of its text sizes
_SMALLEST = 0.45          # footnotes are set down to this share of the body
_NOTES = 0.8              # ... and up to this share
_DENSE = 5                # a page with this many running lines (and three times as many as advert marks) is text
_MANY = 15                # ... and with this many in its own sizes whatever its advert marks (an article about websites)
_NEW_ITEM = 1.8           # a heading this many times the body starts an item on a page of running text
_NEW_ITEM_SPARSE = 1.4    # ... and on a page without running text
_FEW_LINES = 6            # a page of no more lines than this and no running text is an advert or a picture
_PICTURE = 0.35           # share of a page covered by pictures that makes a page without running text an advert
_PICTURE_PAGE = 0.5       # a page this much covered by pictures is a page of text only with _PICTURED running lines
_PICTURED = 10            # (LMBL 2017's full-page advert "HEIÐUR OG HVATNING" has six)
_IN_TYPE = 0.7            # share of a page's characters set in the article's text sizes: a list or table of its own
_SCAN = 0.9               # pages covered this much by a picture are a scan: their pictures say nothing
_FULL_BLEED = 0.9         # an advert's picture over the whole page (its own evidence, with no text needed)
_INK = 0.02               # a page without text lines but this much picture, or _INK_DRAWINGS vector drawings, has ink
_INK_DRAWINGS = 30        # (LMBL 2005 p. 31: two columns of the article drawn as 481 vector outlines)
_CAPTIONS = 10            # lines in the article's type that make a page with a picture its own (a photo spread)
_PROFILE_PAGES = 4        # the article's first pages that typography() may read
_ENOUGH = 40              # ... until it has read this many long lines
_GAP = 2.5                # a vertical gap of this many body sizes separates two blocks of text

# Headings that belong to an article: its outline, bibliography, tables, figures, appendices and summary.
_PART = re.compile(r"^(?:\d{1,2}(?:\.\d{1,2})*\.?\s|[IVX]+\.\s|[A-Z]\.\s|(?:heimild\w*|rita?skrá\w*|dómaskrá\w*|"
                   r"lagaskrá\w*|skrá yfir|töflu?r?|tafla|myndi?r?|viðaukar?|viðauki|fylgiskj\w*|appendix|annex|table|"
                   r"figure|bibliography|references|literature|summary|abstract|ágrip|útdráttur|niðurlag|niðurstöð\w*|"
                   r"lokaorð|samantekt|inngangur|introduction|conclusions?|efnisyfirlit|dómar|lög|alþingistíðindi|"
                   r"greinar|bækur|fræðirit|frumvörp|tilvitnanir|tilvísan\w*|\w*skrá|skammstafanir)\b)", re.I)
# Headings that start a page of an article's own: a numbered section, its tables, appendix, lists and summary.
_PART_PAGE = re.compile(r"^(?:\d{1,2}(?:\.\d{1,2})*\.?\s+[^\W\d_]|[IVX]+\.\s|(?:\w*skrá\b|heimildir|tilvísan\w*|"
                        r"töflu?r?|tafla|viðaukar?|viðauki|fylgiskj\w*|appendix|annex|table|figure|bibliography|"
                        r"references|summary|abstract|skammstafanir)\b)", re.I)
# The journal's masthead or running title set large on a page ("Stjórnmál & stjórnsýsla", "STJÓRNMÁL …")
_MASTHEAD = re.compile(r"^(?:stjornmal|stjornsysla|ulfljotur|logmannabladid|logfraedingur|timarit logfraedinga|"
                       r"timarit logrettu|logretta|logbru)\b")
_SENTENCE_END = re.compile(r"[.!?:;…\"“”»)\]]\s*\d{0,3}\s*$")
_ROLE = re.compile(r"^(?:ritstjóri|formaður|forseti|deildarforseti|framkvæmdastjóri|ritari|prófessor|dósent|lektor|"
                   r"lögmaður|hrl\.?|hdl\.?|héraðsdómari|hæstaréttardómari|dómari|stud\.|cand\.)", re.I)


def _same(a: float, b: float) -> bool:
    return b > 0 and abs(a - b) <= _TOL * b


_LINES: dict[tuple[int, float], tuple[Page, list[Line]]] = {}


def _lines(page: Page, bottom: float) -> list[Line]:
    """firstpage._content_lines, kept per page: each page is asked many times and furniture is costly to tell."""
    key = (id(page), bottom)
    hit = _LINES.get(key)
    if hit is None or hit[0] is not page:
        if len(_LINES) > 4096:
            _LINES.clear()
        hit = _LINES[key] = (page, FP._content_lines(page, bottom))
    return hit[1]


def content(page: Page) -> list[Line]:
    """The page's lines without furniture (folio, running head, the journal's name)."""
    return _lines(page, 1.0)


def _clusters(lines: list[Line]) -> list[tuple[float, list[str]]]:
    """Long lines grouped by type size: sizes in a row, each within _CLUSTER of the one before, are one type (an OCR'd
    scan reads one type as 8.5, 8.7, 9.0 and 9.4 points). (the group's mean size, its lines' texts), the most
    characters first."""
    groups: list[list[Line]] = []
    for ln in sorted(lines, key=lambda x: x.size):
        if groups and ln.size <= groups[-1][-1].size * (1 + _CLUSTER):
            groups[-1].append(ln)
        else:
            groups.append([ln])
    out = [(round(sum(x.size * len(x.text) for x in g) / sum(len(x.text) for x in g), 1), [x.text for x in g])
           for g in groups]
    return sorted(out, key=lambda g: -sum(map(len, g[1])))


_NOTE_LINE = re.compile(r"^(?:\d{1,3}\)?|\*+)\s+\S")


def typography(pages: Sequence[Page]) -> Typography:
    """The running-text sizes of an article: every size that carries at least _SHARE of the characters of the long
    lines of its first pages (as many as it takes to read _ENOUGH long lines). The body is the most common size,
    unless those lines are footnotes (numbered: "3 Í september 1915 …") under a larger size of at least _SHARE: they
    outweigh the body on many law pages."""
    lines: list[Line] = []
    for p in pages:
        if len(lines) >= _ENOUGH:
            break                                     # (the pages after may be adverts: they must not set the type)
        lines += [ln for ln in content(p) if len(ln.text) >= _LONG]
    groups = _clusters(lines)
    total = sum(len(t) for _, texts in groups for t in texts)
    if not total:
        return Typography((), 0.0)
    kept = [g for g in groups if sum(map(len, g[1])) >= _SHARE * total]
    body = kept[0]
    notes = sum(1 for t in body[1] if _NOTE_LINE.match(t)) >= 0.08 * len(body[1])
    larger = [g for g in kept[1:] if body[0] / _NOTES <= g[0] <= 2.0 * body[0]]
    if notes and larger:
        body = larger[0]                              # the most common size is the footnotes'
    lens = sorted(map(len, body[1]))
    return Typography(tuple([body[0]] + [g[0] for g in kept if g is not body]), float(lens[len(lens) // 2]))


def _is_running(ln: Line, t: Typography) -> bool:
    return len(ln.text) >= max(_LONG, 0.5 * t.line) and (
        any(_same(ln.size, s) for s in t.sizes) or _SMALLEST * t.body <= ln.size <= (1 + _TOL) * t.body)


def running_lines(page: Page, t: Typography) -> list[Line]:
    """The article's running text on the page: lines of running length in one of its text sizes, or in a size between
    its footnotes' and its body's."""
    return [ln for ln in content(page) if _is_running(ln, t)]


# --- adverts --------------------------------------------------------------------------------------------------------

# An advert's marks: its contact (a phone number, "Sími 590 2600", "Tel.: 555 …", a web or e-mail address) and the firm
# it names (ehf., hf., slf., lögmannsstofa …). Contact words count only with a number after them ("ég tel", "í síma"
# are running text; the number is a mark of its own: "Sími 568 0887" is two), and words that are a law article's subject as often as an advert's ("banki", "tryggingar",
# "sparisjóður") are no marks.
_AD_MARKS = re.compile(
    r"\b(?:s[íi1lf]m[ia]r?|sfmi|slmi|tel|fax|bréfsími|myndsendir|netfang|pósthólf|box|símnefni)\b(?=\.?\s*:?\s*\+?\d)"
    r"|\bopnunartími\b|\+354|(?<![\d./-])\d{3}[ -]\d{4}(?![\d./-])|www\.|\S@\S+\.\w|\b[\w-]+\.(?:is|com|net)\b"
    r"|\b(?:e\.?h\.?f|h\.f|hf|slf|sf)\.?(?=\s|,|$)|\b(?:lögmannsstofa\w*|lögfræðistofa\w*|málflutningsskrifstofa\w*|"
    r"málflutningsstofa\w*|lögmannsþjónusta\w*)\b", re.I)


# The marks of an advert's contact, which running text does not print: phone numbers, "Sími", web and e-mail addresses.
_STRONG_MARKS = re.compile(r"\b(?:s[íi1lf]m[ia]r?|sfmi|slmi|bréfsími|netfang)\b(?=\.?\s*:?\s*\+?\d)|\+354|(?<![\d./-])\d{3}[ -]\d{4}"
                           r"(?![\d./-])|www\.|https?://|\S@\S+\.\w|\b[\w-]+\.(?:is|com|net)(?:/\S*)?(?=\s|$|[,.;)])",
                           re.I)
_CITED_URL = re.compile(r"https?://\S+|\bwww\.\S+/\S+")


def _marks(text: str) -> int:
    return len(_AD_MARKS.findall(_CITED_URL.sub(" ", text)))


def contact_marks(page: Page) -> int:
    """How many contact marks of an advert the page prints: phone numbers, "Sími", web and e-mail addresses."""
    return sum(len(_STRONG_MARKS.findall(_CITED_URL.sub(" ", ln.text))) for ln in content(page))


def advert_marks(page: Page) -> int:
    """How many advert's marks the page prints: phone numbers and "Sími", web and e-mail addresses, firms (a web page
    cited with its path, as a bibliography does, is no advert's)."""
    return sum(_marks(ln.text) for ln in content(page))


# --- the start of another item ----------------------------------------------------------------------------------------

def _squash(s: str) -> str:
    return _fold(s).replace(" ", "")


def part_of_title(text: str, title: str | None) -> bool:
    """`text` is the article's own title or a part of it: a running head, the first lines of its title block, the
    second name of a double obituary (also as the OCR glued it: "PÁLLPÁLMASSON")."""
    if not title or not text:
        return False
    words = _fold(text).split()
    if len(words) > 1 and not any(w in _fold(title) for w in words if len(w) >= 4):
        return False                                  # no longer word in common (quick: pages ask this often)
    s, st = _squash(text), _squash(title)
    if len(s) >= 6 and s in st or FP.match_score(text, title).score >= FP.SAME_TITLE:
        return True
    return len(s) >= 6 and any(difflib.SequenceMatcher(None, s, _squash(item)).ratio() >= 0.85
                               for item in re.split(r"\s+[—–-]\s+|:\s+", title))


def _article_part(text: str) -> bool:
    t = text.strip(" „\"'»«")
    return bool(_PART.match(t) or _MASTHEAD.match(_fold(t)))


def _byline_near(page: Page, lines: list[Line], first: int, last: int) -> bool:
    """A byline (a line of names, with roles) right under the heading or just above it (Lögrétta sets the author
    over the title; old Úlfljótur and TL "Name:"), not a running head in the top margin (Rannsóknir sets the author's
    name over every even page; a "Name:" there is a byline still)."""
    near = lines[last + 1:last + 3] + [ln for ln in lines[max(0, first - 3):first]
                                       if ln.y > FP._MARGIN_BAND * page.height or ln.text.rstrip().endswith(":")]
    return any(_name_only(ln.text) for ln in near)


def _prominent_at_top(page: Page, t: Typography) -> bool:
    """A cheap test before page_heading: a line in the page's upper part set clearly larger than the body."""
    return any(ln.size >= FP._PROMINENT * t.body and ln.y < 0.75 * page.height for ln in content(page))


def _top_heading(page: Page, t: Typography) -> FP.Heading | None:
    """The heading the page starts with: its first content line (after a section label, or a byline with its role)
    set clearly larger than the article's body, with the lines that go on in its type; else the page's main heading
    (firstpage.page_heading, the block in the largest type: an advert lower down may be larger still)."""
    lines = _lines(page, 1 - FP._MARGIN_BAND)
    i = 0
    while i < len(lines) and (FP.is_label_line(lines[i].text) or len(re.findall(r"[^\W\d_]", lines[i].text)) < 3
                              or _name_only(lines[i].text) or i and _name_only(lines[i - 1].text)
                              and len(lines[i].text) <= 40 and lines[i].size < FP._PROMINENT * t.body):
        i += 1                                        # a label, a letter, a byline and its role over the title
    if i < len(lines) and lines[i].size >= FP._PROMINENT * t.body and lines[i].y < FP._LOW_TITLE[0] * page.height:
        j = i
        while j + 1 < len(lines) and abs(lines[j + 1].size - lines[i].size) <= 0.1 * lines[i].size \
                and 0 < lines[j + 1].y - lines[j].y <= 2.2 * lines[i].size:
            j += 1
        text = FP.join_title([ln.text for ln in lines[i:j + 1]])
        if text:
            return FP.Heading(FP.recase(text), i, j, 1.0, max(ln.size for ln in lines[i:j + 1]))
    return FP.page_heading(page)


def new_item_at_top(page: Page, t: Typography, title: str | None) -> str | None:
    """The heading of another item that starts at the top of the page, else None (see the module's NEW)."""
    if not _prominent_at_top(page, t):
        return None
    h = _top_heading(page, t)
    if h is None or part_of_title(h.text, title) or _article_part(h.text) or FP.is_label(h.text):
        return None
    lines = _lines(page, 1 - FP._MARGIN_BAND)
    if any(_is_running(ln, t) for ln in lines[:h.first]):
        return None                                   # the article's text comes first: a cross-head
    before = lines[h.first - 1] if h.first else None
    if before is not None and abs(before.size - h.size) <= 0.5 and _article_part(before.text):
        return None                                   # the rest of an outline heading ("2. Um ólíkt …" / "…")
    dense = len(running_lines(page, t)) >= _DENSE
    big = h.size >= (_NEW_ITEM if dense else _NEW_ITEM_SPARSE) * t.body
    return h.text if big or _byline_near(page, lines, h.first, h.last) else None


_OBLIQUE = re.compile(r"(?:syni|sonar|dóttur)$")   # "… Ingimar Ingasyni": a name in running text, never a signature
_OCR_SWAPS = (("S", "ð"), ("m", "rn"), ("f", "í"), ("1", "l"), ("I", "l"), ("0", "ó"))


def _repair_token(tok: str) -> str:
    """A name token BÍN does not know as a name read back the way the OCR misreads Icelandic type ("GuSrún" for
    "Guðrún", "Bjömsson" for "Björnsson", "Arn|jótur" for "Arnljótur"), when exactly one reading is a known name."""
    tok = tok.replace("|", "l")
    if len(tok) < 4 or FP._name_token_ok(tok.rstrip(",.:")):
        return tok
    core, tail = re.match(r"(.*?)([,.:]*)$", tok).groups()
    fixes = {core[:i] + new + core[i + len(old):] for old, new in _OCR_SWAPS
             for i in range(1, len(core)) if core.startswith(old, i)}
    known = [f for f in fixes if FP._name_token_ok(f)]
    return known[0] + tail if len(known) == 1 else tok


def repair_name(text: str) -> str:
    return " ".join(_repair_token(tok) for tok in text.split())


def _name_only(text: str) -> list[str]:
    """The people a line names when it names nothing else, a role after a comma aside ("Hjördís Hákonardóttir",
    "Sindri M. Stephensen, ritstjóri", "Gunnlaugur Pétursson og Hjörtur Hjartarson", "GuSrún Erlendsdóttir")."""
    head = " ".join(_TITLE_RE.sub(" ", repair_name(text).split(",")[0]).split()).strip(" .")   # "… hdl.", "hrl."
    if not 1 <= head.count(" ") <= 9:
        return []
    names = FP.names_in(head)
    joins = len(re.findall(r"\s(?:og|and|&)\s", head))
    if not names or len(head.split()) != sum(len(n.split()) for n in names) + joins:
        return []
    return [] if any(_OBLIQUE.search(n) for n in names) else names


_FIRM = re.compile(r"\b(?:e\.?h\.?f|s\.?l\.?f|h\.?f|sf)\.?(?=\s|,|$)", re.I)
_LEADERS = re.compile(r"\.{3,}|…|\s\.\s\.")


def _role_only(rest: str) -> bool:
    """What follows a comma after a signature's name is a role ("ritstjóri", "hrl.", "formaður Þemis"), nothing else
    ("Kauparéttur. Reykjavík 1988." is a book)."""
    rest = rest.strip(" ,.")
    return not rest or len(rest) <= 40 and bool(_ROLE.match(rest) or not _TITLE_RE.sub("", rest).strip(" ,.;"))


def signed_names(lines: list[Line], k: int) -> list[str]:
    """The names a line signs with, stricter than _name_only: no "Name:" (that is the next item's byline), after a
    comma a role only, no leader dots or table cells, no firm or contact mark (a phone number, a web or e-mail
    address, "Sími") on it or on the short lines right under it (LMBL 2017: "Ingi Tryggvason hrl." over "Lína slf.").
    The line above is the article's text and is not looked at ("… tel ég." ends many a note)."""
    text = _joined(lines, k)
    if text.rstrip().endswith(":") or _LEADERS.search(text) or "," in text and not _role_only(text.split(",", 1)[1]):
        return []
    near = [lines[k]] + [x for x in lines[k + 1:k + 3] if len(x.text) <= 40]   # the name line and short lines under it
    if any(_FIRM.search(x.text) or _STRONG_MARKS.search(x.text) for x in near):
        return []
    return _name_only(text)


def item_below_signature(page: Page, t: Typography, title: str | None) -> tuple[int, str] | None:
    """An item that starts half way down the page right after the article ends: its running text, a name line (the
    signature), then within two lines a heading set clearly larger than the body (TL 1985 "Á víð og dreif": "Hjördís
    Hákonardóttir" / "NÝ STJÓRNSÝSLULÖGGJÖF í DANMÖRKU"). (index of the signature in content(page), heading)."""
    lines = content(page)
    seen = 0
    for k, ln in enumerate(lines[:-1]):
        if _is_running(ln, t):
            seen += 1
            continue
        if seen < 2 or len(ln.text) > 60 or not _after_text(lines, k, t) or not signed_names(lines, k):
            continue
        for nxt in lines[k + 1:k + 3]:
            if nxt.size >= FP._PROMINENT * t.body and len(nxt.text) <= 80 and not _article_part(nxt.text) \
                    and not part_of_title(nxt.text, title) and not FP.names_in(nxt.text):
                return k, nxt.text
    return None


def item_heading(page: Page, t: Typography, title: str | None) -> str | None:
    """The page starts another item in the way a contents-less news section does (Task 23b): its first line is a
    section label ("Á VÍÐ OG DREIF", "REKABÁLKUR") or a heading in capitals set in bold or larger than the body ("ÚR
    SKÝRSLU STJÓRNAR DÓMARAFÉLAGS ÍSLANDS STARFS-") that is no part of the article (an outline, its bibliography) and
    not its own title. The heading's text, or None. Weaker evidence than a NEW page: analysis asks it only where a
    signature that names other people than the registry shows that the range ran on into the next item."""
    lines = [ln for ln in content(page) if len(re.findall(r"[^\W\d_]", ln.text)) >= 3]
    if not lines or lines[0].y > 0.4 * page.height:
        return None
    first = lines[0]
    if FP.is_label_line(first.text):
        nxt = lines[1] if len(lines) > 1 and (lines[1].bold or not _is_running(lines[1], t)) else None
        return f"{first.text} — {nxt.text}" if nxt is not None and FP._all_caps(nxt.text) else first.text
    if not FP._all_caps(first.text) or not (first.bold or first.size >= 1.1 * t.body) or len(first.text.split()) < 2:
        return None
    if _article_part(first.text) or part_of_title(first.text, title):
        return None
    return first.text


# --- classification ------------------------------------------------------------------------------------------------

def _own_head(page: Page, title: str | None, authors: Sequence[str]) -> bool:
    """The page's running head names the article: its title, or its author (Úlfljótur 2010 sets the author's name over
    every even page, also over its appendix of tables)."""
    for ln in page.lines:
        if ln.y > FP._MARGIN_BAND * page.height:
            break
        f = f" {_fold(ln.text)} "
        if any(len(a.split()) >= 2 and f" {_fold(a)} " in f for a in authors) or (
                len(ln.text) >= 12 and part_of_title(re.sub(r"\b\d+\b", "", ln.text).strip(" —–-"), title)):
            return True
    return False


def _part_page(lines: list[Line]) -> bool:
    """The page starts with a part of an article: a numbered section, its tables, appendix, bibliography."""
    return bool(lines) and bool(_PART_PAGE.match(lines[0].text.strip(" „\"'»«")))


def _in_type(lines: list[Line], t: Typography) -> bool:
    """Printed matter in the article's type: two lines or more, mostly set in its text sizes (a list, a table, the
    article's last lines)."""
    chars = sum(len(ln.text) for ln in lines)
    if len(lines) < 2 or chars < 60:
        return False
    own = sum(len(ln.text) for ln in lines if any(_same(ln.size, s) for s in t.sizes)
              or _SMALLEST * t.body <= ln.size <= (1 + _TOL) * t.body)
    return chars > 0 and own >= _IN_TYPE * chars


def _in_sizes(ln: Line, t: Typography) -> bool:
    return any(_same(ln.size, s) for s in t.sizes) or _SMALLEST * t.body <= ln.size <= (1 + _TOL) * t.body


def classify(page: Page, t: Typography, title: str | None, picture: float = 0.0, *, after_text: bool = False,
             authors: Sequence[str] = (), drawings: int = 0) -> Kind:
    """The kind of a page after the article's first (see the module). `picture`: the share of the page covered by
    pictures (0.0 for a scan, whose page is one picture); `drawings`: its vector drawings (asked only of a page without
    text lines). `after_text`: the page before it is the article's text, so a page that starts with a line of its body
    is its last page, and a list or a table set in its type is its own.
    A page without text lines is BLANK only without ink: a full-page picture is an advert, any other picture or vector
    drawings (text drawn as outlines) is OTHER. A picture alone makes no advert of a page with many lines in the
    article's type and no advert marks (a photo spread with captions): OTHER, as the end is then uncertain."""
    lines = [ln for ln in content(page) if len(re.findall(r"[^\W\d_]", ln.text)) >= 2]
    if not lines:
        if picture >= _FULL_BLEED:
            return Kind.ADVERT
        return Kind.OTHER if picture >= _INK or drawings >= _INK_DRAWINGS else Kind.BLANK
    running, marks, strong = running_lines(page, t), advert_marks(page), contact_marks(page)
    many = sum(1 for ln in running if any(_same(ln.size, s) for s in t.sizes)) >= _MANY
    text = len(running) >= (_PICTURED if picture >= _PICTURE_PAGE else _DENSE) and (many or len(running) >= 3 * marks)
    pictured = picture >= _PICTURE and (picture >= _FULL_BLEED or strong
                                        or sum(1 for ln in lines if _in_sizes(ln, t)) < _CAPTIONS)
    advert = marks >= 2 or pictured or len(lines) <= _FEW_LINES
    if _own_head(page, title, authors):
        return Kind.TEXT                              # under its own running head: a section heading is no new item
    if new_item_at_top(page, t, title):
        return Kind.ADVERT if advert and not text else Kind.NEW
    if text or _part_page(lines):
        return Kind.TEXT
    if after_text and marks < 2 and picture < _PICTURE and (
            running and running[0] is lines[0] and lines[0].y <= 0.25 * page.height or _in_type(lines, t)
            or _goes_on(lines, t, page)):
        return Kind.TEXT                              # its last lines at the top of the page; a list or table that ends it
    return Kind.ADVERT if advert else Kind.OTHER


def _goes_on(lines: list[Line], t: Typography, page: Page) -> bool:
    """The page's first line goes on with a sentence of the page before (Task 23b): it starts in lower case at the top
    of the page, in a size near the article's (a scan's type measures a little larger on some pages: TL 1968-2 p. 78
    "þennan hátt er þá og minnzt …" over a chart; TL 2014-3 p. 307, its English abstract run on)."""
    first = lines[0] if lines else None
    return first is not None and first.text[:1].islower() and len(first.text) >= 15 \
        and first.y <= 0.25 * page.height and 0.85 * min(t.sizes) <= first.size <= 1.15 * max(t.sizes) \
        and (not page.width or first.x1 - first.x0 >= 0.4 * page.width)    # (not a stamp in the margin)


def text_ends(page: Page, t: Typography) -> bool:
    """The article's text ends a sentence on this page (Task 23b): the last line in its body size in reading order
    (the foot of its rightmost column; footnotes below it do not count) ends in a full stop, a question mark or a
    closing quote, a footnote mark after it allowed."""
    run = [ln for ln in content(page) if _same(ln.size, t.body) and len(re.findall(r"[^\W\d_]", ln.text)) >= 2]
    if not run:
        return False
    right = [ln for ln in run if page.width and ln.x0 >= 0.45 * page.width]
    last = max(right if right and _columns(page, t) else run, key=lambda ln: ln.y)
    return bool(_SENTENCE_END.search(last.text))


def _apart(page: Page, t: Typography) -> bool:
    """A page that is no part of the text before it: nothing in text lines (a picture, a drawing), or only a block in
    the lower half of the page in a type not the article's (an advert under a blank upper half: Úlfljótur 1983 p. 27)."""
    lines = [ln for ln in content(page) if len(ln.text.strip()) >= 2]      # (text in a broken font is text too)
    return not lines or (min(ln.y for ln in lines) > 0.5 * page.height
                         and sum(1 for ln in lines if _in_sizes(ln, t)) < _CAPTIONS)


def continues(page: Page, t: Typography, title: str | None, authors: Sequence[str] = ()) -> bool:
    """A page that goes on with the article after an advert: it does not start with a heading of its own (Lögberg's
    notice after TL 2004's adverts) and is set in its type, not only in the size of its footnotes (LMBL's notices to
    members), or it starts with a part of the article or under its running head."""
    lines = [ln for ln in content(page) if len(re.findall(r"[^\W\d_]", ln.text)) >= 2]
    if not lines:
        return False
    if _part_page(lines) or _own_head(page, title, authors):
        return True
    top = [ln for ln in lines[:3] if ln.y <= lines[0].y + 0.2 * page.height]
    in_sizes = sum(1 for ln in lines if len(ln.text) >= max(_LONG, 0.5 * t.line)
                   and any(_same(ln.size, s) for s in t.sizes))
    return in_sizes >= 3 and not any(ln.size >= FP._PROMINENT * t.body for ln in top)


def _tail_block(page: Page, t: Typography) -> bool:
    """The last lines of an article at the foot of the page where the next item starts (Úlfljótur 1957 p. 11): a
    block in its body type in the lower half, after a gap of _GAP body sizes, that begins inside a sentence."""
    lines = content(page)
    return any(_is_running(b, t) and _same(b.size, t.body) and b.y > 0.5 * page.height and b.text[:1].islower()
               and b.y - a.y >= _GAP * t.body for a, b in zip(lines, lines[1:]))


def _columns(page: Page, t: Typography) -> bool:
    """Running text in two or more columns: lines side by side, or lines that start half way across the page."""
    run = running_lines(page, t)
    side = sum(1 for a, b in zip(run, run[1:]) if abs(a.y - b.y) <= 1.5)
    mid = sum(1 for ln in run if page.width and ln.x0 > 0.4 * page.width)
    return side >= 3 or mid >= 3


def _last_body_line(page: Page, t: Typography) -> Line | None:
    """The page's last line in the body size (a paragraph's short last line too: "ákvörðun þess.")."""
    body = [ln for ln in content(page) if _same(ln.size, t.body) and len(re.findall(r"[^\W\d_]", ln.text)) >= 2]
    return body[-1] if body else None


def ends_mid_sentence(page: Page, t: Typography) -> bool:
    """The page's last line of running text in the body size stops inside a sentence, on a page of one column (on a
    page of columns the lowest line need not be the last one read)."""
    last = _last_body_line(page, t)
    return last is not None and not _columns(page, t) and not _SENTENCE_END.search(last.text)


def _scaled(t: Typography, page: Page) -> Typography:
    """A scan's type measured on one page: the article's sizes scaled to the page's own body size when that is off the
    article's by more than _TOL but within 15% (an OCR'd scan reads the same type a little larger on some pages: TL
    1970-1 p. 39 at 12.1 pt over 11.2 pt; Task 23b)."""
    r = FP.body_size(page) / t.body if t.body else 1.0
    return Typography(tuple(round(x * r, 2) for x in t.sizes), t.line) if 0.87 <= r <= 1.15 and abs(r - 1) > _TOL \
        else t                                        # (within _TOL the article's own sizes already fit)


class End(NamedTuple):
    last: int                       # the article's last page (PDF index)
    uncertain: bool = False         # pages after its text that may still be its own were kept (`last` includes them)
    outside: tuple[int, ...] = ()   # pages after `last` that are no item's: adverts, blank pages, pictures
    unlisted: tuple[int, str] | None = None     # (page, heading) of the item that starts right after it
    signed: tuple[str, ...] = ()    # the name(s) the article is signed with where its text ends
    kept: tuple[int, ...] = ()      # the uncertain pages kept after its text (`uncertain`)


def _filler(page: Page, kind: Kind) -> bool:
    """A page that is no item's text: blank, an advert, or one with fewer than _DENSE long lines in any one size."""
    if kind in (Kind.BLANK, Kind.ADVERT):
        return True
    sizes = Counter(round(ln.size * 2) / 2 for ln in content(page) if len(ln.text) >= _LONG)
    return not sizes or max(sizes.values()) < _DENSE


def _has_text(page: Page, t: Typography, after: int = -1) -> bool:
    """The page holds an item's text (after content line `after`): three running lines or more in the body size. Only
    running text decides; ordinary words an advert may also print ("ég tel", "hf.", "banki") change nothing."""
    lines = content(page)[after + 1:]
    return sum(1 for ln in lines if len(ln.text) >= max(_LONG, 0.5 * t.line) and _same(ln.size, t.body)) >= 3


_ISSUE_LINE = re.compile(r"\s*\d{1,2}\.\s*(?:hefti|tbl\.?|tölublað)\b.*\b(?:árg|ÁRG)", re.I)


_CONTENTS_LINE = re.compile(r"\s*efni(?:syfirlit)?\s*:?\s*$", re.I)


def _issue_cover(page: Page) -> bool:
    """The page is the cover of the next issue in a volume: it prints the issue line on its own ("4. HEFTI 35. ÁRGANGUR
    DESEMBER 1985", "4. hefti • 54. árgangur"), short, in its upper half below the margin band, and either at 95% of
    the body size or larger, or together with a contents line ("EFNI:", "Efnisyfirlit") in that same part of the page
    whatever the type size (an inner cover, TL 1974 pdf 194: "3. HEFTI 25. ÁRGANGUR" at 85% above its "EFNI:"). A running
    head ("3. tbl. 2023 – 76. árg. Úlfljóts"), a citation ("Tímarit lögfræðinga, 1. hefti 74. árg. 2024, bls. 9–61"), a
    bibliography line or a footnote is no cover. (Read on every line: content() drops the issue line as furniture.)"""
    body = FP.body_size(page)
    upper = [ln for ln in page.lines if FP._MARGIN_BAND * page.height < ln.y < 0.5 * page.height]
    issue = [ln for ln in upper if _ISSUE_LINE.match(ln.text) and len(ln.text) <= 50 and "bls" not in ln.text.lower()]
    return any(ln.size >= 0.95 * body for ln in issue) or (
        bool(issue) and any(_CONTENTS_LINE.match(ln.text) for ln in upper))


def _contact(page: Page, after: int = -1) -> bool:
    """The page (after content line `after`) prints an advert's contact: a phone number, "Sími", a web or e-mail
    address (HR's "hr.is/lagadeild", Bifröst's courses): the item that starts there is an advert, not one to flag."""
    return any(_STRONG_MARKS.search(_CITED_URL.sub(" ", ln.text)) for ln in content(page)[after + 1:])


def article_end(page: Callable[[int], Page], first: int, last: int, title: str | None,
                picture: Callable[[int], float] | None = None, authors: Sequence[str] = (),
                ink: Callable[[int], int] | None = None) -> End:
    """Where the article that starts on PDF page `first` ends, at most at `last` (the page before the next located
    article, or the file's last page). Its pages are classified until another item starts, at a page's top or under
    its signature half way down one of its pages; the end is its last TEXT page, and OTHER pages right after it are
    kept as uncertain. A page of text after adverts must go on with the article (continues), else nothing after the
    adverts is its. When an item starts at the top of a page while the article's last page stopped mid-sentence (one
    column), the article goes on on that page: if that page stops mid-sentence too and the next is its text, the
    heading was an insert ("Til lesenda", a notice) and it goes on; otherwise its last lines are at the foot of that
    page (an old layout) and it ends there. `unlisted` is set only when the item that follows has text of its own (an
    advert's heading is no item; an item under a name line half way down without text of its own is no item either).
    `ink(i)`: the vector drawings of page i (asked of a page without text lines). Without running text to measure (a
    picture, a one-line page) the range stays."""
    following = range(first + 1, min(first + _PROFILE_PAGES, last + 1))
    scan = picture is None or all(picture(i) >= _SCAN for i in (first, *following))
    profile = [page(first)] + [page(i) for i in following      # its next pages, except those that look like adverts
                               if advert_marks(page(i)) < 3 and (scan or picture(i) < _PICTURE_PAGE)]
    t = typography(profile)
    if not t.sizes:
        return End(last)
    kinds: dict[int, Kind] = {}

    def kind(i: int, after_text: bool = False) -> Kind:
        if i not in kinds:
            bare = ink is not None and not scan and not any(re.search(r"[^\W\d_]{2}", ln.text) for ln in content(page(i)))
            kinds[i] = classify(page(i), _scaled(t, page(i)) if scan else t, title, 0.0 if scan else picture(i),
                                after_text=after_text, authors=authors, drawings=ink(i) if bare else 0)
        return kinds[i]

    def starts(i: int, heading: str | None, after: int = -1) -> tuple[int, str] | None:
        """The item that starts on page i (below content line `after`), when it has text of its own there or on one of
        the next two pages and is no advert (no contact marks where it starts)."""
        has = _has_text(page(i), t, after) or after < 0 and any(_has_text(page(j), t)
                                                                for j in range(i + 1, min(i + 3, last + 1)))
        return (i, heading or "") if has and not _contact(page(i), after) and not _issue_cover(page(i)) else None
    def heading(i: int) -> str:
        """The new item's heading, or the line in the largest type on its upper half ("VIÐTAL")."""
        h = new_item_at_top(page(i), t, title) or (top := _top_heading(page(i), t)) and top.text
        upper = [ln for ln in content(page(i)) if ln.y < 0.5 * page(i).height
                 and len(re.findall(r"[^\W\d_]", ln.text)) >= 3]
        return h or (max(upper, key=lambda ln: ln.size).text[:60] if upper else "")
    end, pending, unlisted, signed_at, stop = first, [], None, None, last + 1
    apart: set[int] = set()                           # pending pages that are no part of the text before them (23b)
    own = next((k for k, ln in enumerate(content(page(first))) if part_of_title(ln.text, title)), None)
    if own is not None and (mid := item_below_signature(page(first), t, title)) and mid[0] > own \
            and _has_text(page(first), t, mid[0]):                  # (below its own title only)
        stop, signed_at, unlisted = first + 1, mid[0], starts(first, mid[1], mid[0])
    i = first + 1
    while i < stop:
        k = kind(i, after_text=(end == i - 1 and not pending))
        if k == Kind.NEW and end == i - 1 and not pending and ends_mid_sentence(page(end), t):
            if ends_mid_sentence(page(i), t) and i < last and kind(i + 1) == Kind.TEXT:
                kinds[i] = k = Kind.TEXT              # an insert at the top of a page of the article
            elif _tail_block(page(i), t):
                end = i                               # its last lines at the foot of the next item's first page
        if k == Kind.TEXT and end < i - 1 and not pending and not continues(page(i), t, title, authors):
            k = Kind.NEW                              # text after adverts that does not go on with the article
        if k == Kind.NEW:
            stop, unlisted = i, starts(i, heading(i))
            break
        if k == Kind.TEXT:
            end, pending = i, []
            if (mid := item_below_signature(page(i), t, title)) and _has_text(page(i), t, mid[0]):
                stop, signed_at, unlisted = i + 1, mid[0], starts(i, mid[1], mid[0])
        elif k == Kind.OTHER and (pending[-1] if pending else end) == i - 1:
            if all(j in apart for j in pending) and _apart(page(i), t) and text_ends(page(end), t):
                apart.add(i)                          # its text has ended; a picture or an advert's box (23b)
            pending.append(i)
        i += 1
    if pending and all(j in apart for j in pending):
        pending = []                                  # no text of its own after them: they are not the article's
    if pending:
        end = pending[-1]
    outside = tuple(j for j in range(end + 1, last + 1) if j != stop and _filler(page(j), kind(j)))
    at = signed_at if signed_at is not None and stop == end + 1 else None
    return End(end, bool(pending), outside, unlisted, tuple(signature(page(end), t, title, before=at)), tuple(pending))


# --- signatures ----------------------------------------------------------------------------------------------------

def _subjects(title: str | None) -> list[str]:
    """The people an article's title names (an obituary's subject, an interviewee): they do not sign it."""
    return [n for part in re.split(r"\s+[—–-]\s+|:\s+|,\s+|\s+við\s+", title or "")
            for n in FP.names_in(part, styled=True)]


def _not_subject(names: list[str], title: str | None) -> list[str]:
    subjects = _subjects(title)
    return [n for n in names if not any(difflib.SequenceMatcher(None, _fold(n), _fold(s)).ratio() >= 0.8
                                        for s in subjects)]


def _joined(lines: list[Line], k: int) -> str:
    """A signature of two people over two lines ("Gunnlaugur Pétursson og" / "Hjörtur Hjartarson") as one."""
    text = lines[k].text
    if re.search(r"\s(?:og|and|&)$", text) and k + 1 < len(lines):
        return f"{text} {lines[k + 1].text}"
    return text


def _set_apart(lines: list[Line], k: int, t: Typography, page: Page) -> bool:
    """A signature stands apart from the text: bold, in another size, indented or set right, a role after it."""
    ln = lines[k]
    if ln.bold or not _same(ln.size, t.body) or "," in ln.text:
        return True
    body = [x for x in lines if _is_running(x, t) and _same(x.size, t.body)]
    left = min((x.x0 for x in body), default=0.0)
    width = max((x.x1 for x in body), default=0.0) - left
    if width > 0 and ln.x0 - left >= 0.25 * width:
        return True
    return k + 1 < len(lines) and bool(_ROLE.match(lines[k + 1].text))


def _after_text(lines: list[Line], k: int, t: Typography) -> bool:
    """The line ends a paragraph of the article's text: within the three lines above it is a line of its running text
    in the body size, and the line just above is in that size (a table's or a list's cells are not)."""
    above = lines[max(0, k - 3):k]
    return bool(above) and _same(above[-1].size, t.body) and any(_is_running(x, t) and _same(x.size, t.body)
                                                                 for x in above)


_SIGNATURE_MAX = 1.4      # a name set this many times the body or larger is an advert's or a heading, no signature
_AD_BLOCK = 6             # lines under a name line that are read for an advert's contact (Task 23b)


def _caption_or_advert(lines: list[Line], k: int, t: Typography) -> bool:
    """The name line is no signature (Task 23b): set much larger than the body (a translator's classified advert,
    "Lúðvík Emil Kaaber" in 21 pt), a caption in the margin beside the text column (LMBL 2012 pdf 26: "Guðrún Helga /
    Brynleifsdóttir hrl." under a photo left of the column), or the head of a block that prints an advert's contact
    (a phone number, an address on the web) in the lines that follow it closely."""
    ln = lines[k]
    if ln.size >= _SIGNATURE_MAX * t.body:
        return True
    body = [x for x in lines if _is_running(x, t) and _same(x.size, t.body)]
    if body and ln.x1 and ln.x1 <= min(x.x0 for x in body):
        return True
    block, prev = [], ln
    for x in lines[k + 1:k + 1 + _AD_BLOCK]:
        if x.x1 and ln.x1 and (x.x1 < ln.x0 or x.x0 > ln.x1):
            continue                                  # beside it, not under it (a download stamp up the margin)
        if not 0 < x.y - prev.y <= _GAP * max(x.size, prev.size):
            break
        block.append(x)
        prev = x
    return any(_STRONG_MARKS.search(_CITED_URL.sub(" ", x.text)) or _FIRM.search(x.text) for x in block)


def signature(page: Page, t: Typography, title: str | None, *, before: int | None = None) -> list[str]:
    """The name(s) the article is signed with where its text ends on this page: a name line right after its running
    text, set apart (bold, another size, indented or right, a role after a comma or below), after which no line of
    running text in the body size follows (footnotes and a short last line beside it may), on a page that is no list
    of names, and no caption or advert (_caption_or_advert); or the name line just above an item that starts half way
    down the page (`before`, an index in content(page)). [] otherwise."""
    lines = content(page)
    if before is not None:
        return _not_subject(signed_names(lines, before), title)
    if sum(1 for ln in lines if _name_only(ln.text)) > 3:
        return []                                     # a list of people (Lögfræðingatal), not a signature
    for k in range(len(lines) - 1, -1, -1):
        ln = lines[k]
        if k and re.search(r"\s(?:og|and|&)$", lines[k - 1].text) and _name_only(_joined(lines, k - 1)):
            continue                                  # the second line of a two-person signature
        if ln.size >= 0.8 * t.body and len(ln.text) <= 60 and _set_apart(lines, k, t, page) \
                and _after_text(lines, k, t) and not _caption_or_advert(lines, k, t) \
                and (names := _not_subject(signed_names(lines, k), title)):
            return names
        if _is_running(ln, t) and _same(ln.size, t.body):
            return []                                 # running text after any name line: not signed here
    return []


def part_signatures(page: Page, t: Typography, title: str | None) -> list[str]:
    """The signatures that end the parts of a memorial by several contributors on this page: a name line (not its
    subject's) after running text and before a gap of _GAP body sizes, a heading, or the page's end (footnotes aside)."""
    lines = content(page)
    out: list[str] = []
    seen = False
    for k, ln in enumerate(lines):
        joined = _joined(lines, k)
        names = _not_subject(signed_names(lines, k), title) if seen and ln.size >= 0.8 * t.body and len(ln.text) <= 60 \
            and not (k and re.search(r"\s(?:og|and|&)$", lines[k - 1].text)) and _after_text(lines, k, t) else []
        if not names:
            seen = seen or (_is_running(ln, t) and _same(ln.size, t.body))
            continue
        rest = lines[k + (2 if joined != ln.text else 1):]
        nxt = next((x for x in rest if _same(x.size, t.body) or x.size >= FP._PROMINENT * t.body), None)
        if nxt is None or nxt.y - ln.y >= _GAP * t.body or nxt.size >= FP._PROMINENT * t.body:
            out += [n for n in names if n not in out]
    return out
