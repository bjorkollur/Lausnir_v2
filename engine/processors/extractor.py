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


def _date_from_verdict_text(text: str | None, search_chars: int = 800) -> "date | None":
    """
    Extract the verdict date stamped on the document itself.

    Searches the first `search_chars` characters of `text` for the pattern
    'Dómur/Úrskurður [weekday] D. [month] YYYY.' and returns that date,
    or None if the pattern is absent.
    """
    if not text:
        return None
    m = _VERDICT_DATE_RE.search(text[:search_chars])
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
    r'^(?:#{1,6}\s+)?((?:Úrskurður|Dómur)\s+(?:Landsréttar|Héraðsdóms\b[^\n,]*?)'
    r'(?:\s*,?\s*\w+daginn?\s+\d{1,2}\.\s+' + _MONTHS_IS_RE + r'\s+\d{4}\.?'
    r'|\s+\d{1,2}\.\s+' + _MONTHS_IS_RE + r'\s+\d{4}\.?)\s*)$',
    re.MULTILINE | re.IGNORECASE,
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


# ─── PDF extraction ──────────────────────────────────────────────────────────

_MARGIN_NUM_RE = re.compile(r'^\d{1,3}$')   # paragraph margin numbers (pure digit)
_ROMAN_NUM_RE  = re.compile(r'^[IVX]+$')   # Roman numerals I, II, III… (no period)

# Matches a new numbered paragraph: 1–3 digit number, period, space, uppercase.
# 4-digit years like "2015. Þar er..." do NOT match, avoiding false paragraph breaks.
_NEW_PARA_RE = re.compile(r'^\d{1,3}\.\s+[A-ZÁÐÉÍÓÚÝÞÆÖ]')


_INLINE_PARA_START_RE = re.compile(r'^\d{1,3}\.\s')
"""Matches numbered-paragraph starts like ``1. text`` or ``12.  text``."""


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
        lt = " ".join(s["text"] for s in line.get("spans", [])).strip()
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
        heading_texts = [s["text"].strip() for s in first_spans if s["text"].strip()]
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
                body = re.sub(r'^(\d{1,3}) +(?=\S)', r'\1. ', body)
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
    if first_span["bbox"][0] < 115:
        body = re.sub(r'^(\d{1,3}) +(?=\S)', r'\1. ', body)

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
        # Style A — x-coordinate delta between first and second line
        if len(lines) > 1:
            second_spans = lines[1].get("spans", [])
            if second_spans and first_span["bbox"][0] - second_spans[0]["bbox"][0] > 20:
                _para_start = True
        # Style B — leading whitespace in raw span text
        if not _para_start and first_span["text"].startswith("    "):
            _para_start = True
        # Style C — centred/right-shifted single-line block
        if not _para_start and len(lines) == 1 and first_span["bbox"][0] > 150 and len(body.strip()) <= 60:
            _para_start = True
        if _para_start:
            body = "\n\n" + body

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

    # Insert Markdown table separator rows (| --- | --- |) after the header
    # row of each pipe-table sequence.  Must run after full block assembly so
    # it sees consecutive table rows regardless of whether they came from a
    # single bordered-table block or multiple no-border single-row blocks.
    result = _insert_md_table_separators(result)

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
        return _html_to_plain(value)
    if isinstance(value, dict):
        content = value.get("document", {}).get("content")
        text = _rich_text_to_plain(content)
        return text or None
    return None


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
        "plaintiffs": plf or None,
        "defendants": dfd or None,
        "keywords": api_keywords,
        "summary": raw.get("presentings") or None,
        "body_text": plain_body or None,
        "lower_body_text": lower_body,
        "raw_api_data": raw,
    }


def _extract_heradsdomstolar(raw: dict, config: SourceConfig) -> dict:
    # Héraðsdómar come from island.is GraphQL — similar shape to haestirettur
    # court abbreviation includes location, e.g. 'Hérd. Rvk.'
    court_name = raw.get("court") or raw.get("courtName") or ""
    from engine.processors.court_names import graphql_to_abbreviation
    abbr = graphql_to_abbreviation(court_name) or config.abbreviation
    title = raw.get("title") or raw.get("caseTitle") or ""
    plf, dfd = _parse_parties_gegn(title)
    return {
        "case_number": raw.get("caseNumber"),
        "document_date": _parse_icelandic_date(raw.get("date")),
        "court": abbr,
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
    # richText detail node
    content = raw.get("content") or {}
    body = _rich_text_to_plain(content.get("json", {}).get("content"))
    return {
        "case_number": raw.get("caseNumber") or raw.get("id"),
        "document_date": _parse_icelandic_date(raw.get("date")),
        "court": config.abbreviation,
        "verdict_type": config.verdict_type_default,
        "instance_tier": config.instance_tier,
        "plaintiffs": plf or None,
        "defendants": dfd or None,
        "keywords": _keywords_from_content(raw.get("keywords")),
        "summary": None,
        "body_text": body or raw.get("text") or None,
        "lower_body_text": None,
        "raw_api_data": raw,
    }


def _extract_personuvernd(raw: dict, config: SourceConfig) -> dict:
    return {
        "case_number": raw.get("caseNumber") or raw.get("id"),
        "document_date": _parse_icelandic_date(raw.get("date")),
        "court": config.abbreviation,
        "verdict_type": raw.get("type") or config.verdict_type_default,
        "instance_tier": config.instance_tier,
        "plaintiffs": None,
        "defendants": None,
        "keywords": _keywords_from_content(raw.get("keywords")),
        "summary": raw.get("abstract") or None,
        "body_text": raw.get("text") or None,
        "lower_body_text": None,
        "raw_api_data": raw,
    }


def _extract_endurupptokudomur(raw: dict, config: SourceConfig) -> dict:
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


# ─── Registry ─────────────────────────────────────────────────────────────────

_EXTRACTORS: dict[str, Any] = {
    "haestirettur": _extract_haestirettur,
    "landsrettur": _extract_landsrettur,
    "heradsdomstolar": _extract_heradsdomstolar,
    "felagsdomur": _extract_felagsdomur,
    "malskotsbeidnir": _extract_malskotsbeidnir,
    "personuvernd": _extract_personuvernd,
    "endurupptokudomur": _extract_endurupptokudomur,
}
