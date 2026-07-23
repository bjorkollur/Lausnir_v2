"""Resolve book metadata (title/author/ISBN) for dropfolder ingestion.

No external API exists for an arbitrary dropped PDF, so metadata is inferred
via a tiered fallback: ISBN (found in text) -> OpenLibrary lookup, then
filename + regex author search, then filename + Claude API as a last resort.
"""
from __future__ import annotations

import logging
import re

import httpx

log = logging.getLogger(__name__)

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
    """Look up title/author/publish_date on OpenLibrary. None if not found or on error."""
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
    return {
        "title": entry.get("title"),
        "author": authors[0]["name"] if authors else None,
        "publish_date": entry.get("publish_date"),
    }
