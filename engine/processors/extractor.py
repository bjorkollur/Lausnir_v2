"""
Extracts structured NORM-layer fields from raw_api_data.
Each source has its own extraction logic registered here.
The extractor never reads from DB — only transforms raw_api_data.
"""
from __future__ import annotations

import base64
import io
import re
from datetime import date
from typing import Any

import fitz  # PyMuPDF
import pdfplumber
from bs4 import BeautifulSoup

from engine.config.sources import SourceConfig


class Extractor:
    """Extract normalised fields from raw_api_data for a given source."""

    def __init__(self, config: SourceConfig) -> None:
        self.config = config

    def extract(self, raw: dict[str, Any]) -> dict[str, Any]:
        """
        Returns a dict of Document column values extracted from raw.
        Caller is responsible for merging with external_id, url, source_id.
        """
        fn = _EXTRACTORS.get(self.config.short_name)
        if fn is None:
            raise NotImplementedError(
                f"No extractor registered for {self.config.short_name!r}. "
                "Add one to engine/processors/extractor.py"
            )
        return fn(raw, self.config)


# ─── Shared helpers ───────────────────────────────────────────────────────────

_MONTHS_IS = {
    "janúar": 1, "febrúar": 2, "mars": 3, "apríl": 4, "maí": 5, "júní": 6,
    "júlí": 7, "ágúst": 8, "september": 9, "október": 10, "nóvember": 11, "desember": 12,
}


def _parse_icelandic_date(s: str | None) -> date | None:
    if not s:
        return None
    # ISO format first
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    # "5. maí 2020" format
    m = re.match(r"(\d{1,2})\.\s*([a-záéíóúýðþæö]+)\s+(\d{4})", s, re.IGNORECASE)
    if m:
        month = _MONTHS_IS.get(m[2].lower())
        if month:
            try:
                return date(int(m[3]), month, int(m[1]))
            except ValueError:
                return None
    return None


def _keywords_from_content(content_list: list | None) -> list[str]:
    if not content_list:
        return []
    out = []
    for item in content_list:
        if isinstance(item, str):
            out.append(item.strip())
        elif isinstance(item, dict):
            title = item.get("title") or item.get("name") or ""
            if title:
                out.append(title.strip())
    return [k for k in out if k]


_BLOCK_NODES = {
    "paragraph", "heading-1", "heading-2", "heading-3",
    "heading-4", "heading-5", "heading-6",
    "ordered-list", "unordered-list", "list-item",
    "blockquote", "hr",
}

_HEADING_MARKERS = {
    "heading-1": "# ",
    "heading-2": "## ",
    "heading-3": "### ",
    "heading-4": "#### ",
    "heading-5": "##### ",
    "heading-6": "###### ",
}


_MONTHS_IS_RE = (
    r'(?:janúar|febrúar|mars|apríl|maí|júní|júlí|ágúst|september|október|nóvember|desember)'
)

# Matches the preamble/title block at the start of a Landsréttur PDF:
# everything up to (but not including) "Dómur/Úrskurður Landsréttar" which opens the body.
_LANDSRETTUR_PREAMBLE_RE = re.compile(r'^.*?(?=(?:Dómur|Úrskurður) Landsréttar\b)', re.DOTALL)

# Matches the preamble of a Héraðsdómur PDF (court name, date, parties) plus the
# standalone verdict-type heading ("Dómur" or "## Dómur") that precedes the body.
# The optional "## " prefix is added by heading_fonts detection in _pdf_bytes_to_text.
_HERADSDOMUR_PREAMBLE_RE = re.compile(
    r'^.+?\n\n(?:#{1,6}\s+)?(?:Dómur|Úrskurður|Dómsúrskurður|Niðurstaða|Álit):?\n\n',
    re.DOTALL | re.IGNORECASE,
)

# Strips heading blocks (## Court name, ## parties) that appear at the top of
# Structure A héraðsdómar PDFs after the leading verdict-type heading.
_STRUCT_A_RE = re.compile(r'^(?:#{1,6}\s+[^\n]*\n\n|\([^)\n]*\)\n\n)+', re.DOTALL)

# Extracts the keyword string after "Lykilorð" in the PDF preamble.
# e.g. "Lykilorð Kærumál. Gæsluvarðhald. 2. mgr. 95. gr. laga nr. 88/2008."
_LANDSRETTUR_PDF_KEYWORDS_RE = re.compile(
    r'\bLykilorð\s+(.+?)(?=\n\n|\nÚtdráttur|\nDómur|\nÚrskurður|\Z)',
    re.DOTALL,
)

# Split on ". " before an uppercase letter (new topic keyword).
_KW_UPPERCASE_SPLIT_RE = re.compile(r'\.\s+(?=[A-ZÁÉÍÓÚÝÐÞÆÖ])')

# Further split a chunk at ". DIGIT" when preceded by a full word (≥4 lowercase
# chars). This separates "Gæsluvarðhald" from "2. mgr. 95. gr. laga nr. 88/2008"
# while leaving "laga nr. 88" and "2. mgr. 95" intact (abbreviations are ≤3 chars).
# Fixed-width lookbehind: check exactly the 4 chars before ". "
_KW_DIGIT_SPLIT_RE = re.compile(
    r'(?<=[a-záéíóúýðþæö][a-záéíóúýðþæö][a-záéíóúýðþæö][a-záéíóúýðþæö])\.\s+(?=\d)'
)

# Extracts the verdict date from the body/preamble text of any Icelandic court document.
# Matches: "Dómur þriðjudaginn 5. maí 2022." or "Úrskurður fimmtudaginn 5. maí 2022."
# This format is used by Landsréttur (in PDF preamble) and héraðsdómstólar.
# Hæstiréttur richText does NOT include this line → returns None → falls back to API date.
_VERDICT_DATE_RE = re.compile(
    r'(?:Dómur|Úrskurður)\s+\w+daginn?\s+(\d{1,2}\.\s+' + _MONTHS_IS_RE + r'\s+\d{4})\.',
    re.IGNORECASE,
)

# Héraðsdómar heading format: "## Héraðsdóms Reykjavíkur 7. febrúar 2018 í máli nr. X"
# The date appears between the court name and "í máli" (or end of line).
_HERADSDOMUR_HEADING_DATE_RE = re.compile(
    r'(?:#{1,6}\s+)?Héraðsdóm\w*\s+\w[\w\s.]*?\s+(\d{1,2}\.\s+' + _MONTHS_IS_RE + r'\s+\d{4})\s+[íi]\s+máli',
    re.IGNORECASE,
)


def _date_from_verdict_text(text: str | None, search_chars: int = 800) -> "date | None":
    """
    Extract the verdict date stamped on the document itself.

    Tries two patterns in the first `search_chars` characters:
    1. 'Dómur/Úrskurður [weekday] D. [month] YYYY.' (Landsréttur + some Héraðsdómar)
    2. '## Héraðsdóms [court] D. [month] YYYY í máli' (Héraðsdómar heading format)
    Returns None if neither pattern is found.
    """
    if not text:
        return None
    window = text[:search_chars]
    m = _VERDICT_DATE_RE.search(window)
    if m:
        return _parse_icelandic_date(m.group(1))
    m = _HERADSDOMUR_HEADING_DATE_RE.search(window)
    return _parse_icelandic_date(m.group(1)) if m else None


def _correct_date_if_wrong(
    api_date: "date | None",
    verdict_text: str | None,
) -> "date | None":
    """
    Return the API date unless the document text reveals it is wrong.

    Logic:
    - Extract the date stamped on the document itself (weekday + D. month YYYY).
    - If found AND it differs from the API date → API is likely wrong; use doc date.
    - If found AND it matches the API date → API is confirmed correct; use API date.
    - If not found → no cross-check possible; use API date.

    This means the body-text date is only ever used as a *correction*, never as
    an unconditional override, so correctly-dated long-running cases are unaffected.
    """
    doc_date = _date_from_verdict_text(verdict_text)
    if doc_date is not None and doc_date != api_date:
        return doc_date
    return api_date

_LOWER_COURT_SPLIT_RE = re.compile(
    # Variant A: full date present — heading marker optional.
    # Héraðsdóm\w* handles all declension forms (Héraðsdóms, Héraðsdómur,
    # Héraðsdóm, Héraðsdóma, Héraðsdómsdóms …).  \w+dag\w* handles
    # daginn / dagurinn variants.
    # [^\n]{0,30}(?<![alpha])$ — allow up to 30 chars of case-number suffix after
    # the year (e.g. " í máli nr. E-2569/2021:") but reject if the last char on
    # the line is alphabetic — that indicates a continuation sentence wrapped to
    # the next line (e.g. "…2023 þar\nsem máli…").
    r'^(?:#{1,6}\s+)?(?:Úrskurður|Dóm(?:ur|ar))\s+(?:Landsréttar|Héra(?:ðsd|ðds)óm\w*\b[^\n,]{0,25}?)'
    r'(?:\s*,?\s*\w+dag\w*\s+\d{1,2}\.?\s*' + _MONTHS_IS_RE + r'\s+\d{4}\.?'
    r'|\s*,?\s*\d{1,2}\.?\s*' + _MONTHS_IS_RE + r'\s+\d{4}\.?)'
    r'[^\n]{0,30}(?<![a-zA-ZÀ-ɏ])$'
    r'|'
    # Variant B: ## heading present but date absent/redacted/partial or replaced
    # by a case number.  The ## is the safety guard against false positives.
    # [^\n]{0,45} allows district name + redacted date components (≤38 chars seen),
    # but blocks the 61-char "…2023 í málinu nr. X þar" continuation pattern.
    r'^#{1,6}\s+(?:Úrskurður|Dóm(?:ur|ar))\s+Héra(?:ðsd|ðds)óm\w*\b[^\n]{0,45}$'
    r'|'
    # Variant C: ## heading starting directly with "Héraðsdóm" (no Dómur/Úrskurður
    # prefix) — rare early-2018 format, e.g. "## Héraðsdóms Reykjaness, föstudaginn…"
    r'^#{1,6}\s+Héra(?:ðsd|ðds)óm\w*\b[^\n]{0,45}$'
    r'|'
    # Variant D: ## heading with direct district name — Héraðsdóms word omitted
    # entirely.  e.g. "## Dómur Norðurlands eystra 6. apríl 2021".
    # The ## marker is a required safety guard against false positives.
    # District list covers all Icelandic héraðsdómar.
    r'^#{1,6}\s+(?:Úrskurður|Dóm(?:ur|ar))\s+'
    r'(?:Reykjavíkur|Reykjaness|Vesturlands|Norðurlands\s+(?:eystra|vestra)'
    r'|Austurlands|Suðurlands|Suðurnesja)\b[^\n]*$'
    r'|'
    # Variant E: ## heading with only a weekday/date — court name entirely absent.
    # e.g. "## Úrskurður föstudaginn 21. júní 2019"  (kærumál, rare format).
    # Requires ## AND (optional weekday +) numeric date for safety.
    r'^#{1,6}\s+(?:Úrskurður|Dóm(?:ur|ar))\s+(?:\w+dag\w*\s+)?\d{1,2}\.\s*'
    + _MONTHS_IS_RE +
    r'\s+\d{4}\.?[^\n]*$',
    re.MULTILINE | re.IGNORECASE,
)

# Matches non-bold lower-court heading blocks that _heading_marker misses.
# Used in _block_to_text to force these as ## headings so _split_lower_court
# can find them even when the PDF uses plain (non-bold) font for the heading.
# Also matches the rare form where Héraðsdóms is omitted and the district name
# follows directly (e.g. "Dómur Norðurlands eystra …").
_LOWER_COURT_BLOCK_RE = re.compile(
    r'^(?:Dóm(?:ur|ar)|Úrskurður)\s+'
    r'(?:Héra(?:ðsd|ðds)óm\w*'
    r'|Reykjavíkur|Reykjaness|Vesturlands|Norðurlands\s+(?:eystra|vestra)'
    r'|Austurlands|Suðurlands|Suðurnesja)\b',
    re.IGNORECASE,
)


def _split_lower_court(text: str) -> tuple[str, str | None]:
    """Split body at first lower court section header. Returns (body, lower_body)."""
    m = _LOWER_COURT_SPLIT_RE.search(text)
    if not m:
        return text, None
    body = text[:m.start()].rstrip()
    lower = text[m.start():].strip()
    return body, lower or None


def _keywords_from_pdf_preamble(full_text: str) -> list[str]:
    """
    Extract keywords from the 'Lykilorð' line in a Landsréttur PDF preamble.
    Returns [] if not found. Used as fallback when the API returns no keywords.

    Input:  "Lykilorð Kærumál. Gæsluvarðhald. 2. mgr. 95. gr. laga nr. 88/2008."
    Output: ["Kærumál", "Gæsluvarðhald", "2. mgr. 95. gr. laga nr. 88/2008"]

    Splitting rules:
    1. Split on ". " before uppercase → separates topic words (Kærumál, Gæsluvarðhald).
    2. Within each chunk, split on ". DIGIT" when preceded by ≥4 lowercase chars
       (a full word, not a 2–3-char abbreviation like mgr/gr/nr) → separates
       "Gæsluvarðhald" from "2. mgr. 95. gr. laga nr. 88/2008" without breaking
       internal legal references.
    """
    m = _LANDSRETTUR_PDF_KEYWORDS_RE.search(full_text)
    if not m:
        return []
    raw_kws = m.group(1).strip()
    # Pass 1: split on ". " before uppercase
    chunks = _KW_UPPERCASE_SPLIT_RE.split(raw_kws)
    # Pass 2: within each chunk split on ". DIGIT" after a long word
    parts: list[str] = []
    for chunk in chunks:
        parts.extend(_KW_DIGIT_SPLIT_RE.split(chunk))
    return [p.strip().rstrip(".") for p in parts if p.strip()]


def _rich_text_to_plain(nodes: list | None) -> str:
    if not nodes:
        return ""
    parts = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_type = node.get("nodeType", "")
        if node_type == "text":
            parts.append(node.get("value", ""))
        elif "content" in node:
            inner = _rich_text_to_plain(node["content"])
            if node_type in _BLOCK_NODES:
                marker = _HEADING_MARKERS.get(node_type, "")
                parts.append(("\n\n" + marker + inner) if inner else "")
            else:
                parts.append(inner)
    text = "".join(parts)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _html_to_plain(html: str | None) -> str | None:
    """Strip HTML tags to plain text. Returns None for blank/None/non-string input."""
    if not html or not isinstance(html, str):
        return None
    text = BeautifulSoup(html, "html.parser").get_text(separator="\n").strip()
    return text or None


