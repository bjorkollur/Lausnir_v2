# Hæstiréttur Import — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import all ~12,207 Hæstiréttur verdicts from island.is GraphQL into lausnir_v2 PostgreSQL with idempotent upsert, batch-concurrent fetching, and checkpoint-based resume.

**Architecture:** Three-file change set: (1) shared WAF-safe HTTP utilities, (2) extractor updated for HTML body stripping and verdict-type detection, (3) import script using sequential list POSTs + parallel detail GETs per page, `ON CONFLICT DO UPDATE` upsert, per-page checkpoint, and `rich` progress display.

**Tech Stack:** httpx (async HTTP), SQLAlchemy asyncpg (DB), bs4 (HTML→plain), rich (progress), pytest + pytest-asyncio (tests)

---

## File Map

| File | Status | Responsibility |
|------|--------|---------------|
| `engine/processors/http_utils.py` | **Create** | Browser-headed `AsyncClient`; `post_with_retry` / `get_with_retry` with 429/405 backoff |
| `engine/processors/extractor.py` | **Modify** | Add `_html_to_plain`, `_detect_verdict_type`; update `_extract_haestirettur` |
| `scripts/__init__.py` | **Create** (empty) | Makes `scripts` a package so tests can import from it |
| `scripts/import_haestirettur.py` | **Create** | Full import script: fetch → extract → validate → upsert → render |
| `checkpoints/haestirettur.json` | Created at runtime | Page-level resume state |
| `tests/__init__.py` | **Create** (empty) | Test package root |
| `tests/conftest.py` | **Create** (empty) | Shared fixtures placeholder |
| `tests/test_http_utils.py` | **Create** | Tests for retry, backoff, headers |
| `tests/test_extractor_haestirettur.py` | **Create** | Tests for `_html_to_plain`, `_detect_verdict_type`, updated extractor |
| `tests/test_import_haestirettur.py` | **Create** | Tests for checkpoint helpers, `_get_build_id`, `_build_document` |

---

### Task 1: pytest-asyncio config + test directory

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/__init__.py`, `tests/conftest.py`

- [ ] **Step 1: Add pytest-asyncio auto mode to `pyproject.toml`**

Append to the end of `pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

- [ ] **Step 2: Create test package files**

```bash
touch tests/__init__.py tests/conftest.py
```

- [ ] **Step 3: Verify pytest runs without error**

```bash
uv run pytest tests/ -v
```

Expected: exit code 5 (`no tests ran`), zero errors.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml tests/__init__.py tests/conftest.py
git commit -m "chore: configure pytest asyncio_mode=auto"
```

---

### Task 2: `engine/processors/http_utils.py`

**Files:**
- Create: `engine/processors/http_utils.py`
- Create: `tests/test_http_utils.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_http_utils.py`:

```python
import httpx
import pytest
from unittest.mock import AsyncMock, patch

from engine.processors.http_utils import make_client, post_with_retry, get_with_retry


class _Replay(httpx.AsyncBaseTransport):
    """Return a fixed sequence of responses, repeating the last one indefinitely."""

    def __init__(self, *responses: httpx.Response):
        self._responses = list(responses)
        self._index = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        resp = self._responses[self._index]
        self._index = min(self._index + 1, len(self._responses) - 1)
        resp._request = request
        return resp


def test_make_client_browser_headers():
    client = make_client()
    assert "Mozilla" in client.headers["user-agent"]
    assert client.headers["referer"] == "https://island.is/"
    assert client.timeout.read == 30.0


async def test_post_returns_json_on_200():
    transport = _Replay(httpx.Response(200, json={"data": "ok"}))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await post_with_retry(client, "https://x.com/api", {})
    assert result == {"data": "ok"}


async def test_post_retries_twice_then_succeeds():
    transport = _Replay(
        httpx.Response(429),
        httpx.Response(429),
        httpx.Response(200, json={"ok": True}),
    )
    with patch("engine.processors.http_utils.asyncio.sleep", new_callable=AsyncMock):
        async with httpx.AsyncClient(transport=transport) as client:
            result = await post_with_retry(client, "https://x.com/api", {}, max_retries=3)
    assert result == {"ok": True}


async def test_post_raises_after_exhausting_retries():
    transport = _Replay(httpx.Response(429))
    with patch("engine.processors.http_utils.asyncio.sleep", new_callable=AsyncMock):
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await post_with_retry(client, "https://x.com/api", {}, max_retries=3)


async def test_post_raises_immediately_on_non_retryable_error():
    transport = _Replay(httpx.Response(500))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await post_with_retry(client, "https://x.com/api", {})


