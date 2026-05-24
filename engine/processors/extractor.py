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
    """'A ehf. gegn B hf.' → ([{name:A ehf.}], [{name:B hf.}])"""
    if not title:
        return [], []
    parts = re.split(r"(?<!\w)gegn", title, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) == 2:
        return (
            [{"name": parts[0].strip(), "lawyer": None}],
            [{"name": parts[1].strip(), "lawyer": None}],
        )
    return [{"name": title.strip(), "lawyer": None}], []


# ─── PDF extraction ──────────────────────────────────────────────────────────

_MARGIN_NUM_RE = re.compile(r'^\d{1,3}$')  # paragraph margin numbers


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
    """
    Convert a single dict-mode block to a text string.

    Rules:
    - Heading block (all spans BoldMT/configured font): return '## text'
    - Margin-number block (first line is a small-size paragraph number, x < 90pt):
      merge number with following text as 'N text…'
    - Body block: join lines, de-hyphenating PDF word-wraps
    """
    if block.get("type") != 0:  # type 1 = image, skip
        return None
    lines = block.get("lines", [])
    if not lines:
        return None

    # ── Detect heading ────────────────────────────────────────────────────────
    # A heading block has exactly one line whose dominant font is a heading font.
    # We check all spans; if every non-empty span is a heading font → heading.
    if len(lines) == 1:
        spans = lines[0].get("spans", [])
        texts = [s["text"].strip() for s in spans if s["text"].strip()]
        if texts:
            # Use first span's font/size to check heading
            first_span = next((s for s in spans if s["text"].strip()), None)
            if first_span:
                marker = _heading_marker(
                    first_span.get("font", ""),
                    first_span.get("size", 0),
                    crop,
                )
                if marker:
                    return marker + " ".join(texts)

    # ── Detect margin number (paragraph number) ───────────────────────────────
    # First line of block: single span, small size (≤10.5pt), x < 90, pure digit(s)
    first_line_spans = lines[0].get("spans", [])
    margin_num: str | None = None
    body_start_line = 0
    if first_line_spans:
        s = first_line_spans[0]
        if (
            len(first_line_spans) == 1
            and s.get("size", 12) <= 10.5
            and s["bbox"][0] < 90
            and _MARGIN_NUM_RE.match(s["text"].strip())
        ):
            margin_num = s["text"].strip()
            body_start_line = 1

    # ── Join body lines with de-hyphenation ───────────────────────────────────
    text_parts: list[str] = []
    for line in lines[body_start_line:]:
        line_text = " ".join(s["text"] for s in line.get("spans", [])).strip()
        if not line_text:
            continue
        if text_parts and text_parts[-1].endswith("-"):
            # PDF word-wrap hyphen: join without space, remove hyphen
            text_parts[-1] = text_parts[-1][:-1] + line_text
        else:
            text_parts.append(line_text)

    body = " ".join(text_parts).strip()
    if not body:
        return margin_num  # edge case: number-only block

    if margin_num:
        return f"{margin_num} {body}"
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

    page_parts: list[str] = []
    for page_num, page in enumerate(doc):
        page_height = page.rect.height
        header = crop.header_pt if crop else 0.0
        footer = crop.footer_pt if crop else 0.0
        if crop and crop.skip_header_on_first and page_num == 0:
            header = 0.0

        clip = fitz.Rect(0, header, page.rect.width, page_height - footer)
        d = page.get_text("dict", clip=clip)

        block_texts: list[str] = []
        for block in d.get("blocks", []):
            t = _block_to_text(block, crop)
            if t:
                block_texts.append(t)

        if block_texts:
            page_parts.append("\n\n".join(block_texts))

    doc.close()
    if not page_parts:
        return None
    full = "\n\n".join(page_parts)
    return re.sub(r"\n{3,}", "\n\n", full).strip() or None


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
    return {
        "case_number": raw.get("caseNumber") or raw.get("id"),
        "document_date": _parse_icelandic_date(
            raw.get("verdictDate") or raw.get("date") or raw.get("dateOfRuling")
        ),
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
    plain_body = _pdf_b64_to_text(raw.get("pdfString"), config)

    # Strip preamble (court name, date, parties, keywords, abstract — already
    # captured in structured fields). Body begins at "Dómur Landsréttar".
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
        "document_date": _parse_icelandic_date(
            raw.get("verdictDate") or raw.get("date")
        ),
        "court": config.abbreviation,
        "verdict_type": (
            _detect_verdict_type(plain_body, raw.get("keywords") or [])
            or config.verdict_type_default
        ),
        "instance_tier": config.instance_tier,
        "plaintiffs": plf or None,
        "defendants": dfd or None,
        "keywords": _keywords_from_content(raw.get("keywords")),
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
