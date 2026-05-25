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
_NEW_PARA_RE = re.compile(r'^\d{1,3}\.\s+[A-ZÁÉÍÓÚÝÞÆÖ]')


def _build_body_from_lines(lines: list) -> str:
    """Join lines (list of fitz line dicts) into a single string, de-hyphenating wraps."""
    parts: list[str] = []
    for line in lines:
        lt = " ".join(s["text"] for s in line.get("spans", [])).strip()
        if not lt:
            continue
        if parts and parts[-1].endswith("-"):
            parts[-1] = parts[-1][:-1] + lt
        else:
            parts.append(lt)
    return " ".join(parts).strip()


def _is_page_footer_num(block: dict, page_height: float, page_width: float) -> bool:
    """True if this block is a document page number printed in the footer margin.

    PDF generators often place the page number as a separate text object early in
    the page content stream, causing fitz to return it before the body blocks even
    though it sits near the bottom of the page visually.  We detect it by position
    (bottom 13 % of page, horizontally centred) and content (1–3 pure digits).
    """
    lines = block.get("lines", [])
    if len(lines) != 1:
        return False
    spans = lines[0].get("spans", [])
    if len(spans) != 1:
        return False
    s = spans[0]
    if not re.match(r'^\d{1,3}$', s["text"].strip()):
        return False
    x, y = s["bbox"][0], s["bbox"][1]
    return y > page_height * 0.87 and x > page_width * 0.3


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
        body = _build_body_from_lines(lines[1:])
        return f"{marker}{heading}\n\n{body}" if body else marker + heading

    # ── Detect centred Roman-numeral section header (I, II, III…) ─────────────
    # Lower-court bodies sometimes use a centred Roman numeral as a divider.
    # Signature: first line is a single pure-Roman span placed far to the right
    # of where the body text begins on subsequent lines (delta x > 100 pt).
    if len(lines) > 1 and len(first_nonempty) == 1:
        if _ROMAN_NUM_RE.match(first_span["text"].strip()):
            second_spans = lines[1].get("spans", [])
            second_x = second_spans[0]["bbox"][0] if second_spans else 0.0
            if second_x > 0 and first_span["bbox"][0] > second_x + 100:
                roman = first_span["text"].strip()
                body = _build_body_from_lines(lines[1:])
                return f"### {roman}\n\n{body}" if body else f"### {roman}"

    # ── Detect margin number (paragraph number) ───────────────────────────────
    # Case A: first span is a pure digit at small size and left-margin x position.
    # Threshold is 115 pt — Landsréttur docs use two layouts:
    #   older style  x ≈ 94  (combined "N   text…" span)
    #   newer style  x ≈ 105 (standalone "N" span; body continues at x ≈ 122)
    # Body text always starts at x ≥ 120, so 115 is a safe upper bound.
    margin_num: str | None = None
    body_start_line = 0
    if (
        len(first_spans) == 1
        and first_span.get("size", 12) <= 10.5
        and first_span["bbox"][0] < 115
        and _MARGIN_NUM_RE.match(first_span["text"].strip())
    ):
        margin_num = first_span["text"].strip()
        body_start_line = 1

    # ── Join body lines with de-hyphenation ───────────────────────────────────
    body = _build_body_from_lines(lines[body_start_line:])
    if not body:
        return margin_num  # edge case: number-only block

    if margin_num:
        # Case A: standalone number detected — add period separator
        return f"{margin_num}. {body}"

    # Case B: combined "3   Ákærði..." span — fitz merged the paragraph number
    # with the body text into a single span.  Insert a period when the first
    # span is small-size and positioned in the left margin.
    # Lookahead uses \S (any non-whitespace) so redacted content like
    # "[…]" (which starts with "[") is handled in addition to uppercase letters.
    if (
        first_span.get("size", 12) <= 10.5
        and first_span["bbox"][0] < 115
    ):
        body = re.sub(r'^(\d{1,3})\s{2,}(?=\S)', r'\1. ', body)

    # ── Detect first-line indent → signals a new paragraph ───────────────────
    # Some lower-court PDFs separate paragraphs with first-line indentation
    # rather than blank lines.  When the first line's x position is noticeably
    # larger than the continuation lines' x (delta > 20 pt), this block starts
    # a new paragraph.  We prefix "\n\n" so the smart-join phase handles it.
    if len(lines) > 1 and not margin_num:
        second_spans = lines[1].get("spans", [])
        if second_spans:
            if first_span["bbox"][0] - second_spans[0]["bbox"][0] > 20:
                body = "\n\n" + body

    return body


def _pdf_bytes_to_text(pdf_bytes: bytes, config: SourceConfig) -> str | None:
    """
    Extract structured text from PDF bytes using span-level font information.

    Features:
    - Crop header/footer per PdfCrop settings
    - Font-based heading detection (heading_fonts) → '## heading'
    - Size-based heading detection (heading_sizes) as fallback
    - Margin paragraph numbers merged with their text ('6 Áfrýjandi var…')
    - PDF word-wrap de-hyphenation ('flugumferðar-\\nstjórar' → 'flugumferðarstjórar')
    """
    crop = config.pdf_crop
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        return None

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

        for block in d.get("blocks", []):
            # Skip centred page-footer numbers — they appear first in fitz's
            # block list (early in the PDF content stream) even though they sit
            # near the bottom of the page, and would otherwise get injected into
            # the middle of body text across page boundaries.
            if _is_page_footer_num(block, page_height, page_width):
                continue
            t = _block_to_text(block, crop)
            if t:
                all_blocks.append(t)

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
    result = all_blocks[0]
    for curr in all_blocks[1:]:
        prev_last = result.rsplit("\n", 1)[-1]  # last line of accumulated text
        if (
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
