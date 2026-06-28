"""Parse Alþingi lagasafn HTML files into structured Document data."""
from __future__ import annotations

import hashlib
import io
import re
import zipfile
from datetime import date
from typing import Any

from bs4 import BeautifulSoup, Tag

# ── Verdict-type detection ─────────────────────────────────────────────────────
_VT_PREFIXES: list[tuple[str, str]] = [
    ("forsetaúrskurður", "Forsetaúrskurður"),
    ("forsetabréf",      "Forsetabréf"),
    ("auglýsing",        "Auglýsing"),
    ("reglugerð",        "Reglugerð"),
    ("samþykkt",         "Samþykkt"),
    ("tilskipun",        "Tilskipun"),
    ("bréf",             "Bréf"),
    ("lög",              "Lög"),
]

def _detect_verdict_type(title: str) -> str:
    t = title.strip().lower()
    for prefix, vt in _VT_PREFIXES:
        if t.startswith(prefix):
            return vt
    return "Lög"  # fallback

# ── Date parsing ───────────────────────────────────────────────────────────────
_MONTHS_IS: dict[str, int] = {
    "janúar": 1, "febrúar": 2, "mars": 3, "apríl": 4,
    "maí": 5, "júní": 6, "júlí": 7, "ágúst": 8,
    "september": 9, "október": 10, "nóvember": 11, "desember": 12,
}
_DATE_RE = re.compile(
    r'(\d{1,2})\.\s*(' + '|'.join(_MONTHS_IS) + r')\s+(\d{4})',
    re.IGNORECASE,
)

def _parse_date(text: str) -> date | None:
    m = _DATE_RE.search(text.lower())
    if not m:
        return None
    day, month_str, year = int(m.group(1)), m.group(2).lower(), int(m.group(3))
    return date(year, _MONTHS_IS[month_str], day)

# ── Case-number extraction ─────────────────────────────────────────────────────
# Title format: "1944  nr. 33  17. júní" → "33/1944"
_NR_RE = re.compile(r'(\d{4})\s+nr\.\s*(\d+)', re.IGNORECASE)

def _parse_case_number(strong_text: str) -> str | None:
    m = _NR_RE.search(strong_text)
    if not m:
        return None
    year, nr = m.group(1), m.group(2)
    return f"{int(nr)}/{year}"

# ── HTML stripping ─────────────────────────────────────────────────────────────
_IMG_RE = re.compile(r'<img[^>]*>', re.IGNORECASE)
_TAG_RE = re.compile(r'<[^>]+>')
_NBSP  = re.compile(r'&nbsp;|&#160;')
_AMP   = re.compile(r'&amp;')
_ELLIP = re.compile(r'&hellip;')
_WS    = re.compile(r'[ \t]{2,}')

def _strip_html(html_fragment: str) -> str:
    text = _IMG_RE.sub('', html_fragment)
    text = _TAG_RE.sub('', text)
    text = _NBSP.sub(' ', text)
    text = _AMP.sub('&', text)
    text = _ELLIP.sub('…', text)
    text = _WS.sub(' ', text)
    return text.strip()

# ── Article / provision extraction ────────────────────────────────────────────
_SPAN_GR_RE = re.compile(r'^G(\d+)$')  # matches id="G1", id="G2", not id="G1M1"

def _extract_provisions(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Find all <span id="GN"> anchors and collect the article text that follows."""
    provisions: list[dict[str, Any]] = []
    for span in soup.find_all('span', id=_SPAN_GR_RE):
        m = _SPAN_GR_RE.match(span.get('id', ''))
        if not m:
            continue
        num = int(m.group(1))
        # Collect sibling text until next G-span
        parts: list[str] = []
        for sib in span.next_siblings:
            if isinstance(sib, Tag):
                if sib.name == 'span' and _SPAN_GR_RE.match(sib.get('id', '')):
                    break
                # Skip the bold "N. gr." label itself (it's already represented by num)
                if sib.name == 'b' and re.match(r'^\d+\.', sib.get_text(strip=True)):
                    continue
                parts.append(_strip_html(str(sib)))
            else:
                t = str(sib).strip()
                if t:
                    parts.append(t)
        text = ' '.join(p for p in parts if p).strip()
        text = _WS.sub(' ', text)
        if text:
            provisions.append({"num": num, "text": text})
    return provisions

# ── Main parser ───────────────────────────────────────────────────────────────
def parse_law_html(html_bytes: bytes, filename: str) -> dict[str, Any]:
    """Parse one law HTML file. Returns a dict with all Document fields."""
    md5 = hashlib.md5(html_bytes).hexdigest()
    external_id = filename.removesuffix('.html')

    soup = BeautifulSoup(html_bytes, 'html.parser', from_encoding='iso-8859-1')

    # Law name from <h2>
    h2 = soup.find('h2')
    law_name = h2.get_text(strip=True) if h2 else external_id

    # Verdict type
    verdict_type = _detect_verdict_type(law_name)

    # Case number from <strong> or <title> (some files have "Lagasafn." in <strong>)
    strong = soup.find('strong')
    case_number = _parse_case_number(strong.get_text()) if strong else None
    if case_number is None:
        title = soup.find('title')
        if title:
            case_number = _parse_case_number(title.get_text())

    # Effective date — search inside <small> tags for "Tók/Tóku gildi"
    document_date: date | None = None
    for small in soup.find_all('small'):
        text = small.get_text()
        if 'gildi' in text.lower():
            document_date = _parse_date(text)
            if document_date:
                break
    # Fallback: year from case_number
    if document_date is None and case_number:
        try:
            year = int(case_number.split('/')[1])
            document_date = date(year, 1, 1)
        except (IndexError, ValueError):
            pass

    # Provisions
    provisions = _extract_provisions(soup)

    # body_text: structured as "N. gr.\n{text}\n\n" per article
    if provisions:
        body_parts: list[str] = []
        for p in provisions:
            body_parts.append(f"{p['num']}. gr.")
            body_parts.append(p['text'])
            body_parts.append('')
        body_text = '\n'.join(body_parts).strip()
    else:
        # No greinar found — use full stripped body text
        body = soup.find('body')
        body_text = _strip_html(str(body)) if body else _strip_html(soup.get_text())
        body_text = re.sub(r'\n{3,}', '\n\n', body_text).strip()

    return {
        "external_id": external_id,
        "law_name":    law_name,
        "case_number": case_number,
        "court":       "Alþingi",
        "verdict_type": verdict_type,
        "document_date": document_date,
        "body_text":   body_text,
        "provisions":  provisions,
        "md5":         md5,
    }


def build_chapter_map(zip_bytes: bytes) -> dict[str, int]:
    """Parse chapter index files inside ZIP. Returns {html_filename → kafli_num}."""
    result: dict[str, int] = {}
    href_re = re.compile(r'href=["\']([^"\']+\.html)["\']')
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for kafli_num in range(1, 49):
            idx_name = f"{kafli_num:02d}.html"
            if idx_name not in zf.namelist():
                continue
            raw = zf.read(idx_name).decode('iso-8859-1', errors='replace')
            for href in href_re.findall(raw):
                fname = href.split('/')[-1]
                if fname.endswith('.html') and fname not in (idx_name,):
                    result[fname] = kafli_num
    return result