def _detect_verdict_type(plain_text: str | None, keywords: list) -> str | None:
    """Return 'Úrskurður' if body text signals an úrskurður, else None."""
    if not plain_text:
        return None
    if re.search(r"Úrskurðarorð|úrskurðar\b", plain_text, re.IGNORECASE):
        return "Úrskurður"
    return None


def _parse_parties_gegn(title: str | None) -> tuple[list, list]:
    """
    Parse plaintiff/defendant from an island.is verdict title.

    Handles two formats:
    1. Civil/standard: 'A ehf. gegn B hf.'  →  split on 'gegn'
    2. Criminal appeal (kærumál): 'Lögreglustjórinn ... \\nVarnaraðili X (...)'
       → split on '\\nVarnaraðili'; strips the role label from the defendant name.
    """
    if not title:
        return [], []

    # Format 1: split on 'gegn'
    parts = re.split(r"(?<!\w)gegn", title, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) == 2:
        return (
            [{"name": parts[0].strip(), "lawyer": None}],
            [{"name": parts[1].strip(), "lawyer": None}],
        )

    # Format 2: kærumál — '\nVarnaraðili [name]' with no 'gegn'
    m = re.search(r'\n[Vv]arnaraðili\s+', title)
    if m:
        plaintiff_text = title[:m.start()].strip()
        defendant_text = title[m.end():].strip()  # strip the 'Varnaraðili ' role label
        return (
            [{"name": plaintiff_text, "lawyer": None}],
            [{"name": defendant_text, "lawyer": None}],
        )

    return [{"name": title.strip(), "lawyer": None}], []


_PLF_LABEL_RE = re.compile(r'(?:Stefnend[ua]r?|Stefnandi|Sækjandi|Sóknaraðil(?:i|ar)|Endurupptökubeiðandi)\s*:', re.I)
_DFD_LABEL_RE = re.compile(r'(?:Stefnd[uia]|Ákærð[uia](?:/sakborningar)?|Varnaraðii?l(?:i|ar)|Gagnaðil(?:i|ar))\s*:', re.I)
_GEGN_LINE_RE = re.compile(r'^gegn$', re.I)


def _parse_parties_role_based(title: str | None) -> tuple[list, list]:
    """
    Parse plaintiff/defendant from role-labelled Héraðsdómar title.

    Handles:
    - Civil:    'Stefnendur: A (lögm.)\\nStefndu: B (lögm.)'
    - Criminal: 'Sækjandi: X (saksókn.)\\r\\nÁkærðu/sakborningar: Y (lögm.)'
    - Appeals:  'Sóknaraðili: X\\nVarnaraðili: Y' (also plural Varnaraðilar/Sóknaraðilar)
    - Gegn-in-role: 'Sækjandi: X\\ngegn\\nY' (standalone gegn line as separator)

    Falls back to _parse_parties_gegn when no role label is found.
    """
    if not title:
        return [], []

    lines = re.split(r'\r?\n', title)
    plf_parts: list[str] = []
    dfd_parts: list[str] = []
    current: str | None = None

    for line in lines:
        stripped = line.strip()
        if _PLF_LABEL_RE.match(stripped):
            current = "plf"
            plf_parts.append(_PLF_LABEL_RE.sub("", stripped, count=1).strip())
        elif _DFD_LABEL_RE.match(stripped):
            current = "dfd"
            dfd_parts.append(_DFD_LABEL_RE.sub("", stripped, count=1).strip())
        elif _GEGN_LINE_RE.match(stripped) and current == "plf":
            # standalone "gegn" line acts as plaintiff→defendant separator
            current = "dfd"
        elif current == "plf" and stripped:
            plf_parts.append(stripped)
        elif current == "dfd" and stripped:
            dfd_parts.append(stripped)

    plf_text = " ".join(plf_parts).strip()
    dfd_text = " ".join(dfd_parts).strip()

    if not plf_text and not dfd_text:
        # "gegn" present → split as adversarial parties; otherwise the title
        # is a prose summary (pre-2013 API format) — no party data available.
        if " gegn " in title.lower():
            return _parse_parties_gegn(title)
        return [], []

    # Role labels found but defendant still empty: try splitting plaintiff on " gegn "
    # (handles inline format like 'Sækjandi: X gegn Y' without a label on defendant)
    if plf_text and not dfd_text:
        m = re.search(r'(?<!\w)gegn\s+', plf_text, re.IGNORECASE)
        if m:
            dfd_text = plf_text[m.end():].strip()
            plf_text = plf_text[:m.start()].strip()

    plf = [{"name": plf_text, "lawyer": None}] if plf_text else []
    dfd = [{"name": dfd_text, "lawyer": None}] if dfd_text else []
    return plf, dfd


# Headings that mark court name or verdict type — not party names.
_COURT_OR_VERDICT_H_RE = re.compile(
    r'^## (?:DÓMUR|Dómur|Úrskurður|Dómsúrskurður|Niðurstaða|Álit|'
    r'Héraðsdóm\w+|Dómur\s+Héraðsdóms\w*|Héraðs\w+|DÓMSÚRSKURÐUR)',
    re.IGNORECASE,
)


def _extract_parties_from_heradsdomstolar_preamble(pdf_text: str) -> tuple[list, list]:
    """Extract parties by locating 'gegn' in the preamble (first ~3000 chars).

    Handles multiple layouts:
      A)  ## Plaintiff\\n\\n## gegn\\n\\n## Defendant
      B)  ## Plaintiff\\n\\n(lawyer) gegn\\n\\n## Defendant   (lawyer inline, dfd is heading)
      C)  ## Plaintiff\\n\\n(lawyer) gegn\\n\\nDefendant (lawyer)  (dfd is plain text)
      D)  ## Plt-part1\\n\\nPlt-part2 (lawyer) gegn\\n\\nDefendant (lawyer) (name spans paras)
      E)  ## Plaintiff\\n\\n(lawyer) gegn Defendant (lawyer)  (same-line gegn)
      F)  Stefnandi: Plaintiff (lawyer) Stefndi: Defendant (lawyer) Dómari: ...
    """
    text = pdf_text[:3000]

    # Pattern F: Stefnandi / Stefndi labeled format in PDF preamble.
    # Flatten to single line so lawyer paragraphs merge with the name line.
    flat = text.replace('\n', ' ')
    m = re.search(
        r'Stefnandi:\s+(.+?)\s+Stefnd[iau]:\s+(.+?)(?:\s+Dómar[ai]|\s{3,}|$)',
        flat, re.IGNORECASE,
    )
    if m:
        return (
            [{"name": " ".join(m.group(1).split()), "lawyer": None}],
            [{"name": " ".join(m.group(2).split()), "lawyer": None}],
        )

    # Pattern A: standalone ## gegn heading
    # Pattern B/C/D: gegn at end of paragraph (followed by \n\n)
    # Pattern E: gegn inline on same line as defendant (no \n\n after gegn)
    gegn_m = (
        re.search(r'\n\n## gegn:?\n\n', text, re.IGNORECASE)
        or re.search(r'\bgegn:?\s*\n\n', text[:1500], re.IGNORECASE)
        or re.search(r'\bgegn:?\s+(?=[A-ZÁÐÉÍÓÚÝÞÆÖ\(])', text[:1000], re.IGNORECASE)
    )
    if not gegn_m:
        return [], []

    same_line_gegn = not text[gegn_m.end() - 1 : gegn_m.end()].endswith('\n')

    # ── Plaintiff ──────────────────────────────────────────────────────────────
    pre_paras = [p.strip() for p in text[: gegn_m.start()].split('\n\n') if p.strip()]
    plf_text = ""
    for i in range(len(pre_paras) - 1, -1, -1):
        p = pre_paras[i]
        if p.startswith('## ') and not _COURT_OR_VERDICT_H_RE.match(p):
            name = p[3:].strip()
            nxt = (pre_paras[i + 1] if i + 1 < len(pre_paras) else "").strip()
            m = re.match(r'^(.*?)\s*(\([^)]{3,80}\))\s*$', nxt)
            if m:
                cont = m.group(1).strip()
                lawyer = m.group(2)
                plf_text = f"{name} {cont} {lawyer}".strip() if cont else f"{name} {lawyer}"
            elif nxt and not nxt.startswith('## ') and len(nxt) < 80:
                plf_text = f"{name} {nxt}"
            else:
                plf_text = name
            break

    # Fallback for old PDFs without ## headings: try last plain-text party name
    # before gegn (skip lawyer-paren lines and court intro lines).
    if not plf_text:
        _INTRO_RE = re.compile(
            r'^(?:Ár\s+\d|Mál\s+|Málið|[Hh]éraðsdóm|\d{1,2}\.\s+[a-z]|\d+\s+\w)',
        )
        for p in reversed(pre_paras):
            if p.startswith('(') and p.endswith(')'):
                continue  # lawyer attribution
            if _INTRO_RE.match(p) or len(p) > 120:
                # Long court-intro line: try to pull the party name from just
                # before gegn (same-line format: "... Ákæruvaldið (lög.) gegn ...")
                inline_m = re.search(
                    r'([A-ZÁÐÉÍÓÚÝÞÆÖ][^\n:(]{2,60}?)\s*(?:\([^)]{3,60}\))?\s*$',
                    p,
                )
                if inline_m:
                    plf_text = inline_m.group(1).strip()
                continue
            if not p.startswith('## '):
                plf_text = p
                break

    # ── Defendant ──────────────────────────────────────────────────────────────
    _SKIP_RE = re.compile(
        r'^(?:## (?:Úrskurður|Dómur|DÓMUR|Dómsúrskurður)|Mál\s+(?:þetta|nr\.)|'
        r'Málið|Árið\s+\d|\d+\.\s)',
        re.IGNORECASE,
    )
    dfd_text = ""

    if same_line_gegn:
        # Pattern E: defendant is on the same line as gegn
        rest = text[gegn_m.end():].split('\n\n')[0].strip()
        # Truncate at sentence-break conjunctions that start prose, not names
        rest = re.split(r'\s+en\s+málið\b|\s+en\s+dómteki|\s+samdægurs\b', rest)[0].strip()
        m = re.match(r'^(.*?)\s*(\([^)]{3,80}\))\s*$', rest)
        if m:
            name = m.group(1).strip()
            dfd_text = f"{name} {m.group(2)}" if name else ""
        elif rest and len(rest) < 150:
            dfd_text = rest
    else:
        post_paras = [p.strip() for p in text[gegn_m.end():].split('\n\n') if p.strip()]
        for i, p in enumerate(post_paras):
            if _SKIP_RE.match(p):
                continue
            if p.startswith('## ') and not _COURT_OR_VERDICT_H_RE.match(p):
                name = p[3:].strip()
                nxt = (post_paras[i + 1] if i + 1 < len(post_paras) else "").strip()
                m = re.match(r'^(.*?)\s*(\([^)]{3,80}\))\s*$', nxt)
                if m:
                    cont = m.group(1).strip()
                    lawyer = m.group(2)
                    dfd_text = f"{name} {cont} {lawyer}".strip() if cont else f"{name} {lawyer}"
                elif nxt.startswith('(') and nxt.endswith(')'):
                    dfd_text = f"{name} {nxt}"
                else:
                    dfd_text = name
            else:
                m = re.match(r'^(.*?)\s*(\([^)]{3,80}\))\s*$', p)
                if m:
                    name = m.group(1).strip()
                    lawyer = m.group(2)
                    dfd_text = f"{name} {lawyer}" if name else ""
                elif len(p) < 150:
                    dfd_text = p
            if dfd_text:
                break

    plf = [{"name": plf_text, "lawyer": None}] if plf_text else []
    dfd = [{"name": dfd_text, "lawyer": None}] if dfd_text else []
    return plf, dfd


def _parse_stefndu_list(text: str) -> list[str]:
    """Parse 'A, Street N, City, B, Street N, City, og C, Street N, City.' → [A, B, C].

    Each party appears as Name, StreetAddress, City in the running list.  Address items
    contain digits; city items immediately follow an address item.  Everything else is a
    party name.
    """
    text = text.rstrip('.')
    chunks = re.split(r',\s*', text)
    names: list[str] = []
    prev_had_digit = False
    for chunk in chunks:
        chunk = re.sub(r'^\s*og\s+', '', chunk).strip()
        if not chunk:
            continue
        if re.search(r'\d', chunk):
            prev_had_digit = True          # this is a street address
        elif prev_had_digit:
            prev_had_digit = False         # this is a city — skip
        else:
            prev_had_digit = False
            if chunk:
                names.append(chunk)
    return names


