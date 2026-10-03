# engine/timarit/toc.py
"""Table-of-contents text → TocEntry list (spec §5.1).

Two layouts cover the archive. page_first: each entry starts with its page number
(TL issues since 2003, Lögmannablaðið). page_last: each entry ends with its page number,
often after dotted leaders (TL annual volumes, Lögbrú, Lögfræðingur). toc_style() says
which one a source uses; find_toc_pages() says which pages of a PDF hold the TOC.

The text layer breaks entries over several lines, and the number is often alone on
its own line.
- page_first cuts the text at its page marks and keeps the lines of each stretch apart.
  Only an increasing run of marks counts, the running footer and the colophon are cut off,
  and a TOC whose numbers are detached from its titles gives [] (see _parse_page_first).
- page_last works on a buffer of "pending" lines: a line that ends in a page number (or is
  a page number) closes the entry, and the pending lines that continue into it become its
  title. A pending line that does not continue (a description such as
  "Um starfsemi … 1984-1985", or a running header) is dropped.

Authors are split from titles by "… eftir Nafn", by "Nafn: Titill", by author lines above the
title, by "Nafn, Titill", or by a patronymic surname followed by the title
("Elín Sif Kjartansdóttir Frá ritstjóra"). An 'eftir' name is in the accusative/dative and is
lemmatised to the nominative with BÍN word by word.

Task 22b (the pilot audit): every title leaves parse_toc through clean_title (OCR'd dotted leaders such as
"…eeee0eeeaeen…", page ranges "65— 86"); a page range counts by its first page; "Sami:" is the author above; a
credit before the title ("Ritdómur eftir X:", "Reviewed by X:") names the author and makes the entry a review; a page
number that goes back in a page-last TOC is unknown (None), so analyze locates the entry by its title.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from engine.processors.lemmatizer import _bin
from engine.timarit.authors import _SPLIT as _NAME_SEP
from engine.timarit.authors import clean_author, looks_like_person, split_authors

_UP = "A-ZÁÉÍÓÚÝÞÆÖ"

# --- page numbers -------------------------------------------------------------------------------
# The anchor is the brief's, unchanged: a 1–4 digit number at the end after dotted leaders or space.
_PAGE_LAST = re.compile(r"^(?P<body>.+?)(?:\s*\.{2,}\s*|\s+)(?P<page>\d{1,4})\s*$")
_PAGE_ONLY = re.compile(r"^[\s.…]*(?P<page>\d{1,4})\s*$")  # "5" or "........ 70": the entry text is on the lines above
# A postal code before a town ("108 Reykjavík" in a footer, a colophon or an ad) is not a page number.
_POSTAL = "(?:Reykjavík|Kópavogur|Hafnarfjörður|Garðabær|Akureyri)"
_PAGE_FIRST_SPLIT = re.compile(rf"(?:(?<=\s)|^)(\d{{1,4}})\s+(?!{_POSTAL}\b)(?=[{_UP}„])")
_TRAIL_LEADER = re.compile(r"(?:\s*\.){2,}\s*$|\s+\.\s*$|\s*…+\s*$")  # ". . . ." / "......" / " ." at the end
_LEADER_ONLY = re.compile(r"^[.\s…]*$")
# A page range at the end of a line ("… 12— 15", "87—103", "65— 86" alone, "… 65—" with the end on the next line):
# the article starts on the first page. Years ("1977-2008") are never pages (see _plausible).
_PAGE_RANGE = re.compile(r"(?<!\S)(\d{1,4})\s*[—–-]\s*(\d{1,4})?\s*$")

# --- OCR'd leaders --------------------------------------------------------------------------------
# The OCR reads a dotted leader as dots mixed with these characters ("…eeee0eeeaeen…", "0000vvrvesnnerrsrerrrsrss").
_JUNK_CHARS = frozenset("eoanrszvcuwmitl0.,:;·…|()")
_LONG_LEADER = re.compile(r"\s*(?:\.\s*){5,}")          # no title has five dots in a row
_SHORT_LEADER_TAIL = re.compile(r"\s*(?:\.{2,}|…)[\s\d.—–\-]*$")   # "daga .. 65—", "Handbók..."
_TITLE_PAGE_TAIL = re.compile(r"\s+(\d{1,4})\s*[—–-]\s*(?:\d{1,4})?\s*$")
_FOOTNOTE_MARK = re.compile(r"\s*\*+$")

# --- headings and furniture ---------------------------------------------------------------------
_HEFTI = re.compile(r"^\s*(\d)\.\s*hefti\b", re.I)
_SECTION = re.compile(
    r"^(Fræðigreinar|Ritdómar|Ritstjórnargrein(?:ar)?|Viðtal|Viðtöl|Fréttir|Dómareifanir|Annað efni|Greinar|Erindi)\s*$",
    re.I,
)
_NOISE = re.compile(r"^(?:efnisyfirlit|grein|bls\.?)\s*$", re.I)  # column heads and the TOC title itself
_END = re.compile(r"^(?:höfundaskrá|atriðisorðaskrá)\s*$", re.I)  # back-matter indexes list "Nafn: Titill 7" again
# Where a page_first TOC ends and the running footer begins: "1. hefti • 62. árgangur / vor 2012 /
# Lögfræðingafélag Íslands • Álftamýri 9 • 108 Reykjavík". Searched from the first entry on, because
# 2019+ covers print "4. hefti • 71. árgangur" above the entries.
_FOOTER_START = re.compile(r"\d{1,2}\.\s*hefti\b|Lögfræðingafélag Íslands\s*[•·]|Útgefandi:", re.I)
_ISSN = re.compile(r"ISSN\s*\d{4}\s*[-–]\s*\d{3}[\dXx]")  # ends the colophon that some journals print before the entries
_ROLE_WORDS = ("ritstjóri", "framkvæmdastjóri", "ráðgjafarráð", "útgefandi", "ábyrgðarmaður", "ritnefnd")

# --- authors ------------------------------------------------------------------------------------
# A name after "eftir" must start like a name; "l" before "n" is the OCR's reading of "I" (lngibjörgu).
_EFTIR = re.compile(rf"\s+eftir\s+(?P<name>(?:[{_UP}]|l(?=n))[^\d]{{3,80}}?)\s*$")
_TITLE_START = re.compile(rf"[{_UP}„]")
_MONONYM = re.compile(rf"^[{_UP}][a-záéíóúýðþæö]+$")
_FEMALE_SURNAME = re.compile(r"d[óo]tt(?:ir|ur)$")
_MALE_SURNAME = re.compile(r"(?:s[oe]n|syni|sonar)$")
_DECLINED_SURNAME = re.compile(r"(?:dóttur|syni|sonar)$")  # dative/genitive patronymics; -son/-dóttir are nominative
_NAME_HLUTI = ("ism", "föð", "ætt")  # BÍN: given name, patronymic, family name
_LIST_END = re.compile(r"\s+(?:og|&)$", re.I)  # an author list that goes on on the next line
_LIST_START = re.compile(r"^(?:og|&)\s+", re.I)  # … or that began on the line above
_LONE_SURNAME = re.compile(rf"^[{_UP}]\S*(?:son|sen|dóttir)[:,]?$", re.I)
# The rest of a name that wrapped inside an author list: "EYRÚN INGADÓTTIR OG VÍÐIR" / "SMÁRI PETERSEN"
_NAME_TAIL = re.compile(rf"^(?:[{_UP}]\S*\s+)?[{_UP}]\S*(?:son|sen|dóttir)[:,]?$", re.I)
_LIST_CONTINUES = re.compile(r"(?:\bog|&|,)\s*$", re.I)
# A credit before the title: "Ritdómur eftir Margréti Heinreksdóttur: Mál …", "Reviewed by Rachael Lorna Johnstone: …"
# (the colon may be missing when the title is on the next line). Such an entry is a review.
_LEAD_CREDIT = re.compile(r"^(?:(?P<review>Ritdómur|Bókardómur|Reviewed|Review)|Grein|Viðtal)\s+(?P<prep>eftir|by)\s+")
_SAME_AUTHOR = re.compile(r"^(?:Sami|Sama|Sömu)\s*:\s*", re.I)   # "Sami: Erlendar bækur" — by the author of the entry above

# An article's own outline: numbered section headings ("1. Inngangur", "2.1. Almennt", "3.2 Fræðikenningar") or a
# section number alone on its line ("2.1"). Not "1. hefti", "42. ÁRGANGUR", "3. tbl." nor a date ("1. júlí 1992").
_OUTLINE_HEADING = re.compile(
    r"^\d{1,2}\.(?:\d{1,2}\.?)*\s+(?!(?:hefti|tbl|tölubl|árg|jan|feb|mar|apr|maí|jún|júl|ág|sep|okt|nóv|des)\w*)[^\W\d]"
    r"|^\d{1,2}(?:\.\d{1,2})+\.?$",
    re.I,
)
_MIN_TOC_NUMBERS = 3  # page numbers a TOC page must show (three-article covers exist)
_MAX_ENTRY_CHARS = 400  # page_first: a longer stretch after the first mark is a header and colophon, not an entry
_MAX_STRAY_MARKS = 0.2  # page_first: share of marks allowed to break the increasing run before the TOC is given up
_STYLE = {"timarit_logfraedinga": "page_first", "logmannabladid": "page_first"}
_KINDS = ("issue", "volume")


@dataclass
class TocEntry:
    title: str
    authors: list[str]
    page_start: int | None
    section: str | None = None
    issue: str | None = None


def toc_style(source: str, kind: str = "issue") -> str:
    """Layout of a source's TOC. `kind` is the file's granularity (naming.FileIdentity: "issue" or "volume").

    Tímarit lögfræðinga issues print "page / Author: Title" (page_first), but its annual volumes print
    "Title ..... page" like most journals (page_last). Lögmannablaðið prints "page / NAME / title".
    """
    if kind not in _KINDS:
        raise ValueError(f"unknown file kind: {kind!r}")
    if source == "timarit_logfraedinga" and kind == "volume":
        return "page_last"
    return _STYLE.get(source, "page_last")


def _plausible(page: int) -> bool:
    """A TOC page number. A year (1900–2099) is the date line or a date in a title, never a page here."""
    return 0 < page and not 1900 <= page <= 2099


# --- titles ---------------------------------------------------------------------------------------

def _bin_knows(word: str) -> bool:
    return bool(_bin.lookup(word)[1] or _bin.lookup(word.lower())[1])


def _junk_token(tok: str) -> tuple[bool, bool]:
    """(junk, strong) for one token: could it be what the OCR made of a dotted leader? Strong junk ("eee.", "00e0",
    "ee0eeeaeen", "...") is never a word. Weak junk is any other token of the same letters ("er", "rss",
    "Constitution"): it goes only after strong junk (see _strip_junk_tail)."""
    low = tok.lower()
    if not low or any(c not in _JUNK_CHARS for c in low):
        return False, False
    core = low.strip(".,:;·…|()")
    if not core:
        return True, True
    if core.isdigit():
        return set(core) == {"0"}, set(core) == {"0"}
    strong = "0" in core or "." in core or bool(re.search(r"(.)\1\1", core)) or (len(core) >= 4 and len(set(core)) <= 2)
    return True, strong


def _strip_junk_tail(s: str) -> str:
    """Drop a tail of leader junk ("… þjóðaréttar eee.", "… Comparative Law eee"): the longest run of junk tokens at
    the end, cut at its first strong token, so a word before the leader stays ("Constitution eee..." → "Constitution")
    and a title that merely ends in a short word ("Hvað er") is left alone."""
    toks = s.split()
    k = len(toks)
    while k > 1 and _junk_token(toks[k - 1])[0]:
        k -= 1
    cut = next((i for i in range(k, len(toks)) if _junk_token(toks[i])[1]), None)
    return " ".join(toks[:cut]) if cut is not None and cut > 0 else s


def clean_title(title: str | None) -> str | None:
    """A registry title without what the TOC's layout and OCR glued to it: dotted leaders, including OCR'd ones
    ("… ...........eeeeeeeee00eeeeeeeeeee er", "eee."), a page reference (".. 65—"), a footnote star. Everything from
    a run of five dots on goes (no title has one); a shorter leader only when nothing but numbers follows it."""
    if title is None:
        return None
    s = " ".join(title.replace("\xad", "").split())
    if m := _LONG_LEADER.search(s):
        s = s[:m.start()]
    s = _SHORT_LEADER_TAIL.sub("", s)
    if (m := _TITLE_PAGE_TAIL.search(s)) and _plausible(int(m.group(1))) and int(m.group(1)) < 1800:
        s = s[:m.start()]
    s = _strip_junk_tail(s)
    s = _FOOTNOTE_MARK.sub("", s).strip(" ,;")
    return s.rstrip(" .") if s.endswith(" .") else s


# Section headings that a registry (a TOC line, a spreadsheet row) or a page prints where the title belongs, with the
# article type they stand for (None: says nothing about the type). Keys are lower case.
SECTION_LABELS: dict[str, str | None] = {
    "fræðabálkur": "Fræðigrein", "fræðigreinar": "Fræðigrein", "greinar": "Fræðigrein", "ritrýndar greinar": "Fræðigrein",
    "gestabálkur": None, "rekabálkur": None, "minningarbálkur": "Annað", "minning": "Annað", "in memoriam": "Annað",
    "leiðari": "Ritstjórnargrein", "ritstjórnargrein": "Ritstjórnargrein", "formannspistill": "Ritstjórnargrein",
    "pistill formanns": "Ritstjórnargrein", "á víð og dreif": None, "ritdómar": "Ritdómur", "ritdómur": "Ritdómur",
    "bókarfregn": "Ritdómur", "bókarfregnir": "Ritdómur", "bókabálkur": "Ritdómur", "dómareifanir": "Dómareifun", "fréttir": "Frétt",
    "viðtal": "Viðtal", "viðtöl": "Viðtal", "vefrit": None, "aðsent": None, "umfjöllun": None, "grein": None,
    "af vettvangi dómsmála": None, "af vettvangi": None,     # TL's column logo, OCR'd "Aí vettvangi" / "(lóinsinála"
}


# Front matter a TOC lists among the articles (Rannsóknir í félagsvísindum: "Höfundalisti … 7"): not an article (fix 7
# of Task 22b: dropped from the article list).
_FRONT_MATTER = frozenset({"höfundalisti", "höfundar efnis", "höfundar", "efnisyfirlit", "útgáfuupplýsingar",
                           "ritnefnd", "höfundar greina", "um höfunda"})


def is_front_matter(title: str | None) -> bool:
    return bool(title) and _label_key(title) in _FRONT_MATTER


def _label_key(s: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", s).split()).lower()


def is_label(s: str | None) -> bool:
    return bool(s) and _label_key(s) in SECTION_LABELS


def label_type(s: str | None) -> str | None:
    return SECTION_LABELS.get(_label_key(s)) if s else None


def split_label(title: str) -> tuple[str | None, str]:
    """(section label, the rest) of a title that starts with one: "GESTABÁLKUR Viðtal við …", "RITSTJÓRNARGREIN: Ný
    löggjöf …"; a title that is only a label gives (label, ""). The label counts only when a colon follows it or it is
    printed in capitals before a word that is not ("Fréttir af aðalfundi" is a title). The label comes back capitalised."""
    words = title.split()
    for k in (4, 3, 2, 1):
        if len(words) < k:
            continue
        head = " ".join(words[:k])
        if not is_label(head):
            continue
        rest = " ".join(words[k:])
        label = _label_key(head).capitalize()
        if not rest:
            return label, ""
        if head.endswith(":"):
            return label, rest
        if rest.startswith(":"):
            return label, rest[1:].strip()
        if head.isupper() and not words[k].isupper():
            return label, rest
    return None, title


def _toc_numbers(text: str) -> list[int]:
    """Page-number candidates on a page, in reading order: a line that is a number, ends in one, or starts
    with one before a word. Values that cannot be TOC page numbers are dropped:

    - years (see _plausible);
    - the end of a phone number ("Sími: 568 9095") and a postal code before a town ("112 Reykjavík");
    - a number alone on a line next to another number alone on a line: a table of figures, or a cover
      that lists page numbers apart from their titles;
    - a value one off from its neighbour in reading order: footnote markers (1, 2, 3 …), consecutive pages.
    """
    lines = [raw.strip() for raw in text.splitlines() if raw.strip()]
    found = []  # (value, alone, line index)
    for idx, line in enumerate(lines):
        if re.fullmatch(r"\d{1,4}", line):
            found.append((int(line), True, idx))
        elif m := re.search(r"(?<!\d)\s(\d{1,4})$", line) or re.match(rf"^(\d{{1,4}})\s+(?!{_POSTAL}\b)\S", line):
            found.append((int(m.group(1)), False, idx))
    found = [f for f in found if _plausible(f[0])]
    alone = {idx for _, is_alone, idx in found if is_alone}
    values = [v for v, is_alone, idx in found if not (is_alone and (idx - 1 in alone or idx + 1 in alone))]
    return [v for k, v in enumerate(values)
            if not ((k > 0 and abs(values[k - 1] - v) == 1) or (k + 1 < len(values) and abs(values[k + 1] - v) == 1))]


def _longest_increasing(values: list[int]) -> list[int]:
    """Indices of a longest strictly increasing subsequence (the earliest one among equals)."""
    best: list[list[int]] = []
    for i, v in enumerate(values):
        prev = max((c for j, c in enumerate(best) if values[j] < v), key=len, default=[])
        best.append(prev + [i])
    return max(best, key=len, default=[])


def _is_outline(text: str) -> bool:
    """The page shows an article's own outline: at least three numbered section headings, and at least half as many
    as it has TOC-number candidates (Lögrétta prints one under the first article's title: "1. Inngangur\\t" / "10",
    "2. Vanefndir samkvæmt lánasamningum\\t" / "10", "2.1. Almennt\\t" / "10" …). An issue TOC lists titles and authors,
    not numbered headings."""
    headings = sum(1 for line in text.splitlines() if _OUTLINE_HEADING.match(line.strip()))
    return headings >= _MIN_TOC_NUMBERS and 2 * headings >= len(_toc_numbers(text))


def find_toc_pages(page_texts: list[str], max_scan: int = 6) -> list[int]:
    """The TOC pages among the first `max_scan` pages: the first page that shows at least three page
    numbers in increasing order (see _toc_numbers), plus the pages directly after it that do too.

    The run ends at the first page that does not qualify, so what follows the TOC is not taken: ads,
    articles with footnotes, an article's own outline (Lögbrú prints one on the pages after the TOC).
    A TOC split by an advertisement page is read only up to the ad.

    An article's own outline (_is_outline) is never a TOC page, and the scan stops there: the article has begun, and
    its body pages (footnotes ending in page references) must not be taken for a TOC either. Lögrétta issue PDFs
    start with their first article, so they get [] and the caller falls back to Claude / review.
    """
    out: list[int] = []
    for i, t in enumerate(page_texts[:max_scan]):
        if _is_outline(t):
            break
        if len(_longest_increasing(_toc_numbers(t))) >= _MIN_TOC_NUMBERS:
            out.append(i)
        elif out:
            break
    return out


# --- names --------------------------------------------------------------------------------------

def _lemma(token: str, gender: str | None) -> str:
    entries = _bin.lookup(token)[1]
    names = [e for e in entries if e.hluti in _NAME_HLUTI] or list(entries)
    if gender:
        names = [e for e in names if e.ofl == gender] or names
    if not names:
        return token
    # A name that reads the same in the nominative, accusative and dative singular is already
    # nominative (Þór, though BÍN lists the lemma Þórr first); the rare variant lemma Sigurð
    # lacks the plain dative, so Sigurð still goes to Sigurður.
    if {"NFET", "ÞFET", "ÞGFET"} <= {e.mark for e in names if e.ord == token}:
        return token
    lemma = names[0].ord
    return lemma[:1].upper() + lemma[1:]


def _nominative(name: str) -> str:
    """'Gauk Jörundsson' → 'Gaukur Jörundsson'; word by word through BÍN.

    The last token is left alone unless it is a declined patronymic (-dóttur, -syni): family names
    such as Hafstein would otherwise become 'Hafsteinn'. The surname's gender (-son / -dóttir) chooses
    between homographic lemmas (Arnljót is both a woman's name and the accusative of Arnljótur).
    Initials and tokens BÍN does not know are kept as printed.
    """
    tokens = name.split()
    if not tokens:
        return name
    last = tokens[-1]
    gender = "kvk" if _FEMALE_SURNAME.search(last) else "kk" if _MALE_SURNAME.search(last) else None
    out = []
    for i, tok in enumerate(tokens):
        if len(tok) < 3 or not tok[0].isupper() or (i == len(tokens) - 1 and not _DECLINED_SURNAME.search(tok)):
            out.append(tok)
        else:
            out.append(_lemma(tok, gender))
    return " ".join(out)


def _person_names(s: str) -> list[str]:
    """`s` as a list of person names, or [] unless *all* of it is names.

    split_authors() drops the parts that are not names, which is right for an author field but would
    let a title such as "Deilur Íslendinga og Dana" through as one author. Here every non-empty part
    must survive.
    """
    s = _LIST_START.sub("", _LIST_END.sub("", s.strip(" ,:;")))  # "EYRÚN INGADÓTTIR OG" / "OG KATRÍN SMÁRI ÓLAFSDÓTTIR"
    if not s:
        return []
    parts = [p for p in _NAME_SEP.split(s) if p.strip() and clean_author(p)[0]]  # a bare title ("dósent") is no part
    names = split_authors(s)
    if names and len(names) == len(parts) and all(looks_like_person(n, strict=False) for n in names) and not _label_word_in(names[0]):
        return names
    return []


def _label_word_in(name: str) -> bool:
    """The name has a token before its last that BÍN knows only as an ordinary word ("Málþing Fkl", "Úrskurðarnefnd
    Lögmanna", "Fræðslufundur Með Jólaívafi", "Saga Annars Stofnanda"): a label or a heading, not a person.
    The last token is exempt, because a surname may be stored only as a compound noun (Líndal = lín-dalur,
    Ámason). Applied to the first name of a list only: a later name may be an OCR error ("Dóm Guðmundsdóttir")."""
    return any(len(t) >= 3 and (e := _bin.lookup(t)[1]) and all(x.hluti == "alm" for x in e) for t in name.split()[:-1])


def _mononym(s: str) -> list[str]:
    """A lone capitalised word before a colon is an author only if BÍN knows it as a name ("Aðalsteinn:"),
    not when it is a label ("Viðtal:", "Ritdómur:")."""
    if _MONONYM.match(s) and any(e.hluti in _NAME_HLUTI for e in _bin.lookup(s)[1]):
        return [s]
    return []


def _join_wrapped_surnames(lines: list[str]) -> list[str]:
    """'HRAFNHILDUR SÆBERG' / 'ÞORSTEINSDÓTTIR': a surname that wrapped onto a line of its own is put back."""
    out: list[str] = []
    for line in lines:
        if out and _LONE_SURNAME.match(line) and _person_names(out[-1]):
            out[-1] = f"{out[-1]} {line}"
        elif out and _NAME_TAIL.match(line) and not _person_names(out[-1]) and _person_names(f"{out[-1]} {line}"):
            out[-1] = f"{out[-1]} {line}"    # "GUÐRÚN OLSEN OG BERGÞÓR" / "BERGSSON": a name wrapped inside the list
        else:
            out.append(line)
    return out


_COMPOUND_DASH_NEXT = re.compile(r"^(?:og|eða|né|and|or)\b", re.I)


def _join_lines(lines: list[str]) -> str:
    """The entry's lines as one string; a word hyphenated at a line end is joined ("þjóð-" / "anna"), but not a
    compound dash before "og" ("lífs- og líkamstjón") or a hyphen after a capital ("EES-" / "reglur")."""
    out = ""
    for ln in lines:
        if out.endswith("-") and out[-2:-1].islower() and ln[:1].islower() and not _COMPOUND_DASH_NEXT.match(ln):
            out = out[:-1] + ln
        else:
            out = f"{out} {ln}" if out else ln
    return out.strip()


_EN_FUNCTION_WORDS = frozenset({"The", "A", "An", "On", "In", "Of", "To", "And", "For", "From", "Towards"})


def _plain_word(tok: str) -> bool:
    """A capitalised token that starts a title rather than ends a name: an English function word, or a word BÍN
    knows only as an ordinary word."""
    return tok in _EN_FUNCTION_WORDS or bool((e := _bin.lookup(tok)[1]) and all(x.hluti == "alm" for x in e))


def _lead_credit(body: str) -> tuple[list[str], str, bool] | None:
    """(names, title, is_review) of "Ritdómur eftir Margréti Heinreksdóttur: Mál …" / "Reviewed by X: Title" /
    "Reviewed by X The Yearbook …" (no colon: the longest run of 2–4 tokens that reads as names, then a capital)."""
    m = _LEAD_CREDIT.match(body)
    if not m:
        return None
    rest = body[m.end():]
    head, colon, title = rest.partition(":")
    names = _person_names(head) if colon and len(head) <= 70 else []
    if not names:
        toks = rest.split()
        for k in (4, 3, 2):
            if len(toks) > k and _TITLE_START.match(toks[k]) and not _plain_word(toks[k - 1]) \
                    and (names := _person_names(" ".join(toks[:k]))):
                title = " ".join(toks[k:])
                break
    if not names or not title.strip():
        return None
    if m.group("prep") == "eftir":
        names = [_nominative(n) for n in names]
    return names, title.strip(), bool(m.group("review"))


def _split_author_title(lines: list[str]) -> tuple[list[str], str]:
    """Split an entry's text lines (page number and leaders already removed) into authors and title.

    Tried in this order; the first that applies wins:
      1. '… eftir Nafn' at the end → Nafn in the nominative, the rest is the title.
      2. 'Nafn: Titill' over the joined lines, so an author list that wraps over lines is whole (the name
         may be a lone given name known to BÍN).
      3. The first line is names only, e.g. 'Pétur Fannar Gíslason,' or 'Elín Sif Kjartansdóttir', and
         the next line starts a title (not lowercase). A list continues onto the next line after "og" or
         a comma, or when that line starts with "og" ('EYRÚN INGADÓTTIR OG' / 'KATRÍN SMÁRI ÓLAFSDÓTTIR' / title).
         A line in capitals followed by another one is a heading, not an author line.
      4. 'Nafn, Titill' (one or more comma-separated names).
      5. Names without punctuation: the shortest run of 2–4 name tokens that ends in a patronymic
         (-son/-sen/-dóttir), when an upper-case word starts the rest ('Elín Sif Kjartansdóttir Frá ritstjóra').
         Family-name authors without punctuation are not recognised: that would take titles for names.
    No author found → ([], whole text).
    """
    lines = _join_wrapped_surnames(lines)
    body = _join_lines(lines)
    if (m := _EFTIR.search(body)):
        raw = re.sub(r"^l(?=n)", "I", m.group("name"))
        names = _person_names(raw)
        if names:
            return [_nominative(n) for n in names], body[:m.start()].strip(" .,:")
    if ":" in body:
        head, title = (x.strip() for x in body.split(":", 1))
        if title and (names := _person_names(head) or _mononym(head)):
            return names, title
    authors: list[str] = []
    used = 0  # author lines above the title
    for line in lines[:-1]:
        following = lines[used + 1]
        goes_on = _LIST_CONTINUES.search(line) or _LIST_START.match(following)
        if (following[:1].islower() and not _LIST_START.match(following)) or not (names := _person_names(line)):
            break
        if line.isupper() and following.isupper() and not goes_on:  # a heading in capitals over two lines
            break
        authors += names
        used += 1
        if not goes_on:
            break
    if authors:
        return authors, _join_lines(lines[used:])
    if "," in body:
        parts = body.split(",")
        for used, part in enumerate(parts[:-1], start=1):
            if not (names := _person_names(part)):
                used -= 1
                break
            authors += names
        if authors:
            return authors, ",".join(parts[used:]).strip()
    words = body.split()
    for k in range(2, min(4, len(words) - 1) + 1):
        if looks_like_person(" ".join(words[:k]), strict=True) and _TITLE_START.match(words[k]):
            return split_authors(" ".join(words[:k])), " ".join(words[k:])
    return [], body


# --- layouts ------------------------------------------------------------------------------------

def _prepare(text: str) -> list[str]:
    """Non-empty lines with whitespace collapsed; a soft hyphen at a line end joins the word."""
    text = re.sub(r"-?\xad\s*", "", text)
    lines = (" ".join(raw.split()) for raw in text.splitlines())
    return [ln for ln in lines if ln and not _LEADER_ONLY.match(ln) and not _junk_line(ln)]


def _junk_line(line: str) -> bool:
    """A line that is only what the OCR made of a dotted leader ("eeen eeen", "00...", "rr", "er"): every token is
    leader junk, and one is strong junk or the line is three letters at most."""
    marks = [_junk_token(t) for t in line.split()]
    return bool(marks) and all(j for j, _ in marks) and (any(s for _, s in marks) or len(line.replace(" ", "")) <= 3)


def _is_role(line: str) -> bool:
    return any(line.lower().startswith(r) for r in _ROLE_WORDS)


def _page_marks(text: str) -> list[re.Match]:
    return [m for m in _PAGE_FIRST_SPLIT.finditer(text) if _plausible(int(m.group(1)))]


def _stretch(text: str, marks: list[re.Match], k: int) -> str:
    """The text that follows mark k, up to the next mark."""
    return text[marks[k].end(): marks[k + 1].start() if k + 1 < len(marks) else len(text)]


def _parse_page_first(lines: list[str]) -> list[TocEntry]:
    """Entries are the stretches of text that follow a page mark: a number that stands before a capital
    letter, alone on its line or in front of the title. Lines are kept apart (an author line above its title).

    Things that are not entries, and how they are kept out:
    - the running footer (TL covers): the text is cut where the footer starts;
    - numbers that merely look like marks ("nr. 3 Hæstaréttar", "108 Reykjavík", "0800 ISSN …"): only the
      longest increasing run of marks is kept;
    - a colophon before the entries (Lögmannablaðið): dropped up to its ISSN if entries follow it; and a
      leading mark whose stretch up to the next kept mark is longer than an entry can be (the page's own
      number in the running header "2 LÖGMANNABLAÐIÐ TBL 01/18 EFNISYFIRLIT", then the colophon) is skipped;
    - a TOC whose numbers are detached from its titles (TL 2005-2008 covers, where the text layer lists
      the numbers apart from the titles): more than a fifth of the marks fall out of the increasing run,
      so nothing is returned and the caller falls back to leitir.is / the LLM instead of pairing numbers
      with the wrong titles. Fewer than two marks is not a TOC either (a lone "108 Reykjavík").
    """
    text = "\n".join(lines)
    issn_ends = [m.end() for m in _ISSN.finditer(text)]
    if issn_ends and len(_page_marks(text[issn_ends[-1]:])) >= _MIN_TOC_NUMBERS:
        text = text[issn_ends[-1]:]
    marks = _page_marks(text)
    if not marks:
        return []
    if footer := _FOOTER_START.search(text, marks[0].start()):
        text = text[:footer.start()]
        marks = [m for m in marks if m.start() < footer.start()]
    kept = [marks[k] for k in _longest_increasing([int(m.group(1)) for m in marks])]
    while kept and len(_stretch(text, kept, 0)) > _MAX_ENTRY_CHARS:
        kept = kept[1:]
    candidates = [m for m in marks if kept and m.start() >= kept[0].start()]
    if len(kept) < 2 or len(candidates) - len(kept) > len(candidates) * _MAX_STRAY_MARKS:
        return []
    entries = []
    for k, m in enumerate(kept):
        body = [ln.strip() for ln in _stretch(text, kept, k).splitlines() if ln.strip()]
        if not body or _is_role(body[0]):
            continue
        if entry := _entry(body, int(m.group(1)), None, None, entries):
            entries.append(entry)
    return entries


def _entry(lines: list[str], page: int | None, section: str | None, issue: str | None,
           before: list[TocEntry]) -> TocEntry | None:
    """One TOC entry from its lines: a credit before the title ("Ritdómur eftir X: …") makes it a review by X;
    "Sami: …" is by the author of the entry before it."""
    if credit := _lead_credit(_join_lines(_join_wrapped_surnames(lines))):
        authors, title, review = credit
        section = "Ritdómar" if review else section
    else:
        authors, title = _split_author_title(lines)
    if not authors and (m := _SAME_AUTHOR.match(title)) and before:
        authors, title = list(before[-1].authors), title[m.end():]
    return TocEntry(title, authors, page, section, issue) if title else None


def _closing(line: str) -> tuple[str, int, bool] | None:
    """(entry text on this line, page, bare) if the line ends in a page number, else None.
    `bare`: text followed directly by the number, with no leaders and nothing but the number after it."""
    if (r := _PAGE_RANGE.search(line)) and _plausible(int(r.group(1))) and (
            r.group(2) is None or (_plausible(int(r.group(2))) and int(r.group(2)) >= int(r.group(1)))):
        line = line[:r.start()] + r.group(1)          # "… 12— 15", "65— 86", "… 65—": the first page
    if m := _PAGE_ONLY.match(line):
        text, bare = "", False
    elif m := _PAGE_LAST.match(line):
        text = _TRAIL_LEADER.sub("", m.group("body")).strip()
        bare = "." not in line[m.end("body"):m.start("page")] and not _TRAIL_LEADER.search(m.group("body"))
    else:
        return None
    page = int(m.group("page"))
    return (text, page, bare) if _plausible(page) else None


def _continues(prev: str, nxt: str) -> bool:
    """Is `nxt` the rest of the entry that `prev` begins? True when `nxt` starts in lower case
    ('eftir Nafn', a wrapped title) or `prev` stops mid-phrase: on a letter or , : ; - (an author line
    above its title, a title wrapped after a word). A line ending in a digit or '?' or a dash is complete."""
    return nxt[:1].islower() or prev[-1:].isalpha() or prev[-1:] in ",:;-"


def _chain(pending: list[str], text: str) -> list[str]:
    """The entry's lines: `text` plus the pending lines above it that lead into it. Stops at an author
    line above a title: names only, ending in a patronymic. (Shape alone is not enough here: the last
    line of a wrapped title such as "Future Prospects" has the shape of a name.)"""
    lines = [text] if text else []
    for prev in reversed(pending):
        if lines and not _continues(prev, lines[0]):
            break
        lines.insert(0, prev)
        names = _person_names(prev)
        if len(lines) > 1 and names and all(looks_like_person(n, strict=True) for n in names):
            break
    return lines


def _parse_page_last(lines: list[str]) -> list[TocEntry]:
    entries: list[TocEntry] = []
    section: str | None = None
    issue: str | None = None
    pending: list[str] = []
    last_page: int | None = None
    for idx, line in enumerate(lines):
        if _END.match(line):
            break
        if m := _HEFTI.match(line):
            issue, pending = m.group(1), []
            continue
        if m := _SECTION.match(line):
            section, pending = m.group(1), []
            continue
        if _NOISE.match(line) or _is_role(line):
            pending = []
            continue
        closing = _closing(line) or _garbled_closing(line, pending)
        if closing and closing[2]:
            # Text + number without leaders is ambiguous ("Hæstaréttardómur 1983, 1718", the footer
            # "Lögfræðingur 3"): accept it only if the page does not go back and no lower-case
            # continuation line follows.
            nxt = lines[idx + 1] if idx + 1 < len(lines) else ""
            if (last_page is not None and closing[1] < last_page) or nxt[:1].islower():
                closing = None
        if not closing:
            pending.append(_TRAIL_LEADER.sub("", line).strip())
            continue
        text, page, _ = closing
        if page is not None and last_page is not None and page < last_page:
            page = None                     # "2" for 27 after page 8: an unknown start, located by the title later
        entry = _entry(_chain(pending, text), page, section, issue, entries)
        pending = []
        if entry:
            entries.append(entry)
            last_page = page if page is not None else last_page
    return entries


_GARBLED_PAGE = re.compile(r"^(?=.*[Ðð0-9])[ÐðlIoO0-9|(]{1,4}$")
_OCR_DIGITS = str.maketrans("lIoO", "1100")


def _garbled_closing(line: str, pending: list[str]) -> tuple[str, int | None, bool] | None:
    """A page number the OCR misread ("Ð", "ð", "l5l", "Ð(") still closes the entry above it: with the number when
    the misreading maps back to digits ("l5l" → 151), else with an unknown page."""
    if not pending or line.isdigit() or not _GARBLED_PAGE.match(line):
        return None
    digits = line.translate(_OCR_DIGITS)
    return "", (int(digits) if digits.isdigit() and _plausible(int(digits)) else None), False


_LEADER_END = re.compile(r"(?:\.\s*){3,}\s*(?:\d{1,4}(?:\s*[—–-]\s*\d{0,4})?)?\s*$")


def style_of_text(text: str, style: str) -> str:
    """The configured style, unless the text shows the other layout plainly: three or more lines that end in a dotted
    leader (and a page number or range) are a page-last TOC, whatever the source's issues print today (Tímarit
    lögfræðinga 1951–1970 issues: "Bjarni Benediktsson: Ólafur Lárusson sjötugur ...... 12— 15")."""
    if style == "page_first" and sum(1 for ln in text.splitlines() if _LEADER_END.search(ln)) >= 3:
        return "page_last"
    return style


def parse_toc(text: str, *, style: str) -> list[TocEntry]:
    if style not in ("page_first", "page_last"):
        raise ValueError(f"unknown TOC style: {style!r}")
    lines = _prepare(text)
    entries = _parse_page_first(lines) if style == "page_first" else _parse_page_last(lines)
    for e in entries:
        e.title = clean_title(e.title) or e.title
    return [e for e in entries if e.title]