async def test_get_returns_response_on_200():
    transport = _Replay(httpx.Response(200, text="body"))
    async with httpx.AsyncClient(transport=transport) as client:
        resp = await get_with_retry(client, "https://x.com/page")
    assert resp.status_code == 200
    assert resp.text == "body"


async def test_get_retries_on_405():
    transport = _Replay(
        httpx.Response(405),
        httpx.Response(200, text="ok"),
    )
    with patch("engine.processors.http_utils.asyncio.sleep", new_callable=AsyncMock):
        async with httpx.AsyncClient(transport=transport) as client:
            resp = await get_with_retry(client, "https://x.com/page", max_retries=2)
    assert resp.status_code == 200
```

- [ ] **Step 2: Run — expect `ModuleNotFoundError`**

```bash
uv run pytest tests/test_http_utils.py -v
```

Expected: `ModuleNotFoundError: No module named 'engine.processors.http_utils'`

- [ ] **Step 3: Create `engine/processors/http_utils.py`**

```python
"""WAF-safe HTTP helpers shared by all import scripts."""
from __future__ import annotations

import asyncio
import logging

import httpx

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://island.is/",
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "is, en;q=0.9",
}

_RETRY_STATUSES = {429, 405}
_BASE_BACKOFF = 15.0
_BACKOFF_FACTOR = 2.5


def make_client(**kwargs) -> httpx.AsyncClient:
    """AsyncClient with browser headers and 30 s timeout."""
    return httpx.AsyncClient(headers=_HEADERS, timeout=30.0, **kwargs)


async def post_with_retry(
    client: httpx.AsyncClient,
    url: str,
    payload: dict,
    *,
    max_retries: int = 3,
) -> dict:
    """POST JSON; retry on 429/405 with exponential backoff (15 s × 2.5^n)."""
    for attempt in range(max_retries):
        resp = await client.post(url, json=payload)
        if resp.status_code in _RETRY_STATUSES:
            if attempt == max_retries - 1:
                resp.raise_for_status()
            delay = _BASE_BACKOFF * (_BACKOFF_FACTOR ** attempt)
            log.warning("WAF throttle %d — sleeping %.1f s", resp.status_code, delay)
            await asyncio.sleep(delay)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError("unreachable")  # pragma: no cover


async def get_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_retries: int = 3,
) -> httpx.Response:
    """GET; retry on 429/405 with same backoff. GET does not trigger CloudFront WAF."""
    for attempt in range(max_retries):
        resp = await client.get(url)
        if resp.status_code in _RETRY_STATUSES:
            if attempt == max_retries - 1:
                resp.raise_for_status()
            delay = _BASE_BACKOFF * (_BACKOFF_FACTOR ** attempt)
            log.warning("Throttled GET %d — sleeping %.1f s", resp.status_code, delay)
            await asyncio.sleep(delay)
            continue
        resp.raise_for_status()
        return resp
    raise RuntimeError("unreachable")  # pragma: no cover
```

- [ ] **Step 4: Run — expect 7 passed**

```bash
uv run pytest tests/test_http_utils.py -v
```

Expected:
```
PASSED test_make_client_browser_headers
PASSED test_post_returns_json_on_200
PASSED test_post_retries_twice_then_succeeds
PASSED test_post_raises_after_exhausting_retries
PASSED test_post_raises_immediately_on_non_retryable_error
PASSED test_get_returns_response_on_200
PASSED test_get_retries_on_405
7 passed
```

- [ ] **Step 5: Commit**

```bash
git add engine/processors/http_utils.py tests/test_http_utils.py
git commit -m "feat: add http_utils — WAF-safe retry helpers"
```

---

### Task 3: Update `engine/processors/extractor.py`

**Files:**
- Modify: `engine/processors/extractor.py`
- Create: `tests/test_extractor_haestirettur.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_extractor_haestirettur.py`:

```python
from datetime import date
import pytest
from engine.config.sources import get_config
from engine.processors.extractor import Extractor, _html_to_plain, _detect_verdict_type

CONFIG = get_config("haestirettur")


# ── _html_to_plain ────────────────────────────────────────────────────────────

def test_html_to_plain_strips_tags():
    assert _html_to_plain("<p>Hello <b>world</b></p>") == "Hello world"


def test_html_to_plain_content_across_paragraphs():
    result = _html_to_plain("<p>First</p><p>Second</p>")
    assert "First" in result and "Second" in result


def test_html_to_plain_none_input():
    assert _html_to_plain(None) is None


def test_html_to_plain_blank_input():
    assert _html_to_plain("") is None
    assert _html_to_plain("   ") is None


# ── _detect_verdict_type ─────────────────────────────────────────────────────

def test_detect_urskurdaford_heading():
    assert _detect_verdict_type("Málsatvik\n\nÚrskurðarorð\n\nHafnað.", []) == "Úrskurður"


