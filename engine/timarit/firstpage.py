"""What an article's own pages print (Task 22b, ruling R40 "heimildin ræður").

The printed page outranks the registries (TOC, the Úlfljótur spreadsheet, leitir.is, the file name) for the title
text, the authors and the page numbers; the registries decide which articles exist and supply a value only where the
page is silent or unreadable.

A page is read as visual lines: PyMuPDF's lines with their font size, sorted top to bottom and joined when they stand
side by side on one baseline. The text layer's own order is not the reading order (a title may come last, after the
body), and the font size tells a heading from the body and from the running head.

- printed_title: the lines at the top of a page that print a registry title, as printed (a truncated TOC title gets its
  whole heading, an OCR typo in the registry is corrected by the page, a TOC entry that ran three items together is cut
  to the one the page prints);
- page_heading: the page's main heading when the registry has only a section label ("Fræðabálkur", "Leiðari") or no
  title at all;
- choose_title: which of the two wins;
- byline / signature / merge_authors: authors printed under (or above) the heading, in a credit ("Texti: X",
  "Ritdómur eftir X"), or signed at the end of an editorial;
- page_heads_digest: one line per page for the Claude fallback when an issue has no readable TOC.
"""
from __future__ import annotations

import datetime
import difflib
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import NamedTuple, Sequence

import fitz

from engine.processors.lemmatizer import _bin
from engine.timarit.authors import _is_initial, _is_name_token, clean_author, looks_like_person
from engine.timarit.toc import SECTION_LABELS, _nominative, is_label, split_label
from engine.timarit.ulfljotur_xlsx import _fold

_LABEL_KEYS = tuple(SECTION_LABELS)
SAME_TITLE = 0.85          # a window of the page and a registry title are the same title from this score on
_TOP = 0.6                 # titles, bylines and credits stand in the top part of a first page
_LOW_TITLE = (0.7, 2.5)    # a title block may start this low on the page when set this many times the body (LMBL)
_MAX_WINDOW = 8            # lines a printed title may span
_MARGIN_BAND = 0.12       # running heads and folios stand in the top or bottom 12% of a page
_PROMINENT = 1.25          # a heading's font is at least this much larger than the body's
EMPHASIS = 1.05            # a title line set only a little larger than the body (TL: 11.5 pt over 10.6 pt)
_LIGATURES = str.maketrans({"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl"})
_INNER_SOFT_HYPHEN = re.compile(r"\xad(?!$)")         # inside a line it is invisible; at its end join_title needs it


@dataclass(frozen=True)
class Line:
    text: str
    size: float
    y: float               # top of the line, in points from the top of the page
    bold: bool = False
    x0: float = 0.0        # left and right edge (points; 0.0 when unknown)
    x1: float = 0.0


@dataclass(frozen=True)
class Page:
    lines: list[Line]      # top to bottom
    height: float
    width: float = 0.0     # 0.0 when unknown


EMPTY = Page([], 1.0)


def read_page(page: fitz.Page) -> Page:
    """The page as visual lines: PyMuPDF lines sorted by their baseline, then left edge; two lines on the same baseline
    whose horizontal gap is under one font size are one line (spans set in two sizes, OCR'd words). A wider gap is a
    column gutter: the columns stay apart. A line's size is the size most of its characters are set in."""
    items = []
    for block in page.get_text("dict", flags=0)["blocks"]:
        for ln in block.get("lines", []):
            spans = [s for s in ln["spans"] if s["text"].strip()]
            if not spans:
                continue
            text = _INNER_SOFT_HYPHEN.sub("", " ".join("".join(s["text"] for s in ln["spans"]).translate(_LIGATURES).split()))
            base = max(s["origin"][1] for s in spans)
            items.append([base, min(s["bbox"][0] for s in spans), max(s["bbox"][2] for s in spans),
                          _main_size(spans), any(s["flags"] & 16 for s in spans), text, ln["bbox"][1]])
    items.sort(key=lambda it: (round(it[0]), it[1]))
    merged: list[list] = []
    for it in items:
        prev = merged[-1] if merged else None
        if prev and abs(it[0] - prev[0]) <= 2 and it[1] > prev[1] and it[1] - prev[2] <= max(it[3], prev[3]):
            size = prev[3] if len(prev[5]) >= len(it[5]) else it[3]       # the type most of the line is set in
            prev[2], prev[3], prev[4], prev[5] = it[2], size, prev[4] or it[4], f"{prev[5]} {it[5]}"
            prev[6] = min(prev[6], it[6])
        else:
            merged.append(it)
    lines = [Line(unicodedata.normalize("NFC", t), round(size, 1), round(top, 1), bold, round(x0, 1), round(x1, 1))
             for _, x0, x1, size, bold, t, top in merged]
    return Page(lines, page.rect.height or 1.0, page.rect.width)


def _main_size(spans: list[dict]) -> float:
    """The font size most of a line's characters are set in: a drop cap ("Þ" + "inn. Þá var …") or a footnote mark
    does not change it."""
    chars: Counter[float] = Counter()
    for sp in spans:
        chars[round(sp["size"], 1)] += len(sp["text"].strip())
    return max(chars.items(), key=lambda kv: (kv[1], kv[0]))[0]


class PageReader:
    """Visual lines of a document's pages, read on first use (a dict extraction costs a few ms a page)."""

    def __init__(self, doc: fitz.Document | None):
        self._doc = doc
        self._pages: dict[int, Page] = {}

    def __len__(self) -> int:
        return len(self._doc) if self._doc is not None else 0

    def __call__(self, i: int) -> Page:
        if self._doc is None or not 0 <= i < len(self._doc):
            return EMPTY
        if i not in self._pages:
            self._pages[i] = read_page(self._doc[i])
        return self._pages[i]

    def drawings(self, i: int) -> int:
        """How many vector drawings page i has (an article's text drawn as outlines has hundreds)."""
        if self._doc is None or not 0 <= i < len(self._doc):
            return 0
        return len(self._doc[i].get_cdrawings())

    def picture(self, i: int) -> float:
        """The share of page i that pictures cover (an advert's photograph; a scan's whole page), at most 1.0."""
        if self._doc is None or not 0 <= i < len(self._doc):
            return 0.0
        page = self._doc[i]
        area = page.rect.width * page.rect.height or 1.0
        boxes = [fitz.Rect(info["bbox"]) & page.rect for info in page.get_image_info()]
        return min(1.0, sum(b.width * b.height for b in boxes if not b.is_empty) / area)


# --- text measures ---------------------------------------------------------------------------------------------

def _bin_knows(word: str) -> bool:
    return bool(_bin.lookup(word)[1] or _bin.lookup(word.lower())[1])


def text_quality(s: str) -> float:
    """Share of the words (two letters or more) that BÍN knows, an acronym counting as known: an OCR typo
    ("reflur", "maf", "aSalfundi", "lslandi") lowers it. 1.0 for a text without words."""
    words = re.findall(r"[^\W\d_]{2,}", s)
    if not words:
        return 1.0
    known = sum(1 for w in words if (w.isupper() and len(w) <= 5) or _bin_knows(w))
    return known / len(words)


