"""Section headings: normalisation and classification into section_kind.

Shared by renderer.py (verdict-section marker) and segmenter.py (passages).
Patterns tolerate spaced letters ("Ú rskurðarorð") and ð/d confusion from
old PDF encodings, exactly like the renderer always has.
"""
from __future__ import annotations

import re


def _spaced(word: str) -> str:
    """Pattern matching a word with optional whitespace between each character."""
    return r'\s*'.join(re.escape(c) for c in word)


def _spaced_eth(word: str) -> str:
    """Like _spaced but maps ð/Ð → [ðÐdD] for OCR robustness (old PDFs use plain D)."""
    return r'\s*'.join(
        r'[ðÐdD]' if c in 'ðÐ' else re.escape(c)
        for c in word
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

SECTION_KINDS: frozenset[str] = frozenset({
    "reifun", "malsmedferd", "malsatvik", "malsastaedur", "nidurstada", "domsord", "annad",
})

# "IV. Niðurstaða" / "3. Málsatvik" / "I." — strip the enumerator before matching.
_ENUM_PREFIX_RE = re.compile(r'^\s*(?:[IVXLC]+|\d{1,2})\.\s*', re.IGNORECASE)
_HEADING_MARKS_RE = re.compile(r'^\s*#{1,6}\s*|\*\*|_')

_NIDURSTADA_RE = re.compile(
    r'^(?:forsendur(?:\s+og\s+)?\s*)?n\s*i\s*[ðÐdD]\s*u\s*r\s*s\s*t\s*[aö]\s*[ðÐdD]\s*[au]\s*r?\b'
    r'|^forsendur\b|^álit\b',
    re.IGNORECASE,
)
_MALSATVIK_RE = re.compile(r'^(?:málsatvik|málavextir|atvik\s+máls)\b', re.IGNORECASE)
_MALSASTAEDUR_RE = re.compile(
    r'^(?:málsástæður|lagarök|röksemdir|sjónarmið)\b', re.IGNORECASE)
_MALSMEDFERD_RE = re.compile(
    r'^(?:málsmeðferð|dómkröfur|kröfur|kæruefni|kæra)\b', re.IGNORECASE)


def normalize_heading(raw: str) -> str:
    """'## Dómsorð:' → 'Dómsorð'; '**IV. Niðurstaða.**' → 'IV. Niðurstaða'."""
    s = _HEADING_MARKS_RE.sub("", raw or "")
    s = re.sub(r'\s+', " ", s).strip()
    s = s.rstrip(":.").strip()
    return s


def classify_section(heading: str | None) -> str:
    """Map a (raw or normalised) heading to a section_kind. None/unknown → 'annad'."""
    if not heading:
        return "annad"
    h = normalize_heading(heading)
    if _VERDICT_SECTION_PATTERNS.search(h):
        return "domsord"
    body = _ENUM_PREFIX_RE.sub("", h)
    if _NIDURSTADA_RE.search(body):
        return "nidurstada"
    if _MALSATVIK_RE.search(body):
        return "malsatvik"
    if _MALSASTAEDUR_RE.search(body):
        return "malsastaedur"
    if _MALSMEDFERD_RE.search(body):
        return "malsmedferd"
    return "annad"