def test_detect_urskurdar_verb():
    assert _detect_verdict_type("Dómurinn úrskurðar að kröfunni sé hafnað.", []) == "Úrskurður"


def test_detect_case_insensitive():
    assert _detect_verdict_type("úrskurðarorð\n\nHafnað.", []) == "Úrskurður"


def test_detect_returns_none_for_domur():
    assert _detect_verdict_type("Dómsorð\n\nStefndi greiði 500.000 kr.", []) is None


def test_detect_returns_none_for_empty():
    assert _detect_verdict_type(None, []) is None
    assert _detect_verdict_type("", []) is None


# ── _extract_haestirettur ─────────────────────────────────────────────────────

def _raw(**overrides) -> dict:
    base = {
        "id": "haestirettur-domar-test-1",
        "title": "Jón Jónsson gegn Sigríður Sigurðardóttir",
        "caseNumber": "E-123/2024",
        "verdictDate": "2024-05-05T00:00:00Z",
        "keywords": ["Kröfuréttur", "Skaðabótamál"],
        "presentings": "Reifun málsins.",
        "court": "Hæstiréttur",
    }
    return {**base, **overrides}


def test_extract_strips_html_from_rich_text():
    result = Extractor(CONFIG).extract(_raw(richText="<p>Dómsorð</p><p>Stefndi greiði.</p>"))
    assert "<p>" not in result["body_text"]
    assert "Dómsorð" in result["body_text"]


def test_extract_detects_urskurdur_from_rich_text():
    result = Extractor(CONFIG).extract(_raw(richText="<p>Úrskurðarorð</p><p>Hafnað.</p>"))
    assert result["verdict_type"] == "Úrskurður"


def test_extract_defaults_to_domur():
    result = Extractor(CONFIG).extract(_raw(richText="<p>Dómsorð</p><p>Stefndi greiði.</p>"))
    assert result["verdict_type"] == "Dómur"


def test_extract_fallback_to_text_key():
    result = Extractor(CONFIG).extract(_raw(text="Dómsorð\nStefndi greiði."))
    assert result["body_text"] == "Dómsorð\nStefndi greiði."


def test_extract_parses_parties_from_title():
    result = Extractor(CONFIG).extract(_raw(richText="<p>body</p>"))
    assert result["plaintiffs"] == [{"name": "Jón Jónsson", "lawyer": None}]
    assert result["defendants"] == [{"name": "Sigríður Sigurðardóttir", "lawyer": None}]


def test_extract_parses_verdict_date():
    result = Extractor(CONFIG).extract(_raw(richText="<p>body</p>"))
    assert result["document_date"] == date(2024, 5, 5)


def test_extract_uses_presentings_as_summary():
    result = Extractor(CONFIG).extract(_raw(richText="<p>body</p>"))
    assert result["summary"] == "Reifun málsins."


def test_extract_raw_api_data_includes_pdf_string():
    result = Extractor(CONFIG).extract(_raw(richText="<p>b</p>", pdfString="AAAA=="))
    assert result["raw_api_data"]["pdfString"] == "AAAA=="
```

- [ ] **Step 2: Run — expect `ImportError` (functions don't exist yet)**

```bash
uv run pytest tests/test_extractor_haestirettur.py -v
```

Expected: `ImportError: cannot import name '_html_to_plain' from 'engine.processors.extractor'`

- [ ] **Step 3: Add `from bs4 import BeautifulSoup` import to `engine/processors/extractor.py`**

At the top of `engine/processors/extractor.py`, after the existing `from datetime import date` line, add:

```python
from bs4 import BeautifulSoup
```

- [ ] **Step 4: Add `_html_to_plain` and `_detect_verdict_type` after the existing `_rich_text_to_plain` function (after line 89)**

```python
def _html_to_plain(html: str | None) -> str | None:
    """Strip HTML tags to plain text. Returns None for blank/None input."""
    if not html:
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
```

- [ ] **Step 5: Replace `_extract_haestirettur` in `engine/processors/extractor.py`**

Replace the existing function (lines 107–123) with:

```python
def _extract_haestirettur(raw: dict, config: SourceConfig) -> dict:
    title = raw.get("title") or raw.get("caseTitle") or ""
    plf, dfd = _parse_parties_gegn(title)
    plain_body = _html_to_plain(raw.get("richText"))
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
        "body_text": plain_body or raw.get("text") or raw.get("content") or None,
        "lower_body_text": raw.get("lowerCourtText") or None,
        "raw_api_data": raw,
    }