def _partial(short: str, long: str) -> float:
    """Best similarity of `short` to a window of `long` of the same length that starts at a word."""
    sm = difflib.SequenceMatcher(None, autojunk=False)
    sm.set_seq2(short)
    best = 0.0
    for i in [0] + [k + 1 for k, ch in enumerate(long) if ch == " "]:
        sm.set_seq1(long[i:i + len(short)])
        if sm.real_quick_ratio() > best and sm.quick_ratio() > best:
            best = max(best, sm.ratio())
    return best


def _subsequence(short: str, long: str) -> bool:
    words, it = short.split(), iter(long.split())
    return len(words) >= 3 and all(any(w == x for x in it) for w in words)


class Score(NamedTuple):
    score: float           # same title from SAME_TITLE on
    whole: float           # similarity of the whole strings


def match_score(a: str, b: str, *, min_share: float = 0.3, loose: bool = False, contain: bool = False) -> Score:
    """How well two titles agree (folded: case, accents, punctuation). The whole strings' similarity, or, when the
    shorter is at least 15 characters and `min_share` of the longer, its best match inside the longer one (a TOC
    title cut short, a registry title with a section label or another item glued to it); with `contain`, only `b`
    inside `a`. `loose` (a title taken from a file name) also accepts the shorter one's words, three or more, in order
    inside the longer."""
    return _score_folded(_fold(a), _fold(b), min_share=min_share, loose=loose, contain=contain)


def _score_folded(fa: str, fb: str, *, min_share: float, loose: bool, contain: bool = False) -> Score:
    """`contain`: only `fb` (the registry title) inside `fa` (the page) counts as a partial match, never a fragment of
    the title printed on the page (a double obituary's second name is not where the pair starts)."""
    if not fa or not fb:
        return Score(0.0, 0.0)
    if fa == fb:
        return Score(1.0, 1.0)
    sm = difflib.SequenceMatcher(None, fa, fb, autojunk=False)
    whole = sm.ratio() if sm.real_quick_ratio() >= 0.5 and sm.quick_ratio() >= 0.5 else 0.0
    score = whole
    short, long = sorted((fa, fb), key=len)
    if len(short) >= 15 and len(short) >= min_share * len(long) and not (contain and short is not fb) \
            and set(short.split()[0]) <= set(long):
        score = max(score, _partial(short, long))
    if loose and _subsequence(short, long):
        score = max(score, SAME_TITLE + 0.01)
    return Score(score, whole)


# --- the top of a page ------------------------------------------------------------------------------------------

_JOURNAL_WORDS = re.compile(
    r"^(?:ulfljotur|ulfljots|logmannabladid|logfraedingur|timarit logfraedinga|logbru|logretta|timarit logrettu|"
    r"stjornmal (?:og|&) stjornsysla|vefrit ulfljots|tbl|arg|argangur|hefti)\b")
_NOT_A_TITLE = re.compile(r"^(?:efnisyfirlit|efni|ágrip|útdráttur|abstract|summary|inngangur|meðal efnis|heimildaskrá|"
                          r"heimildir|niðurstöður|lokaorð|introduction|conclusions?|references|contents)\b", re.I)
_OUTLINE = re.compile(r"^\d{1,2}(?:\.\d{1,2})*\.?\s+[A-ZÁÉÍÓÚÝÞÆÖ]|^[IVX]+\.\s")   # "1. Inngangur", not "37. norræna …"
_SUBTITLE = 1.1            # a subtitle right under the title block may be set this little above the body
_EDITOR = re.compile(r"\((?:eds?|ritstj|ritst|ritstjórar|ritstjóri)\.?\)\s*$", re.I)
_DATE_LINE = re.compile(r"^\s*(?:janúar|febrúar|mars|apríl|maí|júní|júlí|ágúst|september|október|nóvember|desember|"
                        r"vor|haust)\s+(?:19|20)\d\d\s*$", re.I)
# a sentence inside a line ("… 12. mars. Að", "… sl. Þá") or a line that ends one: lead text, not a subtitle
_SENTENCE = re.compile(r"[^\W\d_]{3}[.!]\s+[A-ZÁÉÍÓÚÝÞÆÖ]|(?:\S+\s+){4}.*[^\W\d_]{3}\.$")


_MONTHS_FOLDED = frozenset({"januar", "februar", "mars", "april", "mai", "juni", "juli", "agust", "september",
                            "oktober", "november", "desember", "vor", "haust", "sumar", "vetur"})
_JOURNAL_NAMES = ("timarit logfraedinga", "logfraedinga", "timarit", "ulfljotur", "logmannabladid", "logfraedingur",
                  "logretta", "timarit logrettu", "logbru")


def _closest_journal(name: str) -> str:
    return max(_JOURNAL_NAMES, key=lambda j: difflib.SequenceMatcher(None, name, j).ratio())


def _garbled(text: str) -> bool:
    """OCR noise in large type, such as TL's masthead as the 1985 scan reads it ("itmaiut— —", "LÖI.I It I IH M.A",
    "rniAiíii—"): no word of four letters or more that BÍN knows, and a dash or dots inside words. A misread title
    in mixed case ("SamMmsömun" for "Samsömun") is still the title."""
    if any(_bin_knows(w) for w in re.findall(r"[^\W\d_]{4,}", text)):
        return False
    if "—" in text:
        return True
    if any(w.islower() or w[:1].isupper() and w[1:].islower() for w in re.findall(r"[^\W\d_]{5,}", text)):
        return False                                  # an ordinary word (English: "under", "Polar")
    return bool(re.search(r"[^\W\d_]\.[^\W\d_]", text))


def is_furniture(line: Line) -> bool:
    """A page number, the journal's running head or masthead ("Úlfljótur 271", "155 / LÖGBRÚ", "LÖGMANNABLAÐIÐ TBL
    01/17", "1. tbl. 2010 - 63. árg.", "Tímarit" alone, the OCR'd "löqfræðinqa"), a lone letter (Lögrétta's article
    letters)."""
    f = _fold(line.text)
    rest = re.sub(r"\b\d+\b", " ", f).split()
    if not rest:
        return bool(re.fullmatch(r"[\W_]*\d{1,4}[\W_]*", line.text)) or len(f.split()) > 2
    if len(rest) == 1 and len(rest[0]) == 1:
        return True
    words = " ".join(rest)
    if bool(_JOURNAL_WORDS.match(words)) and len(rest) <= 6 or ("tbl" in rest and "arg" in rest):
        return True
    named = [w for w in rest if w not in _MONTHS_FOLDED]
    if not named:
        return False                                  # a date ("desember 2004") may be part of a title
    if named == ["timarit"]:
        return True                                   # TL's masthead word on a line of its own
    # the journal's name as the OCR read the masthead ("löqfræðinqa", "Tímarit löqfræðinqa mars 2004"): close to a
    # name and with a word BÍN does not know (a real word, "Lögfræðingatal", is a heading)
    raw = [w for w in re.findall(r"[^\W\d_]+", line.text) if _fold(w) not in _MONTHS_FOLDED]
    return len(named) <= 2 and not all(_bin_knows(w) for w in raw) \
        and difflib.SequenceMatcher(None, " ".join(named), _closest_journal(" ".join(named))).ratio() >= 0.8