def _extract_parties_from_body_start(body_text: str) -> tuple[list, list]:
    """Extract parties from the opening paragraph of old-format héraðsdómar body text.

    Old docs have no structured API title; party names appear in the intro sentence:
    - Criminal Ár: 'Ákæruvaldið (saksóknari) gegn X, sem tekið var...'
    - Criminal á hendur: '...á hendur [Defendant], kt...'
    - Civil höfðar: '[Plaintiff], höfðar... á hendur [Defendant]'
    - Civil af/af hálfu: '...af hálfu [Plaintiff]... á hendur [Defendant]'
    Returns ([], []) when no reliable match is found.
    """
    # Collapse multiple whitespace (OCR artifact) before matching
    s = re.sub(r'\s+', ' ', (body_text or "").replace('\n', ' '))[:1200]

    # 0a. Stefnandi / Stefndi labeled format in preamble-style (colon separated)
    m = re.search(
        r'Stefnandi:\s+(.+?)\s+Stefnd[iau]:\s+(.+?)(?:\s+Dómar[ai]|\s{2}|$)',
        s, re.IGNORECASE,
    )
    if m:
        plf_raw = m.group(1).strip()
        dfd_raw = m.group(2).strip()
        return [{"name": plf_raw, "lawyer": None}], [{"name": dfd_raw, "lawyer": None}]

    # 0b. "Stefnandi er X, heimilisfang. Stefndu eru A, götu 1, bær, B, götu 2, bær..."
    # Last-resort body-text extraction — only used when preamble extraction yields nothing.
    m_plf = re.search(r'Stefnandi\s+er\s+([^,\.]{1,80})', s, re.IGNORECASE)
    m_dfd = re.search(
        r'Stefnd[ui]\s+er[u]?\s+(.+?)(?:\.\s+[A-ZÁÐÉÍÓÚÝÞÆÖ]|\.\s*$)',
        s, re.IGNORECASE,
    )
    if m_plf and m_dfd:
        plf_name = ' '.join(m_plf.group(1).split())
        # Plural 'eru' → multi-defendant list with address stripping.
        # Singular 'er'  → single defendant; take only up to first comma.
        if re.search(r'\beru\b', m_dfd.group(0), re.IGNORECASE):
            dfd_names = _parse_stefndu_list(m_dfd.group(1))
        else:
            dfd_name = m_dfd.group(1).split(',')[0].strip()
            dfd_names = [dfd_name] if dfd_name else []
        plf = [{"name": plf_name, "lawyer": None}]
        dfd = [{"name": n, "lawyer": None} for n in dfd_names] if dfd_names else []
        if plf or dfd:
            return plf, dfd

    # 1. Criminal Ár format: Ákæruvaldið (saksóknari) gegn X
    m = re.search(
        r'Ákæruvaldið\s+(?:\(([^)]+)\)\s+)?gegn\s+([^(,\n]{5,60}?)(?:\s*\(|\s+kt\b|\s+fædd|,\s|\s+sem\b)',
        s, re.IGNORECASE,
    )
    if m:
        lawyer = (m.group(1) or "").strip()
        dfd_name = m.group(2).strip()
        plf_name = f"Ákæruvaldið{' (' + lawyer + ')' if lawyer else ''}"
        return [{"name": plf_name, "lawyer": None}], [{"name": dfd_name, "lawyer": None}]

    # 2. Find defendant: "á hendur Name" or "gegn Name"
    dfd_m = (
        re.search(
            r'á\s+hendur\s+(?:ákærða\s+|ákærðu\s+)?'
            r'([A-ZÁÐÉÍÓÚÝÞÆÖ][^,]{4,80}?)(?:,|\s+kt\b|\s+\[|\s+fædd|\s+fyrir\b)',
            s, re.IGNORECASE,
        )
        or re.search(
            r'\bgegn:?\s+([A-ZÁÐÉÍÓÚÝÞÆÖ][^,]{4,80}?)(?:,|\s+og\b|\s*\.\s)',
            s, re.IGNORECASE,
        )
    )

    # 3. Find plaintiff via various civil/criminal intro patterns
    plf_m = (
        re.search(r'\bhöfðar\s+([A-ZÁÐÉÍÓÚÝÞÆÖ][^,]{4,80}?)(?=,)', s, re.IGNORECASE)
        or re.search(r'Mál\s+þetta\s+höfðaði\s+([^,]{4,80}?)(?=,\s+með\b|\s+með\b)', s, re.IGNORECASE)
        or re.search(r'\baf\s+hálfu\s+([A-ZÁÐÉÍÓÚÝÞÆÖ][^,]{4,80}?)(?=,|\s+með\b)', s, re.IGNORECASE)
        or re.search(r'var\s+höfðað\s+af\s+([A-ZÁÐÉÍÓÚÝÞÆÖ][^,]{4,80}?)(?=,)', s, re.IGNORECASE)
        or re.search(r'Stefnandi\s+er\s+([^,\.]{1,80})', s, re.IGNORECASE)
    )

    # For criminal cases: look for prosecution entity when no civil plaintiff found
    if not plf_m and dfd_m:
        plf_m = (
            re.search(r'(Ákæruvaldið(?:\s+\([^)]+\))?)', s[:800], re.IGNORECASE)
            or re.search(r'Mál\s+þetta\s+höfðaði\s+([^,]{4,80}?)(?=,\s+með\b|\s+með\b)', s, re.IGNORECASE)
            or re.search(
                r'útgefinni\s+af\s+((?:lögreglustjóra|ríkissaksóknara|héraðssaksóknara)[^,\s]{0,50}[^,]*?)(?=\s*\d|\s*,)',
                s, re.IGNORECASE,
            )
            or re.search(
                r'((?:lögreglustjóri(?:nn)?\s+[^\n,]{5,40}|ríkissaksóknari|héraðssaksóknari))',
                s[:600], re.IGNORECASE,
            )
        )

    plf_text = plf_m.group(1).strip() if plf_m else ""
    dfd_text = dfd_m.group(1).strip() if dfd_m else ""

    plf = [{"name": plf_text, "lawyer": None}] if plf_text else []
    dfd = [{"name": dfd_text, "lawyer": None}] if dfd_text else []
    return plf, dfd


# ─── PDF extraction ──────────────────────────────────────────────────────────

_MARGIN_NUM_RE = re.compile(r'^\d{1,3}$')   # paragraph margin numbers (pure digit)
_ROMAN_NUM_RE  = re.compile(r'^[IVX]+$')   # Roman numerals I, II, III… (no period)

# Matches a new numbered paragraph: 1–3 digit number, period, space, uppercase.
# 4-digit years like "2015. Þar er..." do NOT match, avoiding false paragraph breaks.
_NEW_PARA_RE = re.compile(r'^\d{1,3}\.\s+[A-ZÁÐÉÍÓÚÝÞÆÖ]')


_INLINE_PARA_START_RE = re.compile(r'^\d{1,3}\.\s+[A-ZÁÐÉÍÓÚÝÞÆÖ\[]')
"""Matches numbered-paragraph starts like ``1. Texti`` or ``12.  Annað``.

The uppercase-letter guard (``[A-ZÁÐÉÍÓÚÝÞÆÖ[]``) prevents Icelandic dates
such as "24. júní" or "31. maí" from being mistaken for paragraph numbers —
month names are all lowercase in Icelandic."""


def _build_body_from_lines(
    lines: list,
    crop: "PdfCrop | None" = None,
) -> str:
    """Join fitz line dicts into a single string, de-hyphenating wraps.

    Embedded-paragraph detection
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Some lower-court PDFs pack multiple numbered paragraphs into a single
    fitz block.  The layout encodes each paragraph as::

        x = 64   "N. First word of paragraph …"   ← left-margin paragraph number
        x = 82   "… continuation of same paragraph …"

    so a block that spans two paragraphs looks like::

        x = 82   continuation of paragraph N-1
        x = 64   "N. First word of paragraph N"   ← embedded start

    When a non-first line sits ≥ 10 pt to the LEFT of the first non-empty
    line in the block **and** matches ``N. text``, it is treated as the start
    of a new numbered paragraph and preceded by ``\\n\\n`` in the output.
    The caller (``_block_to_text``) returns this string verbatim; the
    ``_pdf_bytes_to_text`` assembler preserves the internal ``\\n\\n`` so the
    paragraph boundaries survive the block-joining step.

    Inline-heading detection
    ~~~~~~~~~~~~~~~~~~~~~~~~
    Some lower-court PDFs embed section headings (e.g. "Dómkröfur",
    "Lagarök", "Niðurstaða") as bold lines WITHIN large fitz blocks — at
    the same x-coordinate as surrounding body text, so x-position alone
    cannot distinguish them.  When *crop* is provided and **all** non-empty
    spans on a non-first line carry a heading font (per *crop.heading_fonts*)
    AND the collapsed line text is short (≤ 80 chars), the line is promoted
    to a ``## heading`` with blank lines on both sides.
    """
    # Baseline x-coordinate: x of the first non-empty line in this block.
    first_x: float | None = None
    for line in lines:
        if any(s["text"].strip() for s in line.get("spans", [])):
            first_x = line["bbox"][0]
            break

    parts: list[str] = []
    after_heading = False
    prev_x: float | None = None   # x of the previous non-empty line (Style D)
    saw_blank: bool = False        # explicit blank fitz line *or* Y-gap > 25 pt
    prev_y: float | None = None   # Y of the previous non-empty line (gap detection)
    for line in lines:
        lt = " ".join(s["text"] for s in line.get("spans", [])).replace('\xa0', ' ').strip()
        if not lt:
            # Explicit blank fitz line — remember it so the next real line
            # opens a new paragraph (unless we haven't output anything yet).
            if parts:
                saw_blank = True
            continue

        line_x = line["bbox"][0]
        line_y = line["bbox"][1]

        # Y-gap paragraph detection: some PDFs filter blank lines but leave a
        # visible vertical gap (> 25 pt) between paragraphs.  Treat it the same
        # as an explicit blank line so the paragraph boundary is preserved.
        if prev_y is not None and line_y - prev_y > 25 and parts:
            saw_blank = True

        nonempty_spans = [s for s in line.get("spans", []) if s["text"].strip()]

        # ── Inline heading detection ──────────────────────────────────────────
        # Detects bold heading lines embedded inside a larger block (e.g.
        # "Dómkröfur", "Lagarök" at x=82 within a 48-line body block).
        # Guards:
        #  • parts non-empty → not the very first line (handled by _block_to_text)
        #  • crop provided → heading_fonts configured for this source
        #  • all non-empty spans are bold → not mixed bold/plain inline text
        #  • len ≤ 80 → short standalone phrase, not a sentence
        is_inline_heading = False
        if parts and crop is not None and nonempty_spans and len(lt) <= 80:
            is_inline_heading = all(
                bool(_heading_marker(s.get("font", ""), s.get("size", 0), crop))
                for s in nonempty_spans
            )

        is_embedded_para = (
            parts                                        # not the very first line
            and not is_inline_heading                    # already handled above
            and first_x is not None
            and line_x < first_x - 10                   # ≥ 10 pt further left
            and _INLINE_PARA_START_RE.match(lt)          # "N. text"
        )

        # ── Style D: first-line-indent paragraph detection ────────────────────
        # Some lower-court PDFs typeset paragraphs with first-line indentation:
        # the first line of a paragraph is indented further right (x≈118) while
        # continuation lines are flush left (x≈82).  Within a single large fitz
        # block all these lines appear together, so we detect a paragraph start
        # when the current line jumps significantly right of the previous line.
        # Guards: not the first line, not already classified, prev_x known, and
        # the current line doesn't end a hyphenated word (still mid-word).
        is_indent_start = (
            parts
            and prev_x is not None
            and not is_inline_heading
            and not is_embedded_para
            and line_x > prev_x + 20
            and not (parts and parts[-1].rstrip("\n").endswith("-"))
        )

        if is_inline_heading:
            marker = _heading_marker(
                nonempty_spans[0].get("font", ""),
                nonempty_spans[0].get("size", 0),
                crop,
            )
            parts.append("\n\n" + marker + _collapse_spaced_letters(lt))
            after_heading = True
        elif after_heading or saw_blank:
            # Explicit blank line, Y-gap, or first line after an inline heading:
            # always open a new paragraph.
            parts.append("\n\n" + lt)
            after_heading = False
        elif is_embedded_para or is_indent_start:
            # Close the current paragraph and open a new one.
            parts.append("\n\n" + lt)
        elif parts and parts[-1].rstrip("\n").endswith("-"):
            # De-hyphenate across a line wrap.
            parts[-1] = parts[-1].rstrip("\n")[:-1] + lt
        else:
            parts.append(lt)

        prev_x = line_x
        prev_y = line_y
        saw_blank = False

    if not parts:
        return ""

    # Join parts: regular parts with a space; parts that open with \n keep
    # their own separator (no extra space inserted before the newline).
    result = ""
    for p in parts:
        if result and not p.startswith("\n"):
            result += " " + p
        else:
            result += p
    return result.strip()


def _is_page_footer_num(block: dict, page_height: float, page_width: float) -> bool:
    """True if this block is a document page number printed in the footer margin.

    PDF generators often place the page number as a separate text object early in
    the page content stream, causing fitz to return it before the body blocks even
    though it sits near the bottom of the page visually.  We detect it by position
    (bottom 20 % of page, horizontally centred) and content (1–3 pure digits).

    The threshold is 80 % (not the more conservative 87 %) because some PDFs render
    the page number at ~82–85 % of page height — still clearly in the footer area —
    and these would otherwise be misidentified as paragraph numbers and injected into
    the extracted text.  The "all trailing lines blank" check prevents false positives
    from genuine paragraph numbers that happen to appear near the bottom: a real
    paragraph would have body text on the lines that follow, while a page-number
    block has only blank continuation lines.
    """
    lines = block.get("lines", [])
    if not lines:
        return False
    # The page number must be the only non-blank content in the block.
    # Some PDF generators append blank lines after the number, producing a
    # 3-line block: ["11 ", " ", " "].  We allow that by checking that all
    # lines after the first are pure whitespace.
    first_spans = lines[0].get("spans", [])
    if len(first_spans) != 1:
        return False
    s = first_spans[0]
    if not re.match(r'^\d{1,3}$', s["text"].strip()):
        return False
    x, y = s["bbox"][0], s["bbox"][1]
    if not (y > page_height * 0.80 and x > page_width * 0.3):
        return False
    # Verify that any remaining lines are blank (no real content)
    for line in lines[1:]:
        if any(sp["text"].strip() for sp in line.get("spans", [])):
            return False
    return True


def _collapse_spaced_letters(text: str) -> str:
    """Collapse letter-spaced headings: 'D Ó M S O R Ð:' → 'DÓMSORÐ:'.

    PDF files sometimes store heading characters with wide letter-spacing,
    causing fitz to emit each character as a separate 'word'.  If every
    whitespace-delimited token contains at most one alphabetic character
    (plus possible punctuation such as ':') and there are at least 3 tokens,
    we collapse them by removing the spaces.  This avoids false positives on
    normal phrases like 'A og B' (where 'og' has two alpha chars).
    """
    tokens = text.split()
    if len(tokens) >= 3 and all(sum(c.isalpha() for c in t) <= 1 for t in tokens):
        return "".join(tokens)
    return text


