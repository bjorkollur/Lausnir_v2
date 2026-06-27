"""
Derives Layer 3 outputs (markdown, urlausn) purely from DB columns.
No database access — only takes a Document and SourceConfig.
These outputs are always re-generatable; never treat them as source of truth.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.config.sources import SourceConfig
    from engine.database.models import Document

_MONTHS_IS = [
    "janúar", "febrúar", "mars", "apríl", "maí", "júní",
    "júlí", "ágúst", "september", "október", "nóvember", "desember",
]


def _date_str(dt) -> str:
    if not dt:
        return ""
    return f"{dt.day}. {_MONTHS_IS[dt.month - 1]} {dt.year}"


def _court_eignarfall(abbr: str) -> str:
    """Convert court abbreviation to eignarfall (genitive) for H1/H2 titles."""
    _MAP = {
        "Hrd.":            "Hæstaréttar",
        "Lrd.":            "Landsréttar",
        "Féld.":           "Félagsdóms",
        "Endurupptkd.":    "Endurupptökudóms",
        "Hrd. málsk.":     "Hæstaréttar",
        "Persónuvnd.":     "Persónuverndar",
        "UA":              "Umboðsmanns",
        "Hérd. Rvk.":      "Héraðsdóms Reykjavíkur",
        "Hérd. Reykn.":    "Héraðsdóms Reykjaness",
        "Hérd. Vestl.":    "Héraðsdóms Vesturlands",
        "Hérd. Vestfj.":   "Héraðsdóms Vestfjarða",
        "Hérd. Norðvest.": "Héraðsdóms Norðurlands vestra",
        "Hérd. Norðeyst.": "Héraðsdóms Norðurlands eystra",
        "Hérd. Austl.":    "Héraðsdóms Austurlands",
        "Hérd. Suðl.":     "Héraðsdóms Suðurlands",
    }
    return _MAP.get(abbr, abbr)


# ─── Niðurstöðumerkingar ─────────────────────────────────────────────────────
# HTML-athugasemd sem er ósýnileg í rendered markdown en auðfundin með leit.
# Sett inn rétt áður en niðurstöðukaflinn byrjar í body_text / lower_body_text.

VERDICT_MARKER = "<!-- VERDICT -->"
LOWER_COURT_VERDICT_MARKER = "<!-- LOWER COURT VERDICT -->"

def _spaced(word: str) -> str:
    """Pattern matching a word with optional whitespace between each character."""
    return r'\s*'.join(re.escape(c) for c in word)


def _spaced_eth(word: str) -> str:
    """Like _spaced but maps ð/Ð → [ðÐdD] for OCR robustness (old PDFs use plain D)."""
    return r'\s*'.join(
        r'[ðÐdD]' if c in 'ðÐ' else re.escape(c)
        for c in word
    )


# Matches "Dómsorð"/"Úrskurðarorð" when it appears mid-line so it can be moved
# to its own line before _DOMSORÐ_RE promotes it to ##.
# Two cases:
#   a) preceded by space/tab  (e.g. "...Hæstarétti. Dómsorð:\n\n")
#   b) preceded immediately by sentence-ending punctuation with no space
#      (e.g. "...kvað upp dóm þennan.Dómsorð:Stefndi…")
# The colon is REQUIRED (not optional) to avoid false-splitting sentence references
# like "...í dómsorðI greinir" where the inflected word follows a space.
_INLINE_DÓMSORÐ_RE = re.compile(
    r'(?<!\n)(?<!#)(?:[ \t]+|(?<=[.!?]))(?!#)(' + _spaced('Dómsorð') + r')(\s*:)',
    re.IGNORECASE,
)
_INLINE_ÚRSKURÐARORÐ_RE = re.compile(
    r'(?<!\n)(?<!#)(?:[ \t]+|(?<=[.!?]))(?!#)(' + _spaced('Úrskurðarorð') + r')(\s*:)',
    re.IGNORECASE,
)

# (?=[;:_.\s]|$) prevents promoting inflected forms like "Dómsorðar" or "Úrskurðarorðig"
# that may appear at line start in older richText docs (the inflection character is
# neither punctuation nor whitespace, so the lookahead fails).
# '_' is included so that the richText italic-colon artefact "Dómsorð_:_" is promoted
# here before _ITALIC_COLON_RE normalises "## Dómsorð_:_" → "## Dómsorð:".
_DOMSORÐ_RE = re.compile(
    r'^(?!#)' + _spaced('Dómsorð') + r'(\:?)(?=[;:_.\s]|$)',
    re.MULTILINE | re.IGNORECASE,
)
_ÚRSKURÐARORÐ_RE = re.compile(
    r'^(?!#)' + _spaced('Úrskurðarorð') + r'(\:?)(?=[;:_.\s]|$)',
    re.MULTILINE | re.IGNORECASE,
)
_ROMAN_H2_RE = re.compile(
    r'^(?!#)((?=[MDCLXVI])M{0,4}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3}))\.?\s*$',
    re.MULTILINE | re.IGNORECASE,
)

# Promotes bold Roman-numeral section headings to ## (Fix B).
# Handles two forms that arise from HTML <strong> tag serialisation:
#   **I. Málsmeðferð kærunefndar.**  → single <strong> element
#   **IV.****Niðurstaða.**            → two adjacent <strong> elements
# Group 1 = numeral+dot (e.g. "IV."), group 2 = title text.
_BOLD_ROMAN_HEADING_RE = re.compile(
    r'^\*\*([IVX]+\.)'
    r'(?:\*\*\*\*|\s+)'
    r'([^*\n]{1,80}?)'
    r'\.*\*\*\s*$',
    re.MULTILINE,
)


_LOWER_COURT_H1_RE = re.compile(
    r'^(?!#)((?:Úrskurður|Dómur)\s+(?:Landsréttar|Héraðsdóms)\b[^\n]*)',
    re.MULTILINE | re.IGNORECASE,
)


def _ensure_lower_court_h1(text: str) -> str:
    """Make Dómur/Úrskurður Landsréttar/Héraðsdóms lines H1 in lower body."""
    return _LOWER_COURT_H1_RE.sub(r'# \1', text)


_LOWER_SUBHEADINGS_RE = re.compile(
    r'^(?!#)('
    r'Niðurstaða[n]?'
    r'|Niðurstöður'
    r'|Málsatvik(?:\s+og\s+sönnunarfærsla)?'
    r'|Málavextir'
    r'|Atvik\s+málsins'
    r'|Málsmeðferð\s+og\s+dómkröfur\s+aðila'
    r'|Málsatvik\s+og\s+dómkröfur\s+aðila'
    r'|Dómkröfur\s+aðila'
    r'|Kröfur\s+aðila'
    r'|Málsástæður\s+og\s+lagarök(?:\s+\w+)*'
    r'|Helstu\s+málsástæður\s+og\s+lagarök(?:\s+\w+)*'
    r'|Helstu\s+ágreiningsefni\s+og\s+yfirlit\s+málsatvika'
    r'|Málsástæður\s+(?:stefnanda|stefndu?|aðila)(?:\s+og\s+lagarök)?'
    r'|Forsendur(?:\s+og\s+niðurstaða|\s+dómsins|\s+úrskurðarins)?'
    r'|Lagarök(?:\s+\w+)*'
    r'|Rökstuðningur(?:\s+\w+)*'
    r'|Sératkvæði'
    r')(\s*:?)$',
    re.MULTILINE | re.IGNORECASE,
)


def _ensure_lower_subheadings(text: str) -> str:
    """Convert known Icelandic court subheadings to ## in lower court text."""
    return _LOWER_SUBHEADINGS_RE.sub(r'## \1\2', text)