def body_size(page: Page) -> float:
    """The body's font size: the median size over the page's characters."""
    sizes = sorted((ln.size, len(ln.text)) for ln in page.lines)
    total = sum(n for _, n in sizes)
    if not total:
        return 0.0
    acc = 0
    for size, n in sizes:
        acc += n
        if acc * 2 >= total:
            return size
    return sizes[-1][0]


def _content_lines(page: Page, bottom: float) -> list[Line]:
    margin = [ln.y for ln in page.lines if is_furniture(ln) and len(ln.text.strip()) > 1
              and not _MARGIN_BAND * page.height < ln.y < (1 - _MARGIN_BAND) * page.height]
    return [ln for ln in page.lines if ln.y < bottom * page.height and not is_furniture(ln)
            and not any(abs(ln.y - y) <= 2 for y in margin)]


def top_lines(page: Page) -> list[Line]:
    """The lines in the top part of the page that are neither furniture nor a running head: a line on the baseline of
    a page number or journal line in the page's top or bottom margin is the running head beside it (Rannsóknir prints
    the article's title there on odd pages: "Nokkur orð um eignarhugtakið …" / "11"), never where the article starts.
    (A lone letter in a heading, "í", is no page number.)"""
    return _content_lines(page, _TOP)


# footnote marks after a title: "bankahruni1", "háttað?1", "490/2018*1", "keldulands1)2"
_TRAILING_MARK = re.compile(r"(?:(?<=[^\W\d_])|(?<=[?!”“\"]))\d{1,2}(?:\)\d{1,2})?\)?$|\s*\*+\d{0,2}$")
_GLUED_YEAR = re.compile(r"([^\W\d_]{2,})(\d{4})\b")                # OCR: "MAÍ1985"


def join_title(lines: list[str]) -> str | None:
    """Title lines as one title: a soft hyphen or a hyphen before a lower-case word joins the word ("Rök-" /
    "semdir"); "EES-" / "launþega" keeps its hyphen; "lífs-" / "og" keeps its compound dash. A footnote mark at the end
    goes ("ærumeiðingamálum1", "höfundaréttar*")."""
    out = ""
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if out.endswith("\xad"):
            out = out[:-1] + ln
        elif out.endswith("-") and ln[:1].islower() and out[-2:-1].islower() \
                and not re.match(r"(?:og|eða|né|and|or)\b", ln, re.I):
            out = out[:-1] + ln
        elif out.endswith("-") and out[-2:-1].isalpha() and not re.match(r"(?:og|eða|né|and|or)\b", ln, re.I) \
                and (ln[:1].isupper() or out[-2:-1].isupper()):
            out = out + ln                       # "EES-" / "launþega", "Norður-" / "Íshafsins": the hyphen stays
        else:
            out = f"{out} {ln}" if out else ln
    out = _TRAILING_MARK.sub("", out.replace("\xad", "").strip()).strip()
    out = re.sub(r"\s+([?!.,:;])", r"\1", out)                      # "Íslandi ?" as set by some layouts
    out = re.sub(r"\s+[-–—]$", "", out)                              # "- Gjaldfelling lánasamninga -"
    out = _TRAILING_MARK.sub("", out).strip()
    if re.search(r"[^\W\d_]{4}\.$", out) and not out.endswith(".."):
        out = out[:-1]                                                  # "Frá aðalfundi." (not "o.fl.")
    return (out[:1].upper() + out[1:]) if out else None


class Heading(NamedTuple):
    text: str
    first: int             # index of its first line in top_lines(page) (page_heading: in the page's content lines)
    last: int
    score: float = 1.0
    size: float = 0.0      # the largest type among its lines


def _window_text(lines: list[Line], i: int, j: int) -> str:
    return join_title([_GLUED_YEAR.sub(r"\1 \2", ln.text) for ln in lines[i:j + 1]]) or ""


def printed_title(page: Page, title: str, *, min_share: float = 0.3, loose: bool = False,
                  contain: bool = False) -> Heading | None:
    """Where the top of the page prints `title`, and how: the window of up to _MAX_WINDOW consecutive lines that
    matches it best (a window matching the whole title before one that only contains it or is contained in it), widened
    to the neighbouring lines of the same heading (same font size, set larger than the body). None when no window
    reaches SAME_TITLE."""
    lines = top_lines(page)
    ft = _fold(title)
    folded = [_fold(_GLUED_YEAR.sub(r"\1 \2", ln.text)) for ln in lines]
    words = [w for w in ft.split() if len(w) >= 4] or ft.split()
    top = " ".join(folded)
    if not words or not any(w in top for w in words):
        return None                      # not one of the title's words is there: no window can match (and it is fast)
    best: tuple | None = None
    for i in range(len(lines)):
        acc = ""
        for j in range(i, min(i + _MAX_WINDOW, len(lines))):
            acc = f"{acc} {folded[j]}" if acc else folded[j]
            if len(acc) > 2.5 * len(ft) + 20:
                break
            sc = _score_folded(acc, ft, min_share=min_share, loose=loose, contain=contain)
            whole = sc.whole >= SAME_TITLE         # a window that is the whole title, ranked by how whole it is
            key = (whole, sc.whole if whole else sc.score, sc.whole, -(j - i))
            if best is None or key > best[0]:
                best = (key, i, j, sc.score)
    if best is None or best[3] < SAME_TITLE:
        return None
    _, i, j, score = best
    body = body_size(page)
    window = _fold(_window_text(lines, i, j))

    def same_heading(a: Line, b: Line) -> bool:
        """`a` continues the heading line `b`: same type, set above the body, close by; not a byline above it."""
        return (abs(a.size - b.size) <= 0.5 and a.bold == b.bold and a.size >= EMPHASIS * body
                and abs(a.y - b.y) <= 2.2 * a.size and not a.text.rstrip().endswith(":") and not names_in(a.text)
                and _fold(a.text) not in window)          # a heading printed twice is not two headings

    while i > 0 and same_heading(lines[i - 1], lines[i]) and not is_label(lines[i - 1].text):
        i -= 1
    while j + 1 < len(lines) and (same_heading(lines[j + 1], lines[j]) or (
            re.search(r"(?:\bog|&|,)$", lines[j].text.strip()) and abs(lines[j + 1].size - lines[j].size) <= 0.5)):
        j += 1
    return Heading(_window_text(lines, i, j), i, j, score, max(ln.size for ln in lines[i:j + 1]))


def title_at_top(page: Page, title: str, *, min_share: float = 0.3, loose: bool = False) -> float:
    """The best score of `title` among the windows at the top of the page (0.0 when it is not there)."""
    h = printed_title(page, title, min_share=min_share, loose=loose)
    return h.score if h else 0.0


