"""Stjórnmál og stjórnsýsla: title and authors from an article's first page as printed (Task 23b).

The journal's first pages come in three layouts. The early volumes (2005–2010) set the title in large type in the
middle of the page, a subtitle and the byline ("Ólafur Þ. Harðarson prófessor og") in body type under it and the issue
line ("1. tbl. 1. árg. 2005", "Fræðigreinar") below. The later volumes print the masthead ("STJÓRNMÁL & STJÓRNSÝSLA",
"n Fræðigreinar") at the top, then the title, then the byline: one line per author ("Name, role, affiliation") or several
authors in one sentence ("… University of Iceland and Beinta í Jákupsstovu, Associate Professor …"). Some volumes add a
header with the citation, contacts, DOI and licence ("Icelandic Review of Politics and Administration Vol 11, Issue 1
(1-18)"), some text layers hold one word per line, and some pages are typeset twice, a few points apart.

So the page is read as visual lines (firstpage.read_page): the masthead, header and footer are passed over; the title is
the run of lines from the first one in the page's largest type down to the byline, each line drawn twice taken once; the
byline gives the authors, each name split from its role and affiliation.
"""
from __future__ import annotations

import re

from engine.timarit import firstpage as FP
from engine.timarit.authors import _PARTICLES, _is_initial, _is_name_token, clean_author, looks_like_person
from engine.timarit.firstpage import Line, Page
from engine.timarit.ulfljotur_xlsx import _fold

# The masthead and the journal's header and footer lines: never a title or a byline.
_MASTHEAD = re.compile(r"^(?:n\s+)?(?:\d\s+)?(?:(?:stjornmal|stjornsysla|fraedigrein\w*|ritrynd\w*(?: grein\w*)?)\s*)+$")
_HEADER = re.compile(r"icelandic review of politics|©|\bcontacts?\s*:|tengili[ðd]\w*\s*:|article first published|"
                     r"vefbirting|birtist á vefnum|publisher\s*:|útgefandi\s*:|\bdoi\b|this work is licensed|"
                     r"creative commons|netfang|e-?mail\s*:|\S@\S|https?://|www\.|stofnun stjórnsýslufræða|"
                     r"\b\d{1,2}\.\s*tbl\b|\b\d{1,3}\.\s*árg\b|\bsími\b", re.I)
# What ends the byline: the abstract, the start of the text.
_END = re.compile(r"^(?:útdráttur|abstract|ágrip|summary|inngangur|efnisyfirlit|introduction|key ?words|lykilorð)\b",
                  re.I)
_TITLE_SHARE = 0.6          # a subtitle is set at least this share of the title's size; smaller type is the byline
_GAP = 2.0                  # a gap of this many lines' height between the title and the byline
_MAX_TITLE_LINES = 8
_MAX_BYLINE_LINES = 10
_BODY_LINE = 95             # a line this long is running text, not a title or a byline
_TOP = 0.75                 # the title starts in this upper share of the page
# Words of a role or an affiliation: a run of capitalised words with one of these is no person's name.
_NOT_NAME = frozenset(
    "professor profesor adjunct associate assistant senior junior lecturer lektor dosent docent reader researcher "
    "research fellow scholar postdoctoral doctoral doktorsnemi student candidate phd director deputy chief head manager "
    "auditor audit officer advisor adviser consultant specialist analyst coordinator chair chairman president dean "
    "editor university college school faculty department institute centre center ministry office council agency "
    "authority police hospital district science sciences studies law politics political relations administration "
    "economics sociology anthropology philosophy history management business public social international iceland "
    "norway denmark sweden finland faroe islands reykjavik akureyri cia crma mpa cgap msc mba polit dr".split())
_HONORIFIC = frozenset({"dr", "prof", "doktor"})       # "Dr. Gyða Margrét Pétursdóttir"
_INITIALS = re.compile(r"^(?:[A-ZÁÉÍÓÚÝÞÆÖ]\.){1,3}$|^[A-ZÁÉÍÓÚÝÞÆÖ]{2,3}$")      # "J.K.", "JK" after a given name
_SEPARATOR = re.compile(r"^(?:,|;|&|og|and)$", re.I)
_CONJUNCTION = re.compile(r"^(?:&|og|and)$", re.I)
_COMPOUND = re.compile(r"(\w)-\s+og\s+(?=[A-ZÁÉÍÓÚÝÞÆÖ])")       # "Baldvins- og Bjargardóttir": one surname
_GLUE = "⁠"            # joins "Baldvins- og Bjargardóttir" while the byline is split on "og"
_LINE_MARK = re.compile(r"(?:(?<=[^\W\d_])|(?<=[)”“\"?!]))\.?\d{1,2}$|\*+$")   # a footnote mark after a title, a name


def _furniture(ln: Line, page: Page) -> bool:
    f = _fold(ln.text)
    number = not re.search(r"[^\W\d_]", ln.text) and ln.size >= 1.3 * FP.body_size(page)    # "2010" under "1992–"
    return (not f or bool(_MASTHEAD.match(f)) or bool(_HEADER.search(ln.text)) or FP.is_furniture(ln) and not number
            or ln.y > (1 - FP._MARGIN_BAND) * page.height)


