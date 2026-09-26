# Lögfræðibækur (dropfolder ingestion) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user drop a law-book PDF into a local folder and have it OCR'd if needed, converted to full text, and made searchable in Lausnir as a new `logfraedibaekur` source — no external API exists for this content, so metadata (title/author) is inferred from the PDF itself via a tiered fallback chain.

**Architecture:** One new `SourceConfig` (`logfraedibaekur`) following the existing `logfraediritgerdir` "non-verdict content" pattern (`case_number_is_title=True`, `parse_parties="none"`). A new pure module `engine/processors/book_metadata.py` resolves title/author/ISBN via ISBN→OpenLibrary, then filename+regex, then filename+Claude API. A new `scripts/import_baekur.py` orchestrates: extract text (reusing the existing `parse_pdf()` → `docling_ocr_pdf()` OCR-fallback pattern, with a longer timeout since books run hundreds of pages) → resolve metadata → build/validate/upsert `Document` → write markdown → move the PDF to the RAW layer. Chunking reuses the existing generic `scripts/backfill_chunks.py --source logfraedibaekur` unchanged.

**Tech Stack:** Python 3.13, `uv run python`, SQLAlchemy 2 async ORM, `httpx.AsyncClient`, `anthropic.AsyncAnthropic` (Haiku), `pdfplumber` (via existing `parse_pdf`), Docling+Tesseract (via existing `docling_ocr_pdf`), pytest + pytest-asyncio.

## Global Constraints

- Python runtime: `uv run python` (project uses uv)
- DB URL: `postgresql+asyncpg://geiri@localhost/lausnir_v2`
- All DB access: async (`AsyncSession`, `AsyncSessionLocal`) — no sync SQLAlchemy
- New source `short_name`: `"logfraedibaekur"`, `display_name`: `"Lögfræðibækur"`, `abbreviation`: `"Bók."`, `verdict_type_default`/`verdict_types_allowed`: `"Bók"`
- Dropfolder location: `{DATA_DIR}/dropfolder` (new `DROPFOLDER_DIR` constant, same `os.environ.get("DATA_DIR", ...)` base as `MARKDOWN_DIR`/`RAW_DIR`)
- Raw PDF storage after processing: `{DATA_DIR}/raw/logfraedibaekur/{external_id}.pdf` (via `config.pdf_path(external_id)`)
- Metadata resolution chain (in order): ISBN regex+checksum on first ~4000 chars of extracted text → OpenLibrary lookup (`openlibrary.org/api/books`) → filename-derived title + regex author search → Claude API (`claude-haiku-4-5-20251001`) author extraction as last resort
- `external_id` = cleaned ISBN if found, else a lowercase ASCII slug of the filename
- OCR fallback: reuse `docling_ocr_pdf()`, but with a new optional `timeout` parameter (default stays `300` for existing callers); the book import script passes `timeout=1800` since books run hundreds of pages vs. single rulings
- Chunking (`document_chunks` + `fts_is`): NOT written by the import script — run the existing `scripts/backfill_chunks.py --source logfraedibaekur` afterwards, unchanged
- Test mocking: `unittest.mock` (`patch`, `AsyncMock`, `MagicMock`) — this codebase does not use `respx` or `httpx_mock`
- Run tests with: `uv run pytest tests/<name>.py -v`

---

## File Structure

- **Create** `engine/processors/book_metadata.py` — ISBN detection+checksum, OpenLibrary lookup, filename slug, regex author search, Claude fallback, and the `resolve_book_metadata()` orchestrator. Pure/async functions, no PDF-parsing concerns (caller passes already-extracted text).
- **Modify** `engine/processors/pdf_parser.py` — add an optional `timeout` parameter to `docling_ocr_pdf()` (backward compatible, default unchanged).
- **Modify** `engine/config/sources.py` — add `DROPFOLDER_DIR` constant and the new `logfraedibaekur` `SourceConfig` entry.
- **Modify** `engine/processors/extractor.py` — add `_extract_logfraedibaekur()` and register it in `_EXTRACTORS`.
- **Modify** `engine/config/source_groups.py` — add `"logfraedibaekur"` to `_BAEKUR_SOURCES`.
- **Modify** `engine/search/queries.py` — add `"logfraedibaekur"` to `CHUNKED_SCOPE_KEYS`.
- **Modify** `tests/test_source_groups.py` — update `test_skemman_in_baekur` to expect both sources.
- **Create** `scripts/import_baekur.py` — the dropfolder ingestion script (`extract_text()`, `build_document()`, `process_dropfolder()`, `main()`).
- **Create** `tests/test_book_metadata.py`, `tests/test_pdf_parser.py`, `tests/test_extractor_baekur.py`, `tests/test_import_baekur.py`.

---

### Task 1: ISBN detection + OpenLibrary lookup

**Files:**
- Create: `engine/processors/book_metadata.py`
- Test: `tests/test_book_metadata.py`

**Interfaces:**
- Produces: `find_isbn(text: str) -> str | None`, `lookup_openlibrary(client: httpx.AsyncClient, isbn: str) -> dict | None` (keys `title: str|None`, `author: str|None`, `publish_date: str|None`). Later tasks in this file consume both.

- [ ] **Step 1: Write the failing tests for ISBN detection**

Create `tests/test_book_metadata.py`:

```python
from engine.processors.book_metadata import find_isbn


def test_finds_isbn13_with_hyphens():
    text = "Einhver texti\nISBN: 978-0-306-40615-7\nMeiri texti"
    assert find_isbn(text) == "9780306406157"


def test_finds_isbn13_without_hyphens():
    text = "9780306406157 kápusíða"
    assert find_isbn(text) == "9780306406157"


def test_finds_isbn10_with_x_check_digit():
    # 0-19-853453-1 is a real, valid ISBN-10 (Oxford UP)
    text = "ISBN 0-19-853453-1"
    assert find_isbn(text) == "0198534531"


def test_rejects_invalid_checksum():
    text = "ISBN 978-0-306-40615-8"  # wrong check digit (valid ISBN's last digit changed 7→8)
    assert find_isbn(text) is None


def test_returns_none_when_no_isbn_present():
    assert find_isbn("Bara venjulegur texti, engin tala hér.") is None


def test_returns_none_for_empty_text():
    assert find_isbn("") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_book_metadata.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'engine.processors.book_metadata'`