_ITALIC_COLON_RE = re.compile(r'^(## (?:Dómsorð|Úrskurðarorð))_:_', re.MULTILINE)


def _ensure_h2_headings(text: str) -> str:
    """Upgrade Dómsorð/Úrskurðarorð and standalone Roman numerals to H2.

    Does NOT promote **IV. Title.** bold headings — that step is source-specific
    and handled separately via _promote_bold_roman_headings() for HTML-scraped
    committee sources (h1_use_display_name=True).  Court sources (Hrd./Lrd./
    héraðsdómar) use this function unmodified.
    """
    # First, move any mid-line occurrences onto their own line so the ^-anchored
    # patterns below can find them (rare in older richText docs).
    text = _INLINE_DÓMSORÐ_RE.sub(r'\n\n\1\2', text)
    text = _INLINE_ÚRSKURÐARORÐ_RE.sub(r'\n\n\1\2', text)
    text = _DOMSORÐ_RE.sub(r'## Dómsorð\1', text)
    text = _ÚRSKURÐARORÐ_RE.sub(r'## Úrskurðarorð\1', text)
    text = _ROMAN_H2_RE.sub(r'## \1', text)
    # Normalize richText italic-colon artifact: "## Dómsorð_:_" → "## Dómsorð:"
    text = _ITALIC_COLON_RE.sub(r'\1:', text)
    return text