def _undouble(text: str) -> str:
    """A line the PDF draws twice side by side ("X X", "X X X") as one."""
    words = text.split()
    for k in (2, 3):
        n = len(words) // k
        if n and len(words) == n * k and all(words[i * n:(i + 1) * n] == words[:n] for i in range(k)):
            return " ".join(words[:n])
    return text


_ACRONYM_NUMBER = re.compile(r"(?<![^\W\d_])[A-ZÁÉÍÓÚÝÞÆÖ]{2,}\d{1,2}$")   # "VSM94": a name, not a mark


def _clean(ln: Line) -> Line:
    """The line without the doubling of a shadow and without a footnote mark after its last word ("bankahruni1",
    "Process of Devolution.1"); a number that belongs to an acronym stays ("… using VSM94")."""
    text = _undouble(ln.text).strip()
    if (m := _LINE_MARK.search(text)) and not _ACRONYM_NUMBER.search(text):
        text = text[:m.start()].rstrip()
    return Line(text, ln.size, ln.y, ln.bold, ln.x0, ln.x1)


# --- names ------------------------------------------------------------------------------------------------------

def _is_particle(t: str) -> bool:
    return t in _PARTICLES or t == "í"


def _token_ok(t: str, first: bool) -> bool:
    core = t.split(_GLUE)[-1]
    return _is_name_token(core) or _is_initial(core) or (not first and bool(_INITIALS.match(core)))


def _accept(toks: list[str], *, shape: bool) -> str | None:
    """The name a run of tokens is, or None. The run ends before a role's or an affiliation's word ("Kristín Loftsdóttir
    Professor"); two to five name tokens remain, and the name has a surname in -son/-dóttir and starts with no ordinary
    word BÍN knows (a place, an institution: "Háskóla Íslands Friðrik Eysteinsson"), or starts with a given name BÍN
    knows, or, where a name stands (`shape`), has the shape of one; neither of the last two may have an ordinary word
    before its last ("Adjunct Professor", "Háskóla Íslands")."""
    while toks and _fold(toks[0]) in _HONORIFIC:
        toks = toks[1:]
    cut = next((i for i, t in enumerate(toks) if _fold(t.split(_GLUE)[-1]) in _NOT_NAME), len(toks))
    toks = toks[:cut]
    while toks and _is_particle(toks[-1]):
        toks = toks[:-1]
    cores = [t.split(_GLUE)[-1] for t in toks if not _is_particle(t)]
    if not 2 <= len(cores) <= 5 or _is_initial(cores[0]):
        return None
    plain = " ".join(cores)
    if looks_like_person(plain, strict=True) and (FP._given_name(cores[0]) or not FP._plain_words(cores[0] + " x")):
        pass
    elif not ((FP._given_name(cores[0]) or shape) and not FP._plain_words(plain)):
        return None
    return clean_author(" ".join(toks))[0].replace(_GLUE, " ")


def _byline_start(text: str) -> bool:
    """The line starts with a person's name by surname or given name (the early layouts set the byline in the
    subtitle's size)."""
    toks: list[str] = []
    for t in _LINE_MARK.sub("", text.split(",")[0]).split():
        if not (_token_ok(t, not toks) or toks and _is_particle(t)):
            break
        toks.append(t)
    return _accept(toks, shape=False) is not None


def byline_authors(lines: list[Line]) -> list[str]:
    """The authors a byline names. A name may start the byline, follow a comma, "og"/"and"/"&" or start a line, and
    runs on over a line's end while it is not whole (it ends in a given name, an initial or a particle: "Gyða
    Margrét" / "Pétursdóttir"). Where a name stands by the layout (the byline's start, after a conjunction, a bold line
    after one that is not) its shape is enough; elsewhere it needs a surname or a given name BÍN knows."""
    toks: list[tuple[str, int]] = []                  # (token, line)
    for k, ln in enumerate(lines):
        text = _COMPOUND.sub(lambda m: f"{m.group(1)}-{_GLUE}og{_GLUE}", _LINE_MARK.sub("", ln.text))
        for t in re.findall(r"[^\s,;]+|[,;]", FP._FOOTNOTE_MARKS.sub("", text)):
            toks.append((t, k))
    starts: dict[int, bool] = {}                      # token index → its shape alone makes a name
    for i, (t, k) in enumerate(toks):
        prev = toks[i - 1] if i else None
        if prev is None:
            starts[i] = True
        elif prev[1] != k:                            # the first token of a line
            starts[i] = lines[k].bold and not lines[prev[1]].bold or bool(_CONJUNCTION.match(prev[0]))
        elif _SEPARATOR.match(prev[0]):
            starts[i] = bool(_CONJUNCTION.match(prev[0]))
    out: list[str] = []
    for i, shape in starts.items():
        run: list[str] = []
        j = i
        while j < len(toks) and len(run) < 7:
            t, k = toks[j]
            if _SEPARATOR.match(t) or not (_token_ok(t, not run) or run and _is_particle(t)) \
                    or run and re.search(r"[^\W\d_]{4}\.$", run[-1]):
                break                                 # (a full stop after a whole name ends it: "… Kristjánsson. Magister")
            if run and k != toks[j - 1][1]:           # over a line's end only while the name is not whole
                last = run[-1].split(_GLUE)[-1]
                if not (FP._given_name(last) or _is_initial(last) or _is_particle(last)):
                    break
            run.append(t)
            j += 1
        name = _accept(run, shape=shape)
        if name and not any(FP._same_person(name, o) for o in out):
            out.append(name)
    # a name read from the middle of another ("Margrét Pétursdóttir" in "Gyða Margrét Pétursdóttir") is that one
    return [n for n in out if not any(n != o and f" {_fold(n)} " in f" {_fold(o)} " for o in out)]