- [ ] **Step 3: Write the ISBN detection implementation**

Create `engine/processors/book_metadata.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_book_metadata.py -v`
Expected: 6 passed

- [ ] **Step 5: Add OpenLibrary lookup tests (httpx mock, matching the `test_http_utils.py` transport-replay convention)**

Append to `tests/test_book_metadata.py`:

```python
from engine.processors.book_metadata import lookup_openlibrary


class _Replay(httpx.AsyncBaseTransport):
    def __init__(self, response: httpx.Response):
        self._response = response

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self._response._request = request
        return self._response


async def test_lookup_openlibrary_returns_metadata():
    body = {
        "ISBN:9780306406157": {
            "title": "Kröfuréttur I",
            "authors": [{"name": "Páll Sigurðsson"}],
            "publish_date": "1985",
        }
    }
    transport = _Replay(httpx.Response(200, json=body))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await lookup_openlibrary(client, "9780306406157")
    assert result == {
        "title": "Kröfuréttur I",
        "author": "Páll Sigurðsson",
        "publish_date": "1985",
    }


async def test_lookup_openlibrary_returns_none_when_isbn_unknown():
    transport = _Replay(httpx.Response(200, json={}))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await lookup_openlibrary(client, "9780306406157")
    assert result is None


async def test_lookup_openlibrary_returns_none_on_http_error():
    transport = _Replay(httpx.Response(500))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await lookup_openlibrary(client, "9780306406157")
    assert result is None


async def test_lookup_openlibrary_handles_missing_authors():
    body = {"ISBN:9780306406157": {"title": "Ónefnt rit"}}
    transport = _Replay(httpx.Response(200, json=body))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await lookup_openlibrary(client, "9780306406157")
    assert result == {"title": "Ónefnt rit", "author": None, "publish_date": None}
```