def _promote_bold_roman_headings(text: str) -> str:
    """Promote **IV. Title.** (and **IV.****Title.**) bold headings to ##.

    Only called for HTML-scraped committee/ministry sources (h1_use_display_name=True)
    where section headings arrive as bold <strong> paragraphs.  Must NOT be applied
    to court sources where **I. Name** patterns are party names, not section headings.
    """
    return _BOLD_ROMAN_HEADING_RE.sub(
        lambda m: f"## {m.group(1)} {m.group(2).strip()}",
        text,
    )

# #{0,6} leyfir að Dómsorð/Úrskurðarorð séu þegar orðin heading á hvaða stigi sem er.
# Eldri Hrd. PDF-ar nota oft ####/##### fyrir Dómsorð í neðra dómstigstexta.
# _spaced() variants catch lines where letters have spaces between them
# (e.g. "## Ú rskurðarorð:" from older PDF encodings).
# (?-i:[A-Z]) — case-sensitive uppercase lookahead (inside the IGNORECASE pattern)
# to allow merged "DÓMSORÐStefndi" headings (next char is uppercase) while
# rejecting inflected forms like "Dómsorðig" (next char is lowercase inflection).
# Also catches "Ályktunarorð"/"Ályktarorð" — verdict-section words in older formats.
_VERDICT_SECTION_PATTERNS = re.compile(
    r"^#{0,6}\s*(?:"
    + r"D\s*ó\s*m\s*(?:s\s*)?o\s*r\s*(?:[oO]\s*)?[ðÐdD]" + r"|"
    # Fuzzy Úrskurðarorð: (?:[rRðÐdDaA]\s*)* gleypur miðhlutann og nær yfir
    # allar þekktar stafsetningarvillur (vantar r/Ð, víxlað, D í stað Ð o.fl.)
    + r"[ÚúUu]\s*[rR]?\s*(?:[sS]\s*)?[kK]\s*[uU]\s*(?:[rRðÐdDaAsS]\s*)*[oO]\s*[rR]?\s*[ðÐdD]" + r"|"
    + _spaced_eth("Ályktunarorð") + r"|"
    + _spaced_eth("Ályktarorð")
    + r")(?:[;:.\s]|$|(?-i:[A-ZÁÐÉÍÓÚÝÞÆÖ]))",
    re.MULTILINE | re.IGNORECASE,
)

# Extended pattern for committee/ministry sources (h1_use_display_name=True).
# Adds Niðurstaða as a verdict-section marker — in committee rulings "IV. Niðurstaða"
# IS the operative conclusion, whereas in court documents it is a reasoning section
# that precedes the separate Dómsorð/Úrskurðarorð.  Requires #{1,6} prefix to avoid
# false matches on the common noun "niðurstaða" in running text.
_VERDICT_SECTION_PATTERNS_COMMITTEE = re.compile(
    _VERDICT_SECTION_PATTERNS.pattern
    + r"|^#{1,6}\s*(?:[IVX]+\.\s+)?N\s*i\s*[ðÐdD]\s*u\s*r\s*s\s*t\s*a\s*[ðÐdD]\s*a\s*n?"
    r"(?:[;:.\s]|$)",
    re.MULTILINE | re.IGNORECASE,
)

# Passar við "Úrskurður" (með eða án bila á milli stafa, með eða án tvípunkts)
# sem stendur ein og sér á línu — með eða án heading-merkis.
# Notað til að greina þegar PDF notar "Úrskurður" í stað "Úrskurðarorð" sem fyrirsögn.
_LATE_ÚRSKURÐUR_RE = re.compile(
    r'^#{0,2}\s*[ÚúUu]\s*[rR]\s*[sS]\s*[kK]\s*[uU]\s*[rR]\s*[ðÐdD]\s*[uU]\s*[rR]\s*[.:]?\s*$',
    re.MULTILINE | re.IGNORECASE,
)