def _match_label(text: str) -> str | None:
    """The section label a line prints (its key in SECTION_LABELS), also as the OCR misreads it ("Ávíð 02 dreif" for
    "Á víð og dreif", "Aí vettvangi" for "Af vettvangi"); None for any other line."""
    t = _label_text(text)
    if is_label(t):
        return " ".join(re.sub(r"[^\w\s]", " ", t).split()).lower()
    f = _fold(t).replace(" ", "")
    if len(f) < 8:
        return None
    best = max(((difflib.SequenceMatcher(None, f, _fold(k).replace(" ", "")).ratio(), k) for k in _LABEL_KEYS
                if len(k) >= 8), default=(0.0, None))
    if best[0] < 0.8:
        return None
    # every longer word must be a (misread) word of the label: "Af vettvangi félagsins" is a heading of its own
    label_words = _fold(best[1]).split()
    if any(max(difflib.SequenceMatcher(None, w, lw).ratio() for lw in label_words) < 0.6
           for w in _fold(t).split() if len(w) >= 5):
        return None
    return best[1]


def is_label_line(text: str) -> bool:
    return _match_label(text) is not None


def _label_text(text: str) -> str:
    """A label line without OCR noise around it ("t MINNING t" for "† MINNING †")."""
    toks = text.split()
    while toks and len(toks[0]) == 1 and not toks[0].isupper():
        toks = toks[1:]
    while toks and len(toks[-1]) == 1 and not toks[-1].isupper():
        toks = toks[:-1]
    return " ".join(toks)


_PREFIX_LABELS = ("minning", "in memoriam", "minningarorð")


def _continues_above(lines: list[Line], k: int) -> bool:
    """A line in lower case that is the rest of a line above it: a byline's role ("Ásmundur G. Vilhjálmsson" /
    "lögmaður") or the end of a heading in the same type ("4. Nokkur grundvallaratriði" / "í samanburði
    stofnfjárhluta", with the other column's body lines between them in reading order). A title the OCR begins in
    lower case ("áhrif sérfræðigagna á" under a byline block in other type) is no such rest."""
    ln = lines[k]
    if k and names_in(lines[k - 1].text):
        return True
    for above in reversed(lines[:k]):
        if ln.y - above.y > 2.2 * ln.size:
            return False
        if 0 < ln.y - above.y and abs(above.size - ln.size) <= 0.15 * ln.size:
            return True
    return False


def page_heading(page: Page) -> Heading | None:
    """The page's main heading without a registry to look for: the title block, a run of consecutive lines set clearly
    larger than the body (_PROMINENT) in the top part, in whatever steps of size a magazine sets it ("ÖFLUN" /
    "MÁLFLUTNINGS-" / "RÉTTINDA FYRIR" …, "Resolving Iceland‘s Debt Crisis:" / "Causes, …"), with a subtitle that
    follows it in smaller prominent type ("- Gjaldfelling lánasamninga -"); the run in the largest type wins. A run
    ends at a name (a byline under the title; a name alone in the largest type heads an obituary), at a "Name:" line
    (the author above the title is no part of it), a credit, and a lead or body line (over 80 characters, or over 55
    in half the title's type or less). Section labels ("FRÆÐABÁLKUR", "VIÐTAL"), the running head and
    "Efnisyfirlit"/"Ágrip" are not headings. An obituary's label stays in front of the name it heads ("Minning:
    Arnljótur Björnsson")."""
    lines = _content_lines(page, 1 - _MARGIN_BAND)    # a title block that starts in the top part may run on below it
    body = body_size(page)
    if not lines or not body:
        return None
    blocks: list[list[int]] = []
    name_lines: set[int] = set()
    label_above: dict[int, str] = {}
    last_label = None
    keys = [_match_label(ln.text) for ln in lines]
    two_line = {k for k in range(len(lines) - 1) if not keys[k] and is_label_line(f"{lines[k].text} {lines[k + 1].text}")}
    # The first words of a longer label ("Af vettvangi" of "Af vettvangi dómsmála") are a label only when the next
    # line in the same type is the rest of it as the OCR read it (TL 1985: "Aí vettvangi" / "(lóinsinála"); before a
    # word of their own they head the page (LMBL: "AF VETTVANGI" / "FÉLAGSINS").
    for k, key in enumerate(keys):
        longer = [o for o in _LABEL_KEYS if o.startswith(f"{key} ")] if key else []
        if not longer:
            continue
        a, b = lines[k], lines[k + 1] if k + 1 < len(lines) else None
        if b is None or not (abs(a.size - b.size) <= 0.1 * a.size and a.bold == b.bold and 0 < b.y - a.y <= 2 * a.size):
            continue                   # alone in its type it is the label (TL 1985 pdf 63: "Ai' vettvangi")
        keys[k] = None
        rest = [o[len(key) + 1:] for o in longer]
        if text_quality(b.text) < 1.0 or any(
                difflib.SequenceMatcher(None, _fold(b.text), _fold(r)).ratio() >= 0.6 for r in rest):
            two_line.add(k)
    label_lines = two_line | {k + 1 for k in two_line}
    for k, ln in enumerate(lines):
        if k in label_lines or keys[k]:
            last_label = _label_text(f"{ln.text} {lines[k + 1].text}") if k in two_line else (
                last_label if k in label_lines else _label_text(ln.text))
            continue
        if len(re.findall(r"[^\W\d_]", ln.text)) < 3 or _NOT_A_TITLE.match(ln.text) or _OUTLINE.match(ln.text) \
                or len(ln.text) > 80 or _credit_line(ln, body) or _DATE_LINE.match(ln.text):
            continue                   # body or lead text, a credit, an outline heading, the issue's date
        follows = blocks and blocks[-1][-1] == k - 1 and k - 1 not in name_lines
        subtitle = follows and _SUBTITLE * body <= ln.size < _PROMINENT * body and len(ln.text) <= 60 \
            and ln.y - lines[k - 1].y <= 2.2 * lines[k - 1].size and not names_in(ln.text) \
            and not _SENTENCE.search(ln.text) and not _EDITOR.search(ln.text)
        if subtitle:                   # "Og skyldu íslenska ríkisins til að virða" under "Um íslenska / fiskveiði…"
            blocks[-1].append(k)
            continue
        if ln.size < _PROMINENT * body:
            continue
        run = blocks[-1] if blocks and blocks[-1][-1] == k - 1 and k - 1 not in name_lines else None
        if _garbled(ln.text) and (run is None or abs(ln.size - lines[k - 1].size) > 0.1 * ln.size):
            continue                   # OCR noise in large type starts no title (TL 1985's masthead "itmaiut— —")
        smaller = run is not None and ln.size <= 0.75 * max(lines[i].size for i in run)
        name = bool(names_in(ln.text, styled=smaller and not _plain_words(ln.text))   # "Tim Murphy" under a title
                    or _EDITOR.search(ln.text))     # "Natalia Loukacheva (ed.)" under a reviewed book's title
        if name and ln.text.rstrip().endswith(":"):
            continue                   # "Þorgeir Örlygsson:" above the title is its author
        if name:
            run = None
        if run is not None:
            prev, top = lines[k - 1], max(lines[i].size for i in run)
            lead = ln.size <= 0.6 * top and (len(ln.text) > 55 or bool(_SENTENCE.search(ln.text))) \
                or len(ln.text) > 55 and ln.size <= 0.5 * top
            text_run = sum(len(lines[i].text) >= 50 for i in run) >= 2   # text set in large type (LMBL 2005)
            if lead or ln.y - prev.y > 2.2 * max(ln.size, prev.size) or text_run and ln.size >= 1.3 * prev.size:
                run = None
            if lead:
                continue
        if run is not None:
            run.append(k)
        elif (ln.y < _TOP * page.height or ln.y < _LOW_TITLE[0] * page.height and ln.size >= _LOW_TITLE[1] * body) \
                and not (ln.text[:1].islower() and _continues_above(lines, k)):
            blocks.append([k])
            if last_label:
                label_above[k] = last_label
        if name:
            name_lines.add(k)
        last_label = None
    # Running text is no heading: a block with three body-length lines, or a line in the type of the page's running
    # text when footnotes outweigh it in the body size (Lögrétta 2010 pdf 43: text in 9.5 over notes in 7.0) that is
    # long or sits among long lines of that text: a piece of a justified line, the PDF's words on one baseline
    # (Lögrétta 2014 pdf 88: "Alþjóðadómstólnum"), or a paragraph's last line (Lögrétta 2010 pdf 31: "Airlines
    # Service.20")
    long_sizes = Counter(round(ln.size * 2) / 2 for ln in lines if len(ln.text) >= 35)
    running = [sz for sz, n in long_sizes.items() if n >= 8]

    def running_text(k: int) -> bool:
        ln = lines[k]
        if not any(abs(ln.size - sz) <= 0.05 * sz for sz in running):
            return False
        return len(ln.text) >= 35 or any(
            j != k and len(lines[j].text) >= 35 and abs(lines[j].y - ln.y) <= 1.6 * ln.size
            and abs(lines[j].size - ln.size) <= 0.05 * ln.size for j in range(max(0, k - 8), min(len(lines), k + 9)))
    blocks = [b for b in blocks if sum(len(lines[k].text) >= 50 for k in b) < 3 and not any(map(running_text, b))]
    if not blocks:
        return None
    block = max(blocks, key=lambda b: (max(lines[k].size for k in b), -lines[b[0]].y))
    text = _window_text(lines, block[0], block[-1])
    if not text:
        return None
    text = recase(text)
    label = label_above.get(block[0])
    if label and label.lower() in _PREFIX_LABELS:
        text = f"{label.capitalize()}: {text}"
    return Heading(text, block[0], block[-1], 1.0, max(lines[k].size for k in block))


