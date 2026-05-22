# Import Hæstiréttur — Design Spec
_2026-05-22_

## Scope

Three files are created or modified. Nothing else is touched.

| File | Status | Purpose |
|------|--------|---------|
| `engine/processors/http_utils.py` | **New** | Shared WAF-safe HTTP client and retry helpers |
| `engine/processors/extractor.py` | **Modified** | Add `_html_to_plain()`, update `_extract_haestirettur()` |
| `scripts/import_haestirettur.py` | **New** | Full import pipeline for Hæstiréttur |
| `checkpoints/` | **New dir** | Per-source progress files (gitignored) |

`renderer.py`, `validator.py`, `models.py`, `sources.py`, `connection.py` — unchanged.

---

## API

### List query (GraphQL POST)

**Endpoint**: `https://island.is/api/graphql`

```graphql
query GetVerdicts($input: WebVerdictsInput!) {
  webVerdicts(input: $input) {
    total
    items {
      id title court caseNumber verdictDate keywords presentings
    }
  }
}
```

**Variables**: `{ "input": { "court": "Hæstiréttur", "page": N } }`

- Page-based, always 10 items, no `pageSize` parameter
- `total` on first response → `ceil(total / 10)` = `last_page` (~1,221 for 12,207 docs)
- `court` value is case-sensitive Icelandic: `"Hæstiréttur"`

### Detail fetch (Next.js JSON GET)

**URL**: `https://island.is/_next/data/{build_id}/domar/{id}.json`

**Response path**: `r.json()["pageProps"]["pageProps"]["pageProps"]["componentProps"]["item"]`

Relevant fields:
- `item["richText"]` — HTML body text
- `item["pdfString"]` — base64-encoded PDF
- `item["resolutionLink"]` — URL to lower court ruling (may be absent)

### build_id

Extracted once from `GET https://island.is/domar` HTML:

```python
start = html.find('"buildId":"') + len('"buildId":"')
build_id = html[start:html.index('"', start)]
```

Rotates every few hours. Refresh at `page == 1` and every 100 pages thereafter (sequential, before the `asyncio.gather` call for that page).

---

## Module: `engine/processors/http_utils.py`

```python
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://island.is/",
}

def make_client() -> httpx.AsyncClient:
    """Returns an AsyncClient with browser headers and a 30s timeout."""

async def post_with_retry(client, url, payload, *, max_retries=3) -> dict:
    """POST with exponential backoff on 429/405: 15s × 2.5^attempt."""

async def get_with_retry(client, url, *, max_retries=3) -> httpx.Response:
    """GET with same backoff. GET does not trigger CloudFront WAF."""
```

Backoff schedule on 429/405: 15 s → 37.5 s → 93.75 s (3 attempts). On final failure raises `httpx.HTTPStatusError`. All other status codes raise immediately without retry.

---

## Extractor changes (`engine/processors/extractor.py`)

Add at module level (uses `beautifulsoup4`, already in dependencies):

```python
from bs4 import BeautifulSoup

def _html_to_plain(html: str | None) -> str | None:
    if not html:
        return None
    return BeautifulSoup(html, "html.parser").get_text(separator="\n").strip() or None
```

Add `_detect_verdict_type()` — pattern match on stripped body text:

```python
def _detect_verdict_type(plain_text: str | None, keywords: list) -> str | None:
    if not plain_text:
        return None
    if re.search(r"Úrskurðarorð|úrskurðar\b", plain_text, re.IGNORECASE):
        return "Úrskurður"
    return None  # caller falls back to config.verdict_type_default
```

Update `_extract_haestirettur()` — two line changes:

```python
plain_body = _html_to_plain(raw.get("richText"))
...
"body_text": plain_body or raw.get("text") or None,
"verdict_type": _detect_verdict_type(plain_body, raw.get("keywords") or [])
               or config.verdict_type_default,
```

`plain_body` is computed once and reused for both `body_text` and `verdict_type` detection.  
`raw.get("text")` fallback on `body_text` ensures old documents stored with a `"text"` key still extract correctly on backfill.

---

## Import script: `scripts/import_haestirettur.py`

### Startup

1. Load `.env` for `DATABASE_URL` and `DATA_DIR`
2. `await init_db()`
3. Upsert `Source` row: `SELECT` by `short_name`, INSERT if absent — returns `source_id` UUID
4. Read checkpoint from `checkpoints/haestirettur.json` → `start_page` (default: 1), `imported_count` (default: 0)
5. `async with make_client() as client:`

### Main loop