def _promote_late_urskurdur(text: str) -> str:
    """Ef lower court texti hefur enga niðurstöðufyrirsögn en 'Úrskurður' kemur
    fyrir stakt á línu í seinni hluta textans, er hann hækkaður í '## Úrskurðarorð:'.

    Varð til vegna PDF-a þar sem niðurstöðukaflinn er merktur 'Úrskurður' en
    ætti að vera 'Úrskurðarorð' (dæmi: Hrd. 22/2014).
    """
    if _VERDICT_SECTION_PATTERNS.search(text):
        return text
    matches = list(_LATE_ÚRSKURÐUR_RE.finditer(text))
    if not matches:
        return text
    last = matches[-1]
    # Aðeins ef línan er í síðustu 10 línum (línutal, ekki stafafjöldi)
    if text[last.end():].count('\n') > 10:
        return text
    return text[:last.start()] + '## Úrskurðarorð:' + text[last.end():]


# Passar við "Dómur" (með eða án bila, með eða án ##, með eða án tvípunkts)
# sem stendur ein og sér á línu. Notað þegar PDF notar "Dómur" sem niðurstöðufyrirsögn.
_LATE_DÓMUR_RE = re.compile(
    r'^#{0,2}\s*D\s*[óÓoO]\s*m\s*u\s*r\s*:?\s*$',
    re.MULTILINE | re.IGNORECASE,
)


def _promote_late_domur(text: str) -> str:
    """Ef '## Dómur:' kemur fyrir í síðustu 10 línum lower court texta
    og engin niðurstöðufyrirsögn er þegar til staðar, breytist það í '## Dómsorð:'.

    Varð til vegna Landsréttar-dóma þar sem héraðsdómari notar 'Dómur:' sem
    niðurstöðufyrirsögn (dæmi: Lrd. 191/2023).
    """
    if _VERDICT_SECTION_PATTERNS.search(text):
        return text
    matches = list(_LATE_DÓMUR_RE.finditer(text))
    if not matches:
        return text
    last = matches[-1]
    # Aðeins ef línan er í síðustu 10 línum
    if text[last.end():].count('\n') > 10:
        return text
    return text[:last.start()] + '## Dómsorð:' + text[last.end():]


# Passar við "ákvörðun" stakt á línu — notað í eldri sakamálum þar sem dómari
# tekur formlega ákvörðun (t.d. ferðabann) í stað hefðbundinnar Dómsorð-fyrirsagnar.
_LATE_ÁKVÖRÐUN_RE = re.compile(
    r'^#{0,2}\s*[áÁaA]\s*k\s*v\s*[öÖoO]\s*r\s*[ðÐdD]\s*u\s*n\s*:?\s*$',
    re.MULTILINE | re.IGNORECASE,
)


def _promote_late_akvordun(text: str) -> str:
    """Ef 'ákvörðun' kemur fyrir stakt á línu í síðustu 10 línum lower court texta
    og engin niðurstöðufyrirsögn er þegar til staðar, breytist það í '## Dómsorð:'.

    Varð til vegna eldri Hæstaréttar-mála þar sem dómari tekur formlega ákvörðun
    (t.d. ferðabann) með 'ákvörðun' sem fyrirsögn (dæmi: Hrd. 123/2001).
    """
    if _VERDICT_SECTION_PATTERNS.search(text):
        return text
    matches = list(_LATE_ÁKVÖRÐUN_RE.finditer(text))
    if not matches:
        return text
    last = matches[-1]
    if text[last.end():].count('\n') > 10:
        return text
    return text[:last.start()] + '## Dómsorð:' + text[last.end():]


def inject_verdict_marker(
    text: str,
    marker: str = VERDICT_MARKER,
    *,
    pattern: "re.Pattern[str] | None" = None,
) -> str:
    """
    Finnur niðurstöðukafla í texta og setur <!-- NIÐURSTÖÐUR --> rétt á undan.
    Ef ekkert mynstur finnst er textinn skilinn óbreyttur.

    ``pattern`` defaults to ``_VERDICT_SECTION_PATTERNS`` (court sources).
    Pass ``_VERDICT_SECTION_PATTERNS_COMMITTEE`` for committee/ministry sources
    where Niðurstaða is the operative conclusion rather than a reasoning section.
    """
    pat = pattern if pattern is not None else _VERDICT_SECTION_PATTERNS
    match = pat.search(text)
    if not match:
        return text

    pos = match.start()

    # Farðu aftur að upphafi línunnar (til að setja merkinguna á eigin línu)
    line_start = text.rfind("\n", 0, pos)
    if line_start == -1:
        line_start = 0
    else:
        line_start += 1  # eftir \n

    # Settu inn marker með tveimur línubilum umhverfis
    before = text[:line_start].rstrip("\n")
    after = text[line_start:]
    return f"{before}\n\n{marker}\n{after}"