# --- casing and the choice of title -----------------------------------------------------------------------------

def _all_caps(s: str) -> bool:
    """Set in capitals (the OCR may read a capital Í as "í")."""
    letters = [c for c in s if c.isalpha()]
    return len(letters) >= 4 and sum(c.isupper() for c in letters) >= 0.9 * len(letters)


def _caps_word(tok: str) -> bool:
    letters = [c for c in tok if c.isalpha()]
    return bool(letters) and all(c.isupper() for c in letters)


def _proper(word: str) -> bool:
    """BÍN knows the capitalised word as a place or an organisation ("Íslands", "Landsrétt"), or as a name while the
    lower-case word is no ordinary word."""
    cap = word[:1].upper() + word[1:].lower()
    entries = [e for e in _bin.lookup(cap)[1] if e.ord[:1].isupper()]
    if any(e.hluti in ("lönd", "örn", "fyr", "göt", "heö") for e in entries):
        return True
    return bool(entries) and not _bin.lookup(word.lower())[1]


_ABBREVIATIONS = frozenset({"gr", "mgr", "nr", "tl", "bls", "sbr", "hrl", "hdl", "skv", "þ.e", "o.fl"})


def _recase_word(core: str, k: int, known: dict[str, str]) -> str:
    if (f := _fold(core)) in known:
        return known[f]
    if core.lower() in _ABBREVIATIONS and k > 0:
        return core.lower()                           # "6. GR." → "6. gr."
    if len(core) <= 5 and not _bin.lookup(core.lower())[1] and not _proper(core):
        return core                                   # an acronym: EES, LMFÍ, SÞ
    if k == 0 or _proper(core):
        return core[:1].upper() + core[1:].lower()
    return core.lower()


def recase(printed: str, registry: str = "") -> str:
    """A heading printed in capitals in ordinary case: a word the registry title has takes the registry's spelling;
    other words go to lower case except the first, names and places BÍN knows, and acronyms."""
    toks = printed.split()
    if _all_caps(printed):
        upto = len(toks)
    else:                                       # a run of capitals at the start: "STJÓRNARSKRÁIN OG REFSIÁBYRGÐ (…"
        upto = next((k for k, t in enumerate(toks) if not _caps_word(t)), len(toks))
        if upto < 2 or not _all_caps(" ".join(toks[:upto])):
            return printed
    known = {_fold(w): w for w in re.findall(r"[^\W_]+", registry)}
    out = []
    seen = [0]                                    # words recased so far: only the very first is capitalised

    def word(m: re.Match) -> str:
        seen[0] += 1
        return _recase_word(m.group(0), seen[0] - 1, known)
    for k, tok in enumerate(toks):                # each part of a hyphenated word on its own: "EFTA-DÓMSTÓLLINN"
        out.append(re.sub(r"[^\W_]+", word, tok) if k < upto else tok)
    text = " ".join(out)
    return text[:1].upper() + text[1:]


class TitleChoice(NamedTuple):
    title: str | None
    label: str | None      # a section label found in front of the registry title or the printed one
    from_page: bool        # the page's text replaced the registry's


def choose_title(registry: str | None, page: Page, *, loose: bool = False) -> TitleChoice:
    """The article's title: the registry's, or what the page prints (R40).

    - A registry title that is only a section label (also as the OCR misreads one), or none at all: the page's main
      heading (page_heading), when it has one.
    - Otherwise the printed form of the registry title (printed_title), cased like the registry when printed in
      capitals, compared word by word with the registry (_compare): the printed wording wins unless the page's text
      is the worse reading (OCR misreadings, a word split or hyphenated by the line break, extra words BÍN does not
      know) or the page prints only part of what the registry has while the rest is printed on the page too (a
      subtitle in other type, the second name of a double obituary). When the registry wins, its words that the page
      corrects ("f" → "í") are corrected. An unreadable or absent heading keeps the registry title.
    A section label in front ("GESTABÁLKUR …", "RITSTJÓRNARGREIN: …") is split off and returned for the type."""
    label, rest = split_label(registry) if registry else (None, "")
    if rest and is_label_line(rest):           # a label the OCR misread in the TOC ("Á vfð og dreif")
        label, rest = _label_text(rest), ""
    if not rest:
        h = page_heading(page)
        if h and not is_label(h.text):
            return TitleChoice(h.text, label, True)
        return TitleChoice(registry or None, label, False)
    h = printed_title(page, rest, loose=loose)
    if h is None:
        return TitleChoice(rest, label, False)
    plabel, ptext = split_label(h.text)
    ptext = recase(ptext, rest).strip()
    label = label or plabel
    if not ptext or _squash(ptext) == _squash(rest):
        return TitleChoice(rest, label, False)    # the same words; spacing, hyphens, case or punctuation differ
    if ptext.casefold().startswith(rest.casefold()) and re.match(r"\s*,\s*[^\W\d_]*[a-zþæðöáéíóúý]",
                                                                  ptext[len(rest):]):
        return TitleChoice(rest, label, False)    # the registry's whole title, then ", stórþingsmaður" (an office)
    items = re.split(r"\s+[—–]\s+", rest)
    if len(items) > 1 and _squash(ptext) == _squash(items[0]):
        return TitleChoice(rest, label, False)    # the page heads the first of the items the registry lists
    c = _compare(ptext, rest)
    missing_printed = bool(c.missing) and _printed_on(page, c.missing)
    if missing_printed or (c.worse and c.better <= c.worse):
        corrected = _correct(rest, c.corrections)
        return TitleChoice(corrected, label, corrected != rest)
    return TitleChoice(ptext, label, True)