def _collapse_spaced_letters_inline(text: str) -> str:
    """Collapse letter-spaced sequences anywhere in body text.

    Some older lower-court PDFs render "DÓMSORÐ:" and "ÚRSKURÐARORÐ:" with
    letter-spacing so pdfplumber/fitz reads them as "D Ó M S O R Ð:" and
    "Ú R S K U R Ð A R O R Ð:".  When these appear inline (not in a dedicated
    Bold heading block) they slip through the heading-level collapse.

    Detection: a sequence of 5+ single uppercase Icelandic chars separated by
    exactly one space, optionally followed by ':'.  The 5-char minimum avoids
    false positives on short abbreviations (e.g. "A B C") while reliably
    matching DÓMSORÐ (7 chars) and ÚRSKURÐARORÐ (13 chars).
    """
    def _collapse(m: "re.Match") -> str:
        seq = m.group(0)
        tokens = seq.split()
        if all(sum(c.isalpha() for c in t) <= 1 for t in tokens):
            return "".join(tokens)
        return seq

    # Note: include Ð (U+00D0, eth) explicitly — it sits between the standard
    # A-Z range and the accented vowels, so it is NOT covered by [A-ZÁÉÍÓÚÝÞÆÖ].
    return re.sub(r'[A-ZÁÐÉÍÓÚÝÞÆÖ](?: [A-ZÁÐÉÍÓÚÝÞÆÖ]){4,}(?::)?', _collapse, text)


def _heading_marker(font: str, size: float, crop: "PdfCrop | None") -> str:
    """Return a markdown heading prefix if this font/size is configured as a heading, else ''."""
    if crop is None:
        return ""
    # Font-based (e.g. 'Bold' → '## ') — substring match on font name.
    # Italic variants are skipped unless the key itself specifies 'Ital', so
    # key='Bold' matches 'Times New Roman,Bold' and 'TimesNewRomanPS-BoldMT'
    # but NOT 'Times New Roman,BoldItalic' or 'TimesNewRomanPS-BoldItal'.
    for key, marker in (crop.heading_fonts or {}).items():
        if key in font:
            if "Ital" in font and "Ital" not in key:
                continue  # skip italic unless key explicitly targets italic
            return marker
    # Size-based (e.g. 20.0pt → '# ')
    for size_key, marker in (crop.heading_sizes or {}).items():
        if abs(size - size_key) < 0.5:
            return marker
    return ""


def _group_by_y(lines: list, tol: float = 3.0) -> list[list]:
    """Group non-empty fitz lines by shared Y-top coordinate (within *tol* pt).

    Returns a list of groups sorted by ascending Y.  Each group is a list of
    lines whose ``bbox[1]`` values are within *tol* of the group's anchor (the
    first line in the group by Y order).  Groups with a single line represent
    ordinary body rows; groups with ≥ 2 lines represent table columns that
    share the same vertical position.
    """
    nonempty = sorted(
        (l for l in lines if any(s["text"].strip() for s in l.get("spans", []))),
        key=lambda l: l["bbox"][1],
    )
    groups: list[list] = []
    for line in nonempty:
        if groups and abs(line["bbox"][1] - groups[-1][0]["bbox"][1]) <= tol:
            groups[-1].append(line)
        else:
            groups.append([line])
    return groups


def _has_column_gap(group: list, min_gap: float = 20.0) -> bool:
    """Return True if sorted-by-X lines have at least one inter-column gap ≥ *min_gap* pt.

    Distinguishes genuine table columns (large visual white-space gaps, ≥ 20 pt)
    from individual words that a PDF generator stored as separate fitz "lines" all
    at the same Y — word-spacing in 10-12 pt fonts is typically 5–17 pt.
    """
    cols = sorted(group, key=lambda l: l["bbox"][0])
    for i in range(1, len(cols)):
        x_end_prev = cols[i - 1]["bbox"][2]    # right edge of previous column
        x_start_curr = cols[i]["bbox"][0]      # left edge of current column
        if x_start_curr - x_end_prev >= min_gap:
            return True
    return False


def _md_table(rows: list[list[str]]) -> str:
    """Format a list of cell-lists as Markdown pipe-table rows.

    Each cell-list produces one ``| c1 | c2 | … |`` line.  The separator row
    (``| --- | --- | …``) is NOT inserted here — it is added by the
    post-processing step :func:`_insert_md_table_separators` which runs on the
    fully assembled page text and therefore handles both single-row-per-block
    (no-border) tables and all-rows-in-one-block (bordered) tables correctly.

    If *rows* is empty an empty string is returned.
    """
    if not rows:
        return ""
    return "\n".join("| " + " | ".join(cells) + " |" for cells in rows)


def _insert_md_table_separators(text: str) -> str:
    """Post-process assembled PDF text: insert Markdown table separator rows.

    Scans *text* line by line.  Whenever a sequence of consecutive
    ``| … |`` lines is encountered the first line is treated as the header;
    if the immediately following line is not already a separator
    (``| --- | … |``) one is inserted automatically.

    This approach works for:
    • **Bordered tables** (all rows in one fitz/pdfplumber block): the rows
      arrive already joined; the first row gets a separator inserted once.
    • **No-border tables** (each visual row is a separate fitz block): rows
      are joined with ``\\n`` by the block-assembly logic; the same first-row
      detection adds the separator only after the header, not after every row.
    """
    lines = text.split("\n")
    out: list[str] = []
    in_table = False
    for i, line in enumerate(lines):
        is_row = line.startswith("| ") and line.endswith(" |")
        if is_row:
            out.append(line)
            if not in_table:
                # First row of a new table — insert separator if next line isn't one.
                next_line = lines[i + 1] if i + 1 < len(lines) else ""
                if not next_line.startswith("| ---"):
                    ncols = len([x for x in line.split("|") if x.strip()])
                    out.append("| " + " | ".join("---" for _ in range(ncols)) + " |")
            in_table = True
        else:
            in_table = False
            out.append(line)
    return "\n".join(out)


def _block_to_text(block: dict, crop: "PdfCrop | None") -> str | None:
    """Convert a single fitz dict-mode block to a normalised text string.

    Return values:
    - ``'## heading'``          — single-line Bold heading block
    - ``'## heading\\n\\nbody'``  — Bold heading on first line, body on rest
    - ``'### I\\n\\nbody'``       — centred Roman-numeral section header
    - ``'\\n\\nbody'``            — first-line-indented paragraph (new-para signal)
    - ``'N. body…'``             — left-margin paragraph number with period
    - ``'body…'``                — regular body block
    - ``None``                   — image, empty, or page-footer number block
    """
    if block.get("type") != 0:  # type 1 = image, skip
        return None
    lines = block.get("lines", [])
    if not lines:
        return None

    # Skip any leading blank lines in the block.  Some PDFs emit an empty
    # italic-space span as the very first line (e.g. lower-court sections that
    # add a leading superscript / footnote marker).  Without this skip the whole
    # block would be discarded even though subsequent lines have real content.
    first_spans: list = []
    first_nonempty: list = []
    lines_start = 0
    for lines_start, _line in enumerate(lines):
        first_spans = _line.get("spans", [])
        first_nonempty = [s for s in first_spans if s["text"].strip()]
        if first_nonempty:
            break
    else:
        return None  # every line is empty
    if lines_start:
        lines = lines[lines_start:]

    first_span = first_nonempty[0]

    # ── Detect table layout ───────────────────────────────────────────────────
    # When multiple fitz "lines" within a block share the same Y coordinate
    # (within 3 pt) the PDF is encoding a table row — each column is a separate
    # line at the same vertical position rather than a new text row.
    #
    # Two table styles seen in lower-court documents:
    #   • No-border tables: each visual row is a separate fitz block; the block
    #     has 2–4 same-Y lines (one per column).
    #   • Bordered tables: all rows packed into one fitz block; pairs/groups of
    #     same-Y lines form one row each.
    #
    # Guard: some PDFs store each word of a paragraph as a separate fitz "line"
    # at the same Y (word-spacing layout).  We require at least one column gap
    # ≥ 20 pt — normal word-spacing is 5–17 pt; real column gaps are ≥ 20 pt.
    #
    # We MUST check for tables BEFORE the heading check because a bold column
    # header (e.g. "Skýring" in Times New Roman,Bold) would otherwise be
    # incorrectly promoted to a '##' heading.
    #
    # Mixed blocks: some large blocks contain both regular paragraph text
    # (single-column rows) and a table (multi-column rows) — e.g. a 92-line
    # block whose first 80 lines are paragraphs and whose last 12 are a cost
    # breakdown table.  Treating the whole block as a table wraps the paragraph
    # text in spurious pipe characters.  We fix this by locating the first and
    # last multi-column group and only treating that region as a table; lines
    # before the first table row are returned as regular body text.
    #
    # _last_t uses a wider criterion than _first_t: once a table region starts
    # (first gap ≥ 20 pt column), we extend it to include any subsequent group
    # that has ≥ 2 fitz lines at the same Y even if the inter-column gap is
    # < 20 pt.  This handles summary rows like "Alls: | 10.279.088 krónur."
    # where two bold adjacent cells have a narrow gap (~8 pt) but are clearly
    # part of the same table, not separate paragraph text.
    _y_groups = _group_by_y(lines)
    _multicol_idx = [i for i, g in enumerate(_y_groups) if len(g) >= 2 and _has_column_gap(g)]
    if _multicol_idx:
        _first_t = _multicol_idx[0]
        # Extend to the last group that has ≥ 2 fitz lines (narrow-gap rows included)
        _last_t = max(i for i, g in enumerate(_y_groups) if len(g) >= 2)
        # Y-gap extension: pull in any immediately-following single-column groups
        # that are within 25 pt of the last table row — these are continuation
        # cells (e.g. "sakarefnis." wrapping from a roman-numeral list item)
        # rather than the start of a new paragraph after the table.
        if _last_t + 1 < len(_y_groups):
            _prev_grp_y = _y_groups[_last_t][0]["bbox"][1]
            for _ext_i in range(_last_t + 1, len(_y_groups)):
                _ext_grp = _y_groups[_ext_i]
                _curr_grp_y = _ext_grp[0]["bbox"][1]
                if _curr_grp_y - _prev_grp_y > 25:
                    break
                _last_t = _ext_i
                _prev_grp_y = _curr_grp_y
        # Lines in the table region (first to last multi-row group, inclusive)
        _table_groups = _y_groups[_first_t : _last_t + 1]
        _tcells: list[list[str]] = []
        for _grp in _table_groups:
            _cols = sorted(_grp, key=lambda l: l["bbox"][0])
            _cells = [
                " ".join(s["text"].strip() for s in _c.get("spans", []) if s["text"].strip())
                for _c in _cols
            ]
            _cells = [c for c in _cells if c]
            if _cells:
                if len(_cells) == 1 and _tcells:
                    # Single-cell continuation: merge into the last cell of the
                    # previous row (e.g. "starfssviðs hennar;" wrapping after
                    # "i)  | [text]" in a roman-numeral list table).
                    _tcells[-1] = _tcells[-1][:-1] + [
                        _tcells[-1][-1].rstrip() + " " + _cells[0].lstrip()
                    ]
                else:
                    _tcells.append(_cells)
        if _tcells:
            _result_parts: list[str] = []
            # Pre-table lines → regular body text
            _pre_lines = [l for g in _y_groups[:_first_t] for l in g]
            if _pre_lines:
                _pre_body = _build_body_from_lines(_pre_lines, crop)
                if _pre_body:
                    _result_parts.append(_pre_body)
            _result_parts.append(_md_table(_tcells))
            # Post-table lines → regular body text (rare)
            _post_lines = [l for g in _y_groups[_last_t + 1 :] for l in g]
            if _post_lines:
                _post_body = _build_body_from_lines(_post_lines, crop)
                if _post_body:
                    _result_parts.append(_post_body)
            return "\n\n".join(_result_parts)

    # ── Detect heading (single-line or multi-line block) ──────────────────────
    # Multi-line case: some lower-court PDFs put a heading (e.g. the spaced
    # "Ú R S K U R Ð A R O R Ð:") on the first line of a block whose remaining
    # lines contain the verdict body.  We handle both cases here.
    marker = _heading_marker(first_span.get("font", ""), first_span.get("size", 0), crop)
    if marker:
        heading_texts = [s["text"].strip().replace('\xa0', ' ').strip() for s in first_spans if s["text"].strip()]
        heading = _collapse_spaced_letters(" ".join(heading_texts))
        if len(lines) == 1:
            return marker + heading
        # Do NOT pass crop here: we are inside a heading block and the
        # remaining lines are its continuation body, not independent headings.
        # Passing crop would promote party-name lines in preamble blocks to '##'.
        body = _build_body_from_lines(lines[1:])
        # A section heading and the first numbered paragraph may be merged into
        # the same fitz block (heading on L0, "N body…" on L1).  Apply the
        # same margin-number period insertion that the non-heading path uses.
        # We match one-or-more SPACES only (not \s which includes newlines).
        body = re.sub(r'^(\d{1,3}) +(?=\S)', r'\1. ', body)
        return f"{marker}{heading}\n\n{body}" if body else marker + heading

    # ── Detect centred Roman-numeral section header (I, II, III…) ─────────────
    # Lower-court bodies sometimes use a centred Roman numeral as a divider.
    # Signature: first line is a single pure-Roman span; the body text begins on
    # a subsequent line significantly to the LEFT (delta x > 100 pt).
    #
    # A centred paragraph number ("1 ") may appear between the Roman numeral and
    # the body text (both at the same centred x position).  We scan forward past
    # such intermediate lines to find where the body actually begins.
    if len(lines) > 1 and len(first_nonempty) == 1:
        if _ROMAN_NUM_RE.match(first_span["text"].strip()):
            roman_x = first_span["bbox"][0]
            body_line_start = 0  # 0 means "not found"
            for _i in range(1, len(lines)):
                _spans_i = lines[_i].get("spans", [])
                _nonempty_i = [s for s in _spans_i if s["text"].strip()]
                if _nonempty_i and _nonempty_i[0]["bbox"][0] < roman_x - 100:
                    body_line_start = _i
                    break
            if body_line_start:
                roman = first_span["text"].strip()
                # Any lines between the Roman numeral and the body (e.g. a
                # centred paragraph number "1 " or an italic subtitle like
                # "Málsatvik") become part of the body prefix.
                # No crop here — Roman-numeral section opener; body lines
                # are not expected to contain further inline headings.
                pre = _build_body_from_lines(lines[1:body_line_start])
                body = _build_body_from_lines(lines[body_line_start:], crop)
                if pre:
                    pre_stripped = pre.strip()
                    if len(pre_stripped) <= 60 and "\n\n" not in pre_stripped:
                        # Short single-phrase subtitle (e.g. "Málsatvik",
                        # "Helstu málsástæður") → promote to #### subheading.
                        body = (
                            f"#### {pre_stripped}\n\n{body}".strip()
                            if body
                            else f"#### {pre_stripped}"
                        )
                    else:
                        body = (pre_stripped + " " + body).strip() if body else pre_stripped
                # Insert period after any leading margin number ("1 Í" → "1. Í").
                # MULTILINE so it fires on saw_blank-introduced paragraphs too.
                body = re.sub(r'^(\d{1,3}) +(?=\S)', r'\1. ', body, flags=re.MULTILINE)
                return f"### {roman}\n\n{body}" if body else f"### {roman}"

    # ── Detect margin number (paragraph number) ───────────────────────────────
    # Case A: first span is a pure digit.  Two layout variants:
    #
    #   Left-margin layout (used by both Landsréttur upper-court and most
    #     lower-court embeds):
    #     x < 115 pt.  No size constraint — different lower-court PDFs use
    #     different sizes (sz≈10 for older, sz≈12 for newer), and the
    #     combination of a pure digit, left-margin x position and a single
    #     non-empty span is already specific enough to paragraph numbers.
    #
    #   Centred layout (lower-court embeds where the paragraph number is
    #     typeset at the horizontal centre of the page and body text follows
    #     left-aligned on subsequent lines):
    #     num_x >> body_x — the body's first non-blank line starts more than
    #     100 pt to the LEFT of the paragraph number.  No size constraint —
    #     different lower-court PDFs use different sizes (sz≈10 or sz≈12).
    #     The structural guards (single non-empty span on L0, pure digit,
    #     body starting far to the left) are sufficient without a size check.
    #     Page-footer numbers are always filtered by _is_page_footer_num
    #     before this point, so no false positives from footer page numbers.
    #
    # We check len(first_nonempty) == 1 (not len(first_spans) == 1) so that
    # blocks whose first line has [margin_num, blank_arial_space] — two spans
    # but only one non-empty — are still recognised as Case A.
    margin_num: str | None = None
    body_start_line = 0
    _is_margin_num_candidate = (
        len(first_nonempty) == 1
        and _MARGIN_NUM_RE.match(first_span["text"].strip())
    )
    if _is_margin_num_candidate and first_span["bbox"][0] < 115:
        # Left-margin layout
        margin_num = first_span["text"].strip()
        body_start_line = 1
    elif _is_margin_num_candidate and len(lines) > 1:
        # Centred layout: verify that at least one subsequent line has body text
        # starting more than 100 pt to the LEFT of the paragraph number.  We scan
        # ALL remaining lines (not just lines[1]) because an intermediate centred
        # subtitle (e.g. italic section title "Sóknaraðili BRU SICAR" at x≈244)
        # may appear between the paragraph number (x≈290) and the body (x≈82).
        _num_x = first_span["bbox"][0]
        for _l in lines[1:]:
            _ne = [s for s in _l.get("spans", []) if s["text"].strip()]
            if _ne and _ne[0]["bbox"][0] < _num_x - 100:
                margin_num = first_span["text"].strip()
                body_start_line = 1  # always skip only L0; keep subtitle in body
                break

    # ── Join body lines with de-hyphenation ───────────────────────────────────
    body = _build_body_from_lines(lines[body_start_line:], crop)

    # ── Normalise split-span "N . text" → "N. text" ──────────────────────────
    # Some PDFs encode a section heading as two adjacent fitz spans on the same
    # line: the number in one span ("  3") and the period+text in another
    # (". Niðurstaða ").  _build_body_from_lines joins them with a space →
    # "3 . Niðurstaða".  Collapse the spurious space before the period so the
    # body starts with "3. Niðurstaða" and is recognised as a properly numbered
    # section rather than triggering a missing-period warning.
    # Guard: require a space after the period ("N . ") to avoid collapsing
    # decimal numbers like "3 .5".
    body = re.sub(r'^(\d{1,3}) \. ', r'\1. ', body)

    if not body:
        # Standalone margin-number block (the body is in the following block).
        # Return with a leading \n\n (new-paragraph signal) and a trailing period
        # so smart-join can recognise this as the start of a new paragraph and
        # attach the next block as its body rather than opening another \n\n.
        return f"\n\n{margin_num}." if margin_num else None

    if margin_num:
        # Case A: standalone number detected — add period separator
        return f"{margin_num}. {body}"

    # Case B: combined "3 Ákærði..." span — fitz merged the paragraph number
    # with the body text into a single span.  Insert a period when the first
    # span is positioned in the left margin.  No size constraint — different
    # lower-court PDFs use different sizes (sz≈10 older, sz≈12 newer) for
    # paragraph numbers, and the x-position guard is sufficient.
    # We match one-or-more SPACES (not \s) so that "\n\n" paragraph breaks
    # already present in the body are never accidentally collapsed to ". ".
    # Lookahead uses \S so redacted content like "[…]" is also handled.
    # MULTILINE: saw_blank and Y-gap detection in _build_body_from_lines can
    # introduce \n\n mid-block; ^ must match those internal line starts too.
    if first_span["bbox"][0] < 115:
        body = re.sub(r'^(\d{1,3}) +(?=\S)', r'\1. ', body, flags=re.MULTILINE)

    # ── Detect paragraph / structural-unit boundary ──────────────────────────
    # Some lower-court PDFs use neither blank lines nor paragraph numbers to
    # separate paragraphs and section headings.  Three encoding styles:
    #
    # Style A (x-coordinate indent): the first line's x is ≥ 20 pt to the
    #   RIGHT of the continuation lines.  Typical in some court PDF styles.
    #
    # Style B (whitespace indent): the first span's RAW text starts with ≥ 4
    #   spaces.  Word-generated embeds encode the indent as literal space
    #   characters, not as an x-position shift.  Must be checked before
    #   _build_body_from_lines strips the text.
    #   Example: 854/2019 lower court — every paragraph's first span starts
    #   with 8 spaces; continuation lines have no leading spaces.
    #
    # Style C (centred single-line block): a standalone short line (≤ 60 chars)
    #   whose x position is > 150 pt — i.e. noticeably right of the normal body
    #   margin (~82–96 pt).  Captures section markers like "Niðurstaða" and
    #   Roman-numeral dividers ("I.", "II.") that are typeset centred on the
    #   page without bold formatting.
    #
    # All styles prefix the assembled body with "\n\n" so the smart-join phase
    # in _pdf_bytes_to_text treats this block as a new paragraph or heading.
    if not margin_num:
        _para_start = False

        # Style F — single-line Roman-numeral block → ## section heading.
        # Héraðsdómstólar PDFs typeset charge/section headers (I, II, III or I.,
        # II.) as standalone 1-line blocks in regular (non-bold) font at the body
        # margin.  Style B would add \n\n but not promote to a heading.
        if len(lines) == 1 and len(first_nonempty) == 1:
            _body_strip = body.strip()
            if re.match(r'^[IVX]+\.?\s*$', _body_strip):
                return "## " + _body_strip.rstrip('.') + '.'

        # Style A — x-coordinate delta between first and second line
        if len(lines) > 1:
            second_spans = lines[1].get("spans", [])
            if second_spans and first_span["bbox"][0] - second_spans[0]["bbox"][0] > 20:
                _para_start = True
        # Style B — leading whitespace in raw span text (ASCII or non-breaking spaces)
        # Old héraðsdómar PDFs use \xa0 (non-breaking space) as paragraph indent.
        if not _para_start and (first_span["text"].startswith("    ") or first_span["text"].startswith("\xa0\xa0\xa0\xa0")):
            _para_start = True
        # Style C — centred/right-shifted single-line block
        if not _para_start and len(lines) == 1 and first_span["bbox"][0] > 150 and len(body.strip()) <= 60:
            _para_start = True
        if _para_start:
            body = "\n\n" + body

    # Style D — lower-court heading in plain (non-bold) font.
    # Some PDFs typeset "Úrskurður Héraðsdóms Reykjavíkur miðvikudaginn …"
    # in regular Times New Roman at x≈148 — just under Style C's x>150
    # threshold and/or over its ≤60-char limit.  _heading_marker returns None
    # so the block would be absorbed as body text and _split_lower_court would
    # never find it.  Detect the pattern here and force a ## heading so the
    # split can work regardless of font or x-position.
    if (
        not margin_num
        and len(lines) == 1
        and _LOWER_COURT_BLOCK_RE.match(body.strip())
    ):
        return "## " + body.strip()

    return body