```

- [ ] **Step 6: Run — expect 14 passed**

```bash
uv run pytest tests/test_extractor_haestirettur.py -v
```

Expected: 14 tests, all PASSED.

- [ ] **Step 7: Run full suite to confirm nothing regressed**

```bash
uv run pytest tests/ -v
```

Expected: 21 total tests, all PASSED.

- [ ] **Step 8: Commit**

```bash
git add engine/processors/extractor.py tests/test_extractor_haestirettur.py
git commit -m "feat: html-to-plain and verdict-type detection in extractor"
```

---

### Task 4: Import script — helpers (checkpoint, API fetchers, document builder)

**Files:**
- Create: `scripts/__init__.py` (empty)
- Create: `scripts/import_haestirettur.py` (helpers only — `main()` added in Task 6)
- Create: `tests/test_import_haestirettur.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_import_haestirettur.py`:

```python
import json
import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.config.sources import get_config

CONFIG = get_config("haestirettur")
SOURCE_ID = uuid.uuid4()


# ── Checkpoint helpers ────────────────────────────────────────────────────────

def test_load_checkpoint_defaults_on_missing_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from scripts.import_haestirettur import _load_checkpoint
    start, total, imported = _load_checkpoint()
    assert (start, total, imported) == (1, 0, 0)


def test_load_checkpoint_resumes_from_saved_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "checkpoints").mkdir()
    (tmp_path / "checkpoints" / "haestirettur.json").write_text(
        json.dumps({"last_completed_page": 47, "total_pages": 1221, "imported": 470})
    )
    from scripts.import_haestirettur import _load_checkpoint
    start, total, imported = _load_checkpoint()
    assert (start, total, imported) == (48, 1221, 470)


def test_save_checkpoint_writes_correct_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from scripts.import_haestirettur import _save_checkpoint
    _save_checkpoint(10, 1221, 100)
    data = json.loads((tmp_path / "checkpoints" / "haestirettur.json").read_text())
    assert data == {"last_completed_page": 10, "total_pages": 1221, "imported": 100}


def test_save_then_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from scripts.import_haestirettur import _load_checkpoint, _save_checkpoint
    _save_checkpoint(99, 500, 990)
    start, total, imported = _load_checkpoint()
    assert (start, total, imported) == (100, 500, 990)


# ── _get_build_id ─────────────────────────────────────────────────────────────

async def test_get_build_id_extracts_correctly():
    from scripts.import_haestirettur import _get_build_id

    mock_resp = MagicMock()
    mock_resp.text = 'stuff before {"buildId":"abc-123-xyz","page":"/domar"} stuff after'

    with patch("scripts.import_haestirettur.get_with_retry", new_callable=AsyncMock) as m:
        m.return_value = mock_resp
        result = await _get_build_id(MagicMock())

    assert result == "abc-123-xyz"


async def test_get_build_id_raises_when_marker_absent():
    from scripts.import_haestirettur import _get_build_id

    mock_resp = MagicMock()
    mock_resp.text = "no build id here"

    with patch("scripts.import_haestirettur.get_with_retry", new_callable=AsyncMock) as m:
        m.return_value = mock_resp
        with pytest.raises(ValueError, match="buildId"):
            await _get_build_id(MagicMock())


# ── _build_document ───────────────────────────────────────────────────────────

_LIST_ITEM = {
    "id": "haestirettur-domar-100",
    "title": "A ehf. gegn B hf.",
    "caseNumber": "E-42/2023",
    "verdictDate": "2023-06-01T00:00:00Z",
    "keywords": ["Kröfuréttur", "Samningslög"],
    "presentings": "Ágrip málsins.",
    "court": "Hæstiréttur",
}

_DETAIL = {
    "richText": "<p>Dómsorð</p><p>Stefndi greiði.</p>",
    "pdfString": "AAAA==",
    "resolutionLink": None,
}


def test_build_document_basic_fields():
    from scripts.import_haestirettur import _build_document
    from engine.database.models import Document

    doc = _build_document(_LIST_ITEM, _DETAIL, SOURCE_ID, CONFIG)

    assert isinstance(doc, Document)
    assert doc.external_id == "haestirettur-domar-100"
    assert doc.case_number == "E-42/2023"
    assert doc.court == "Hrd."
    assert doc.document_date == date(2023, 6, 1)
    assert doc.url == "https://island.is/domar/haestirettur-domar-100"
    assert doc.source_id == SOURCE_ID


def test_build_document_body_text_is_plain():
    from scripts.import_haestirettur import _build_document

    doc = _build_document(_LIST_ITEM, _DETAIL, SOURCE_ID, CONFIG)
    assert doc.body_text is not None
    assert "<p>" not in doc.body_text
    assert "Dómsorð" in doc.body_text