def _squash(s: str) -> str:
    return _fold(s).replace(" ", "")


class _Comparison(NamedTuple):
    worse: int                     # places where the page reads worse than the registry
    better: int                    # places where the page corrects the registry
    missing: list[str]             # registry words the page's heading lacks
    corrections: dict[str, str]    # registry word → printed word, where the page corrects it


def _compare(printed: str, registry: str) -> _Comparison:
    """The printed title against the registry's, aligned word by word (folded). Worse: a printed word BÍN does not
    know where the registry's is known ("aSalfundi", "lslandi", "mn" for "um", "MISKL"), a word the line break or the
    OCR split or hyphenated ("V iðskiptabréf sreglur", "skaða-bótaábyrgðar"), two unknown spellings ("lextDook" /
    "Textbook": no way to tell), or printed-only words of four letters or more that are mostly unknown ("stórþingsmaöur").
    Better: a registry word BÍN does not know where the printed one is ("reflur" → "reglur", "maf" → "maí", "f" → "í").
    A word cut short at the end of the registry title ("St" for "States") and printed-only words that BÍN knows (a
    subtitle, the beginning a TOC dropped) do not count."""
    a = re.findall(r"[^\W_][\w\-]*", printed)
    b = re.findall(r"[^\W_][\w\-]*", registry)
    fa, fb = [_fold(x).replace(" ", "") for x in a], [_fold(x).replace(" ", "") for x in b]
    worse = better = 0
    missing: list[str] = []
    added: list[str] = []
    corrections: dict[str, str] = {}
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, fa, fb, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if tag == "delete":
            added += a[i1:i2]
            continue
        if tag == "insert":
            missing += b[j1:j2]
            continue
        if "".join(fa[i1:i2]) == "".join(fb[j1:j2]) and i2 - i1 != j2 - j1:
            worse += 1                            # split or joined differently
            continue
        for k, (x, y) in enumerate(zip(a[i1:i2], b[j1:j2])):
            fx, fy = fa[i1 + k], fb[j1 + k]
            if fx == fy:
                continue
            if j1 + k == len(b) - 1 and fx.startswith(fy):
                continue                          # the registry's last word cut short
            if fx.replace("-", "") == fy.replace("-", ""):
                worse += "-" in x and "-" not in y
                continue
            kx, ky = _word_ok(x), _word_ok(y)
            worse += (ky and not kx) or (not kx and not ky)
            if kx and not ky:
                better += 1
                corrections[y] = x
        added += a[i1 + (j2 - j1):i2]
        missing += b[j1 + (i2 - i1):j2]
    long_added = [w for w in added if len(w) >= 4 and not any(c.isdigit() for c in w)]
    if long_added and text_quality(registry) >= 0.7 and sum(not _word_ok(w) for w in long_added) * 2 > len(long_added):
        worse += 1                                # (an English title says nothing through BÍN)
    return _Comparison(worse, better, missing, corrections)


def _printed_on(page: Page, words: list[str]) -> bool:
    """Most of `words` are printed on the page above its bottom margin (a subtitle in other type, a second name, the
    end of a title set low on the page: LMBL 2017 pdf 7 "… félagsmanna í" / "LÖGMANNAFÉLAGI ÍSLANDS")."""
    top = f" {_fold(' '.join(ln.text for ln in _content_lines(page, 1 - _MARGIN_BAND)))} "
    found = sum(1 for w in words if f" {_fold(w)} " in top)
    return found * 5 >= len(words) * 3


def _correct(title: str, corrections: dict[str, str]) -> str:
    for wrong, right in corrections.items():
        title = re.sub(rf"(?<![\w-]){re.escape(wrong)}(?![\w-])", right, title, count=1)
    return title


def _word_ok(w: str) -> bool:
    """BÍN knows the word (each part of a hyphenated one), or it is a number ("1985", "2003-2005"). Letters and digits
    mixed ("H0JESTE", "rit1nu") are an OCR misreading."""
    if not re.search(r"[^\W\d_]", w):
        return True
    return not re.search(r"\d", w) and all(_bin_knows(p) for p in w.split("-") if p)


# --- authors ----------------------------------------------------------------------------------------------------

# A credit names the author: "Texti: X" and "Viðtal tók X" wherever they stand; "eftir X", "Grein eftir X",
# "Ritdómur eftir X", "Reviewed by X" only at the start of a line that stands apart from the body (set in other type
# or short), never inside running text ("Í bók eftir Pál Sigurðsson segir …").
_CREDIT_ANYWHERE = re.compile(r"(?:^|(?<=\s))(?P<word>texti|viðtal tók|viðtalið tók)\s*:?\s+(?P<rest>\S.*)$", re.I)
_CREDIT_LEAD = re.compile(r"^(?P<word>eftir|grein eftir|ritdómur eftir|ritdómar eftir|reviewed by)\s*:?\s+(?P<rest>\S.*)$",
                          re.I)
_CREDIT_LINE_CHARS = 60
_CREDIT_END = re.compile(r"\s+[-–—|]\s+|\s+(?:Myndir?|Ljósmynd(?:ir)?|Ljósmyndari)\s*:", re.I)
_FOOTNOTE_MARKS = re.compile(r"[*†‡¹²³⁴⁵⁶⁷⁸⁹⁰]+|(?<=[^\W\d_])\d{1,2}(?=[\s,;:)]|$)")
_SKRIFA = re.compile(r"\s+skrifa(?:r|ði|ðu)?\s*$", re.I)
_ROLE_LINE = re.compile(r"^(?:ritstjóri|formaður|forseti|deildarforseti|framkvæmdastjóri|ritari|stjórn)\b", re.I)
_LIST_ENTRY = re.compile(r"^([^:]{4,40}):\s*\S")         # "Arnljótur Björnsson: Dómar í skaðabótamálum 1965—1972"
_BYLINE_STOP = re.compile(r"^(?:efnisyfirlit|ágrip|útdráttur|abstract|summary|inngangur|\d+(?:\.\d+)*\.?\s+\S|"
                          r"[IVX]+\.\s)", re.I)


def _given_name(tok: str) -> bool:
    return any(e.hluti == "ism" for e in _bin.lookup(tok)[1])


def _oblique(tok: str) -> bool:
    """BÍN knows the token as a given name, but never in the nominative ("Ármanni", "Margréti"): a name in running
    text or after "eftir", not a byline."""
    names = [e for e in _bin.lookup(tok)[1] if e.hluti == "ism"]
    return bool(names) and not any(e.mark.startswith("NF") for e in names)