```python
build_id = await _get_build_id(client)

for page in range(start_page, last_page + 1):
    # Refresh build_id every 100 pages (sequential, before gather)
    if page > 1 and page % 100 == 0:
        build_id = await _get_build_id(client)

    # Sequential POST (WAF-sensitive)
    data = await _fetch_list_page(client, page)
    if page == start_page:
        last_page = math.ceil(data["total"] / 10)

    # Concurrent GETs (WAF-safe)
    details = await asyncio.gather(
        *[_fetch_detail(client, build_id, v["id"]) for v in data["items"]],
        return_exceptions=True,
    )

    # Extract → validate → batch DB write
    docs = []
    for list_item, detail in zip(data["items"], details):
        doc = _build_document(list_item, detail, source_id, config)
        docs.append(doc)

    async with AsyncSessionLocal() as session:
        for doc in docs:
            # ON CONFLICT (source_id, external_id) DO UPDATE — true idempotent upsert.
            # session.merge() is NOT used because it operates on UUID primary key, not the
            # business key. A re-run after a crash would raise IntegrityError with merge().
            await _upsert_doc(session, doc)
        await session.commit()

    # Render markdown + save PDFs, then batch-update markdown_path (one transaction per page)
    async with AsyncSessionLocal() as s:
        for doc in docs:
            md_path = _render_and_save(doc, config)
            if md_path:
                await s.execute(
                    update(Document)
                    .where(Document.source_id == doc.source_id,
                           Document.external_id == doc.external_id)
                    .values(markdown_path=str(md_path))
                )
        await s.commit()

    # Checkpoint after commit
    _save_checkpoint(page, last_page, imported_count)
```

### `_build_document(list_item, detail, source_id, config)`

- If `detail` is an `Exception`: log warning, build doc with `None` body fields and a validation error noting fetch failure
- Merge: `raw = {**list_item, "richText": ..., "pdfString": ..., "resolutionLink": ...}`
- `external_id = list_item["id"]`
- `url = f"https://island.is/domar/{list_item['id']}"`
- `fields = Extractor(config).extract(raw)`
- Build `Document(**fields, external_id=..., url=..., source_id=..., raw_api_data=raw)`
- `errors = validate(doc, config)` → `doc.validation_errors = errors or None`

### `_render_and_save(doc, config) → Path | None`

- `write_markdown(doc, config, DATA_DIR)` → returns `Path` of written file
- If `doc.raw_api_data.get("pdfString")`: decode base64 → write to `DATA_DIR/raw/haestirettur/{doc.external_id}.pdf`
- Both wrapped in `try/except` → `log.warning(...)` on failure, never raises
- Returns the markdown `Path` on success, `None` on failure
- Caller writes `markdown_path` back to DB in a separate `UPDATE` after this returns

### Checkpoint

**File**: `checkpoints/haestirettur.json`
```json
{ "last_completed_page": 47, "total_pages": 1221, "imported": 470 }
```

- Read at startup → skip pages ≤ `last_completed_page`
- Written after every `session.commit()` (never before)
- Never auto-deleted — user removes manually when import is complete

### Progress display

`rich` Progress bar (already in dependencies):
```
Importing haestirettur  ━━━━━╸  470/12207 docs  Page 47/1221  [04:12 | ~8h left]
Errors: 3  (stored in validation_errors)
```

Error count = docs where `validation_errors` is non-empty. Shown live, detail visible via DB query after the run.

---

## Field mapping

| API field | Source | `raw_api_data` key | Document column |
|-----------|--------|--------------------|-----------------|
| `id` | list | `id` | `external_id` |
| `caseNumber` | list | `caseNumber` | `case_number` |
| `verdictDate` | list | `verdictDate` | `document_date` (ISO → date) |
| `title` | list | `title` | `plaintiffs` / `defendants` (via `_parse_parties_gegn`) |
| `keywords` | list | `keywords` | `keywords` |
| `presentings` | list | `presentings` | `summary` |
| `court` | list | `court` | `court` → `"Hrd."` (via `SourceConfig.abbreviation`) |
| `richText` | detail | `richText` | `body_text` (via `_html_to_plain`); also drives `verdict_type` detection |
| `pdfString` | detail | `pdfString` | PDF file on disk |
| `resolutionLink` | detail | `resolutionLink` | stored in `raw_api_data` only |

`lower_body_text` → `None` (Hæstiréttur lower court text is separate document via `resolutionLink`, not embedded).  
`instance_tier` → `3` (from `SourceConfig`).  
`verdict_type` → `_detect_verdict_type(plain_body, keywords)` with fallback to `config.verdict_type_default` (`"Dómur"`). Matches `"Úrskurðarorð"` or `"úrskurðar\b"` in body text → `"Úrskurður"`. List query has no type field.

---

## Error handling summary

| Failure | Behaviour |
|---------|-----------|
| GraphQL 429/405 | Retry 3× with backoff 15s×2.5ⁿ, then raise |
| GraphQL other 4xx/5xx | Raise immediately (bad query or outage) |
| Detail GET failure | Exception captured by `return_exceptions=True`; doc stored with `validation_errors` noting fetch failure |
| build_id stale (404 on detail) | Refresh `build_id` and retry once |
| `_html_to_plain` returns empty | Falls back to `raw.get("text")`, then `None`; validator flags `body_text` missing |
| `write_markdown` failure | Warning logged, `markdown_path` left `None` |
| PDF save failure | Warning logged, no impact on DB doc |

---

## What this design does NOT include

- Embedding generation (`embedding` column) — separate backfill step using OpenAI
- Concurrent page fetching — list POSTs remain sequential (WAF constraint)
- Automatic checkpoint deletion — manual cleanup after completed run
- Re-import of already-imported docs — `_upsert_doc()` is idempotent via `ON CONFLICT (source_id, external_id) DO UPDATE SET updated_at=now()`. A full re-run will touch every row but not corrupt data.