def test_build_document_parties_parsed():
    from scripts.import_haestirettur import _build_document

    doc = _build_document(_LIST_ITEM, _DETAIL, SOURCE_ID, CONFIG)
    assert doc.plaintiffs == [{"name": "A ehf.", "lawyer": None}]
    assert doc.defendants == [{"name": "B hf.", "lawyer": None}]


def test_build_document_raw_api_data_includes_pdf():
    from scripts.import_haestirettur import _build_document

    doc = _build_document(_LIST_ITEM, _DETAIL, SOURCE_ID, CONFIG)
    assert doc.raw_api_data["pdfString"] == "AAAA=="
    assert doc.raw_api_data["richText"] == _DETAIL["richText"]


def test_build_document_failed_detail_sets_validation_error():
    from scripts.import_haestirettur import _build_document

    err = ConnectionError("timeout")
    doc = _build_document(_LIST_ITEM, err, SOURCE_ID, CONFIG)

    assert doc.external_id == "haestirettur-domar-100"
    assert doc.body_text is None
    assert doc.validation_errors is not None
    assert any(e.get("field") == "detail_fetch" for e in doc.validation_errors)


def test_build_document_failed_detail_raw_is_list_item_only():
    from scripts.import_haestirettur import _build_document

    doc = _build_document(_LIST_ITEM, Exception("fail"), SOURCE_ID, CONFIG)
    assert "richText" not in doc.raw_api_data
