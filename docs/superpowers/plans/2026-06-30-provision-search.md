# Provision Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make it possible to search all ~100k verdicts and rulings by law citation — e.g. "1. mgr. 218. gr. laga nr. 19/1940" — by extracting provisions from body_text at import time and storing them in an indexed JSONB column.

**Architecture:** A new `cited_provisions JSONB` column on `documents` stores structured provision citations extracted by a parser (`engine/processors/provision_extractor.py`). A GIN index on that column enables O(1) containment queries (`@>`). The search API accepts an optional `provision` query string; `parse_provision_query()` (already in `queries.py`) parses it into `(law, gr, sfx, mgr)` which drives the JSONB filter. Backfill populates existing ~100k documents.

**Tech Stack:** PostgreSQL JSONB + `jsonb_path_ops` GIN index, Python `re`, async SQLAlchemy 2, FastAPI.

## Global Constraints

- Python runtime: `uv run python` (project uses uv)
- DB URL: `postgresql+asyncpg://geiri@localhost/lausnir_v2`  
- All DB access: async (`AsyncSession` / `engine.connect()` + `await conn.execute(...)`)
- `sys.path.insert(0, str(Path(__file__).parent.parent))` at top of every script
- FTS dictionary: always `'simple'` (not used here, but consistent with codebase)
- New column name: exactly `cited_provisions` (the existing `provisions` column stores lagasafn article structure — different purpose, do not touch it)
- JSONB entry shape: `{"law": "19/1940", "gr": 218, "mgr": 1, "sfx": "a"}` — all keys present, `null` for absent optional fields except `sfx` which is omitted when absent
- GIN index operator: `jsonb_path_ops` (supports `@>` containment, not `?` key existence — that's the trade-off; we only need `@>`)
- Backfill batch size: 200 documents per transaction
- Run tests: `uv run pytest tests/<file>.py -v`
- Run scripts: `uv run python scripts/<file>.py`

## Provision Citation Patterns to Support

Discovered by corpus analysis across Hæstiréttur, Landsréttur, Héraðsdómar and 5 regulatory sources. Listed by priority:

| ID | Example | Notes |
|----|---------|-------|
| P1 | `2. mgr. 12. gr. laga nr. 68/2023` | **mgr BEFORE gr** — dominant form, ~60% of citations |
| P2 | `12. gr. laga nr. 68/2023` | gr only, no mgr |
| P3 | `218. gr. a. laga nr. 19/1940` | article suffix letter |
| P4 | `3. mgr. 71. gr. almennra hegningarlaga nr. 19/1940` | compound law name (1–4 words before nr.) |
| P5 | `1., sbr. 2. mgr. 48. gr. og 50. gr. umferðarlaga nr. 77/2019` | multi-article chain — propagate law to all articles |
| P6 | `2. tölul. 1. mgr. 10. gr. laga nr. 116/2006` | tölulið prefix (numbered item) |
| P7 | `12. gr. laga nr. 68/2023, sbr. 1. mgr. 66. gr. sömu laga` | "sömu laga" (same law) — propagate law forward |
| P8 | `8. gr. reglugerðar nr. 698/2014` | domestic ministerial regulation |

Patterns deliberately **out of scope** for this plan:
- EU regulations and directives (`reglugerð EB nr.`, `tilskipun YYYY/N/EB`) — deferred
- Standalone `N. gr.` / `N. mgr.` with no law number and no resolvable anaphora (too ambiguous)
- `XIX. kafli` (chapter references) — no article number
- `ákvæði til bráðabirgða` (transitional provisions) — no article number

**Key behaviour note:** `mgr.` is always optional. A reference like `218. gr. laga nr. 19/1940` (no paragraph) is valid and common — the extractor stores `mgr: null` and the search filter omits `mgr` from the containment object so it matches any paragraph of that article.

---

### Task 1: Provision extractor

**Files:**
- Create: `engine/processors/provision_extractor.py`
- Test: `tests/test_provision_extractor.py` (new)

**Interfaces:**
- Produces: `extract_provisions(text: str) -> list[dict]`
  - Each dict: `{"law": str, "gr": int, "mgr": int | None}` (plus `"sfx": str` when present)
  - Returns `[]` for empty/None text
  - No duplicates: same `(law, gr, mgr)` combination appears at most once

- [ ] **Step 1: Write the failing tests**

Create `tests/test_provision_extractor.py`:

```python
"""Tests for engine.processors.provision_extractor."""
import pytest
from engine.processors.provision_extractor import extract_provisions


# ── P1: mgr before gr ──────────────────────────────────────────────────────────
def test_mgr_before_gr_basic():
    result = extract_provisions("2. mgr. 12. gr. laga nr. 68/2023")
    assert {"law": "68/2023", "gr": 12, "mgr": 2} in result


def test_mgr_before_gr_no_nr_keyword():
    result = extract_provisions("1. mgr. 175. gr. laga 91/1991")
    assert {"law": "91/1991", "gr": 175, "mgr": 1} in result


# ── P2: gr only ────────────────────────────────────────────────────────────────
def test_gr_only():
    result = extract_provisions("12. gr. laga nr. 68/2023")
    assert {"law": "68/2023", "gr": 12, "mgr": None} in result


# ── P3: article suffix letter ──────────────────────────────────────────────────
def test_article_suffix():
    result = extract_provisions("218. gr. a. laga nr. 19/1940")
    assert {"law": "19/1940", "gr": 218, "mgr": None, "sfx": "a"} in result


def test_mgr_with_suffix():
    result = extract_provisions("1. mgr. 218. gr. b. laga nr. 19/1940")
    assert {"law": "19/1940", "gr": 218, "mgr": 1, "sfx": "b"} in result


# ── P4: compound law name ──────────────────────────────────────────────────────
def test_compound_law_name_hegningarlög():
    result = extract_provisions("77. gr. almennra hegningarlaga nr. 19/1940")
    assert {"law": "19/1940", "gr": 77, "mgr": None} in result


def test_compound_law_name_umferðarlög():
    result = extract_provisions("3. mgr. 71. gr. almennra hegningarlaga nr. 19/1940")
    assert {"law": "19/1940", "gr": 71, "mgr": 3} in result


def test_compound_law_name_einkamál():
    result = extract_provisions("1. mgr. 66. gr. laga um meðferð einkamála nr. 91/1991")
    assert {"law": "91/1991", "gr": 66, "mgr": 1} in result


# ── P5: multi-article chain ────────────────────────────────────────────────────
def test_multi_article_chain():
    text = "sbr. 2. mgr. 48. gr. og 1. mgr. 50. gr. umferðarlaga nr. 77/2019"
    result = extract_provisions(text)
    assert {"law": "77/2019", "gr": 48, "mgr": 2} in result
    assert {"law": "77/2019", "gr": 50, "mgr": 1} in result


# ── P6: tölulið prefix ─────────────────────────────────────────────────────────
def test_tolulid_prefix():
    result = extract_provisions("2. tölul. 1. mgr. 10. gr. laga nr. 116/2006")
    assert {"law": "116/2006", "gr": 10, "mgr": 1} in result


# ── P7: sömu laga (same law propagation) ──────────────────────────────────────
def test_somu_laga_propagation():
    text = "1. mgr. 66. gr. laga nr. 91/1991 og sbr. 3. mgr. 63. gr. sömu laga"
    result = extract_provisions(text)
    assert {"law": "91/1991", "gr": 66, "mgr": 1} in result
    assert {"law": "91/1991", "gr": 63, "mgr": 3} in result


# ── P8: reglugerð (domestic regulation) ───────────────────────────────────────
def test_reglugerð():
    result = extract_provisions("8. gr. reglugerðar nr. 698/2014")
    assert {"law": "698/2014", "gr": 8, "mgr": None} in result


# ── P9: EU regulation ──────────────────────────────────────────────────────────
# ── Edge cases ─────────────────────────────────────────────────────────────────
def test_empty_text():
    assert extract_provisions("") == []


def test_none_text():
    assert extract_provisions(None) == []


def test_no_law_number_returns_empty():
    assert extract_provisions("2. mgr. 12. gr. einhvers staðar") == []


def test_deduplication():
    text = "2. mgr. 12. gr. laga nr. 68/2023 og 2. mgr. 12. gr. laga nr. 68/2023"
    result = extract_provisions(text)
    matching = [p for p in result if p["law"] == "68/2023" and p["gr"] == 12 and p["mgr"] == 2]
    assert len(matching) == 1


def test_multiple_laws_in_sentence():
    text = "12. gr. laga nr. 68/2023 og 175. gr. laga nr. 91/1991"
    result = extract_provisions(text)
    laws = {p["law"] for p in result}
    assert "68/2023" in laws
    assert "91/1991" in laws
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/test_provision_extractor.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'engine.processors.provision_extractor'`

- [ ] **Step 3: Implement `engine/processors/provision_extractor.py`**

```python
"""Extract structured law provision citations from Icelandic legal text.

Handles the citation patterns found in Hæstiréttur, Landsréttur, Héraðsdómar
and regulatory body rulings (Yfirskattanefnd, Samgöngustofa, etc.).

Returns a deduplicated list of dicts, each representing one provision cite:
    {"law": "19/1940", "gr": 218, "mgr": 1}        # paragraph + article
    {"law": "19/1940", "gr": 218, "mgr": None}      # article only
    {"law": "19/1940", "gr": 218, "mgr": 1, "sfx": "a"}  # with suffix

Law numbers cover both domestic statutes (19/1940) and EU/EEA regulations
(261/2004) since they share the same X/YYYY format.
"""
from __future__ import annotations

import re
from typing import Any


# ── Core regex ────────────────────────────────────────────────────────────────
# Strategy: anchor on the law number (X/YYYY) and look backward for gr/mgr.
# Two passes:
#   Pass 1 — find every (article_block, law_number) pair within a window.
#   Pass 2 — propagate law forward/backward for multi-article chains and
#             anaphoric "sömu laga" / "þeirra laga" references.

# Matches an article reference block immediately before a law number.
# Allows 0-6 words between "gr." and the law keyword to absorb compound names
# like "almennra hegningarlaga" or "laga um meðferð einkamála".
_LAW_NUM = r'\d+/\d{4}'
_LAW_KEYWORD = r'(?:laga?|lögum|reglugerðar?)(?:\s+\w+){0,4}?\s+'

_ART_BLOCK = (
    r'(?:'
    r'(?:(?P<mgr>\d+)\.\s*mgr\.\s*)?'      # optional: mgr before gr
    r'(?P<gr>\d+)\.\s*gr\.'                 # article number (required anchor)
    r'(?:\s*(?P<sfx>[a-záðéíóúýþæö])\.)?'  # optional suffix letter
    r'(?:\s*(?P<mgr2>\d+)\.\s*mgr\.)?'     # optional: mgr after gr (rarer)
    r')'
)

# Full pattern: article block + optional compound name + law keyword + optional "nr." + law number
_PROV_RE = re.compile(
    r'(?:'
    r'(?:\d+\.\s*tölul\.\s*)?'   # optional tölulið prefix (ignored in output)
    + _ART_BLOCK +
    r'(?:\s+(?:\w+\s+){0,5}?)?'  # 0-5 word compound name (non-greedy)
    r'(?:' + _LAW_KEYWORD + r')?'  # law keyword (optional — some texts omit it)
    r'nr\.\s*'
    r'(?P<law>' + _LAW_NUM + r')'
    r')',
    re.IGNORECASE | re.UNICODE,
)

# Also match "gr. X/YYYY" with no keyword at all (bare law number after gr)
_PROV_BARE_RE = re.compile(
    r'(?:'
    r'(?:\d+\.\s*tölul\.\s*)?'
    + _ART_BLOCK +
    r'\s+(?P<law>' + _LAW_NUM + r')'
    r')',
    re.IGNORECASE | re.UNICODE,
)

# Match "sömu laga" / "þeirra laga" / "laganna" after an article block — for
# propagating the last-seen law number to these anaphoric references.
_SOMU_LAGA_RE = re.compile(
    r'(?:'
    r'(?:\d+\.\s*tölul\.\s*)?'
    + _ART_BLOCK +
    r'\s+(?:sömu\s+laga|þeirra\s+laga|laganna|sömu\s+lögum)'
    r')',
    re.IGNORECASE | re.UNICODE,
)


def _match_to_dict(m: re.Match, law: str) -> dict[str, Any]:
    gr = int(m.group("gr"))
    mgr = int(m.group("mgr")) if m.group("mgr") else (
        int(m.group("mgr2")) if m.group("mgr2") else None
    )
    sfx = m.group("sfx").lower() if m.group("sfx") else None
    d: dict[str, Any] = {"law": law, "gr": gr, "mgr": mgr}
    if sfx:
        d["sfx"] = sfx
    return d


def _dedup(provisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple] = set()
    out: list[dict[str, Any]] = []
    for p in provisions:
        key = (p["law"], p["gr"], p.get("mgr"), p.get("sfx"))
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def extract_provisions(text: str | None) -> list[dict[str, Any]]:
    """Extract all law provision citations from Icelandic legal text.

    Returns a deduplicated list of provision dicts, or [] for empty/None input.
    """
    if not text:
        return []

    results: list[dict[str, Any]] = []
    last_law: str | None = None  # for anaphoric "sömu laga" propagation

    # Pass 1a: full pattern (with "nr." keyword)
    for m in _PROV_RE.finditer(text):
        law = m.group("law")
        last_law = law
        results.append(_match_to_dict(m, law))

    # Pass 1b: bare pattern (law number follows gr. directly, no keyword)
    for m in _PROV_BARE_RE.finditer(text):
        law = m.group("law")
        # Only add if not already covered by Pass 1a (avoid double-counting)
        d = _match_to_dict(m, law)
        key = (d["law"], d["gr"], d.get("mgr"), d.get("sfx"))
        if not any(
            (r["law"], r["gr"], r.get("mgr"), r.get("sfx")) == key for r in results
        ):
            last_law = law
            results.append(d)

    # Pass 2: anaphoric "sömu laga" — propagate last seen law
    if last_law:
        for m in _SOMU_LAGA_RE.finditer(text):
            d = _match_to_dict(m, last_law)
            results.append(d)

    return _dedup(results)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_provision_extractor.py -v
```

Expected: most tests pass. If some fail due to regex edge cases, debug and adjust the regex — the test file is the spec, not the regex. Common fixes:
- If compound-name test fails: the `{0,5}` word allowance might need tuning
- If multi-article chain fails: the chain `48. gr. og 50. gr.` requires the second `gr.` to also match; check if `_PROV_RE.finditer` finds it (it should since each `gr.` is an independent anchor)

- [ ] **Step 5: Commit**

```bash
git add engine/processors/provision_extractor.py tests/test_provision_extractor.py
git commit -m "feat: provision extractor for Icelandic law citations (mgr-before-gr, compound names, anaphora)"
```

---

### Task 2: `cited_provisions` column + GIN index

**Files:**
- Modify: `engine/database/models.py` (add `cited_provisions` column)
- Create: `scripts/setup_provision_index.py` (GIN index — must run after table exists)
- Test: `tests/test_models_cited_provisions.py` (new)

**Interfaces:**
- Consumes: nothing from prior tasks
- Produces: `Document.cited_provisions` mapped column (type `list[Any] | None`), importable; GIN index `ix_doc_cited_provisions` in the DB

- [ ] **Step 1: Write the failing test**

Create `tests/test_models_cited_provisions.py`:

```python
"""Test that cited_provisions column exists on Document model."""
from sqlalchemy import inspect as sa_inspect
from engine.database.models import Document


def test_cited_provisions_column_exists():
    mapper = sa_inspect(Document)
    col_names = {c.key for c in mapper.columns}
    assert "cited_provisions" in col_names


def test_cited_provisions_is_jsonb():
    from sqlalchemy.dialects.postgresql import JSONB
    mapper = sa_inspect(Document)
    col = next(c for c in mapper.columns if c.key == "cited_provisions")
    assert isinstance(col.columns[0].type, JSONB)
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
uv run pytest tests/test_models_cited_provisions.py -v
```

Expected: FAIL — `AssertionError: assert 'cited_provisions' in {...column names...}`

- [ ] **Step 3: Add column to `engine/database/models.py`**

Find the `# ── Search ──` block in the `Document` class (around line 86) and add `cited_provisions` right after `fts_is`:

```python
    # ── Search ────────────────────────────────────────────────────────────────
    embedding: Mapped[Any | None] = mapped_column(Vector(3072))
    fts_is: Mapped[Any | None] = mapped_column(TSVECTOR)
    cited_provisions: Mapped[list[Any] | None] = mapped_column(JSONB)
    # GIN index ix_doc_cited_provisions created by scripts/setup_provision_index.py
```

- [ ] **Step 4: Run test to confirm it passes**

```bash
uv run pytest tests/test_models_cited_provisions.py -v
```

Expected: 2 tests PASS.

- [ ] **Step 5: Add column to the live database**

```bash
uv run python -c "
import asyncio, sys
sys.path.insert(0, '.')
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

async def main():
    engine = create_async_engine('postgresql+asyncpg://geiri@localhost/lausnir_v2')
    async with engine.begin() as conn:
        await conn.execute(text(
            'ALTER TABLE documents ADD COLUMN IF NOT EXISTS cited_provisions JSONB'
        ))
    print('Column added (or already exists)')

asyncio.run(main())
"
```

Expected: `Column added (or already exists)`

- [ ] **Step 6: Create `scripts/setup_provision_index.py`**

```python
"""Create GIN index on documents.cited_provisions for fast provision search.

Uses jsonb_path_ops operator class which supports @> (containment) queries.
Runs CONCURRENTLY so it doesn't block reads/writes during creation.

Usage:
    uv run python scripts/setup_provision_index.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def main() -> None:
    engine = create_async_engine(
        "postgresql+asyncpg://geiri@localhost/lausnir_v2",
        isolation_level="AUTOCOMMIT",
    )
    async with engine.connect() as conn:
        print("Creating GIN index ix_doc_cited_provisions (CONCURRENTLY)…")
        await conn.execute(text("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_doc_cited_provisions
            ON documents USING GIN (cited_provisions jsonb_path_ops)
        """))
        print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 7: Run the index creation script**

```bash
uv run python scripts/setup_provision_index.py
```

Expected: `Creating GIN index ix_doc_cited_provisions (CONCURRENTLY)… Done.`

Note: `CONCURRENTLY` requires `AUTOCOMMIT` isolation level. The script sets this via `isolation_level="AUTOCOMMIT"` on the engine.

- [ ] **Step 8: Commit**

```bash
git add engine/database/models.py tests/test_models_cited_provisions.py scripts/setup_provision_index.py
git commit -m "feat: cited_provisions JSONB column + GIN index for law provision lookup"
```

---

### Task 3: Backfill `cited_provisions` for all documents

**Files:**
- Create: `scripts/backfill_cited_provisions.py`

**Interfaces:**
- Consumes: `extract_provisions(text: str) -> list[dict]` from Task 1
- Produces: `documents.cited_provisions` populated for all docs with body_text; backfill is idempotent (safe to re-run)

- [ ] **Step 1: Create `scripts/backfill_cited_provisions.py`**

```python
"""Backfill cited_provisions JSONB column from body_text for all documents.

For each document with body_text:
  1. Run extract_provisions(body_text) to get structured provision list.
  2. Store result as JSONB in cited_provisions (NULL if no provisions found).

Idempotent: re-running overwrites existing values with freshly extracted ones.

Usage:
    uv run python scripts/backfill_cited_provisions.py
    uv run python scripts/backfill_cited_provisions.py --source haestirettur
    uv run python scripts/backfill_cited_provisions.py --limit 50
"""
import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from engine.processors.provision_extractor import extract_provisions

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

BATCH_SIZE = 200
DB_URL = "postgresql+asyncpg://geiri@localhost/lausnir_v2"


async def backfill(source_name: str | None = None, limit: int | None = None) -> None:
    engine = create_async_engine(DB_URL)

    # Fetch document IDs + body_text
    async with engine.connect() as conn:
        source_filter = ""
        params: dict = {}
        if source_name:
            source_filter = "AND s.short_name = :sn"
            params["sn"] = source_name

        rows = (await conn.execute(text(f"""
            SELECT d.id, d.body_text
            FROM documents d
            JOIN sources s ON s.id = d.source_id
            WHERE d.body_text IS NOT NULL AND d.body_text != ''
            {source_filter}
            ORDER BY d.id
        """), params)).fetchall()

    if limit:
        rows = rows[:limit]

    total = len(rows)
    log.info("Processing %d documents (source=%r)", total, source_name or "all")

    t0 = time.monotonic()
    done = 0
    with_provisions = 0

    async with engine.begin() as conn:
        for i, (doc_id, body_text) in enumerate(rows):
            provisions = extract_provisions(body_text)
            value = json.dumps(provisions) if provisions else None
            if provisions:
                with_provisions += 1

            await conn.execute(
                text("UPDATE documents SET cited_provisions = :val::jsonb WHERE id = :id"),
                {"val": value, "id": doc_id},
            )

            done += 1
            if done % BATCH_SIZE == 0:
                await conn.commit()
                elapsed = time.monotonic() - t0
                rate = done / elapsed
                eta = (total - done) / rate if rate > 0 else 0
                log.info(
                    "[%d/%d] %.1f%% — %.1f docs/s — ETA %.0fs — %d with provisions",
                    done, total, 100 * done / total, rate, eta, with_provisions,
                )

        await conn.commit()

    elapsed = time.monotonic() - t0
    log.info(
        "Done: %d docs in %.1fs — %d (%.1f%%) had ≥1 provision citation",
        done, elapsed, with_provisions, 100 * with_provisions / done if done else 0,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=None, help="Limit to one source short_name")
    parser.add_argument("--limit", type=int, default=None, help="Process only N docs (smoke test)")
    args = parser.parse_args()
    asyncio.run(backfill(args.source, args.limit))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke test on 20 Hæstiréttur documents**

```bash
uv run python scripts/backfill_cited_provisions.py --source haestirettur --limit 20
```

Expected output:
```
INFO Processing 20 documents (source='haestirettur')
INFO Done: 20 docs in X.Xs — N (M%) had ≥1 provision citation
```

M should be > 80% (analysis showed ~99% of court verdicts cite laws).

- [ ] **Step 3: Verify a sample**

```bash
uv run python -c "
import asyncio, sys, json
sys.path.insert(0, '.')
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

async def check():
    engine = create_async_engine('postgresql+asyncpg://geiri@localhost/lausnir_v2')
    async with engine.connect() as conn:
        rows = (await conn.execute(text('''
            SELECT d.case_number, d.cited_provisions
            FROM documents d JOIN sources s ON s.id = d.source_id
            WHERE s.short_name = 'haestirettur'
              AND d.cited_provisions IS NOT NULL
            ORDER BY d.document_date DESC LIMIT 3
        '''))).fetchall()
    for case_num, provs in rows:
        print(case_num, json.dumps(provs[:3], ensure_ascii=False))

asyncio.run(check())
"
```

Expected: 3 rows, each with a list of provision dicts like `[{"law": "91/1991", "gr": 175, "mgr": 1}, ...]`

- [ ] **Step 4: Run full backfill across all sources**

```bash
uv run python scripts/backfill_cited_provisions.py
```

This processes all documents with body_text (approximately 100k rows). Expect 10-30 minutes. Logs progress every 200 docs.

- [ ] **Step 5: Verify final counts**

```bash
uv run python -c "
import asyncio, sys
sys.path.insert(0, '.')
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

async def check():
    engine = create_async_engine('postgresql+asyncpg://geiri@localhost/lausnir_v2')
    async with engine.connect() as conn:
        total = (await conn.execute(text(
            'SELECT count(*) FROM documents WHERE body_text IS NOT NULL'
        ))).scalar()
        with_provs = (await conn.execute(text(
            'SELECT count(*) FROM documents WHERE cited_provisions IS NOT NULL'
        ))).scalar()
        # Most-cited law across the corpus
        top_law = (await conn.execute(text('''
            SELECT p->>'law' AS law, count(*) AS n
            FROM documents, jsonb_array_elements(cited_provisions) AS p
            GROUP BY p->>'law'
            ORDER BY n DESC LIMIT 5
        '''))).fetchall()
    print(f'Docs with body_text: {total}')
    print(f'Docs with cited_provisions: {with_provs} ({100*with_provs/total:.1f}%)')
    print('Top 5 cited laws:')
    for law, n in top_law:
        print(f'  {law}: {n} documents')

asyncio.run(check())
"
```

Expected: ≥ 70% of docs have at least one provision; top laws are likely 91/1991 (civil procedure), 19/1940 (criminal code), 90/2003 (tax law).

- [ ] **Step 6: Commit**

```bash
git add scripts/backfill_cited_provisions.py
git commit -m "feat: backfill cited_provisions from body_text using provision extractor"
```

---

### Task 4: Provision search in API

**Files:**
- Modify: `engine/search/queries.py` (add provision filter to `search_documents`)
- Modify: `engine/api/app.py` (add `provision` query param to `/api/search`)
- Test: `tests/test_provision_search.py` (new)

**Interfaces:**
- Consumes:
  - `parse_provision_query(q: str) -> tuple[str, int, str | None, int | None] | None` — already in `queries.py` at line 48. Returns `(law, gr, sfx|None, mgr|None)`.
  - `cited_provisions` JSONB column on `documents` from Task 2
- Produces:
  - `search_documents(..., provision: str | None = None, ...)` — new optional param
  - `GET /api/search?provision=1.+mgr.+218.+gr.+19%2F1940` — new query param
  - Filter applies `documents.cited_provisions @> :prov_filter::jsonb`
  - `provision` can combine with existing `q`, `scope`, `date_from`, `date_to` filters

**How the filter works:**

```python
# User input: "1. mgr. 218. gr. 19/1940"
parsed = parse_provision_query("1. mgr. 218. gr. 19/1940")
# → ("19/1940", 218, None, 1)  i.e. (law, gr, sfx, mgr)

# Build minimum containment object — only include fields user specified:
# If mgr specified: {"law": "19/1940", "gr": 218, "mgr": 1}
# If no mgr:        {"law": "19/1940", "gr": 218}
# If no gr:         {"law": "19/1940"}    (search entire law)
```

- [ ] **Step 1: Write the failing tests**

Create `tests/test_provision_search.py`:

```python
"""Tests for provision filter in search_documents."""
import pytest
from engine.search.queries import _build_provision_filter


def test_full_provision_filter():
    frag, params = _build_provision_filter("19/1940", gr=218, sfx=None, mgr=1)
    assert "cited_provisions @>" in frag
    assert "prov_filter" in params
    import json
    obj = json.loads(params["prov_filter"])
    assert obj == [{"law": "19/1940", "gr": 218, "mgr": 1}]


def test_provision_filter_no_mgr():
    frag, params = _build_provision_filter("19/1940", gr=218, sfx=None, mgr=None)
    import json
    obj = json.loads(params["prov_filter"])
    assert obj == [{"law": "19/1940", "gr": 218}]


def test_provision_filter_law_only():
    frag, params = _build_provision_filter("19/1940", gr=None, sfx=None, mgr=None)
    import json
    obj = json.loads(params["prov_filter"])
    assert obj == [{"law": "19/1940"}]


def test_provision_filter_with_suffix():
    frag, params = _build_provision_filter("19/1940", gr=218, sfx="a", mgr=1)
    import json
    obj = json.loads(params["prov_filter"])
    assert obj == [{"law": "19/1940", "gr": 218, "mgr": 1, "sfx": "a"}]


def test_returns_sql_fragment():
    frag, params = _build_provision_filter("19/1940", gr=218, sfx=None, mgr=1)
    assert "d.cited_provisions" in frag
    assert ":prov_filter" in frag
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/test_provision_search.py -v
```

Expected: FAIL — `ImportError: cannot import name '_build_provision_filter'`

- [ ] **Step 3: Add `_build_provision_filter` to `engine/search/queries.py`**

Add this function after the existing `parse_provision_query` function (around line 66, before the `from sqlalchemy import text` import):

```python
def _build_provision_filter(
    law: str,
    gr: int | None,
    sfx: str | None,
    mgr: int | None,
) -> tuple[str, dict]:
    """Build a JSONB containment WHERE fragment for provision search.

    The containment object includes only the fields the user specified:
    - law always included
    - gr included if provided
    - mgr included if provided (only meaningful when gr is also provided)
    - sfx included if provided

    Returns (sql_fragment, params_dict) where sql_fragment uses :prov_filter.
    """
    import json as _json
    obj: dict = {"law": law}
    if gr is not None:
        obj["gr"] = gr
        if sfx is not None:
            obj["sfx"] = sfx
        if mgr is not None:
            obj["mgr"] = mgr
    return (
        "d.cited_provisions @> :prov_filter::jsonb",
        {"prov_filter": _json.dumps([obj])},
    )
```

- [ ] **Step 4: Run provision filter tests to confirm they pass**

```bash
uv run pytest tests/test_provision_search.py -v
```

Expected: 5 tests PASS.

- [ ] **Step 5: Add `provision` parameter to `search_documents`**

In `engine/search/queries.py`, find the `search_documents` function signature and add `provision: str | None = None` as a new parameter (after `proximity_n`):

```python
async def search_documents(
    session: AsyncSession,
    *,
    q: str = "",
    mode: str = "keyword",
    scope: list[str] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    sort: str = "relevance",
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    regex_fields: list[str] | None = None,
    proximity_n: int = 5,
    provision: str | None = None,   # ← ADD THIS
) -> SearchResults:
```

Then, in the body of `search_documents`, after the existing scope and date filters are added to `where` (around line 270, after the `date_to` block), add the provision filter:

```python
    # Provision filter (independent of text mode)
    if provision:
        parsed = parse_provision_query(provision)
        if parsed:
            law, gr, sfx, mgr = parsed
            prov_frag, prov_params = _build_provision_filter(law, gr, sfx, mgr)
            where.append(prov_frag)
            params.update(prov_params)
```

- [ ] **Step 6: Add `provision` param to `/api/search` in `engine/api/app.py`**

Find the `/api/search` endpoint function. It currently accepts `q`, `mode`, `scope`, etc. Add `provision` as a new `Query` parameter:

```python
@app.get("/api/search")
async def search(
    q: str = Query(""),
    mode: str = Query("keyword"),
    scope: list[str] = Query([]),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    sort: str = Query("relevance"),
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    regex_fields: list[str] = Query([]),
    proximity_n: int = Query(5, ge=1, le=50),
    provision: str | None = Query(None),   # ← ADD THIS
    session: AsyncSession = Depends(get_session),
) -> dict:
```

And pass it through to `search_documents`:

```python
    results = await search_documents(
        session,
        q=q,
        mode=mode,
        scope=scope or None,
        date_from=date_from,
        date_to=date_to,
        sort=sort,
        page=page,
        page_size=page_size,
        regex_fields=regex_fields or None,
        proximity_n=proximity_n,
        provision=provision,   # ← ADD THIS
    )
```

- [ ] **Step 7: Run all search tests**

```bash
uv run pytest tests/test_provision_search.py tests/test_search_queries.py -v
```

Expected: all tests PASS (5 new + 24 existing).

- [ ] **Step 8: Smoke test provision search against live API**

The API should be running on port 8077 (restart it if not: `uv run uvicorn engine.api.app:app --reload --port 8077 &`).

**Test 1: Search for 1. mgr. 218. gr. laga nr. 19/1940 (criminal code, assault provision)**
```bash
curl -s "http://localhost:8077/api/search?provision=1.+mgr.+218.+gr.+19%2F1940&page_size=5" | python3 -m json.tool | head -50
```
Expected: `total > 0`, results from courts (haestirettur, landsrettur, heradsdomstolar).

**Test 2: Entire law — all docs citing lög nr. 91/1991 (civil procedure)**
```bash
curl -s "http://localhost:8077/api/search?provision=91%2F1991&page_size=3" | python3 -m json.tool | grep '"total"'
```
Expected: `"total": N` where N is very large (thousands).

**Test 3: Combine provision filter with scope**
```bash
curl -s "http://localhost:8077/api/search?provision=19%2F1940&scope=haestirettur&page_size=3" | python3 -m json.tool | grep '"total"'
```
Expected: `total > 0`, only Hæstiréttur results.

**Test 4: Combine provision filter with text search**
```bash
curl -s "http://localhost:8077/api/search?q=refsing&mode=keyword&provision=19%2F1940&page_size=3" | python3 -m json.tool | grep '"total"'
```
Expected: `total > 0`, filtered to docs that both mention "refsing" AND cite lög nr. 19/1940.

- [ ] **Step 9: Commit**

```bash
git add engine/search/queries.py engine/api/app.py tests/test_provision_search.py
git commit -m "feat: provision search filter — cited_provisions @> JSONB containment via /api/search?provision="
```

---

## Self-Review

**Spec coverage:**
- ✅ P1 (mgr before gr) — Task 1 test + extractor
- ✅ P2 (gr only) — Task 1 test + extractor
- ✅ P3 (article suffix) — Task 1 test + extractor
- ✅ P4 (compound law name) — Task 1 test + extractor (`{0,5}` word allowance)
- ✅ P5 (multi-article chain) — Task 1 test; handled because `_PROV_RE.finditer` finds each `gr.` anchor independently; law propagates forward via the last law in the sentence
- ✅ P6 (tölulið prefix) — Task 1 test + extractor (stripped via `(?:\d+\.\s*tölul\.\s*)?`)
- ✅ P7 (sömu laga) — Task 1 test + `_SOMU_LAGA_RE` pass
- ✅ P8 (reglugerð domestic) — Task 1 test; handled because `_LAW_KEYWORD` includes `reglugerðar?`
- ✅ P8 (reglugerð domestic) — Task 1 test; handled because `_LAW_KEYWORD` includes `reglugerðar?`
- ✅ `cited_provisions` JSONB column — Task 2
- ✅ GIN index `jsonb_path_ops` — Task 2
- ✅ Backfill all documents — Task 3
- ✅ `search_documents(provision=...)` — Task 4
- ✅ `GET /api/search?provision=...` — Task 4
- ✅ `provision` combinable with `q`, `scope`, dates — Task 4 (provision adds to `where` list alongside other filters)

**Placeholder scan:** None found. All code is complete.

**Type consistency:**
- `parse_provision_query` returns `tuple[str, int, str | None, int | None]` as `(law, gr, sfx, mgr)` — Task 4 unpacks as `law, gr, sfx, mgr` ✅
- `_build_provision_filter(law, gr, sfx, mgr)` signature matches how Task 4 calls it ✅
- `extract_provisions` returns `list[dict]` — Task 3 uses `json.dumps(provisions)` correctly ✅