def names_in(text: str, *, credit: bool = False, styled: bool = False) -> list[str]:
    """The people a byline names: "GUÐRÚN OLSEN LÖGMAÐUR OG BERGÞÓR BERGSSON LÖGFRÆÐINGUR SKRIFA", "Björn Ingi L. Jónsson,
    lögfræðingur", "Páll Hreinsson:", "ÁRMANN SNÆVARR,". Each part (split on og / & / ;) gives its leading run of name
    tokens without titles; it counts when it is a name by shape and surname (-son/-dóttir), or by shape with a given
    name BÍN knows first (Róbert R. Spanó), or by shape alone after a credit word or on a line set apart from the body
    (`styled`: "Tim Murphy" under a title). A given name BÍN knows only in an oblique case is running text ("Ármanni
    Snævarr boðið"), except after a credit word, which governs it. A part that is no name makes the whole line no
    byline, unless it follows one that is."""
    t = _FOOTNOTE_MARKS.sub("", text)          # "Björg Thorarensen*", "Eiríkur Tómasson¹", "Jón Jónsson1,"
    t = _SKRIFA.sub("", t).strip(" :;")
    t = _CREDIT_END.split(t)[0]
    out: list[str] = []
    after_role = False                # the part before ended in a role or affiliation ("…, Faculty of Social and …")
    for part in re.split(r"\s+(?:og|and)\s+|\s*&\s*|\s*;\s*", t, flags=re.I):
        lenient_ok = (credit or styled) and not after_role
        after_role = "," in part
        name = clean_author(part.split(",")[0])[0]
        toks = []
        for tok in name.split():
            if not (_is_name_token(tok) or _is_initial(tok)):
                break
            toks.append(tok)
        cand = " ".join(toks)
        if not 2 <= len(toks) <= 4 or (not credit and _oblique(toks[0])):
            if not out:
                return []
            break
        if looks_like_person(cand, strict=True) or (looks_like_person(cand, strict=False)
                                                    and (lenient_ok or _given_name(toks[0]))):
            out.append(cand)
        elif not out:
            return []
    return out


def _credit_match(ln: Line, body: float) -> re.Match | None:
    if m := _CREDIT_ANYWHERE.search(ln.text):
        return m
    apart = ln.bold or (body and abs(ln.size - body) >= 0.08 * body) or len(ln.text) <= _CREDIT_LINE_CHARS
    return _CREDIT_LEAD.match(ln.text) if apart and ln.text[:1].isupper() else None   # "eftir …" continues a sentence


def _credit_line(ln: Line, body: float) -> bool:
    return _credit_match(ln, body) is not None


def _credit_names(ln: Line, body: float) -> list[str]:
    """The authors a credit line names (nominative after "eftir"); an institution is no author ("eftir Hæstarétti
    Íslands")."""
    m = _credit_match(ln, body)
    if not m:
        return []
    names = [n for n in names_in(m.group("rest"), credit=True) if not _plain_words(n)]
    return [_nominative(n) for n in names] if "eftir" in m.group("word").lower() else names


def _not_in_title(names: list[str], title: str | None) -> list[str]:
    f = f" {_fold(title or '')} "
    return [n for n in names if f" {_fold(n)} " not in f]


_NO_PERSON = frozenset({"alm", "fyr", "lönd", "örn", "göt", "bær"})    # BÍN: ordinary words, institutions, places


def _plain_words(text: str) -> bool:
    """The line is no person's name: a word before the last that BÍN knows only as an ordinary word, an institution or
    a place ("Könnun Dómstólaráðs", "Spjall um kennsluháttu", "Háskóla Íslands", "Hæstarétti Íslands"). The last word
    may be a surname BÍN stores as a compound noun ("Líndal")."""
    toks = clean_author(text.split(",")[0])[0].split()
    return any(len(t) >= 2 and (e := _bin.lookup(t)[1]) and all(x.hluti in _NO_PERSON for x in e) for t in toks[:-1])


_ROLE_WORDS = re.compile(r"\b(?:professor|prófessor|dósent|lektor|lögmaður|lögfræðingur|dómari|héraðsdómari|"
                         r"faculty|university|háskól\w*|lagadeild\w*|ráðuneyti\w*|stofnun\w*|ritstjóri|stud\.|cand\.|"
                         r"hdl\.|hrl\.|fellow|research\w*)\b", re.I)


def _caption(text: str) -> bool:
    """A photo's caption naming its person, ending in a period ("Ágúst Þór Árnason.", LMBL 2012): no byline. A byline
    may end in a title's period ("hdl.") or an initial's, never in a full name and a period."""
    m = re.search(r"([^\W\d_]{3,})\.\s*$", text)
    return bool(m) and m.group(1)[:1].isupper() and _name_token_ok(m.group(1)) and bool(names_in(text))


def byline(page: Page, heading: Heading | None, title: str | None) -> list[str]:
    """Authors printed with the heading: credits anywhere in the top part ("Texti: Kristján Örvar Sveinsson",
    "Grein eftir Margréti Steinarsdóttur", "Ritdómur eftir …", "Reviewed by …"); name lines just under the heading,
    with role and affiliation lines between them skipped, ending at the body (a line in body type longer than fifty
    characters), an outline or "Ágrip"; and a name block that ends in a colon just above the heading (TL "Páll
    Hreinsson:", Úlfljótur "ÁRMANN SNÆVARR, / CAND. JUR.:"). A name that the title itself contains (an interview, an
    obituary) is no author. More than four names under a heading are a list (Lögfræðingatal), not a byline."""
    lines = top_lines(page)
    body = body_size(page)
    names: list[str] = []
    for ln in lines:
        names += _credit_names(ln, body)
    if heading is not None and not names:          # a credit says who wrote it (a review prints the book's authors)
        below: list[str] = []
        name_style = None             # (size, bold) of the first name line: later lines in that style are bylines too
        skipped = 0
        for ln in lines[heading.last + 1:heading.last + 10]:
            if _BYLINE_STOP.match(ln.text) or len(ln.text) > 100 or (
                    (entry := _LIST_ENTRY.match(ln.text)) and names_in(entry.group(1))):
                break                                # (a list of "Name: Title" entries under a heading is no byline)
            if ln.text[:1].islower() and not _ROLE_WORDS.search(ln.text) and len(ln.text) > 40:
                break                                # body text
            if ln.text[:1].islower() or _ROLE_LINE.match(ln.text) or _credit_line(ln, body) or _caption(ln.text):
                continue
            apart = ln.bold or abs(ln.size - body) >= 0.08 * body
            styled = not _plain_words(ln.text) and (
                (apart and not below and not skipped) or (name_style == (ln.size, ln.bold)))
            found = names_in(ln.text, styled=styled)
            if found:
                below += found
                name_style = name_style or (ln.size, ln.bold)
                continue
            if _ROLE_WORDS.search(ln.text):
                continue
            if not styled and len(ln.text) > 50:
                break
            skipped += 1
            if below or skipped > 2:
                break
        names += below if len(below) <= 4 else []
        above = lines[max(0, heading.first - 2):heading.first]
        if above and above[-1].text.rstrip().endswith(":"):
            names += names_in(above[-1].text, styled=True) or names_in(" ".join(ln.text for ln in above), styled=True)
    out: list[str] = []
    for n in _not_in_title(names, title):
        if not any(_same_person(n, o) for o in out):
            out.append(n)
    return out