Add `import httpx` at the top of `tests/test_book_metadata.py` if not already present (the ISBN tests above don't need it, this step does).

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_book_metadata.py -v`
Expected: 10 passed

- [ ] **Step 7: Commit**

```bash
git add engine/processors/book_metadata.py tests/test_book_metadata.py
git commit -m "feat: add ISBN detection and OpenLibrary lookup for book metadata"
```

---

### Task 2: Filename/regex/Claude fallback chain + orchestrator

**Files:**
- Modify: `engine/processors/book_metadata.py`
- Test: `tests/test_book_metadata.py`

**Interfaces:**
- Consumes: `find_isbn(text) -> str|None`, `lookup_openlibrary(client, isbn) -> dict|None` (Task 1)
- Produces: `slugify_filename(pdf_path: Path) -> str`, `find_author_regex(text: str) -> str|None`, `find_author_llm(text: str) -> str|None` (async), `parse_publish_year(s: str|None) -> date|None`, `external_id_from_filename(pdf_path: Path) -> str`, `resolve_book_metadata(client: httpx.AsyncClient, text: str, pdf_path: Path) -> dict` (async, keys `title: str`, `author: str|None`, `isbn: str|None`, `external_id: str`, `document_date: date|None`). Task 7 (`scripts/import_baekur.py`) calls `resolve_book_metadata()` directly and consumes its return dict.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_book_metadata.py`:

```python
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

from engine.processors.book_metadata import (
    external_id_from_filename,
    find_author_llm,
    find_author_regex,
    parse_publish_year,
    resolve_book_metadata,
    slugify_filename,
)


def test_slugify_filename_replaces_separators():
    assert slugify_filename(Path("Skadabotarettur_a_Islandi.pdf")) == "Skadabotarettur a Islandi"


def test_slugify_filename_strips_extension_only():
    assert slugify_filename(Path("Bók-með-bandstriki.pdf")) == "Bók með bandstriki"


def test_find_author_regex_eftir_pattern():
    text = "Titill bókar\n\nEftir Pál Sigurðsson\n\nMeiri texti hér."
    assert find_author_regex(text) == "Pál Sigurðsson"


def test_find_author_regex_hofundur_pattern():
    text = "Höfundur: Sigríður Logadóttir\n\nInngangur."
    assert find_author_regex(text) == "Sigríður Logadóttir"


def test_find_author_regex_returns_none_when_absent():
    assert find_author_regex("Ekkert höfundarmerki hér, bara texti.") is None


def test_parse_publish_year_extracts_four_digit_year():
    assert parse_publish_year("October 1, 1988") == date(1988, 1, 1)
    assert parse_publish_year("1985") == date(1985, 1, 1)


def test_parse_publish_year_returns_none_for_missing():
    assert parse_publish_year(None) is None
    assert parse_publish_year("") is None
    assert parse_publish_year("n.d.") is None


def test_external_id_from_filename_is_ascii_slug():
    assert external_id_from_filename(Path("Kröfuréttur I.pdf")) == "krofurettur_i"


async def test_find_author_llm_parses_json_response():
    mock_message = AsyncMock()
    mock_message.content = [type("Block", (), {"text": '{"author": "Páll Sigurðsson"}'})()]
    with patch("engine.processors.book_metadata.AsyncAnthropic") as MockClient:
        MockClient.return_value.messages.create = AsyncMock(return_value=mock_message)
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            result = await find_author_llm("einhver texti")
    assert result == "Páll Sigurðsson"


async def test_find_author_llm_returns_none_on_failure():
    with patch("engine.processors.book_metadata.AsyncAnthropic") as MockClient:
        MockClient.return_value.messages.create = AsyncMock(side_effect=RuntimeError("boom"))
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            result = await find_author_llm("einhver texti")
    assert result is None


async def test_resolve_book_metadata_uses_isbn_and_openlibrary():
    body = {
        "ISBN:9780306406157": {
            "title": "Kröfuréttur I",
            "authors": [{"name": "Páll Sigurðsson"}],
            "publish_date": "1985",
        }
    }
    transport = _Replay(httpx.Response(200, json=body))
    text = "ISBN 978-0-306-40615-7\n\nKröfuréttur I"
    async with httpx.AsyncClient(transport=transport) as client:
        meta = await resolve_book_metadata(client, text, Path("einhver_skra.pdf"))
    assert meta == {
        "title": "Kröfuréttur I",
        "author": "Páll Sigurðsson",
        "isbn": "9780306406157",
        "external_id": "9780306406157",
        "document_date": date(1985, 1, 1),
    }


async def test_resolve_book_metadata_falls_back_to_filename_and_regex():
    transport = _Replay(httpx.Response(200, json={}))  # ISBN not found on OpenLibrary
    text = "9780306406157\n\nEftir Jón Jónsson\n\nInngangur."
    async with httpx.AsyncClient(transport=transport) as client:
        meta = await resolve_book_metadata(client, text, Path("Skadabotarettur.pdf"))
    assert meta["title"] == "Skadabotarettur"
    assert meta["author"] == "Jón Jónsson"
    assert meta["isbn"] == "9780306406157"
    assert meta["external_id"] == "9780306406157"


async def test_resolve_book_metadata_falls_back_to_llm_when_regex_finds_nothing():
    mock_message = AsyncMock()
    mock_message.content = [type("Block", (), {"text": '{"author": "Ásta Ólafsdóttir"}'})()]
    text = "Engin ISBN hér, ekkert höfundarmerki heldur, bara laus texti."
    with patch("engine.processors.book_metadata.AsyncAnthropic") as MockClient:
        MockClient.return_value.messages.create = AsyncMock(return_value=mock_message)
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            async with httpx.AsyncClient() as client:
                meta = await resolve_book_metadata(client, text, Path("Ohefdbaerabok.pdf"))
    assert meta["author"] == "Ásta Ólafsdóttir"
    assert meta["isbn"] is None
    assert meta["external_id"] == "ohefdbaerabok"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_book_metadata.py -v`
Expected: FAIL — `ImportError: cannot import name 'external_id_from_filename'` (and siblings)

- [ ] **Step 3: Write the implementation**

Append to `engine/processors/book_metadata.py`:

```python
import json
import os
import unicodedata
from datetime import date
from pathlib import Path

from anthropic import AsyncAnthropic

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
    """Filesystem-safe fallback external_id: lowercase ASCII slug of the filename."""
    s = unicodedata.normalize("NFKD", pdf_path.stem).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r'[^A-Za-z0-9]+', '_', s).strip('_').lower()
    return s or "book"


async def find_author_llm(text: str) -> str | None:
    """Ask Claude to extract the author name from the book's opening pages.

    Last resort — only called when regex finds nothing. Returns None on any failure.
    """
    client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    try:
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
    """Resolve {title, author, isbn, external_id, document_date} for a dropped book PDF.

    Tier 1: ISBN found in text -> OpenLibrary lookup.
    Tier 2: filename -> title, regex on text -> author.
    Tier 3: regex found nothing -> Claude API on text -> author.
    """
    isbn = find_isbn(text)
    if isbn:
        ol = await lookup_openlibrary(client, isbn)
        if ol and ol.get("title"):
            return {
                "title": ol["title"],
                "author": ol.get("author"),
                "isbn": isbn,
                "external_id": isbn,
                "document_date": parse_publish_year(ol.get("publish_date")),
            }

    title = slugify_filename(pdf_path)
    author = find_author_regex(text)
    if author is None:
        author = await find_author_llm(text)

    return {
        "title": title,
        "author": author,
        "isbn": isbn,
        "external_id": isbn or external_id_from_filename(pdf_path),
        "document_date": None,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_book_metadata.py -v`
Expected: 20 passed

- [ ] **Step 5: Commit**

```bash
git add engine/processors/book_metadata.py tests/test_book_metadata.py
git commit -m "feat: add filename/regex/Claude fallback chain for book metadata"
```

---

### Task 3: `docling_ocr_pdf` optional timeout

**Files:**
- Modify: `engine/processors/pdf_parser.py:408-446` (the `docling_ocr_pdf` function)
- Test: `tests/test_pdf_parser.py` (new)

**Interfaces:**
- Produces: `docling_ocr_pdf(pdf_bytes: bytes, timeout: int = 300) -> str | None` (signature change is backward compatible — all 3 existing callers in `scripts/import_enf.py`, `scripts/import_landsdomar.py`, `scripts/import_fjarskiptastofa.py` call it with a single positional arg and keep working unchanged).

- [ ] **Step 1: Write the failing test**

Create `tests/test_pdf_parser.py`:

```python
from unittest.mock import MagicMock, patch

from engine.processors.pdf_parser import docling_ocr_pdf


def test_docling_ocr_pdf_default_timeout_is_300():
    with patch("engine.processors.pdf_parser.subprocess.run") as mock_run:
        mock_run.side_effect = FileNotFoundError()  # short-circuit, we only inspect the call
        docling_ocr_pdf(b"%PDF-fake")
    _, kwargs = mock_run.call_args
    assert kwargs["timeout"] == 300


def test_docling_ocr_pdf_accepts_custom_timeout():
    with patch("engine.processors.pdf_parser.subprocess.run") as mock_run:
        mock_run.side_effect = FileNotFoundError()
        docling_ocr_pdf(b"%PDF-fake", timeout=1800)
    _, kwargs = mock_run.call_args
    assert kwargs["timeout"] == 1800


def test_docling_ocr_pdf_returns_none_on_timeout_expired():
    import subprocess
    with patch("engine.processors.pdf_parser.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="docling", timeout=1800)
        result = docling_ocr_pdf(b"%PDF-fake", timeout=1800)
    assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_pdf_parser.py -v`
Expected: FAIL — `TypeError: docling_ocr_pdf() got an unexpected keyword argument 'timeout'`

- [ ] **Step 3: Add the `timeout` parameter**

In `engine/processors/pdf_parser.py`, replace the `docling_ocr_pdf` signature and its `subprocess.run` call:

```python
def docling_ocr_pdf(pdf_bytes: bytes, timeout: int = 300) -> str | None:
    """OCR an image-based PDF using Docling + Tesseract (Icelandic).

    Used for PDFs whose text layer is garbled due to font encoding issues
    (e.g. Úrskurðarnefnd fjarskipta- og póstmála documents), or for long
    scanned books where the default 300 s is too short (pass a larger
    `timeout` — e.g. `scripts/import_baekur.py` uses 1800).
    Returns plain text with markdown formatting stripped, or None on failure.
    """
    import pathlib
    import re
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = pathlib.Path(tmpdir) / "input.pdf"
        pdf_path.write_bytes(pdf_bytes)

        try:
            subprocess.run(
                [
                    "uvx", "docling", str(pdf_path),
                    "--to", "md",
                    "--force-ocr",
                    "--ocr-engine", "tesseract",
                    "--ocr-lang", "isl",
                    "--output", tmpdir,
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            return None

        md_path = pathlib.Path(tmpdir) / "input.md"
        if not md_path.exists():
            return None

        md_text = md_path.read_text()
        text = re.sub(r"^#+\s*", "", md_text, flags=re.MULTILINE)
        text = re.sub(r"^[-*]\s+", "", text, flags=re.MULTILINE)
        return text.strip() or None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_pdf_parser.py -v`
Expected: 3 passed

- [ ] **Step 5: Run the full existing test suite to confirm no regression in the 3 existing callers**

Run: `uv run pytest tests/ -v -k "enf or landsdomar or fjarskiptastofa"`
Expected: all pass (these callers pass `pdf_bytes` positionally only, unaffected by the new keyword-only-by-default `timeout` param)

- [ ] **Step 6: Commit**

```bash
git add engine/processors/pdf_parser.py tests/test_pdf_parser.py
git commit -m "feat: add optional timeout parameter to docling_ocr_pdf"
```

---

### Task 4: `SourceConfig` + category/chunk-routing wiring

**Files:**
- Modify: `engine/config/sources.py:14-16` (add `DROPFOLDER_DIR`), and append a new `SourceConfig` after the `logfraediritgerdir` entry (around line 830)
- Modify: `engine/config/source_groups.py:40` (`_BAEKUR_SOURCES`)
- Modify: `engine/search/queries.py:252` (`CHUNKED_SCOPE_KEYS`)
- Modify: `tests/test_source_groups.py` (`test_skemman_in_baekur`)
- Test: `tests/test_sources.py` (new test appended)

**Interfaces:**
- Produces: `SOURCE_REGISTRY["logfraedibaekur"]` (a `SourceConfig`), `DROPFOLDER_DIR: str` constant. Task 5 (`_extract_logfraedibaekur`) and Task 7 (`scripts/import_baekur.py`) both consume `get_config("logfraedibaekur")` and `DROPFOLDER_DIR`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sources.py`:

```python
def test_logfraedibaekur_source_registered():
    from engine.config.sources import SOURCE_REGISTRY
    assert "logfraedibaekur" in SOURCE_REGISTRY
    cfg = SOURCE_REGISTRY["logfraedibaekur"]
    assert cfg.display_name == "Lögfræðibækur"
    assert cfg.abbreviation == "Bók."
    assert cfg.parse_parties == "none"
    assert cfg.verdict_type_default == "Bók"
    assert cfg.verdict_types_allowed == ["Bók"]
    assert cfg.case_number_is_title is True


def test_dropfolder_dir_is_under_data_dir():
    from engine.config.sources import DROPFOLDER_DIR, _DATA_DIR
    assert DROPFOLDER_DIR == f"{_DATA_DIR}/dropfolder"


def test_logfraedibaekur_in_baekur_category():
    from engine.config.source_groups import SCOPE_TREE
    baekur = next(c for c in SCOPE_TREE if c["key"] == "baekur")
    leaf_keys = {leaf["key"] for leaf in baekur["children"]}
    assert "logfraedibaekur" in leaf_keys


def test_logfraedibaekur_is_chunked_scope():
    from engine.search.queries import _scope_is_chunked
    assert _scope_is_chunked(["logfraedibaekur"]) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sources.py -v`
Expected: FAIL — `KeyError: 'logfraedibaekur'` / `ImportError: cannot import name 'DROPFOLDER_DIR'`

- [ ] **Step 3: Add `DROPFOLDER_DIR` and the `SourceConfig`**

In `engine/config/sources.py`, change lines 14-16:

```python
_DATA_DIR = os.environ.get("DATA_DIR", "/Volumes/RuleOfLaw/Lausnir_Data")
MARKDOWN_DIR: str = os.path.join(_DATA_DIR, "markdown")
RAW_DIR: str = os.path.join(_DATA_DIR, "raw")
DROPFOLDER_DIR: str = os.path.join(_DATA_DIR, "dropfolder")
```

Then, immediately after the `logfraediritgerdir` `SourceConfig` entry (the one ending `case_number_is_title=True,\n    ),` right before the closing `]` of `_SOURCES`), add:

```python
    # ── Lögfræðibækur (dropfolder) ────────────────────────────────────────────
    SourceConfig(
        short_name="logfraedibaekur",
        display_name="Lögfræðibækur",
        abbreviation="Bók.",
        instance_tier=1,           # á ekki við bækur — sama og logfraediritgerdir
        has_lower_court=False,
        parse_parties="none",      # höfundur → plaintiffs[0].name handvirkt í import
        verdict_type_default="Bók",
        verdict_types_allowed=["Bók"],
        case_number_prefix="",     # titill fer í case_number — ekkert forskeyti
        pdf_crop=None,
        h1_use_display_name=True,
        case_number_is_title=True, # titill, ekki málsnúmer — sama og logfraediritgerdir
    ),