def _bold_outside_parens(name: str) -> str:
    """Bold non-parenthetical segments; leave (...) as plain text."""
    segments = re.split(r'(\([^)]*\))', name)
    result = []
    for seg in segments:
        if seg.startswith("("):
            result.append(seg)
        else:
            s = seg.strip()
            if s:
                result.append(f"**{s}**")
    return " ".join(result)


def _format_parties(parties_json: list | None, config: "SourceConfig") -> str:
    if not parties_json:
        return ""
    parts = []
    for p in parties_json:
        name = re.sub(r"^og\b\s*", "", (p.get("name") or "").strip())
        if not name:
            continue
        lawyer = p.get("lawyer") or ""
        if lawyer and lawyer != name:
            s = f"**{name}** ({lawyer})"
        else:
            s = _bold_outside_parens(name)
        parts.append(s)
    return "\n".join(parts)


def to_markdown(doc: "Document", config: "SourceConfig") -> str:
    """Build the full .md string from DB columns."""
    court_egnf = _court_eignarfall(doc.court or config.abbreviation)
    vt = doc.verdict_type or config.verdict_type_default
    case_nr = doc.case_number or ""

    # Header
    url_line = f"##### {doc.url}\n" if doc.url else ""
    if config.h1_use_display_name:
        title_line = (
            f"# {config.display_name} – {vt} – {case_nr}\n" if case_nr
            else f"# {config.display_name} – {vt}\n"
        )
    else:
        title_line = (
            f"# {vt} {court_egnf} – {case_nr}\n" if case_nr
            else f"# {vt} {court_egnf}\n"
        )
    date_line = f"## {_date_str(doc.document_date)}\n" if doc.document_date else ""

    parties_block = ""
    if config.parse_parties != "none" and (doc.plaintiffs or doc.defendants):
        plf = _format_parties(doc.plaintiffs, config)
        dfd = _format_parties(doc.defendants, config)
        if plf or dfd:
            parties_block = "### Aðilar\n"
            if plf and dfd:
                parties_block += f"{plf}\n\ngegn\n\n{dfd}\n"
            else:
                parties_block += f"{plf or dfd}\n"

    keywords_block = ""
    if doc.keywords:
        keywords_block = f"### Lykilorð\n{'. '.join(doc.keywords)}\n"

    reifun_block = ""
    if doc.summary:
        reifun_block = f"### Reifun\n{doc.summary}\n"

    header = (
        f"{url_line}{title_line}{date_line}\n"
        f"{parties_block}\n"
        f"{keywords_block}\n"
        f"{reifun_block}\n"
    )

    # Body — inject niðurstöðumerking í báðum textalögum
    # Committee sources (h1_use_display_name=True) use the extended pattern so
    # "## IV. Niðurstaða" is recognised as the verdict section.  Court sources
    # use the base pattern where Niðurstaða is a reasoning section, not the ruling.
    _verdict_pat = (
        _VERDICT_SECTION_PATTERNS_COMMITTEE
        if config.h1_use_display_name
        else _VERDICT_SECTION_PATTERNS
    )
    body_h2 = f"## {vt} {court_egnf}"
    if doc.body_text and doc.body_text.strip():
        body_text = doc.body_text.strip()
        if config.h1_use_display_name:
            body_text = _promote_bold_roman_headings(body_text)
        marked_body = inject_verdict_marker(_ensure_h2_headings(body_text), pattern=_verdict_pat)
        body = f"{body_h2}\n\n{marked_body}"
    else:
        body = ""

    # Lower court (appended if present)
    if doc.lower_body_text and doc.lower_body_text.strip():
        lower = _ensure_lower_court_h1(
            _ensure_lower_subheadings(
                _ensure_h2_headings(doc.lower_body_text.strip())
            )
        )
        lower = _promote_late_urskurdur(lower)
        lower = _promote_late_domur(lower)
        lower = _promote_late_akvordun(lower)
        marked_lower = inject_verdict_marker(lower, LOWER_COURT_VERDICT_MARKER)
        body += f"\n\n<!-- lægra dómstig -->\n\n{marked_lower}"

    return header + body + "\n"