# --- the page -----------------------------------------------------------------------------------------------------

# The journal's citation of the article on its first page: "Stjórnmál & stjórnsýsla 2. tbl., 7. árg. 2011 (327-346)",
# "Icelandic Review of Politics and Administration Vol 11, Issue 1 (55–70)".
_CITATION = re.compile(r"(?:stjórnmál\s*&\s*stjórnsýsla|politics and administration)[^(]{0,60}?\(\s*(\d{1,4})\s*[-–]\s*"
                       r"(\d{1,4})\s*\)", re.I)


def folio(text: str, near: int, slack: int = 3) -> int | None:
    """The page number a page's text prints on a line of its own, within `slack` of `near`; None when there is no such
    number or more than one."""
    found = {int(n) for n in re.findall(r"(?m)^\s*(\d{1,4})\s*$", text) if abs(int(n) - near) <= slack}
    return found.pop() if len(found) == 1 else None


def printed_range(page: Page, pages: int) -> tuple[int, int] | None:
    """The page range the journal prints in its citation of the article on the first page, when it spans the file's
    `pages` (one page more or less: a blank last page); None otherwise (the citation copied from another article:
    Vol 7's "(73-96)" on a 22-page file)."""
    for ln in page.lines:
        if (m := _CITATION.search(ln.text)) and abs(int(m.group(2)) - int(m.group(1)) + 1 - pages) <= 1:
            return int(m.group(1)), int(m.group(2))
    return None


def _append(title: str, line: str) -> str:
    """A title line after the title so far; a line printed twice, or a shadow copy that repeats the end of the title
    with other line breaks, adds only its new words."""
    if not title:
        return line
    if re.search(r"\d[–-]$", title) and line[:1].isdigit():
        return title + line                           # "1992–" / "2010"
    ft, fl = f" {_fold(title)} ", f" {_fold(line)} "
    if fl.strip() and ft.endswith(fl):
        return title                                  # (the line just read, drawn again)
    words = line.split()
    for n in range(len(words) - 1, 1, -1):            # the longest start of the line that ends the title
        head = _fold(" ".join(words[:n]))
        if len(head) >= 8 and ft.endswith(f" {head} "):
            return f"{title} {' '.join(words[n:])}"
    return f"{title}\n{line}"


def read_first_page(page: Page) -> tuple[str | None, list[str]]:
    """(title, authors) of an article's first page; (None, []) when no title stands out in large type."""
    content = [c for c in (_clean(ln) for ln in page.lines if not _furniture(ln, page)) if c.text]
    top = [ln for ln in content if ln.y < _TOP * page.height and len(ln.text) < _BODY_LINE]
    if not top:
        return None, []
    size = max(ln.size for ln in top)
    if size < 1.15 * FP.body_size(page) and size < 14:
        return None, []
    k = next(i for i, ln in enumerate(content) if ln in top and ln.size >= size - 0.6)
    title, lines, bold, prev = "", 0, content[k].bold, None
    while k < len(content) and lines < _MAX_TITLE_LINES:
        ln = content[k]
        if _END.match(ln.text) or ln.size < _TITLE_SHARE * size or len(ln.text) >= _BODY_LINE \
                or title and (_byline_start(ln.text) or ln.bold and not bold
                              or ln.y - prev.y > _GAP * max(ln.size, prev.size)):
            break                     # (the later layouts set the byline in bold; the early ones after a wide gap)
        longer = _append(title, ln.text)
        title, lines, k, prev = longer, lines + (longer != title), k + 1, ln      # (a line drawn twice counts once)
    byline: list[Line] = []
    while k < len(content) and len(byline) < _MAX_BYLINE_LINES:
        ln = content[k]
        if _END.match(ln.text) or len(ln.text) >= _BODY_LINE:
            break
        byline.append(ln)
        k += 1
    if any(0 < b.y - a.y < min(a.size, b.size) and abs(a.size - b.size) > 0.5 for a, b in zip(byline, byline[1:])):
        byline = [ln for ln in byline if abs(ln.size - byline[0].size) <= 0.5]    # a byline typeset twice: one copy
    return FP.join_title(title.split("\n"), strip_marks=False), byline_authors(byline)