```

- [ ] **Step 4: Wire into the Bækur category**

In `engine/config/source_groups.py`, change:

```python
_BAEKUR_SOURCES = ["logfraediritgerdir"]
```

to:

```python
_BAEKUR_SOURCES = ["logfraediritgerdir", "logfraedibaekur"]
```

- [ ] **Step 5: Wire into chunk-aware search routing**

In `engine/search/queries.py`, change:

```python
CHUNKED_SCOPE_KEYS: frozenset[str] = frozenset({"logfraediritgerdir", "baekur"})
```

to:

```python
CHUNKED_SCOPE_KEYS: frozenset[str] = frozenset({"logfraediritgerdir", "logfraedibaekur", "baekur"})
```

- [ ] **Step 6: Update the now-outdated hardcoded assertion in `test_source_groups.py`**

In `tests/test_source_groups.py`, change:

```python
def test_skemman_in_baekur():
    baekur = next(c for c in SCOPE_TREE if c["key"] == "baekur")
    assert [leaf["key"] for leaf in baekur["children"]] == ["logfraediritgerdir"]
```

to:

```python
def test_skemman_in_baekur():
    baekur = next(c for c in SCOPE_TREE if c["key"] == "baekur")
    assert [leaf["key"] for leaf in baekur["children"]] == ["logfraediritgerdir", "logfraedibaekur"]