_PLUMBER_PARA_NUM_RE = re.compile(
    r'^(\d{1,3}) +(?=[^\W\d_\s])',  # "35 Í..." → "35. Í..."  (letter-only lookahead)
    re.UNICODE,
)


def _render_plumber_table(rows: list) -> str:
    """Render a pdfplumber table (list of row-lists) as pipe-separated text.

    Multi-line cell text is collapsed to a single space.  None and
    whitespace-only cells are dropped.  Rows where all cells are empty
    after cleaning are skipped.

    Paragraph numbers in the first cell of each row receive an inserted period
    to match the normalisation applied by *_block_to_text* (Case B): e.g. a cell
    extracted by pdfplumber as ``'35 Í 8. mgr…'`` becomes ``'35. Í 8. mgr…'``.
    The lookahead ``[^\\W\\d_\\s]`` restricts the substitution to cells whose
    first non-space character after the digits is a Unicode letter — this avoids
    touching numeric cells like ``'75 95.000'`` where the digit is followed by
    another digit.
    """
    all_cells: list[list[str]] = []
    for row in rows:
        cells = []
        for c in row:
            if c is None:
                continue
            t = str(c).replace("\n", " ").strip()
            if t:
                cells.append(t)
        if cells:
            # Apply paragraph-number period to first cell only (left margin).
            cells[0] = _PLUMBER_PARA_NUM_RE.sub(r'\1. ', cells[0])
            all_cells.append(cells)
    return _md_table(all_cells)


def _exclude_table_lines(
    block: dict,
    t_top: float,
    t_bot: float,
    tol_top: float = 8.0,
    tol_bot: float = 2.0,
) -> dict | None:
    """Return a copy of *block* with all lines inside the table Y zone removed.

    Lines whose Y-top falls within [t_top - tol_top, t_bot + tol_bot] are
    considered inside the table and are excluded.  Returns None if no lines
    remain.

    Asymmetric tolerances:
      tol_top (8 pt) — captures blocks whose first line (often blank/space) sits
        slightly above the drawn top border due to PDF layout conventions.
      tol_bot (2 pt) — handles only sub-pixel drawing coordinate imprecision;
        a small value prevents absorbing the first line of text that follows
        immediately after the table's bottom border (which can be as close as
        ~3–4 pt away in tightly-laid-out documents).
    """
    kept = [
        l for l in block.get("lines", [])
        if not (t_top - tol_top <= l["bbox"][1] <= t_bot + tol_bot)
    ]
    if not kept:
        return None
    new_block = dict(block)
    new_block["lines"] = kept
    ys = [l["bbox"][1] for l in kept]
    ye = [l["bbox"][3] for l in kept]
    new_block["bbox"] = (block["bbox"][0], min(ys), block["bbox"][2], max(ye))
    return new_block