```

- [ ] **Step 2: Run — expect `ModuleNotFoundError`**

```bash
uv run pytest tests/test_import_haestirettur.py -v
```

Expected: `ModuleNotFoundError: No module named 'scripts.import_haestirettur'`

- [ ] **Step 3: Create `scripts/__init__.py`**

```bash
touch scripts/__init__.py
```

- [ ] **Step 4: Create `scripts/import_haestirettur.py` with helpers (no `main()` yet)**

```python
"""Import all Hæstiréttur verdicts from island.is GraphQL into lausnir_v2."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import os
import uuid
from pathlib import Path
from typing import Any

import httpx
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from engine.config.sources import SourceConfig, get_config
from engine.database.connection import AsyncSessionLocal, init_db
from engine.database.models import Document, Source
from engine.processors.extractor import Extractor
from engine.processors.http_utils import get_with_retry, make_client, post_with_retry
from engine.processors.renderer import write_markdown
from engine.processors.validator import validate

log = logging.getLogger(__name__)

_GQL_ENDPOINT = "https://island.is/api/graphql"
_GQL_QUERY = """
query GetVerdicts($input: WebVerdictsInput!) {
  webVerdicts(input: $input) {
    total
    items {
      id title court caseNumber verdictDate keywords presentings
    }
  }
}
"""

_CHECKPOINT_DIR = Path("checkpoints")
_CHECKPOINT_FILE = _CHECKPOINT_DIR / "haestirettur.json"


# ── Checkpoint ────────────────────────────────────────────────────────────────

def _load_checkpoint() -> tuple[int, int, int]:
    """Return (start_page, total_pages, imported_count). Defaults to (1, 0, 0)."""
    if _CHECKPOINT_FILE.exists():
        data = json.loads(_CHECKPOINT_FILE.read_text())
        return data["last_completed_page"] + 1, data["total_pages"], data["imported"]
    return 1, 0, 0


def _save_checkpoint(page: int, total_pages: int, imported: int) -> None:
    _CHECKPOINT_DIR.mkdir(exist_ok=True)
    _CHECKPOINT_FILE.write_text(
        json.dumps(
            {"last_completed_page": page, "total_pages": total_pages, "imported": imported},
            indent=2,
        )
    )


# ── API helpers ───────────────────────────────────────────────────────────────

async def _get_build_id(client: httpx.AsyncClient) -> str:
    """Extract Next.js buildId from island.is/domar HTML."""
    resp = await get_with_retry(client, "https://island.is/domar")
    html = resp.text
    marker = '"buildId":"'
    start = html.find(marker)
    if start == -1:
        raise ValueError("Could not find buildId in island.is/domar response")
    start += len(marker)
    end = html.index('"', start)
    build_id = html[start:end]
    if not build_id:
        raise ValueError("Empty buildId extracted from island.is/domar")
    return build_id


async def _fetch_list_page(client: httpx.AsyncClient, page: int) -> dict:
    """Fetch one page (10 items) from the GraphQL list endpoint."""
    payload = {
        "query": _GQL_QUERY,
        "variables": {"input": {"court": "Hæstiréttur", "page": page}},
    }
    data = await post_with_retry(client, _GQL_ENDPOINT, payload)
    return data["data"]["webVerdicts"]


async def _fetch_detail(
    client: httpx.AsyncClient,
    build_id: str,
    verdict_id: str,
) -> dict:
    """Fetch richText, pdfString, resolutionLink via Next.js JSON route."""
    url = f"https://island.is/_next/data/{build_id}/domar/{verdict_id}.json"
    resp = await get_with_retry(client, url)
    return resp.json()["pageProps"]["pageProps"]["pageProps"]["componentProps"]["item"]


# ── Document builder ──────────────────────────────────────────────────────────

def _build_document(
    list_item: dict,
    detail: dict | Exception,
    source_id: uuid.UUID,
    config: SourceConfig,
) -> Document:
    """Merge list + detail into a Document. Handles failed detail gracefully."""
    ext_id = list_item["id"]

    if isinstance(detail, Exception):
        log.warning("Detail fetch failed for %s: %s", ext_id, detail)
        raw: dict[str, Any] = dict(list_item)
        extra_errors: list[dict] = [{"field": "detail_fetch", "message": str(detail)}]
    else:
        raw = {
            **list_item,
            "richText": detail.get("richText"),
            "pdfString": detail.get("pdfString"),
            "resolutionLink": detail.get("resolutionLink"),
        }
        extra_errors = []

    fields = Extractor(config).extract(raw)
    doc = Document(
        id=uuid.uuid4(),
        source_id=source_id,
        external_id=ext_id,
        url=f"https://island.is/domar/{ext_id}",
        **fields,
    )
    errors = validate(doc, config)
    errors.extend(extra_errors)
    doc.validation_errors = errors or None
    return doc
```

- [ ] **Step 5: Run — expect 14 passed**

```bash
uv run pytest tests/test_import_haestirettur.py -v
```

Expected: 14 tests, all PASSED.

- [ ] **Step 6: Commit**

```bash
git add scripts/__init__.py scripts/import_haestirettur.py tests/test_import_haestirettur.py
git commit -m "feat: import script helpers — checkpoint, API fetchers, document builder"
```

---

### Task 5: Import script — DB upsert + render helpers

**Files:**
- Modify: `scripts/import_haestirettur.py` (append `_ensure_source`, `_upsert_doc`, `_render_and_save`)

No additional tests — these functions require a live DB or complex mocks. Correctness is verified in the smoke test (Task 6).

- [ ] **Step 1: Append the three DB/render helpers to `scripts/import_haestirettur.py`**

Add after the `_build_document` function:

```python
# ── DB helpers ────────────────────────────────────────────────────────────────

async def _ensure_source(session: AsyncSession, config: SourceConfig) -> uuid.UUID:
    """SELECT source by short_name; INSERT if absent. Returns the source UUID."""
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
            base_url="https://island.is/api/graphql",
        ))
        await session.commit()
        return source_id
    return source.id


async def _upsert_doc(session: AsyncSession, doc: Document) -> None:
    """INSERT or UPDATE by (source_id, external_id) — true idempotent upsert."""
    values: dict[str, Any] = {
        "id": doc.id,
        "source_id": doc.source_id,
        "external_id": doc.external_id,
        "url": doc.url,
        "raw_api_data": doc.raw_api_data,
        "case_number": doc.case_number,
        "document_date": doc.document_date,
        "court": doc.court,
        "verdict_type": doc.verdict_type,
        "instance_tier": doc.instance_tier,
        "plaintiffs": doc.plaintiffs,
        "defendants": doc.defendants,
        "keywords": doc.keywords,
        "summary": doc.summary,
        "body_text": doc.body_text,
        "lower_body_text": doc.lower_body_text,
        "validation_errors": doc.validation_errors,
    }
    update_cols = {
        k: v for k, v in values.items()
        if k not in ("id", "source_id", "external_id")
    }
    update_cols["updated_at"] = func.now()
    await session.execute(
        pg_insert(Document)
        .values(**values)
        .on_conflict_do_update(
            constraint="uq_doc_source_external",
            set_=update_cols,
        )
    )


def _render_and_save(doc: Document, config: SourceConfig) -> Path | None:
    """Write .md to disk and decode PDF bytes. Returns markdown Path or None on failure."""
    data_dir = os.environ.get("DATA_DIR", "/Volumes/RuleOfLaw/Lausnir_Data")

    md_path: Path | None = None
    try:
        md_path = write_markdown(doc, config, data_dir)
    except Exception as exc:
        log.warning("write_markdown failed for %s: %s", doc.external_id, exc)

    try:
        pdf_b64 = (doc.raw_api_data or {}).get("pdfString")
        if pdf_b64:
            pdf_dir = Path(data_dir) / "raw" / config.short_name
            pdf_dir.mkdir(parents=True, exist_ok=True)
            (pdf_dir / f"{doc.external_id}.pdf").write_bytes(base64.b64decode(pdf_b64))
    except Exception as exc:
        log.warning("PDF save failed for %s: %s", doc.external_id, exc)

    return md_path
```

- [ ] **Step 2: Run full test suite — confirm nothing broken**

```bash
uv run pytest tests/ -v
```

Expected: 35 tests, all PASSED.

- [ ] **Step 3: Commit**

```bash
git add scripts/import_haestirettur.py
git commit -m "feat: DB upsert and render helpers for import script"
```

---

### Task 6: Import script — `main()` + smoke test

**Files:**
- Modify: `scripts/import_haestirettur.py` (append `main()` and `if __name__ == "__main__":`)

- [ ] **Step 1: Append `main()` to `scripts/import_haestirettur.py`**

```python
# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    config = get_config("haestirettur")
    await init_db()

    async with AsyncSessionLocal() as session:
        source_id = await _ensure_source(session, config)

    start_page, saved_total, imported_count = _load_checkpoint()
    total_errors = 0

    with Progress(
        SpinnerColumn(),
        "[progress.description]{task.description}",
        BarColumn(),
        "[progress.percentage]{task.percentage:>3.0f}%",
        TaskProgressColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        refresh_per_second=2,
    ) as progress:
        task_id = progress.add_task("Importing haestirettur…", total=None)

        async with make_client() as client:
            build_id = await _get_build_id(client)
            log.info("build_id: %s", build_id)

            last_page: int | None = saved_total if saved_total > 0 else None
            page = start_page

            while True:
                if last_page is not None and page > last_page:
                    break

                # Refresh build_id every 100 pages — sequential, before gather
                if page > 1 and page % 100 == 0:
                    build_id = await _get_build_id(client)
                    log.info("Refreshed build_id at page %d", page)

                # Sequential POST — WAF-sensitive
                data = await _fetch_list_page(client, page)

                if last_page is None:
                    last_page = math.ceil(data["total"] / 10)
                    progress.update(task_id, total=data["total"])

                # Concurrent GETs — WAF-safe
                details = await asyncio.gather(
                    *[_fetch_detail(client, build_id, v["id"]) for v in data["items"]],
                    return_exceptions=True,
                )

                docs = [
                    _build_document(item, detail, source_id, config)
                    for item, detail in zip(data["items"], details)
                ]

                # Batch upsert — one transaction per page
                async with AsyncSessionLocal() as session:
                    for doc in docs:
                        await _upsert_doc(session, doc)
                    await session.commit()

                # Batch render + markdown_path update — one transaction per page
                async with AsyncSessionLocal() as session:
                    for doc in docs:
                        md_path = _render_and_save(doc, config)
                        if md_path:
                            await session.execute(
                                update(Document)
                                .where(
                                    Document.source_id == doc.source_id,
                                    Document.external_id == doc.external_id,
                                )
                                .values(markdown_path=str(md_path))
                            )
                    await session.commit()

                page_errors = sum(1 for d in docs if d.validation_errors)
                total_errors += page_errors
                imported_count += len(docs)

                _save_checkpoint(page, last_page, imported_count)

                progress.update(
                    task_id,
                    advance=len(docs),
                    description=(
                        f"Importing haestirettur  "
                        f"[Page {page}/{last_page}  Errors: {total_errors}]"
                    ),
                )

                page += 1

    log.info(
        "Done. %d docs imported, %d with validation errors.",
        imported_count,
        total_errors,
    )


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run full test suite**

```bash
uv run pytest tests/ -v
```

Expected: 35 tests, all PASSED.

- [ ] **Step 3: Smoke test — fetch and insert exactly 1 page (10 docs)**

Run with `DATABASE_URL` and `DATA_DIR` set in your environment:

```bash
DATABASE_URL="postgresql+asyncpg://geiri@localhost/lausnir_v2" \
DATA_DIR="/Volumes/RuleOfLaw/Lausnir_Data" \
uv run python -c "
import asyncio
import scripts.import_haestirettur as s
from engine.config.sources import get_config
from engine.database.connection import init_db, AsyncSessionLocal
from engine.processors.http_utils import make_client

async def smoke():
    config = get_config('haestirettur')
    await init_db()

    async with AsyncSessionLocal() as session:
        source_id = await s._ensure_source(session, config)

    async with make_client() as client:
        build_id = await s._get_build_id(client)
        print('build_id:', build_id[:24] + '...')

        data = await s._fetch_list_page(client, 1)
        print(f'total: {data[\"total\"]}  items on page: {len(data[\"items\"])}')

        details = await asyncio.gather(
            *[s._fetch_detail(client, build_id, v['id']) for v in data['items']],
            return_exceptions=True,
        )
        failed = sum(1 for d in details if isinstance(d, Exception))
        print(f'detail fetches: {len(details)} attempted, {failed} failed')

        docs = [
            s._build_document(item, det, source_id, config)
            for item, det in zip(data['items'], details)
        ]

        async with AsyncSessionLocal() as session:
            for doc in docs:
                await s._upsert_doc(session, doc)
            await session.commit()

        print('Upserted 10 docs. Sample:')
        for doc in docs[:3]:
            body_preview = (doc.body_text or '')[:60].replace(chr(10), ' ')
            print(f'  {doc.case_number} | {doc.verdict_type} | {body_preview}')

asyncio.run(smoke())
"
```

Expected output (exact values vary by live data):
```
build_id: AbCdEfGhIjKlMnOpQrStUvWx...
total: 12207  items on page: 10
detail fetches: 10 attempted, 0 failed
Upserted 10 docs. Sample:
  E-5/2025 | Dómur | Dómsorð  Stefndi greiði stefnanda 1.200.000 kr.
  E-3/2025 | Dómur | Dómsorð  Áfrýjandi greiði gagnaðila málskostnað
  Kæra-12/2025 | Úrskurður | Úrskurðarorð  Kærunni er hafnað.
```

**If `failed > 0`:** build_id may be stale. Re-run the smoke test (it refreshes build_id automatically).  
**If `body_text` is empty for all:** inspect `data['items'][0]` to confirm field names match.

- [ ] **Step 4: Verify 10 rows in DB with correct data**

```bash
psql lausnir_v2 -c "
SELECT case_number, verdict_type, court,
       length(body_text) AS body_len,
       validation_errors IS NULL AS clean
FROM documents
ORDER BY created_at DESC
LIMIT 10;
"
```

Expected: 10 rows, `court = 'Hrd.'`, `body_len > 200` for most, `clean = true` for ≥ 8/10.

- [ ] **Step 5: Verify idempotency — run smoke test a second time, check no duplicates**

```bash
psql lausnir_v2 -c "SELECT count(*) FROM documents;"
```

Expected: still 10 (not 20). If count is 20, the ON CONFLICT constraint name is wrong — check `\d documents` for the actual constraint name.

- [ ] **Step 6: Commit**

```bash
git add scripts/import_haestirettur.py
git commit -m "feat: complete import_haestirettur — main loop with progress bar"
```

---

## Self-Review

### Spec coverage

| Spec requirement | Task |
|-----------------|------|
| `http_utils.py`: `make_client`, `post_with_retry`, `get_with_retry` | Task 2 |
| Browser User-Agent + Referer headers | Task 2 |
| Backoff 15 s × 2.5^n on 429/405, max 3 retries | Task 2 |
| `_html_to_plain()` via bs4 | Task 3 |
| `_detect_verdict_type()` regex on body text | Task 3 |
| `_extract_haestirettur`: richText→body_text, verdictDate, presentings, verdict detection | Task 3 |
| `checkpoints/haestirettur.json` load/save | Task 4 |
| `_get_build_id()` regex from /domar HTML | Task 4 |
| `_fetch_list_page()` GraphQL POST | Task 4 |
| `_fetch_detail()` Next.js JSON GET | Task 4 |
| `_build_document()` with Exception fallback | Task 4 |
| `_ensure_source()` SELECT-or-INSERT | Task 5 |
| `_upsert_doc()` ON CONFLICT DO UPDATE | Task 5 |
| `_render_and_save()` markdown + PDF | Task 5 |
| `main()`: while loop, build_id refresh every 100 pages | Task 6 |
| Batch upsert, one transaction per page | Task 6 |
| Batch markdown_path UPDATE, one transaction per page | Task 6 |
| `rich` progress bar with page/doc/error counts | Task 6 |
| `return_exceptions=True` in gather | Task 6 |
| Checkpoint written after commit, never before | Task 6 |
| `checkpoints/` subdirectory, not project root | Task 4 |

All requirements covered. ✓

### Type consistency

- `_load_checkpoint() → tuple[int, int, int]` → used as `start_page, saved_total, imported_count` ✓
- `_build_document(list_item, detail, source_id, config) → Document` → `docs` list in `main()` ✓
- `_render_and_save(doc, config) → Path | None` → return value drives `markdown_path` update ✓
- `_upsert_doc(session, doc) → None` → called with `await` ✓
- `_fetch_list_page` returns `data["data"]["webVerdicts"]` → dict with `total` and `items` ✓
- `_fetch_detail` returns `resp.json()[...]["item"]` → `dict` or `Exception` via `return_exceptions=True` ✓
- `_ensure_source(session, config) → uuid.UUID` → used as `source_id` in `_build_document` calls ✓