```

- [ ] **Step 7: Run tests to verify everything passes, including the full existing suite**

Run: `uv run pytest tests/test_sources.py tests/test_source_groups.py tests/test_search_queries.py -v`
Expected: all pass

- [ ] **Step 8: Commit**

```bash
git add engine/config/sources.py engine/config/source_groups.py engine/search/queries.py tests/test_sources.py tests/test_source_groups.py
git commit -m "feat: register logfraedibaekur source, category, and chunk routing"
```

---

### Task 5: Extractor for `logfraedibaekur`

**Files:**
- Modify: `engine/processors/extractor.py` (add `_extract_logfraedibaekur`, register in `_EXTRACTORS`)
- Test: `tests/test_extractor_baekur.py` (new)

**Interfaces:**
- Consumes: `get_config("logfraedibaekur")` (Task 4)
- Produces: `_EXTRACTORS["logfraedibaekur"]` registered, callable via `Extractor(config).extract(raw)` where `raw` has keys `title: str`, `author: str|None`, `isbn: str|None`, `document_date: date|None`, `source_filename: str`, `pdf_text: str|None`. Task 7 builds this `raw` dict directly from `resolve_book_metadata()`'s return value plus the extracted body text.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_extractor_baekur.py`:

```python
from datetime import date

from engine.config.sources import get_config
from engine.processors.extractor import Extractor

CONFIG = get_config("logfraedibaekur")


def _raw(**overrides) -> dict:
    base = {
        "title": "Kröfuréttur I",
        "author": "Páll Sigurðsson",
        "isbn": "9780306406157",
        "document_date": date(1985, 1, 1),
        "source_filename": "krofurettur.pdf",
        "pdf_text": "Meginmál bókarinnar hér.",
    }
    return {**base, **overrides}


def test_extract_maps_title_to_case_number():
    result = Extractor(CONFIG).extract(_raw())
    assert result["case_number"] == "Kröfuréttur I"


def test_extract_maps_author_to_plaintiffs():
    result = Extractor(CONFIG).extract(_raw())
    assert result["plaintiffs"] == [{"name": "Páll Sigurðsson", "lawyer": None}]


def test_extract_no_author_gives_none_plaintiffs():
    result = Extractor(CONFIG).extract(_raw(author=None))
    assert result["plaintiffs"] is None


def test_extract_uses_config_court_and_verdict_type():
    result = Extractor(CONFIG).extract(_raw())
    assert result["court"] == "Bók."
    assert result["verdict_type"] == "Bók"


def test_extract_passes_through_document_date():
    result = Extractor(CONFIG).extract(_raw())
    assert result["document_date"] == date(1985, 1, 1)


def test_extract_body_text_from_pdf_text():
    result = Extractor(CONFIG).extract(_raw())
    assert result["body_text"] == "Meginmál bókarinnar hér."


def test_extract_no_body_text_gives_none():
    result = Extractor(CONFIG).extract(_raw(pdf_text=None))
    assert result["body_text"] is None


def test_extract_raw_api_data_excludes_pdf_text():
    result = Extractor(CONFIG).extract(_raw())
    assert "pdf_text" not in result["raw_api_data"]
    assert result["raw_api_data"]["isbn"] == "9780306406157"


def test_extract_instance_tier_and_defendants_are_none():
    result = Extractor(CONFIG).extract(_raw())
    assert result["instance_tier"] is None
    assert result["defendants"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_extractor_baekur.py -v`
Expected: FAIL — `NotImplementedError: No extractor registered for 'logfraedibaekur'`

- [ ] **Step 3: Write the extractor and register it**

In `engine/processors/extractor.py`, add this function immediately before the `_EXTRACTORS` registry dict (right after `_extract_logfraediritgerdir`):

```python
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

    raw_meta = {k: v for k, v in raw.items() if k != "pdf_text"}

    return {
        "case_number": title or None,
        "document_date": raw.get("document_date"),
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
```

Then add the registration to `_EXTRACTORS`, right after the `"logfraediritgerdir": _extract_logfraediritgerdir,` line:

```python
    "logfraedibaekur": _extract_logfraedibaekur,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_extractor_baekur.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add engine/processors/extractor.py tests/test_extractor_baekur.py
git commit -m "feat: add extractor for logfraedibaekur"
```

---

### Task 6: Validation guard test for `logfraedibaekur`

**Files:**
- Test: `tests/test_sources.py` (append)