def _pdf_bytes_to_text(pdf_bytes: bytes, config: SourceConfig) -> str | None:
    """
    Extract structured text from PDF bytes using span-level font information.

    Features:
    - Crop header/footer per PdfCrop settings
    - Font-based heading detection (heading_fonts) → '## heading'
    - Size-based heading detection (heading_sizes) as fallback
    - Margin paragraph numbers merged with their text ('6 Áfrýjandi var…')
    - PDF word-wrap de-hyphenation ('flugumferðar-\\nstjórar' → 'flugumferðarstjórar')
    - pdfplumber table extraction for bordered tables (takes precedence over
      fitz-based same-Y-line detection when the page has drawn border lines)
    """
    crop = config.pdf_crop
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        return None

    # Pre-scan: find which page numbers have drawings (potential bordered tables).
    # This is cheap with fitz and lets us avoid opening pdfplumber for PDFs that
    # have no bordered tables at all.
    _MIN_DRAWINGS_FOR_TABLE = 10  # fewer than this almost never forms a full table
    pages_with_drawings: set[int] = set()
    for pn, pg in enumerate(doc):
        if len(pg.get_drawings()) >= _MIN_DRAWINGS_FOR_TABLE:
            pages_with_drawings.add(pn)

    # Open pdfplumber only when at least one page has potential bordered tables.
    # Failures are non-fatal — we fall back to pure fitz extraction.
    plumber_doc: pdfplumber.PDF | None = None
    if pages_with_drawings:
        try:
            plumber_doc = pdfplumber.open(io.BytesIO(pdf_bytes))
        except Exception:
            pass

    all_blocks: list[str] = []
    for page_num, page in enumerate(doc):
        page_height = page.rect.height
        page_width  = page.rect.width
        header = crop.header_pt if crop else 0.0
        footer = crop.footer_pt if crop else 0.0
        if crop and crop.skip_header_on_first and page_num == 0:
            header = 0.0

        clip = fitz.Rect(0, header, page_width, page_height - footer)
        d = page.get_text("dict", clip=clip)

        # ── pdfplumber bordered-table detection ───────────────────────────────
        # For pages with drawn border lines, pdfplumber can reconstruct table
        # rows that span multiple fitz blocks (e.g. multi-line description +
        # separate amount block at the same Y).  We collect table Y-ranges here
        # and later skip / replace fitz blocks that fall within them.
        table_regions: list[tuple[float, float, str]] = []
        if plumber_doc is not None and page_num in pages_with_drawings:
            try:
                plumber_page = plumber_doc.pages[page_num]
                for ft in plumber_page.find_tables():
                    tdata = ft.extract()
                    if not tdata:
                        continue
                    # Skip bordered text boxes masquerading as tables.
                    # A real data table has ≥ 2 non-empty cells in a meaningful
                    # fraction of its rows.  A bordered paragraph box may have
                    # 1–2 "accidentally" multi-cell rows (paragraph text split
                    # mid-word by the column detector) but nearly all rows are
                    # single-column.  We require ≥ 25 % of non-empty rows to
                    # have ≥ 2 non-empty cells.  This passes real tables (all or
                    # most rows have 2+ cells) while rejecting text boxes where
                    # only 1–2 out of 25–50 rows are accidentally multi-cell.
                    nonempty_row_count = sum(
                        1 for row in tdata
                        if any(c is not None and str(c).strip() for c in row)
                    )
                    multicol_rows = sum(
                        1 for row in tdata
                        if sum(1 for c in row if c is not None and str(c).strip()) >= 2
                    )
                    if nonempty_row_count == 0:
                        continue
                    if multicol_rows / nonempty_row_count < 0.25:
                        continue
                    ttxt = _render_plumber_table(tdata)
                    if ttxt:
                        # ft.bbox = (x0, top, x1, bottom) in page coords from top
                        table_regions.append((ft.bbox[1], ft.bbox[3], ttxt))
            except Exception:
                pass

        # ── Per-page block collection with Y position ─────────────────────────
        # Collect (y_top, text) pairs so that pdfplumber table text can be
        # inserted at the correct position when blocks are sorted by Y.
        page_items: list[tuple[float, str]] = []
        tables_inserted: set[int] = set()

        for block in d.get("blocks", []):
            if _is_page_footer_num(block, page_height, page_width):
                continue

            block_y_top = block["bbox"][1]
            block_y_bot = block["bbox"][3]

            # Check whether this block overlaps with any pdfplumber table zone.
            # We use a Y-range overlap test (not just "top inside zone") because
            # a block may start slightly above the table's drawn top border yet
            # have most of its content inside the table (e.g. a block whose first
            # line is an empty/space span that sits 10–15 pt above the border).
            effective_block = block
            for ti, (ty0, ty1, ttxt) in enumerate(table_regions):
                # Overlap: block bottom > zone top  AND  block top < zone bottom
                if block_y_bot > ty0 - 8 and block_y_top < ty1 + 8:
                    # Block overlaps the table zone.
                    # Insert the pdfplumber table text once at this zone.
                    if ti not in tables_inserted:
                        page_items.append((ty0, ttxt))
                        tables_inserted.add(ti)
                    # Strip lines that fall within the table zone; the remainder
                    # (lines above or below the table) is processed normally.
                    effective_block = _exclude_table_lines(block, ty0, ty1)
                    break

            if effective_block is None:
                continue
            t = _block_to_text(effective_block, crop)
            if t:
                page_items.append((effective_block["bbox"][1], t))

        # Sort page items by Y so pdfplumber tables appear in reading order
        # alongside fitz blocks even when their insertion points differ.
        page_items.sort(key=lambda x: x[0])
        for _, t in page_items:
            all_blocks.append(t)

    if plumber_doc is not None:
        plumber_doc.close()
    doc.close()
    if not all_blocks:
        return None

    # ── Merge standalone Roman-numeral blocks with following italic title ─────
    # Some lower-court PDFs (e.g. Héraðsdómur embeds in Landsréttur) typeset
    # section headings as two consecutive fitz blocks:
    #   Block A:  "I."  (centred, regular Times New Roman, size 10)  → '\n\nI.'
    #   Block B:  "Helstu málsatvik"  (italic, left-margin)           → plain text
    # _block_to_text cannot merge them (no lookahead), so we do it here.
    # Guard: next block must be a short single-line text (≤ 80 chars, no \n)
    # that is NOT already a heading — avoids swallowing body paragraphs.
    _LONE_ROMAN_RE = re.compile(r'^[\n]*([IVX]+)\.\s*$')
    _merged_blocks: list[str] = []
    _bi = 0
    while _bi < len(all_blocks):
        _rm = _LONE_ROMAN_RE.match(all_blocks[_bi])
        if _rm and _bi + 1 < len(all_blocks):
            _nxt = all_blocks[_bi + 1]
            if (
                _nxt
                and not _nxt.startswith("#")
                and "\n" not in _nxt.strip()
                and len(_nxt.strip()) <= 80
            ):
                _merged_blocks.append(f"\n\n### {_rm.group(1)}. {_nxt.strip()}")
                _bi += 2
                continue
        _merged_blocks.append(all_blocks[_bi])
        _bi += 1
    all_blocks = _merged_blocks

    # Smart join: blocks that begin a new heading, numbered paragraph, or
    # first-line-indented paragraph get a blank-line separator; all other blocks
    # are continuation text joined with a space.
    # Block signals:
    #   starts with '#'  → heading (## or ###) — always separated
    #   starts with '\n' → first-line-indent paragraph — carries its own \n\n
    #   matches _NEW_PARA_RE → numbered paragraph
    #   contains ' | '  → table row (produced by _block_to_text table path)
    _STANDALONE_NUM_RE = re.compile(r'^\d{1,3}\.$')
    _TROW_SEP = " | "  # sentinel present in every table-row block

    result = all_blocks[0]
    for curr in all_blocks[1:]:
        prev_last = result.rsplit("\n", 1)[-1]  # last line of accumulated text

        curr_trow = _TROW_SEP in curr
        prev_trow = _TROW_SEP in prev_last

        # Special case: previous line is a standalone margin number ("9.").
        # The body of that paragraph is in the next block.  Glue them with a
        # space rather than inserting another \n\n (which would leave "9." as
        # its own isolated "paragraph").
        # Guard: do NOT glue if the next block is ITSELF a new numbered para
        # ("20. Ákærða...") or a heading — those must stay as separate paragraphs.
        if (
            _STANDALONE_NUM_RE.match(prev_last)
            and not _NEW_PARA_RE.match(curr)
            and not curr.startswith("#")
        ):
            result = result.rstrip("\n") + " " + curr.lstrip()

        elif curr_trow and prev_trow:
            # Consecutive table rows → single newline (keep rows together)
            result += "\n" + curr

        elif curr_trow or prev_trow:
            # Transition between table block and normal text → blank line
            result += "\n\n" + curr.lstrip("\n")

        elif (
            curr.startswith("#")       # ## or ### heading / Roman section
            or prev_last.startswith("#")
            or _NEW_PARA_RE.match(curr)
            or curr.startswith("\n")   # first-line-indent paragraph signal
        ):
            result += "\n\n" + curr.lstrip("\n")
        elif result.endswith("-"):
            # De-hyphenate across block boundary
            result = result[:-1] + curr
        else:
            result += " " + curr.lstrip()

    # Collapse any letter-spaced sequences that appeared inline in body text
    # (e.g. "D Ó M S O R Ð:" → "DÓMSORÐ:" in older lower-court PDFs).
    result = _collapse_spaced_letters_inline(result)

    # Fix split-word artifacts caused by font-encoding issues in some PDFs.
    # The PDF content stream encodes "Dómur" as two runs "Dó" + " mur" and
    # "Úrskurður" similarly, producing heading lines like:
    #   "## Dó mur Héraðsdóms Reykjavíkur  2. desember 2024"
    # These broken words prevent _LOWER_COURT_SPLIT_RE from finding the split
    # point (lower_body_text ends up NULL).  Fix the two known problem words
    # before the text is returned and used by _split_lower_court.
    result = re.sub(r'D\s*ó\s*m\s*u\s*r', 'Dómur', result)
    result = re.sub(r'Ú\s*r\s*s\s*k\s*u\s*r\s*ð\s*u\s*r', 'Úrskurður', result)
    # "H é r a ð s d ó m u r" → "Héraðsdómur": spaced-letter artefact.
    result = re.sub(r'H\s*é\s*r\s*a\s*ð\s*s\s*d\s*ó\s*m\s*u\s*r', 'Héraðsdómur', result)
    # "Héaðsdóm" → "Héraðsdóm": missing 'r' (PDF/OCR artefact).
    result = re.sub(r'Héaðsdóm', 'Héraðsdóm', result)
    # "Héraðdóm" → "Héraðsdóm": missing connector 's' (PDF/OCR artefact).
    result = re.sub(r'Héraðdóm', 'Héraðsdóm', result)
    # "Héraðdsóm" → "Héraðsdóm": transposed 'd' and 's' (PDF/OCR artefact).
    result = re.sub(r'Héraðdsóm', 'Héraðsdóm', result)
    # "Héraðsóm" → "Héraðsdóm": missing 'd' between 's' and 'ó'.
    result = re.sub(r'Héraðsóm', 'Héraðsdóm', result)
    # "Héraðsdom" → "Héraðsdóm": unaccented 'o' (glyph encoding artefact).
    result = re.sub(r'Héraðsdom', 'Héraðsdóm', result)
    # "Héraðssóm" → "Héraðsdóm": double 's', 'd' replaced by second 's'.
    result = re.sub(r'Héraðssóm', 'Héraðsdóm', result)
    # "Hérðasdóm" → "Héraðsdóm": 'a' and 'ð' transposed (chars 4-5 swapped).
    result = re.sub(r'Hérðasdóm', 'Héraðsdóm', result)
    # "Hérðasdsóm" → "Héraðsdóm": severely garbled ('a'↔'ð' swap + extra 's').
    result = re.sub(r'Hérðasdsóm', 'Héraðsdóm', result)
    # "H éraðsdóm" → "Héraðsdóm": space inserted after first letter.
    result = re.sub(r'\bH\s+éraðsdóm', 'Héraðsdóm', result)
    # "Hérað sdóm" → "Héraðsdóm": space between 'ð' and 's'.
    result = re.sub(r'Héra[ðd]\s+sdóm', 'Héraðsdóm', result)
    # "Úrskuður" → "Úrskurður": missing second 'r' (PDF/OCR artefact).
    result = re.sub(r'\bÚrskuður\b', 'Úrskurður', result)
    # Some PDFs use 'z' where Icelandic has 's' (font glyph substitution):
    # "marz" → "mars", "marz-apríl" → "mars-apríl" etc.
    result = re.sub(r'\bm\s*a\s*r\s*z\b', 'mars', result, flags=re.IGNORECASE)
    # Collapse any remaining multiple spaces within heading lines
    # (e.g. "Reykjavíkur  2. desember" → "Reykjavíkur 2. desember").
    result = re.sub(
        r'^(#{1,6} .+)$',
        lambda m: re.sub(r' {2,}', ' ', m.group(0)),
        result,
        flags=re.MULTILINE,
    )

    # Insert Markdown table separator rows (| --- | --- |) after the header
    # row of each pipe-table sequence.  Must run after full block assembly so
    # it sees consecutive table rows regardless of whether they came from a
    # single bordered-table block or multiple no-border single-row blocks.
    result = _insert_md_table_separators(result)

    result = result.replace('\xa0', ' ')  # normalize non-breaking spaces (old PDF encoding)
    result = result.replace('\x00', '')  # PostgreSQL TEXT forbids null bytes
    return re.sub(r"\n{3,}", "\n\n", result).strip() or None


def _pdf_b64_to_text(b64: str | None, config: SourceConfig) -> str | None:
    """Decode base64 pdfString and extract structured text via _pdf_bytes_to_text."""
    if not b64:
        return None
    try:
        pdf_bytes = base64.b64decode(b64)
    except Exception:
        return None
    return _pdf_bytes_to_text(pdf_bytes, config)


# ─── Per-source extractors ────────────────────────────────────────────────────

def _richtext_body(value: str | dict | None) -> str | None:
    """Parse richText from island.is — either HTML string or Contentful rich text dict."""
    if not value:
        return None
    if isinstance(value, str):
        text = _html_to_plain(value)
    elif isinstance(value, dict):
        content = value.get("document", {}).get("content")
        text = _rich_text_to_plain(content)
    else:
        return None
    if not text:
        return None
    # Normalise typos that appear in richText source data
    text = re.sub(r'\bÚskurðarorð\b', 'Úrskurðarorð', text)  # missing 'r'
    return text


def _extract_haestirettur(raw: dict, config: SourceConfig) -> dict:
    title = raw.get("title") or raw.get("caseTitle") or ""
    plf, dfd = _parse_parties_gegn(title)
    plain_body = _richtext_body(raw.get("richText")) or raw.get("text") or raw.get("content")
    if config.has_lower_court and plain_body:
        plain_body, lower_body = _split_lower_court(plain_body)
    else:
        lower_body = raw.get("lowerCourtText") or None
    # Correct API date if document text reveals it is wrong (mismatch only).
    document_date = _correct_date_if_wrong(
        _parse_icelandic_date(
            raw.get("verdictDate") or raw.get("date") or raw.get("dateOfRuling")
        ),
        plain_body,
    )
    return {
        "case_number": raw.get("caseNumber") or raw.get("id"),
        "document_date": document_date,
        "court": config.abbreviation,
        "verdict_type": (
            _detect_verdict_type(plain_body, raw.get("keywords") or [])
            or config.verdict_type_default
        ),
        "instance_tier": config.instance_tier,
        "case_type": raw.get("caseType") or _infer_hrd_lrd_case_type(
            raw.get("keywords"), plf
        ),
        "plaintiffs": plf or None,
        "defendants": dfd or None,
        "keywords": _keywords_from_content(
            raw.get("keywords") or raw.get("categories")
        ),
        "summary": (
            raw.get("presentings") or raw.get("abstract") or raw.get("summary") or None
        ),
        "body_text": plain_body or None,
        "lower_body_text": lower_body,
        "raw_api_data": raw,
    }


def _infer_hrd_lrd_case_type(
    keywords: list | None,
    plaintiffs: list[dict] | None,
) -> str:
    """Infer case_type from keywords and plaintiffs when API does not provide it.

    Kærumál keyword → Kært, absence → Áfrýjað.
    Ákæruvaldið as plaintiff → sakamál, otherwise → einkamál.
    """
    kw_lower = [k.lower() for k in (keywords or []) if isinstance(k, str)]
    has_kaermal = any("kærumál" in k for k in kw_lower)
    has_akaeruvalid = any(
        (p.get("name") or "").startswith("Ákæruvaldið")
        for p in (plaintiffs or [])
    )
    if has_kaermal and has_akaeruvalid:
        return "Kært sakamál"
    if not has_kaermal and has_akaeruvalid:
        return "Áfrýjað sakamál"
    if has_kaermal:
        return "Kært einkamál"
    return "Áfrýjað einkamál"


_LRD_CASE_TYPE_NORMALISE: dict[str, str] = {
    "Einkamál áfrýjað": "Áfrýjað einkamál",
    "Einkamál kært":    "Kært einkamál",
    "Sakamál áfrýjað":  "Áfrýjað sakamál",
    "Sakamál kært":     "Kært sakamál",
}


