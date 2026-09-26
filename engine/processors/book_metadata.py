"""Resolve book metadata (title/author/ISBN) for dropfolder ingestion.

No external API exists for an arbitrary dropped PDF, so metadata is inferred
via a tiered fallback: ISBN (found in text) -> OpenLibrary lookup -> leitir.is
(Icelandic union catalog) lookup, then filename + regex author search, then
filename + Claude API as a last resort.
"""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from datetime import date
from pathlib import Path

import httpx
from anthropic import AsyncAnthropic

log = logging.getLogger(__name__)

_TRANSLIT = str.maketrans({
    "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ý": "y", "ð": "d",
    "þ": "th", "æ": "ae", "ö": "o", "Á": "A", "É": "E", "Í": "I", "Ó": "O",
    "Ú": "U", "Ý": "Y", "Ð": "D", "Þ": "Th", "Æ": "Ae", "Ö": "O",
})

_ISBN_RE = re.compile(
    r'(?:ISBN[:\s-]*)?(97[89][-\s]?(?:\d[-\s]?){9}\d|(?:\d[-\s]?){9}[\dXx])'
)


def _clean_isbn(raw: str) -> str:
    """Strip hyphens/spaces, uppercase any check-digit X."""
    return re.sub(r'[^0-9Xx]', '', raw).upper()


def _isbn10_checksum_valid(digits: str) -> bool:
    if len(digits) != 10:
        return False
    if not digits[:9].isdigit():
        return False
    if not (digits[9].isdigit() or digits[9] == 'X'):
        return False
    total = 0
    for i, ch in enumerate(digits):
        val = 10 if ch == 'X' else int(ch)
        total += (10 - i) * val
    return total % 11 == 0


def _isbn13_checksum_valid(digits: str) -> bool:
    if len(digits) != 13 or not digits.isdigit():
        return False
    total = sum((1 if i % 2 == 0 else 3) * int(d) for i, d in enumerate(digits))
    return total % 10 == 0


def find_isbn(text: str) -> str | None:
    """Return the first checksum-valid ISBN-10 or ISBN-13 found in text, or None."""
    if not text:
        return None
    for m in _ISBN_RE.finditer(text):
        digits = _clean_isbn(m.group(1))
        if len(digits) == 10 and _isbn10_checksum_valid(digits):
            return digits
        if len(digits) == 13 and _isbn13_checksum_valid(digits):
            return digits
    return None


async def lookup_openlibrary(client: httpx.AsyncClient, isbn: str) -> dict | None:
    """Look up title/authors/publisher/publish_date on OpenLibrary. None if not found or on error."""
    url = f"https://openlibrary.org/api/books?bibkeys=ISBN:{isbn}&format=json&jscmd=data"
    try:
        resp = await client.get(url, timeout=15.0)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("OpenLibrary lookup failed for %s: %s", isbn, exc)
        return None
    data = resp.json()
    key = f"ISBN:{isbn}"
    if key not in data:
        return None
    entry = data[key]
    authors = entry.get("authors") or []
    publishers = entry.get("publishers") or []
    return {
        "title": entry.get("title"),
        "authors": [a["name"] for a in authors if a.get("name")] or None,
        "publisher": "; ".join(p["name"] for p in publishers if p.get("name")) or None,
        "publish_date": entry.get("publish_date"),
    }


_LEITIR_URL = (
    "https://leitir.is/primaws/rest/pub/pnxs"
    "?q=any,contains,{isbn}&scope=10000_MYLIB&tab=Everything"
    "&vid=354ILC_NETWORK:10000_UNION&inst=354ILC_NETWORK&lang=is&limit=1&offset=0"
)


def _clean_primo_name(raw: str) -> str:
    # Primo display format: "Full Name birth- role$$QClean Name" — the
    # part after "$$Q" is the display-clean name; use it when present.
    return (raw.split("$$Q")[-1] if "$$Q" in raw else raw).strip()


async def lookup_leitir(client: httpx.AsyncClient, isbn: str) -> dict | None:
    """Look up title/authors/publisher/publish_date on leitir.is (Icelandic union catalog, Primo VE).

    Covers Icelandic-only titles OpenLibrary doesn't have. None if not found or on error.
    """
    try:
        resp = await client.get(_LEITIR_URL.format(isbn=isbn), timeout=15.0)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("leitir.is lookup failed for %s: %s", isbn, exc)
        return None
    data = resp.json()
    docs = data.get("docs") or []
    if not docs:
        return None
    disp = docs[0].get("pnx", {}).get("display", {})
    title = (disp.get("title") or [None])[0]
    if title:
        title = title.strip()
    creators = disp.get("creator") or []
    authors = [_clean_primo_name(c) for c in creators if c and c.strip()] or None
    publisher = (disp.get("publisher") or [None])[0]
    if publisher:
        publisher = publisher.strip()
    date_str = (disp.get("creationdate") or [None])[0]
    return {"title": title, "authors": authors, "publisher": publisher, "publish_date": date_str}