No production code changes — `engine/processors/validator.py` is already fully generic (reads `config.verdict_types_allowed`, `config.case_number_is_title`, `config.parse_parties`). This task locks in that the new config produces a clean validation pass for a well-formed book, and a `document_date` flag (not a blocker) when the date is unknown — matching the project's "store but don't skip" validation philosophy.

**Interfaces:**
- Consumes: `get_config("logfraedibaekur")` (Task 4), `validate()` (existing, unmodified), `Extractor` (Task 5).

- [ ] **Step 1: Write the test**

Append to `tests/test_sources.py`:

```python
def test_logfraedibaekur_validates_clean_when_complete():
    from datetime import date
    from engine.config.sources import get_config
    from engine.database.models import Document
    from engine.processors.extractor import Extractor
    from engine.processors.validator import validate
    import uuid

    config = get_config("logfraedibaekur")
    raw = {
        "title": "Kröfuréttur I",
        "author": "Páll Sigurðsson",
        "isbn": "9780306406157",
        "document_date": date(1985, 1, 1),
        "source_filename": "krofurettur.pdf",
        "pdf_text": "x" * 300,  # over the 200-char minimum
    }
    fields = Extractor(config).extract(raw)
    doc = Document(id=uuid.uuid4(), source_id=uuid.uuid4(), external_id="9780306406157", **fields)
    errors = validate(doc, config)
    assert errors == []


def test_logfraedibaekur_flags_missing_document_date_without_blocking():
    from engine.config.sources import get_config
    from engine.database.models import Document
    from engine.processors.extractor import Extractor
    from engine.processors.validator import validate
    import uuid

    config = get_config("logfraedibaekur")
    raw = {
        "title": "Óþekkt bók",
        "author": None,
        "isbn": None,
        "document_date": None,
        "source_filename": "ohefdbaerabok.pdf",
        "pdf_text": "x" * 300,
    }
    fields = Extractor(config).extract(raw)
    doc = Document(id=uuid.uuid4(), source_id=uuid.uuid4(), external_id="ohefdbaerabok", **fields)
    errors = validate(doc, config)
    fields_with_errors = {e["field"] for e in errors}
    assert "document_date" in fields_with_errors
    assert "keywords" in fields_with_errors  # books never have keywords — expected, non-blocking
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `uv run pytest tests/test_sources.py -v`
Expected: all pass (no implementation changes needed — `validate()` is already generic)

- [ ] **Step 3: Commit**

```bash
git add tests/test_sources.py
git commit -m "test: lock in validator behavior for logfraedibaekur"
```

---

### Task 7: `scripts/import_baekur.py` — the dropfolder ingestion script

**Files:**
- Create: `scripts/import_baekur.py`
- Test: `tests/test_import_baekur.py` (new)

**Interfaces:**
- Consumes: `resolve_book_metadata()` (Task 2), `docling_ocr_pdf(pdf_bytes, timeout=...)` (Task 3), `get_config("logfraedibaekur")` / `DROPFOLDER_DIR` (Task 4), `Extractor` (Task 5), `validate()` (existing), `unique_verdict_filename()` / `write_markdown()` (existing, unmodified).
- Produces: `extract_text(pdf_bytes: bytes) -> str`, `book_stem(title: str, max_len: int = 40) -> str`, `build_document(meta: dict, body_text: str, source_id: uuid.UUID, config: SourceConfig) -> Document`, `process_dropfolder(dropfolder: Path, *, dry_run: bool) -> dict` (async), `main(dry_run: bool) -> None` (async).

- [ ] **Step 1: Write the failing tests for `extract_text` and `book_stem`**

Create `tests/test_import_baekur.py`:

```python
from unittest.mock import patch


def test_extract_text_uses_parse_pdf_when_text_layer_present():
    from scripts.import_baekur import extract_text
    with patch("scripts.import_baekur.parse_pdf", return_value="Alvöru texti") as mock_parse:
        with patch("scripts.import_baekur.docling_ocr_pdf") as mock_ocr:
            result = extract_text(b"%PDF-fake")
    mock_parse.assert_called_once_with(b"%PDF-fake")
    mock_ocr.assert_not_called()
    assert result == "Alvöru texti"


def test_extract_text_falls_back_to_ocr_when_text_layer_empty():
    from scripts.import_baekur import extract_text
    with patch("scripts.import_baekur.parse_pdf", return_value=""):
        with patch("scripts.import_baekur.docling_ocr_pdf", return_value="OCR texti") as mock_ocr:
            result = extract_text(b"%PDF-fake")
    mock_ocr.assert_called_once_with(b"%PDF-fake", timeout=1800)
    assert result == "OCR texti"


def test_extract_text_returns_empty_string_when_both_fail():
    from scripts.import_baekur import extract_text
    with patch("scripts.import_baekur.parse_pdf", return_value=""):
        with patch("scripts.import_baekur.docling_ocr_pdf", return_value=None):
            result = extract_text(b"%PDF-fake")
    assert result == ""


def test_book_stem_transliterates_and_caps_length():
    from scripts.import_baekur import book_stem
    stem = book_stem("Skaðabótaréttur á Íslandi og nágrannalöndum, ítarleg umfjöllun")
    assert stem == stem.encode("ascii", "ignore").decode("ascii")  # pure ASCII
    assert len(stem) <= 40
    assert stem.startswith("Skadabotarettur")


def test_book_stem_empty_title_returns_book_fallback():
    from scripts.import_baekur import book_stem
    assert book_stem("") == "book"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_import_baekur.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.import_baekur'`

- [ ] **Step 3: Write `scripts/import_baekur.py` (part 1: imports, `extract_text`, `book_stem`, `build_document`)**

Create `scripts/import_baekur.py`:

```python
"""Import law books from a local dropfolder into lausnir_v2.

Unlike every other source, this one has no external API — a human drops a
PDF into {DATA_DIR}/dropfolder/ and this script:
  1. Extracts full text (parse_pdf -> docling_ocr_pdf fallback, with a longer
     OCR timeout than court rulings since books run hundreds of pages)
  2. Resolves title/author/ISBN via the tiered metadata chain
     (engine/processors/book_metadata.py)
  3. Builds + validates + upserts the Document, writes .md, moves the PDF
     to {DATA_DIR}/raw/logfraedibaekur/{external_id}.pdf

Chunking (document_chunks + fts_is) is NOT done here — run
    uv run python scripts/backfill_chunks.py --source logfraedibaekur
afterwards, same as for logfraediritgerdir.

Usage:
    set -a; . ./.env; set +a
    uv run python scripts/import_baekur.py --dry-run
    uv run python scripts/import_baekur.py
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import unicodedata
import uuid
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, null as sa_null, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from engine.config.sources import DROPFOLDER_DIR, SourceConfig, get_config
import engine.database.connection as _db_conn
from engine.database.connection import init_db
from engine.database.models import Document, Source
from engine.processors.book_metadata import resolve_book_metadata
from engine.processors.extractor import Extractor
from engine.processors.http_utils import make_client
from engine.processors.pdf_parser import docling_ocr_pdf, parse_pdf
from engine.processors.renderer import unique_verdict_filename, write_markdown
from engine.processors.validator import validate

log = logging.getLogger(__name__)

_OCR_TIMEOUT = 1800  # 30 min — books run hundreds of pages, unlike single rulings

_TRANSLIT = str.maketrans({
    "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ý": "y", "ð": "d",
    "þ": "th", "æ": "ae", "ö": "o", "Á": "A", "É": "E", "Í": "I", "Ó": "O",
    "Ú": "U", "Ý": "Y", "Ð": "D", "Þ": "Th", "Æ": "Ae", "Ö": "O",
})


def extract_text(pdf_bytes: bytes) -> str:
    """Extract full text; falls back to OCR (longer timeout) if the text layer is empty."""
    text = parse_pdf(pdf_bytes)
    if not text:
        text = docling_ocr_pdf(pdf_bytes, timeout=_OCR_TIMEOUT) or ""
    return text


def book_stem(title: str, max_len: int = 40) -> str:
    """Return the filename stem (no extension) derived from the book title."""
    t = title.translate(_TRANSLIT)
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode("ascii")
    t = re.sub(r"[^A-Za-z0-9]+", "_", t).strip("_")
    return t[:max_len].rstrip("_") or "book"


def build_document(
    meta: dict[str, Any],
    body_text: str,
    source_id: uuid.UUID,
    config: SourceConfig,
) -> Document:
    """Build a Document from resolved metadata + extracted text. Pure — no I/O."""
    raw = {
        "title": meta["title"],
        "author": meta["author"],
        "isbn": meta["isbn"],
        "document_date": meta["document_date"],
        "source_filename": None,
        "pdf_text": body_text or None,
    }
    fields = Extractor(config).extract(raw)
    return Document(
        id=uuid.uuid4(),
        source_id=source_id,
        external_id=meta["external_id"],
        url=None,
        **fields,
    )
```

- [ ] **Step 4: Run the `extract_text`/`book_stem` tests to verify they pass**

Run: `uv run pytest tests/test_import_baekur.py -v`
Expected: 5 passed

- [ ] **Step 5: Write the failing test for `build_document`**

Append to `tests/test_import_baekur.py`:

```python
def test_build_document_maps_metadata_and_body():
    from datetime import date
    from engine.config.sources import get_config
    from scripts.import_baekur import build_document

    config = get_config("logfraedibaekur")
    meta = {
        "title": "Kröfuréttur I",
        "author": "Páll Sigurðsson",
        "isbn": "9780306406157",
        "external_id": "9780306406157",
        "document_date": date(1985, 1, 1),
    }
    doc = build_document(meta, "Meginmál bókarinnar.", __import__("uuid").uuid4(), config)

    assert doc.external_id == "9780306406157"
    assert doc.case_number == "Kröfuréttur I"
    assert doc.plaintiffs == [{"name": "Páll Sigurðsson", "lawyer": None}]
    assert doc.body_text == "Meginmál bókarinnar."
    assert doc.court == "Bók."
    assert doc.document_date == date(1985, 1, 1)


def test_build_document_no_author_gives_none_plaintiffs():
    from scripts.import_baekur import build_document
    from engine.config.sources import get_config

    config = get_config("logfraedibaekur")
    meta = {
        "title": "Ónefnd bók",
        "author": None,
        "isbn": None,
        "external_id": "onefnd_bok",
        "document_date": None,
    }
    doc = build_document(meta, "texti", __import__("uuid").uuid4(), config)
    assert doc.plaintiffs is None
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_import_baekur.py -v`
Expected: 7 passed

- [ ] **Step 7: Write `scripts/import_baekur.py` (part 2: `_ensure_source`, `_upsert_doc`, `process_dropfolder`, `main`)**

Append to `scripts/import_baekur.py`:

```python
async def _ensure_source(session: AsyncSession, config: SourceConfig) -> uuid.UUID:
    result = await session.execute(
        select(Source).where(Source.short_name == config.short_name)
    )
    source = result.scalar_one_or_none()
    if source is None:
        source_id = uuid.uuid4()
        session.add(Source(
            id=source_id,
            short_name=config.short_name,
            display_name=config.display_name,
        ))
        await session.commit()
        return source_id
    return source.id


async def _upsert_doc(session: AsyncSession, doc: Document) -> None:
    def _v(val: Any) -> Any:
        return sa_null() if val is None else val

    values: dict[str, Any] = {
        "id": doc.id,
        "source_id": doc.source_id,
        "external_id": doc.external_id,
        "url": _v(doc.url),
        "raw_api_data": _v(doc.raw_api_data),
        "case_number": _v(doc.case_number),
        "document_date": _v(doc.document_date),
        "court": _v(doc.court),
        "verdict_type": _v(doc.verdict_type),
        "instance_tier": _v(doc.instance_tier),
        "case_type": _v(doc.case_type),
        "plaintiffs": _v(doc.plaintiffs),
        "defendants": _v(doc.defendants),
        "keywords": _v(doc.keywords),
        "summary": _v(doc.summary),
        "body_text": _v(doc.body_text),
        "lower_body_text": _v(doc.lower_body_text),
        "validation_errors": _v(doc.validation_errors),
    }
    update_cols = {k: v for k, v in values.items() if k not in ("id", "source_id", "external_id")}
    update_cols["updated_at"] = func.now()
    await session.execute(
        pg_insert(Document)
        .values(**values)
        .on_conflict_do_update(constraint="uq_doc_source_external", set_=update_cols)
    )


async def process_dropfolder(dropfolder: Path, *, dry_run: bool) -> dict:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    config = get_config("logfraedibaekur")
    stats = {"total": 0, "imported": 0, "errors": 0}

    pdfs = sorted(dropfolder.glob("*.pdf"))
    if not pdfs:
        log.info("No PDFs found in %s", dropfolder)
        return stats

    source_id = None
    taken: set[str] = set()
    if not dry_run:
        await init_db()
        async with _db_conn.AsyncSessionLocal() as session:
            source_id = await _ensure_source(session, config)
            existing = (await session.execute(
                select(Document.verdict_filename)
                .where(Document.source_id == source_id)
                .where(Document.verdict_filename.isnot(None))
            )).scalars().all()
        taken = set(existing)

    async with make_client() as client:
        for pdf_path in pdfs:
            stats["total"] += 1
            pdf_bytes = pdf_path.read_bytes()

            try:
                body_text = extract_text(pdf_bytes)
                meta = await resolve_book_metadata(client, body_text[:4000], pdf_path)
                doc = build_document(meta, body_text, source_id or uuid.uuid4(), config)
            except Exception as exc:  # noqa: BLE001
                log.error("Failed to process %s: %s", pdf_path.name, exc)
                stats["errors"] += 1
                continue

            errors = validate(doc, config)
            doc.validation_errors = errors or None
            if errors:
                stats["errors"] += 1

            if dry_run:
                plf = doc.plaintiffs[0] if doc.plaintiffs else {}
                print(f"{pdf_path.name}:")
                print(f"  title      : {doc.case_number}")
                print(f"  author     : {plf.get('name')}")
                print(f"  external_id: {doc.external_id}")
                print(f"  body_text  : {len(doc.body_text or '')} chars")
                if errors:
                    print(f"  ⚠ errors   : {[e['field'] + ':' + e['message'] for e in errors]}")
                stats["imported"] += 1
                continue

            async with _db_conn.AsyncSessionLocal() as session:
                try:
                    await _upsert_doc(session, doc)
                    await session.commit()
                except Exception as exc:  # noqa: BLE001
                    log.error("Upsert failed for %s: %s", pdf_path.name, exc)
                    await session.rollback()
                    stats["errors"] += 1
                    continue

            vf = None
            if doc.body_text:
                vf = unique_verdict_filename(book_stem(doc.case_number or "book"), taken)
                taken.add(vf)
                write_markdown(doc, config, vf=vf)
                async with _db_conn.AsyncSessionLocal() as session:
                    doc_row = (await session.execute(
                        select(Document).where(Document.id == doc.id)
                    )).scalar_one()
                    doc_row.verdict_filename = vf
                    await session.commit()

            raw_pdf_path = config.pdf_path(doc.external_id)
            raw_pdf_path.parent.mkdir(parents=True, exist_ok=True)
            pdf_path.rename(raw_pdf_path)

            stats["imported"] += 1

    return stats


async def main(dry_run: bool) -> None:
    dropfolder = Path(DROPFOLDER_DIR)
    dropfolder.mkdir(parents=True, exist_ok=True)
    stats = await process_dropfolder(dropfolder, dry_run=dry_run)
    print(f"DONE {stats}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Extract+resolve+validate+report, no DB/disk writes")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
```

- [ ] **Step 8: Run the full test file to verify everything passes**

Run: `uv run pytest tests/test_import_baekur.py -v`
Expected: 7 passed (this task added no new automated tests for `process_dropfolder`/`main` — they're DB/filesystem orchestration, verified manually in Step 9, matching this codebase's existing convention of only unit-testing the pure builder functions for `scripts/import_*.py`, e.g. `test_import_haestirettur.py`)

- [ ] **Step 9: Manual smoke test with a real PDF**

```bash
mkdir -p /Volumes/RuleOfLaw/Lausnir_Data/dropfolder
cp /path/to/a/real/law-book.pdf /Volumes/RuleOfLaw/Lausnir_Data/dropfolder/
set -a; . ./.env; set +a
uv run python scripts/import_baekur.py --dry-run
```

Expected: prints one block per PDF showing `title`, `author`, `external_id`, `body_text` char count, and any validation warnings. Confirm the title/author look right (or trace which tier of the fallback chain produced them) before running without `--dry-run`.

Then, without `--dry-run`:

```bash
uv run python scripts/import_baekur.py
```

Expected: `DONE {'total': 1, 'imported': 1, 'errors': 0}`; the PDF has moved from `Lausnir_Data/dropfolder/` to `Lausnir_Data/raw/logfraedibaekur/{external_id}.pdf`; a `.md` file exists under `Lausnir_Data/markdown/logfraedibaekur/`.

Then run chunking (reuses the existing generic script, no code change):

```bash
uv run python scripts/backfill_chunks.py --source logfraedibaekur
```

Expected: `Done: 1 documents chunked in ...s`.

- [ ] **Step 10: Commit**

```bash
git add scripts/import_baekur.py tests/test_import_baekur.py
git commit -m "feat: add dropfolder import script for logfraedibaekur"
```

---

## Post-implementation note (out of scope for this plan, per the design doc)

- `leitir.is` ISBN lookup was deferred — OpenLibrary is the only lookup tier implemented.
- No folder-watching daemon — `scripts/import_baekur.py` is run manually.
- No SHA256 content-hash fallback — filename slug is the fallback `external_id`, per the user's explicit choice (accepted small collision risk).