def _normalise_lrd_case_type(value: str | None) -> str | None:
    if not value:
        return None
    return _LRD_CASE_TYPE_NORMALISE.get(value, value)


def _extract_landsrettur(raw: dict, config: SourceConfig) -> dict:
    title = raw.get("title") or ""
    plf, dfd = _parse_parties_gegn(title)

    # Landsréttur: richText is always None — body comes from PDF
    full_pdf_text = _pdf_b64_to_text(raw.get("pdfString"), config)

    # Extract keywords from PDF preamble BEFORE stripping it, as fallback for
    # docs where the API returns keywords: [] (common in older 2018–2019 docs).
    api_keywords = _keywords_from_content(raw.get("keywords"))
    if not api_keywords and full_pdf_text:
        api_keywords = _keywords_from_pdf_preamble(full_pdf_text)

    # Correct API date if PDF preamble reveals it is wrong (mismatch only).
    document_date = _correct_date_if_wrong(
        _parse_icelandic_date(raw.get("verdictDate") or raw.get("date")),
        full_pdf_text,
    )

    plain_body = full_pdf_text

    # Strip preamble (court name, date, parties, keywords, abstract — already
    # captured in structured fields). Body begins at "Dómur/Úrskurður Landsréttar".
    if plain_body:
        m = _LANDSRETTUR_PREAMBLE_RE.match(plain_body)
        if m:
            plain_body = plain_body[m.end():]

    # Split Landsréttur body from embedded héraðsdómur at the end
    if config.has_lower_court and plain_body:
        plain_body, lower_body = _split_lower_court(plain_body)
    else:
        lower_body = None

    return {
        "case_number": raw.get("caseNumber"),
        "document_date": document_date,
        "court": config.abbreviation,
        "verdict_type": (
            _detect_verdict_type(plain_body, raw.get("keywords") or [])
            or config.verdict_type_default
        ),
        "instance_tier": config.instance_tier,
        "case_type": _normalise_lrd_case_type(raw.get("caseType")) or _infer_hrd_lrd_case_type(
            raw.get("keywords"), plf
        ),
        "plaintiffs": plf or None,
        "defendants": dfd or None,
        "keywords": api_keywords,
        "summary": raw.get("presentings") or None,
        "body_text": plain_body or None,
        "lower_body_text": lower_body,
        "raw_api_data": raw,
    }


_HERADSDOMUR_CASE_TYPE: dict[str, str] = {
    "A": "Aðfarabeiðnir",
    "B": "Bráðabirgðaforsjá og farbann",
    "D": "Opinber skipti",
    "E": "Einkamál",
    "I": "Beiðni um endurupptöku",
    "K": "Ágreiningsmál v/kyrrsetningar og lögbanns",
    "L": "Lögræðismál",
    "M": "Matsmál",
    "Q": "Ágreiningsmál v/opinberra skipta",
    "R": "Rannsóknarmál",
    "S": "Sakamál",
    "T": "Ágreiningsmál v/þinglýsingar",
    "U": "Barnaverndarmál",
    "V": "Vitnamál",
    "X": "Ágreiningsmál v/gjaldþrotaskipta",
    "Y": "Ágreiningsmál v/aðfarargerða",
    "Z": "Ágreiningsmál v/nauðungarsölu",
    "Ö": "Annað",
}


def _heradsdomur_case_type(case_number: str | None) -> str | None:
    if not case_number:
        return None
    prefix = case_number.split("-")[0].upper()
    return _HERADSDOMUR_CASE_TYPE.get(prefix)


def _extract_heradsdomstolar(raw: dict, config: SourceConfig) -> dict:
    from engine.processors.court_names import graphql_to_abbreviation

    court_name = raw.get("court") or ""
    abbr = graphql_to_abbreviation(court_name) or config.abbreviation

    title = raw.get("title") or ""
    plf, dfd = _parse_parties_role_based(title)

    # Body comes from PDF — richText is always None for Héraðsdómar
    full_pdf_text = _pdf_b64_to_text(raw.get("pdfString"), config)

    # Fallback: extract from ## gegn ## preamble heading structure
    if not plf and not dfd and full_pdf_text:
        plf, dfd = _extract_parties_from_heradsdomstolar_preamble(full_pdf_text[:3000])

    document_date = _correct_date_if_wrong(
        _parse_icelandic_date(raw.get("verdictDate") or raw.get("date")),
        full_pdf_text,
    )

    plain_body = full_pdf_text
    if plain_body:
        # "## gegn\n\n" is the party-preamble marker — it only appears in preambles,
        # never in body prose, so it reliably identifies docs that need stripping.
        gegn_m = re.search(r'\n\n## gegn\n\n', plain_body[:3000], re.IGNORECASE)
        if gegn_m:
            # Pattern D: preamble ends with a standalone "## Dómur:" heading.
            end_m = re.search(
                r'\n\n## (?:Dómur|Úrskurður|Dómsúrskurður|Niðurstaða|Álit):\s*\n\n',
                plain_body[gegn_m.end():gegn_m.end() + 1000],
                re.IGNORECASE,
            )
            if end_m:
                plain_body = plain_body[gegn_m.end() + end_m.end():]
            else:
                # Patterns A/C: strip everything up to ## gegn, then strip the
                # remaining defendant heading/lawyer blocks after it.
                after_gegn = plain_body[gegn_m.end():]
                m = _STRUCT_A_RE.match(after_gegn)
                rest = after_gegn[m.end():] if m else after_gegn
                # Pattern C: body prose starts inline after a "(lawyer)" attribution
                # in the same paragraph block as the last defendant heading.
                body_inline = re.match(r'^\([^)]+\)\s+(.{50,})', rest, re.DOTALL)
                plain_body = body_inline.group(1) if body_inline else rest
        else:
            # No party preamble: Structure B (## Verdict + prose) or old format.
            _struct_b_re = r'^#{1,6}\s+(?:Dómur|Úrskurður|Dómsúrskurður|Niðurstaða|Álit)\n\n'
            if re.match(_struct_b_re, plain_body, re.IGNORECASE):
                # True Structure B: second block is prose, not another heading.
                # Some PDFs use plain-text (non-bold) "gegn" so ## gegn isn't
                # detected; if the second block is a ## heading the preamble
                # wasn't stripped — apply _STRUCT_A_RE to remove the heading run.
                _second = plain_body.split('\n\n', 2)
                if len(_second) > 1 and _second[1].startswith('## '):
                    m = _STRUCT_A_RE.match(plain_body)
                    if m and 0 < m.end() < len(plain_body):
                        plain_body = plain_body[m.end():]
            else:
                # Strip a compound court-name heading that some PDFs use as their
                # sole preamble: "## Dómur Héraðsdóms X" or "## Héraðsdómur X".
                _court_heading_m = re.match(
                    r'^## (?:Héraðsdóm\w+|(?:Dómur|Úrskurður)\s+Héraðsdóms\w*)[^\n]*\n\n',
                    plain_body, re.IGNORECASE,
                )
                if _court_heading_m:
                    plain_body = plain_body[_court_heading_m.end():]

                # Old format / Type 1: standard preamble stripper.
                m = _HERADSDOMUR_PREAMBLE_RE.match(plain_body)
                if m:
                    plain_body = plain_body[m.end():]
                    # After stripping, may still have ## court/party heading block.
                    first_line = plain_body.split('\n')[0] if plain_body else ''
                    if first_line.startswith('## ') and ' í máli nr. ' in first_line:
                        m2 = _STRUCT_A_RE.match(plain_body)
                        if m2 and 0 < m2.end() < len(plain_body):
                            plain_body = plain_body[m2.end():]

    # Final fallback: extract from old-format intro sentence in body text.
    # Called when plaintiff is missing even if defendant was found via preamble,
    # so plain-text preamble docs (no ## headings) can still get a plaintiff.
    if not plf and plain_body:
        plf2, dfd2 = _extract_parties_from_body_start(plain_body)
        if not plf:
            plf = plf2
        if not dfd:
            dfd = dfd2

    return {
        "case_number": raw.get("caseNumber"),
        "document_date": document_date,
        "court": abbr,
        "verdict_type": (
            _detect_verdict_type(plain_body, raw.get("keywords") or [])
            or config.verdict_type_default
        ),
        "instance_tier": config.instance_tier,
        "case_type": _heradsdomur_case_type(raw.get("caseNumber")),
        "plaintiffs": plf or None,
        "defendants": dfd or None,
        "keywords": _keywords_from_content(raw.get("keywords")),
        "summary": raw.get("presentings") or None,
        "body_text": plain_body or None,
        "lower_body_text": None,
        "raw_api_data": raw,
    }


def _extract_felagsdomur(raw: dict, config: SourceConfig) -> dict:
    plf, dfd = _parse_parties_gegn(raw.get("title"))
    return {
        "case_number": raw.get("caseNumber"),
        "document_date": _parse_icelandic_date(raw.get("date")),
        "court": config.abbreviation,
        "verdict_type": raw.get("type") or config.verdict_type_default,
        "instance_tier": config.instance_tier,
        "plaintiffs": plf or None,
        "defendants": dfd or None,
        "keywords": _keywords_from_content(raw.get("keywords")),
        "summary": raw.get("abstract") or None,
        "body_text": raw.get("text") or None,
        "lower_body_text": None,
        "raw_api_data": raw,
    }


def _extract_malskotsbeidnir(raw: dict, config: SourceConfig) -> dict:
    plf, dfd = _parse_parties_gegn(raw.get("title"))
    body = _richtext_body(raw.get("richText"))
    return {
        "case_number": raw.get("caseNumber"),
        "document_date": _parse_icelandic_date(raw.get("date")),
        "court": config.abbreviation,
        "verdict_type": config.verdict_type_default,
        "instance_tier": config.instance_tier,
        "plaintiffs": plf or None,
        "defendants": dfd or None,
        "keywords": _keywords_from_content(raw.get("keywords")),
        "summary": None,
        "body_text": body or None,
        "lower_body_text": None,
        "raw_api_data": raw,
    }


_PV_VERDICT_HEADINGS = {"úrskurður", "ákvörðun", "álit", "niðurstaða"}
# Matches: "Mál nr. 2025020471", "Mál númer 2017/1453", "mál 2020010401"
_PV_CASE_NR_RE = re.compile(
    r"mál[i]?\s+(?:(?:nr\.?|númer)\s*)?(\d{10}|\d{4}/\d+)",
    re.IGNORECASE,
)
# Splits body text at first Roman-numeral section heading (e.g. "I.Grundvöllur", "I. Málsatvik")
_PV_ROMAN_I_RE = re.compile(r"\n\nI\.\s*[A-ZÁÐÉÍÓÚÝÞÆÖ]")
# Fallback: bare "nr. 2020072069" (e.g. "Persónuverndar nr. XXXXXXXXXX")
_PV_NR_RE = re.compile(r"nr\.\s*(\d{10})", re.IGNORECASE)


def _pv_parse_content(content_list: list) -> tuple[str | None, str | None, str | None]:
    """Parse Persónuvernd content list into (verdict_type, summary, body_text).

    Content items before the first verdict-type heading become the summary.
    The full text (summary + verdict + body) becomes body_text.
    """
    all_para_nodes: list = []
    for item in content_list or []:
        doc = item.get("document", {}) if isinstance(item, dict) else {}
        all_para_nodes.extend(doc.get("content", []))

    summary_parts: list[str] = []
    body_parts: list[str] = []
    verdict_type: str | None = None
    found_heading = False

    for node in all_para_nodes:
        text = _rich_text_to_plain([node]).strip()
        if not found_heading and text.lower().rstrip(":") in _PV_VERDICT_HEADINGS:
            verdict_type = text.rstrip(":").capitalize()
            found_heading = True
        elif not found_heading and text:
            summary_parts.append(text)
        if text:
            body_parts.append(text)

    summary = "\n\n".join(summary_parts).strip() or None
    body = "\n\n".join(body_parts).strip() or None
    return verdict_type, summary, body


def _extract_personuvernd(raw: dict, config: SourceConfig) -> dict:
    # Case number: only from cardIntro and title — body text may reference other cases
    ci_text = _rich_text_to_plain(
        (raw.get("cardIntro") or [{}])[0].get("document", {}).get("content")
        if isinstance((raw.get("cardIntro") or [None])[0], dict) else None
    )
    title = re.sub(r"\s+", " ", raw.get("title") or "")
    verdict_type, summary, body_text = _pv_parse_content(raw.get("content") or [])
    # Preamble = body text up to first Roman-numeral section (I. Málsatvik, etc.)
    body_preamble = body_text or ""
    roman_m = _PV_ROMAN_I_RE.search(body_preamble)
    if roman_m:
        body_preamble = body_preamble[:roman_m.start()]
    m = (
        _PV_CASE_NR_RE.search(ci_text or "")
        or _PV_CASE_NR_RE.search(title)
        or _PV_NR_RE.search(title)
        or _PV_CASE_NR_RE.search(body_preamble)
    )
    case_number = m.group(1) if m else None

    keywords = [t["title"] for t in (raw.get("filterTags") or []) if t.get("title")]

    return {
        "case_number": case_number,
        "document_date": _parse_icelandic_date(raw.get("date")),
        "court": config.abbreviation,
        "verdict_type": verdict_type or config.verdict_type_default,
        "instance_tier": config.instance_tier,
        "plaintiffs": None,
        "defendants": None,
        "keywords": keywords or None,
        "summary": summary,
        "body_text": body_text,
        "lower_body_text": None,
        "raw_api_data": raw,
    }


def _extract_endurupptokudomur(raw: dict, config: SourceConfig) -> dict:
    plf, dfd = _parse_parties_role_based(raw.get("title"))
    body_text = _pdf_b64_to_text(raw.get("pdfString"), config)
    return {
        "case_number": raw.get("caseNumber"),
        "document_date": _parse_icelandic_date(raw.get("verdictDate")),
        "court": config.abbreviation,
        "verdict_type": config.verdict_type_default,
        "instance_tier": config.instance_tier,
        "plaintiffs": plf or None,
        "defendants": dfd or None,
        "keywords": _keywords_from_content(raw.get("keywords")),
        "summary": raw.get("presentings") or None,
        "body_text": body_text or None,
        "lower_body_text": None,
        "raw_api_data": raw,
    }