def to_urlausn(doc: "Document", config: "SourceConfig") -> str:
    """'Hrd. E-25/2020 5. maí 2020 – Dómur'"""
    abbr = doc.court or config.abbreviation
    parts = [abbr]
    if doc.case_number:
        parts.append(doc.case_number)
    if doc.document_date:
        parts.append(_date_str(doc.document_date))
    vt = doc.verdict_type or config.verdict_type_default
    return f"{' '.join(parts)} – {vt}"


_VERDICT_CODE = {"Dómur": "D", "Úrskurður": "U", "Álit": "A", "Bréf": "B"}

_ICELAND_TO_ASCII = str.maketrans(
    "áéíóúýðöÁÉÍÓÚÝÐÖ",
    "aeiouydoAEIOUYDO",
)


def _ascii(s: str) -> str:
    """Transliterate Icelandic characters to ASCII. þ→th, æ→ae, rest 1-to-1."""
    return s.translate(_ICELAND_TO_ASCII).replace("þ", "th").replace("Þ", "Th").replace("æ", "ae").replace("Æ", "Ae")


def verdict_filename(doc: "Document", config: "SourceConfig") -> str:
    """Return the canonical filename stem (no extension) for a document.

    Format: ``{court}_{case}_{D|U}_{DD-MM-YYYY}``

    Examples::

        Lrd_177-2024_D_06-03-2024    (Landsréttur 177/2024, dómur, 6. mars 2024)
        HerdRvk_E-100-2020_U_05-05-2020  (Héraðsdómur Reykjavíkur, úrskurður)

    Rules:
    - *court*: abbreviation from ``doc.court`` (or ``config.abbreviation``),
      periods and spaces removed, Icelandic chars transliterated to ASCII.
      ``'Hérd. Rvk.'`` → ``'HerdRvk'``, ``'Lrd.'`` → ``'Lrd'``.
    - *case*: ``doc.case_number`` with ``/`` → ``-`` and spaces → ``_``.
    - *verdict*: ``D`` for Dómur, ``U`` for Úrskurður, omitted if unknown.
    - *date*: ``document_date`` formatted as ``DD-MM-YYYY``.
      Omitted when no date is available.
    """
    abbr = doc.court or config.abbreviation
    court_part = _ascii(re.sub(r"[.\s]+", "", abbr))   # 'Hérd. Rvk.' → 'HerdRvk'
    case_part  = (doc.case_number or f"id{doc.external_id}").replace("/", "-").replace(" ", "_")
    verdict_code = _VERDICT_CODE.get(doc.verdict_type or "", "")
    verdict_part = f"_{verdict_code}" if verdict_code else ""
    if doc.document_date:
        d = doc.document_date
        date_part = f"_{d.day:02d}-{d.month:02d}-{d.year}"
    else:
        date_part = ""
    return f"{court_part}_{case_part}{verdict_part}{date_part}"


def unique_verdict_filename(base: str, taken: set[str]) -> str:
    """Return ``base`` if not in ``taken``, else ``base_2``, ``base_3``, …

    The caller is responsible for maintaining ``taken`` across the import run:
    load all existing ``verdict_filename`` values for the source at startup,
    then add each newly assigned name before moving to the next document.

    Example::

        taken = {"HerdRvk_E-100-2020_D_05-05-2020"}
        unique_verdict_filename("HerdRvk_E-100-2020_D_05-05-2020", taken)
        # → "HerdRvk_E-100-2020_D_05-05-2020_2"
    """
    if base not in taken:
        return base
    i = 2
    while f"{base}_{i}" in taken:
        i += 1
    return f"{base}_{i}"


def write_markdown(
    doc: "Document",
    config: "SourceConfig",
    vf: str | None = None,
) -> Path | None:
    """Write .md to disk and return the path, or None if body_text is absent.

    Pass a pre-computed ``vf`` (from ``unique_verdict_filename``) to avoid
    recomputing it and to ensure collision-safe filenames are used.
    """
    if not doc.body_text:
        return None
    if vf is None:
        vf = verdict_filename(doc, config)
    md_path = config.markdown_path(vf)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(to_markdown(doc, config), encoding="utf-8")
    return md_path