_YEAR_RE = re.compile(r'\b(1[5-9]\d{2}|20\d{2})\b')

_AUTHOR_PATTERNS = [
    re.compile(r'(?im)^\s*eftir\s+([A-ZÁÉÍÓÚÝÐÞÆÖ][^\n]{2,60}?)\s*$'),
    re.compile(r'(?im)^\s*h[öo]fundur\s*:?\s+([A-ZÁÉÍÓÚÝÐÞÆÖ][^\n]{2,60}?)\s*$'),
]


def slugify_filename(pdf_path: Path) -> str:
    """Derive a human-readable title from a filename (strip extension, spaces for separators)."""
    stem = pdf_path.stem
    return re.sub(r'[_\-]+', ' ', stem).strip()


def find_author_regex(text: str) -> str | None:
    """Search text for common Icelandic author-attribution patterns."""
    if not text:
        return None
    for pattern in _AUTHOR_PATTERNS:
        m = pattern.search(text)
        if m:
            return m.group(1).strip()
    return None


def parse_publish_year(s: str | None) -> date | None:
    """Extract a plausible 4-digit year from a free-text publish date string."""
    if not s:
        return None
    m = _YEAR_RE.search(s)
    return date(int(m.group(1)), 1, 1) if m else None


def external_id_from_filename(pdf_path: Path) -> str:
    """Filesystem-safe fallback external_id: lowercase ASCII slug of the filename.

    Icelandic-specific letters (ð, þ, æ) have no NFKD decomposition, so a plain
    normalize+ascii-encode silently drops them instead of transliterating —
    translate them to their ASCII equivalents first.
    """
    s = pdf_path.stem.translate(_TRANSLIT)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r'[^A-Za-z0-9]+', '_', s).strip('_').lower()
    return s or "book"


async def find_author_llm(text: str) -> str | None:
    """Ask Claude to extract the author name from the book's opening pages.

    Last resort — only called when regex finds nothing. Returns None on any failure.
    """
    try:
        client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        resp = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            messages=[{
                "role": "user",
                "content": (
                    "Eftirfarandi er upphaf lögfræðibókar. Finndu höfund bókarinnar. "
                    "Svaraðu EINGÖNGU með JSON á forminu {\"author\": \"Nafn\"} eða "
                    "{\"author\": null} ef höfundur finnst ekki.\n\n" + text[:4000]
                ),
            }],
        )
        data = json.loads(resp.content[0].text.strip())
        return data.get("author") or None
    except Exception as exc:  # noqa: BLE001
        log.warning("Claude author extraction failed: %s", exc)
        return None


async def resolve_book_metadata(
    client: httpx.AsyncClient, text: str, pdf_path: Path,
) -> dict:
    """Resolve {title, authors, isbn, publisher, external_id, document_date} for a dropped book PDF.

    Tier 1: ISBN found in text -> OpenLibrary lookup.
    Tier 2: ISBN found, OpenLibrary has no title -> leitir.is lookup (Icelandic titles).
    Tier 3: filename -> title, regex on text -> author.
    Tier 4: regex found nothing -> Claude API on text -> author.

    `authors` is a list[str] | None — OpenLibrary/leitir.is may report several;
    the regex/LLM fallback tiers only ever find one.
    """
    isbn = find_isbn(text)
    if isbn:
        ol = await lookup_openlibrary(client, isbn)
        if ol and ol.get("title"):
            return {
                "title": ol["title"],
                "authors": ol.get("authors"),
                "isbn": isbn,
                "publisher": ol.get("publisher"),
                "external_id": isbn,
                "document_date": parse_publish_year(ol.get("publish_date")),
            }
        leitir = await lookup_leitir(client, isbn)
        if leitir and leitir.get("title"):
            return {
                "title": leitir["title"],
                "authors": leitir.get("authors"),
                "isbn": isbn,
                "publisher": leitir.get("publisher"),
                "external_id": isbn,
                "document_date": parse_publish_year(leitir.get("publish_date")),
            }

    title = slugify_filename(pdf_path)
    author = find_author_regex(text)
    if author is None:
        author = await find_author_llm(text)

    return {
        "title": title,
        "authors": [author] if author else None,
        "isbn": isbn,
        "publisher": None,
        "external_id": isbn or external_id_from_filename(pdf_path),
        "document_date": None,
    }