# ─── Umboðsmaður Alþingis ─────────────────────────────────────────────────────

_UA_DATE_RE = re.compile(
    r'lauk\s+m[aá]linu\s+me[ðd]\s+\w+\s+(\d{1,2}\.\s*\w+\s+\d{4})',
    re.IGNORECASE,
)
_UA_COMPLAINANT_RE = re.compile(
    r'^(.+?)\s+(?:kvartaði|kvartuðu|kvörtuðu|kvartaðist|kvörtuðust)\b',
    re.IGNORECASE | re.MULTILINE,
)
# Respondent: "kvartaði/kvörtuðu yfir stjórnsýslu/aðgerðaleysi/meðferð X í tengslum..."
_UA_RESPONDENT_RE = re.compile(
    r'(?:kvartaði|kvartuðu|kvörtuðu|kvartaðist|kvörtuðust)\s+'
    r'yfir\s+(?:stjórnsýslu|aðgerðaleysi|meðferð|framkvæmd|afgreiðslu)\s+'
    r'(.+?)(?=\s+(?:í\s|vegna\s|og\s|þar\s|sem\s)|[,.]|$)',
    re.IGNORECASE,
)
# Fallback date: any Icelandic date in text ("30. október 1991")
_UA_DATE_FALLBACK_RE = re.compile(
    r'\b(\d{1,2})\.\s*(janúar|febrúar|mars|apríl|maí|júní|júlí|ágúst|september|október|nóvember|desember)\s+(\d{4})\b',
    re.IGNORECASE,
)
# Fallback: "beinist að X og" from body text
_UA_RESPONDENT_BODY_RE = re.compile(
    r'beinist\s+a[ðd]\s+(.+?)(?=\s+(?:og\s|vegna\s|þar\s|sem\s)|[,.]|$)',
    re.IGNORECASE,
)


def _extract_umbodsmadur(raw: dict, config: SourceConfig) -> dict:
    title = (raw.get("title") or "").strip()
    verdict_type = (raw.get("verdict_type") or config.verdict_type_default).strip()
    reifun_text = (raw.get("reifun_text") or "").strip()
    body_text = (raw.get("body_text") or "").strip()

    # Keywords: title split on ". " (trailing periods stripped)
    keywords = [k.strip(" .") for k in title.split(".") if k.strip(" .")] or None

    # Date: "lauk málinu með bréfi/áliti DD. month YYYY"
    # Try reifun first, then body text (old Álits may have it at the end of the body).
    # Fallback: last Icelandic date in reifun (avoids picking up the complaint date from body).
    document_date: date | None = None
    for text in (reifun_text, body_text):
        dm = _UA_DATE_RE.search(text)
        if dm:
            document_date = _parse_icelandic_date(dm.group(1).strip())
            break
    if document_date is None and reifun_text:
        # Use last Icelandic date in reifun; require year ≥ 1987 (Ombudsman established)
        # to avoid picking up referenced historical dates.
        for d, m_name, y in reversed(_UA_DATE_FALLBACK_RE.findall(reifun_text)):
            if int(y) >= 1987:
                document_date = _parse_icelandic_date(f"{d}. {m_name} {y}")
                break

    # Complainant: subject before "kvartaði/kvörtuðu"
    # Strip appositive clauses: "A, búsettur í T-sýslu, kvartaði" → "A"
    plaintiffs = None
    cm = _UA_COMPLAINANT_RE.search(reifun_text)
    if cm:
        name = cm.group(1).strip()
        comma_pos = name.find(",")
        if comma_pos > 0:
            name = name[:comma_pos].strip()
        if name:
            plaintiffs = [{"name": name, "lawyer": None}]

    # Respondent: authority being investigated (best-effort from reifun, then body)
    defendants = None
    rm = _UA_RESPONDENT_RE.search(reifun_text)
    if rm:
        name = rm.group(1).strip()
        if name:
            defendants = [{"name": name, "lawyer": None}]
    if defendants is None:
        rb = _UA_RESPONDENT_BODY_RE.search(body_text[:500])
        if rb:
            name = rb.group(1).strip()
            if name:
                defendants = [{"name": name, "lawyer": None}]

    return {
        "case_number": raw.get("case_number_str") or None,
        "document_date": document_date,
        "court": config.abbreviation,
        "verdict_type": verdict_type,
        "instance_tier": config.instance_tier,
        "plaintiffs": plaintiffs,
        "defendants": defendants,
        "keywords": keywords,
        "summary": reifun_text or None,
        "body_text": body_text or None,
        "lower_body_text": None,
        "raw_api_data": raw,
    }


def _extract_stjornarradid(raw: dict, config: SourceConfig) -> dict:
    """Generic extractor for stjornarradid.is committee rulings.

    body_text is pre-parsed to markdown by the importer; we just normalise the
    structured fields that were stashed alongside it in raw_api_data.
    """
    verdict_type = (raw.get("verdict_type_str") or config.verdict_type_default).strip()
    date_str = (raw.get("date_str") or "").strip()
    document_date = _parse_icelandic_date(date_str) if date_str else None

    if config.parse_parties == "none":
        plaintiffs = None
        defendants = None
    else:
        plaintiffs_raw = raw.get("plaintiffs_raw") or []
        defendants_raw = raw.get("defendants_raw") or []
        plaintiffs = [{"name": n, "lawyer": None} for n in plaintiffs_raw if n] or None
        defendants = [{"name": n, "lawyer": None} for n in defendants_raw if n] or None

    return {
        "case_number": raw.get("case_number_str") or None,
        "document_date": document_date,
        "court": config.abbreviation,
        "verdict_type": verdict_type,
        "instance_tier": config.instance_tier,
        "plaintiffs": plaintiffs,
        "defendants": defendants,
        "keywords": raw.get("keywords_raw") or None,
        "summary": raw.get("summary") or None,
        "body_text": raw.get("body_text") or None,
        "lower_body_text": None,
        "raw_api_data": raw,
    }


# Maps DSpace dc.type.degree (English) → Icelandic Námsstig stem and one-letter
# abbreviation used in the thesis PDF filename. Unknown degrees fall back to the
# raw English string (flagged via validation when case_type looks non-Icelandic).
_DEGREE_MAP: dict[str, tuple[str, str]] = {
    "doctoral": ("Doktors", "D"),
    "master's": ("Meistara", "M"),
    "masters": ("Meistara", "M"),
    "master": ("Meistara", "M"),
    "bachelor's": ("Bakkalár", "B"),
    "bachelors": ("Bakkalár", "B"),
    "bachelor": ("Bakkalár", "B"),
    "diploma": ("Diplóma", "P"),
}


def degree_to_namsstig(degree: str | None) -> tuple[str | None, str | None]:
    """Return (case_type, abbrev). e.g. 'Master's' → ('Meistara ritgerð', 'M')."""
    if not degree:
        return None, None
    stem, abbr = _DEGREE_MAP.get(degree.strip().lower(), (degree.strip(), "X"))
    return f"{stem} ritgerð", abbr


def clean_person_name(name: str | None) -> str | None:
    """Strip trailing birth-year and parenthetical notes from a Skemman person name.

    'Diljá Mist Einarsdóttir 1987-'        → 'Diljá Mist Einarsdóttir'
    'Johnstone, Rachael Lorna, 1977-'      → 'Johnstone, Rachael Lorna'
    'Erla Gunnlaugsdóttir 1984 (lögfræðingur)' → 'Erla Gunnlaugsdóttir'
    """
    if not name:
        return None
    n = re.sub(r"\([^)]*\)", "", name)                       # drop parentheticals
    n = re.sub(r",?\s*\d{4}\s*-?\s*\d{0,4}\s*$", "", n)      # drop trailing birth year
    n = re.sub(r"\s+", " ", n).strip().strip(",").strip()
    return n or None


def _parse_browse_date(s: str | None) -> "date | None":
    """Parse Skemman browse-table date 'D.M.YYYY' (e.g. '5.5.2017')."""
    if not s:
        return None
    m = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", s.strip())
    if not m:
        return None
    try:
        return date(int(m[3]), int(m[2]), int(m[1]))
    except ValueError:
        return None


def _extract_logfraediritgerdir(raw: dict, config: SourceConfig) -> dict:
    """Map a Skemman thesis (OAI dim + browse row + handle file-list) to NORM fields.

    The importer stashes the merged metadata in raw; body_text is the extracted
    plain text of the 'Heildartexti' PDF (None when the file is locked/embargoed).
    No new DB columns: Höfundur→plaintiffs[0].name, Leiðbeinandi→plaintiffs[0].lawyer,
    Titill→case_number, Námsstig→case_type, Útdráttur/Athugasemdir→summary,
    Efnisorð→keywords, Samþykkt→document_date, URI→url/external_id.
    """
    title = (raw.get("title") or "").strip()
    author = clean_person_name(raw.get("author"))
    advisor = clean_person_name(raw.get("advisor"))

    plaintiffs = None
    if author:
        plaintiffs = [{"name": author, "lawyer": advisor}]

    # Útdráttur (abstract) + Athugasemdir (note), both into summary.
    summary_parts = [p.strip() for p in (raw.get("abstract"), raw.get("note")) if p and p.strip()]
    summary = "\n\n".join(summary_parts) or None

    keywords = [s.strip() for s in (raw.get("subjects") or []) if s and s.strip()] or None

    case_type, _abbr = degree_to_namsstig(raw.get("degree"))

    # body lives in body_text only — keep it out of the JSONB metadata payload.
    raw_meta = {k: v for k, v in raw.items() if k != "pdf_text"}

    return {
        "case_number": title or None,
        "document_date": (
            _parse_icelandic_date(raw.get("date_issued"))
            or _parse_browse_date(raw.get("browse_date"))
        ),
        "court": config.abbreviation,
        "verdict_type": config.verdict_type_default,
        "instance_tier": None,            # á ekki við ritgerðir
        "case_type": case_type,
        "plaintiffs": plaintiffs,
        "defendants": None,
        "keywords": keywords,
        "summary": summary,
        "body_text": (raw.get("pdf_text") or None),
        "lower_body_text": None,
        "raw_api_data": raw_meta,
    }


def _extract_logfraedibaekur(raw: dict, config: SourceConfig) -> dict:
    """Map a dropfolder law-book raw dict to NORM fields.

    raw comes from scripts/import_baekur.py: title/author/isbn/document_date
    from resolve_book_metadata(), plus source_filename and pdf_text (full
    extracted body). No new DB columns: title→case_number, author→
    plaintiffs[0].name (no advisor field for books).
    """
    title = (raw.get("title") or "").strip()
    author = raw.get("author")

    plaintiffs = None
    if author:
        plaintiffs = [{"name": author, "lawyer": None}]

    doc_date = raw.get("document_date")
    raw_meta = {k: v for k, v in raw.items() if k != "pdf_text"}
    if isinstance(raw_meta.get("document_date"), date):
        raw_meta["document_date"] = raw_meta["document_date"].isoformat()

    return {
        "case_number": title or None,
        "document_date": doc_date,
        "court": config.abbreviation,
        "verdict_type": config.verdict_type_default,
        "instance_tier": None,
        "case_type": None,
        "plaintiffs": plaintiffs,
        "defendants": None,
        "keywords": None,
        "summary": None,
        "body_text": (raw.get("pdf_text") or None),
        "lower_body_text": None,
        "raw_api_data": raw_meta,
    }


# ─── Registry ─────────────────────────────────────────────────────────────────

_EXTRACTORS: dict[str, Any] = {
    "haestirettur": _extract_haestirettur,
    "landsrettur": _extract_landsrettur,
    "heradsdomstolar": _extract_heradsdomstolar,
    "felagsdomur": _extract_stjornarradid,
    "malskotsbeidnir": _extract_malskotsbeidnir,
    "personuvernd": _extract_personuvernd,
    "endurupptokudomur": _extract_endurupptokudomur,
    "umbodsmadur": _extract_umbodsmadur,
    "afryjunarnefnd_haskoli": _extract_stjornarradid,
    "kaeruna_utlend": _extract_stjornarradid,
    "urvel": _extract_stjornarradid,
    "knhus": _extract_stjornarradid,
    "urvel_atv": _extract_stjornarradid,
    "urvel_felag": _extract_stjornarradid,
    "urvel_faed": _extract_stjornarradid,
    "urvel_greid": _extract_stjornarradid,
    "urvel_barna": _extract_stjornarradid,
    "mannanafnanefnd": _extract_stjornarradid,
    "kaeruna_utbod": _extract_stjornarradid,
    "urnefnd_uppl": _extract_stjornarradid,
    "innvida": _extract_stjornarradid,
    "yfirfasteignamat": _extract_stjornarradid,
    "kaeruna_jafnr": _extract_stjornarradid,
    "matsnefnd_eignarnam": _extract_stjornarradid,
    "endurupptakunefnd": _extract_stjornarradid,
    "urnefnd_hollusta": _extract_stjornarradid,
    "urnefnd_verdtryggt": _extract_stjornarradid,
    "urnefnd_raforka": _extract_stjornarradid,
    "urnefnd_kosninga": _extract_stjornarradid,
    "sveitarstj_alit": _extract_stjornarradid,
    "lausn_stundar": _extract_stjornarradid,
    "matsnefnd_lax": _extract_stjornarradid,
    "velferdar_raduneyti": _extract_stjornarradid,
    "sjavarutv": _extract_stjornarradid,
    "heilbrigdi_raduneyti": _extract_stjornarradid,
    "stjornsyslu_kaerur": _extract_stjornarradid,
    "umhverfi_raduneyti": _extract_stjornarradid,
    "matvael_land": _extract_stjornarradid,
    "mennta_raduneyti": _extract_stjornarradid,
    "landskjor": _extract_stjornarradid,
    "felag_hus_raduneyti": _extract_stjornarradid,
    "ferdathjod": _extract_stjornarradid,
    "vidskiptamal": _extract_stjornarradid,
    "innanr_utl": _extract_stjornarradid,
    "mnh_raduneyti": _extract_stjornarradid,
    "forseta_raduneyti": _extract_stjornarradid,
    "kosninga_ursk": _extract_stjornarradid,
    "utanr_raduneyti": _extract_stjornarradid,
    "logfraediritgerdir": _extract_logfraediritgerdir,
    "logfraedibaekur": _extract_logfraedibaekur,
}