def signature(page: Page) -> list[str]:
    """The name an editorial is signed with at its end ("Róbert R. Spanó", "Guðmundur Egill Erlendsson" / "Ritstjóri"):
    the lowest line among the last eight body lines of the page that is a name and nothing else (a role may follow
    a comma). Footnotes (smaller type) and the running foot are passed over."""
    body = body_size(page)
    lines = [ln for ln in page.lines if not is_furniture(ln) and ln.size >= 0.85 * body]
    for ln in reversed(lines[-8:]):
        head = ln.text.split(",")[0].strip(" .")
        names = names_in(head)
        if names and len(head.split()) == sum(len(n.split()) for n in names):
            return names
    return []


def _same_person(a: str, b: str) -> bool:
    return difflib.SequenceMatcher(None, _fold(a), _fold(b)).ratio() >= 0.85


def _name_token_ok(tok: str) -> bool:
    """BÍN knows the token as a name (given name, patronymic, family name), or it is an initial."""
    return _is_initial(tok) or any(e.hluti in ("ism", "föð", "ætt") for e in _bin.lookup(tok)[1])


def _spelling(registry: str, printed: str) -> str:
    """One person's name as the registry and the page spell it: token by token, the printed token unless it is
    only an OCR misreading of the registry's (it differs only in accents, "Hallgrimsson" / "Hallgrímsson", or BÍN
    knows the registry's token as a name and not the printed one, "Jóliannesson", "Benedilct"). Different token
    counts: the printed name when BÍN knows all its tokens, else the registry's."""
    r, p = registry.split(), printed.split()
    if len(r) != len(p):
        return printed if all(_name_token_ok(t) for t in p) else registry
    out = []
    for x, y in zip(r, p):
        if x == y:
            out.append(x)
        elif _fold(x) == _fold(y) or (_name_token_ok(x) and not _name_token_ok(y)):
            out.append(x)
        else:
            out.append(y)
    return " ".join(out)


class AuthorChoice(NamedTuple):
    authors: list[str]
    from_page: bool
    note: str | None


def merge_authors(registry: list[str], printed: list[str]) -> AuthorChoice:
    """The registry's authors against the page's byline. No byline: the registry's. No registry authors: the byline's.
    When every registry name is on the page (the same person: "Matthíassson" in a TOC, "Matthíasson" printed) and the
    page names at least as many, the page's list (missing co-authors added); when the registry lists more, its own
    list. Either way each person is spelt as _spelling decides. A page name that matches no registry name while the
    registry has more or other names is only noted."""
    if not printed:
        return AuthorChoice(list(registry), False, None)
    if not registry:
        return AuthorChoice(list(printed), True, None)
    match = {r: next((p for p in printed if _same_person(r, p)), None) for r in registry}
    if all(match.values()) and len(printed) >= len(registry):
        back = {p: r for r, p in match.items()}
        names = [_spelling(back[p], p) if p in back else p for p in printed]
        return AuthorChoice(names, names != list(registry), None)
    names = [_spelling(r, match[r]) if match[r] else r for r in registry]
    extra = [p for p in printed if p not in match.values()]
    note = f"á síðunni stendur: {', '.join(printed)}" if extra else None
    return AuthorChoice(names, names != list(registry), note)


# --- the Claude fallback's digest -------------------------------------------------------------------------------

_DIGEST_LINE_CHARS = 70


def _running_keys(pages: Sequence[Page]) -> set[str]:
    """Lines (folded, numbers removed) that stand among the first or last two lines of a quarter of the pages or
    more (at least three): running heads and feet."""
    counts: Counter[str] = Counter()
    for p in pages:
        edge = p.lines[:2] + p.lines[-2:]
        counts.update({re.sub(r"\d+", "", _fold(ln.text)).strip() for ln in edge})
    need = max(3, len(pages) // 4)
    return {k for k, n in counts.items() if n >= need}


def page_heads_digest(pages: Sequence[Page], printed: Sequence[int | None], budget: int | None = None) -> str:
    """One line per PDF page: "[{pdf_index}] p.{printed number or ?}: {first line} | {second line}", the two top
    lines that are not the page number, a lone letter, the running head or foot; a page whose heading (page_heading)
    is not among them gets it as well (Lögrétta prints the author above the title). With a `budget` (characters) a
    long issue is compacted step by step until it fits: the pages without a heading first keep one shortened line,
    then only their number, then drop out, so that every article's first page stays in view."""
    running = _running_keys(pages)
    rows = []
    for i, p in enumerate(pages):
        heads = []
        for ln in p.lines:
            if is_furniture(ln) or re.sub(r"\d+", "", _fold(ln.text)).strip() in running:
                continue
            heads.append(ln.text[:_DIGEST_LINE_CHARS])
            if len(heads) == 2:
                break
        h = page_heading(p)
        if h and not any(_fold(h.text)[:30] in _fold(x) for x in heads):
            heads.append(h.text[:_DIGEST_LINE_CHARS])
        num = printed[i] if i < len(printed) and printed[i] is not None else "?"
        rows.append((f"[{i}] p.{num}", heads, h is not None))

    def render(level: int) -> str:
        out = []
        for tag, heads, has_heading in rows:
            if has_heading or level == 0:
                out.append(f"{tag}: {' | '.join(heads)}")
            elif level == 1:
                out.append(f"{tag}: {heads[0][:30]}" if heads else tag)
            elif level == 2:
                out.append(tag)
        return "\n".join(out)

    text = render(0)
    for level in (1, 2, 3):
        if budget is None or len(text) <= budget:
            break
        text = render(level)
    return text


# --- text-layer parsers: Stjórnmál og stjórnsýsla first pages, Vefrit bylines and dates (moved from analyze) -----

_FURNITURE = re.compile(r"^(?:\d{1,4}|&|stjórnmál|stjórnsýsla|n|(?:n\s+)?(?:fræðigreinar|greinar|ritrýnd(?:ar)? greinar?))$",
                        re.I)
_HEADING_END = re.compile(r"^(?:útdráttur|abstract|ágrip|summary|inngangur|efnisyfirlit)\b|\b\d{1,2}\.\s*tbl\b|\bárg\b",
                          re.I)
_MAX_TITLE_LINES = 6
_MAX_AUTHOR_LINES = 10


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


def sogs_heading(text: str) -> tuple[str | None, list[str]]:
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
    return join_title(title), authors


_MONTHS = {mo: i for i, mo in enumerate(["janúar", "febrúar", "mars", "apríl", "maí", "júní", "júlí", "ágúst",
                                         "september", "október", "nóvember", "desember"], start=1)}
_DATE_DMY = re.compile(r"\b(\d{1,2})\.\s*([^\W\d_]+)\s+(\d{4})\b")     # "Birt 21. janúar 2022"
_DATE_MDY = re.compile(r"([^\W\d_]+)\s+(\d{1,2}),\s*(\d{4})")            # "JANÚAR 29, 2018" (the blog's own date)
_EFTIR = re.compile(r"^\s*Eftir\s", re.M)


def publication_date(text: str) -> str | None:
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


def eftir_authors(text: str) -> list[str]:
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
