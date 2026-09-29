# Read-only MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A stdio MCP server (`python -m engine.mcp`) that lets an LLM client on the same machine search Lausnir, read passages and documents, and run read-only SQL through a dedicated Postgres role.

**Architecture:** New package `engine/mcp/` with pure async tool functions (`tools.py`) that call the existing search core (`search_documents`, `get_passages`, `get_document`, `catalog`, `facet_counts`) and are wrapped by the official `mcp` 2.x `MCPServer` in `server.py`. All DB access goes through `init_db(url=DATABASE_URL_READONLY, create_tables=False)`. A three-layer SELECT guard (role, READ ONLY transaction, `sqlguard`) protects `sql_query`.

**Tech Stack:** Python 3.13, uv, `mcp>=2.2,<3` (`mcp.server.mcpserver.MCPServer`), SQLAlchemy 2 async + asyncpg, pytest (asyncio_mode=auto), PostgreSQL 17.

**Spec:** `docs/superpowers/specs/2026-09-29-mcp-server-design.md`

## Global Constraints

- Dependency pin exactly `"mcp>=2.2,<3"` and `"python-dotenv"` added to `[project].dependencies` in `pyproject.toml`; commit `uv.lock`.
- The server writes **nothing to stdout** except the MCP protocol. All logging to stderr. A test enforces that importing `engine.mcp.server` prints nothing.
- MCP uses **only** `DATABASE_URL_READONLY`. Never fall back to `DATABASE_URL` in `engine/mcp/__main__.py`. (Tests may fall back, see Task 4.)
- `init_db` signature becomes `async def init_db(url: str | None = None, *, create_tables: bool = True) -> None`; existing callers (`engine/api/app.py`, scripts) keep working unchanged.
- MCP-layer caps (verbatim from spec §7): `page_size` 1–25 default 10; `passage_context` `before`/`after` 0–10 default 2; `get_passages` `count` 1–50 default 20; `get_document` `max_chars` 0–40000 default 0; `sql_query` `max_rows` 1–1000 default 200; cells truncated to 500 chars; `statement_timeout` 15 s.
- Tool names, exactly eight: `search`, `passage_context`, `get_passages`, `get_document`, `list_sources`, `facets`, `describe_schema`, `sql_query`. Every tool registered with `ToolAnnotations(read_only_hint=True)`.
- Error text to the LLM is Icelandic, as written in the spec (§4, §7). Errors are raised as `mcp.server.mcpserver.exceptions.ToolError`, never as bare exceptions with stack traces.
- Live DB is read-only for all tasks except Task 7 (role creation), which needs the user's explicit approval before the SQL runs. Never touch dev servers on ports 8077/5173.
- Commit messages end with the two trailer lines the session uses (`Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` and the `Claude-Session:` line copied from `git log -1 --format=%B 19dc663`).
- Run tests with `.env` loaded: `set -a; . ./.env; set +a; uv run pytest -q …` from the repo root.

## Review Focus

1. **`passage_id` from a `summary`-layer passage** (ordinal numbering is per document across layers): `passage_context` must still return the focus passage inside the window and `focus_ordinal` must equal the passage's ordinal. Test in Task 4 (`test_passage_context_summary_layer`).
2. **`get_document(max_chars=N)` on a document whose markdown is shorter than N**: `text_truncated` must be `False` and `text` complete. Test in Task 4.
3. **`sql_query` with a leading comment or whitespace before `SELECT`** (LLMs do this): must be accepted, not rejected as "does not start with SELECT". Test in Task 2.
4. **`sql_query` returning JSONB/UUID/date/bytes cells**: must serialize (no `TypeError: Object of type UUID is not JSON serializable` from the SDK). Test in Task 5 (`test_sql_query_serializes_special_types`).
5. **Unknown scope token combined with a known one** (`["domstolar", "typo"]`): the known one must still be searched, no `hint` about unknown scope because `resolve_scope` returns a non-empty filter. Test in Task 4 (`test_search_hint_only_when_scope_resolves_empty`).

---

### Task 1: `init_db` options and dependencies

**Files:**
- Modify: `engine/database/connection.py:35-41`
- Modify: `pyproject.toml` (dependencies)
- Test: `tests/test_connection_init.py`

**Interfaces:**
- Produces: `async def init_db(url: str | None = None, *, create_tables: bool = True) -> None`. `url=None` keeps today's `DATABASE_URL` lookup. `create_tables=False` skips `Base.metadata.create_all`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_connection_init.py
"""init_db must accept an explicit URL and be able to skip create_all — the
MCP server connects with a SELECT-only role that cannot run DDL."""
import pytest

import engine.database.connection as conn


class _FakeConn:
    def __init__(self, log): self.log = log
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def run_sync(self, fn): self.log.append(("run_sync", fn.__name__))


class _FakeEngine:
    def __init__(self, url, log): self.url = url; self.log = log
    def begin(self): return _FakeConn(self.log)


async def test_init_db_explicit_url_and_no_create_tables(monkeypatch):
    log: list = []
    created: dict = {}
    def fake_create(url, **kw):
        created["url"] = url; created["kw"] = kw
        return _FakeEngine(url, log)
    monkeypatch.setattr(conn, "create_async_engine", fake_create)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    await conn.init_db(url="postgresql+asyncpg://ro@localhost/x", create_tables=False)
    assert created["url"] == "postgresql+asyncpg://ro@localhost/x"
    assert created["kw"]["connect_args"] == {"server_settings": {"plan_cache_mode": "force_custom_plan"}}
    assert log == []                      # no create_all
    assert conn.AsyncSessionLocal is not None


async def test_init_db_default_still_reads_env_and_creates(monkeypatch):
    log: list = []
    monkeypatch.setattr(conn, "create_async_engine", lambda url, **kw: _FakeEngine(url, log))
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://rw@localhost/y")
    await conn.init_db()
    assert log == [("run_sync", "create_all")]


async def test_init_db_without_url_or_env_raises(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError):
        await conn.init_db(create_tables=False)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `set -a; . ./.env; set +a; uv run pytest -q tests/test_connection_init.py`
Expected: FAIL with `TypeError: init_db() got an unexpected keyword argument 'url'`.

- [ ] **Step 3: Implement**

Replace `init_db` in `engine/database/connection.py`:

```python
async def init_db(url: str | None = None, *, create_tables: bool = True) -> None:
    """Create the engine and session factory.

    ``url`` overrides ``DATABASE_URL`` (the MCP server passes
    ``DATABASE_URL_READONLY``). ``create_tables=False`` skips
    ``Base.metadata.create_all`` — required for a SELECT-only role, which may
    not run DDL, and correct for any process that is not the owner of the
    schema (alembic owns it; create_all here is a dev convenience).
    """
    global _engine, AsyncSessionLocal
    _engine = create_async_engine(url or _get_db_url(), echo=False, pool_size=5, max_overflow=10,
                                  connect_args=_CONNECT_ARGS)
    AsyncSessionLocal = async_sessionmaker(_engine, expire_on_commit=False)
    if create_tables:
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
```

Add to `pyproject.toml` `[project].dependencies`, after `"uvicorn",`:

```toml
    # MCP
    "mcp>=2.2,<3",
    "python-dotenv",
```

Run: `uv sync` (updates `uv.lock`; verify `uv run python -c "import mcp, dotenv; print(mcp.__name__)"` prints `mcp`).

- [ ] **Step 4: Run tests**

Run: `set -a; . ./.env; set +a; uv run pytest -q tests/test_connection_init.py && uv run pytest -q`
Expected: 3 new PASS; full suite green (471 + 3).

- [ ] **Step 5: Commit**

```bash
git add engine/database/connection.py pyproject.toml uv.lock tests/test_connection_init.py
git commit -m "feat(db): init_db accepts url and create_tables; add mcp + python-dotenv deps"
```

---

### Task 2: `sqlguard` — SELECT-only validator

**Files:**
- Create: `engine/mcp/__init__.py` (empty docstring module)
- Create: `engine/mcp/sqlguard.py`
- Test: `tests/test_mcp_sqlguard.py`

**Interfaces:**
- Produces: `class SqlRejected(ValueError)`; `def validate_sql(sql: str) -> str` returning the stripped SQL (trailing `;` removed) or raising `SqlRejected(reason)` with an Icelandic reason.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_mcp_sqlguard.py
import pytest

from engine.mcp.sqlguard import SqlRejected, validate_sql


@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "select count(*) from documents",
    "  -- leading comment\n  SELECT id FROM documents LIMIT 5",
    "/* block */ SELECT 1",
    "WITH x AS (SELECT 1 AS a) SELECT a FROM x",
    "EXPLAIN SELECT 1",
    "EXPLAIN (ANALYZE, BUFFERS) SELECT count(*) FROM sources",
    "SHOW server_version",
    "TABLE sources",
    "VALUES (1), (2)",
    "SELECT 1;",                                  # single trailing semicolon ok
    "SELECT 'a;b' AS s",                          # semicolon inside string literal ok
    "SELECT * FROM documents WHERE summary LIKE '%delete%'",   # keyword inside literal ok
    "SELECT into_col FROM t",                     # 'into' as part of an identifier ok
])
def test_accepts(sql):
    assert validate_sql(sql)


@pytest.mark.parametrize("sql,needle", [
    ("", "tóm"),
    ("   ", "tóm"),
    ("DELETE FROM documents", "SELECT"),
    ("UPDATE documents SET summary = ''", "SELECT"),
    ("INSERT INTO sources VALUES (1)", "SELECT"),
    ("DROP TABLE documents", "SELECT"),
    ("TRUNCATE passages", "SELECT"),
    ("SELECT 1; DELETE FROM documents", "ein setning"),
    ("SELECT 1; SELECT 2", "ein setning"),
    ("WITH d AS (DELETE FROM documents RETURNING id) SELECT * FROM d", "breyt"),
    ("WITH u AS (UPDATE sources SET x = 1 RETURNING id) SELECT 1", "breyt"),
    ("SELECT * INTO new_table FROM documents", "INTO"),
    ("SELECT pg_sleep(30)", "pg_sleep"),
    ("SELECT pg_read_file('/etc/passwd')", "pg_read_file"),
    ("SELECT lo_import('/etc/passwd')", "lo_import"),
    ("COPY documents TO '/tmp/x'", "SELECT"),
    ("SELECT * FROM dblink('x', 'y')", "dblink"),
    ("EXPLAIN ANALYZE DELETE FROM documents", "breyt"),
])
def test_rejects(sql, needle):
    with pytest.raises(SqlRejected) as ei:
        validate_sql(sql)
    assert needle.lower() in str(ei.value).lower()


def test_returns_sql_without_trailing_semicolon():
    assert validate_sql("SELECT 1;") == "SELECT 1"
    assert validate_sql("  SELECT 1  ") == "SELECT 1"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest -q tests/test_mcp_sqlguard.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'engine.mcp'`.

- [ ] **Step 3: Implement**

`engine/mcp/__init__.py`:

```python
"""Read-only MCP server for Lausnir (stdio). See docs/superpowers/specs/2026-09-29-mcp-server-design.md."""
```

`engine/mcp/sqlguard.py`:

```python
"""SELECT-only guard for the `sql_query` MCP tool.

This is the third of three layers (spec §5): the `lausnir_ro` role cannot
write, every query runs in a READ ONLY transaction, and this validator gives
the LLM an immediate, understandable refusal for obvious mistakes. It is
deliberately conservative — rejecting a legitimate query costs one retry,
letting a write through would cost the corpus.
"""
from __future__ import annotations

import re

_ALLOWED_FIRST = ("SELECT", "WITH", "EXPLAIN", "SHOW", "TABLE", "VALUES")
_MODIFYING = re.compile(r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|COPY|VACUUM|REINDEX|CLUSTER|LOCK|CALL|DO)\b", re.I)
_FORBIDDEN_FUNCS = re.compile(r"\b(pg_sleep|pg_sleep_for|pg_sleep_until|pg_read_file|pg_read_binary_file|pg_ls_dir|pg_stat_file|lo_import|lo_export|lo_unlink|dblink|dblink_exec|pg_terminate_backend|pg_cancel_backend|set_config|pg_reload_conf)\b", re.I)
_SELECT_INTO = re.compile(r"\bSELECT\b(?:(?!\bFROM\b).)*?\bINTO\b", re.I | re.S)


class SqlRejected(ValueError):
    """Raised with an Icelandic reason the LLM can act on."""


def _strip_comments_and_literals(sql: str) -> str:
    """Remove -- and /* */ comments and blank out string literals so that
    keywords inside literals ('%delete%') and semicolons inside strings
    don't trigger the structural checks."""
    out, i, n = [], 0, len(sql)
    while i < n:
        ch = sql[i]
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j == -1 else j
            out.append(" ")
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            i = n if j == -1 else j + 2
            out.append(" ")
        elif ch == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'" and j + 1 < n and sql[j + 1] == "'":
                    j += 2
                    continue
                if sql[j] == "'":
                    break
                j += 1
            out.append("''")
            i = j + 1
        elif ch == '"':
            j = sql.find('"', i + 1)
            j = n - 1 if j == -1 else j
            out.append(sql[i:j + 1])   # keep quoted identifiers (they may contain 'into')
            i = j + 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def validate_sql(sql: str) -> str:
    if sql is None or not sql.strip():
        raise SqlRejected("Fyrirspurnin er tóm.")
    cleaned = _strip_comments_and_literals(sql).strip()
    # Allow exactly one optional trailing semicolon.
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1].rstrip()
    if ";" in cleaned:
        raise SqlRejected("Aðeins ein setning í einu (semikomma fannst inni í fyrirspurninni).")
    if not cleaned:
        raise SqlRejected("Fyrirspurnin er tóm.")
    first = re.match(r"[A-Za-z]+", cleaned)
    if not first or first.group(0).upper() not in _ALLOWED_FIRST:
        raise SqlRejected("Aðeins lesfyrirspurnir: setningin verður að byrja á SELECT, WITH, EXPLAIN, SHOW, TABLE eða VALUES.")
    if _MODIFYING.search(cleaned):
        raise SqlRejected("Fyrirspurnin inniheldur breytingaskipun (INSERT/UPDATE/DELETE/DDL). Þjónninn er read-only.")
    m = _FORBIDDEN_FUNCS.search(cleaned)
    if m:
        raise SqlRejected(f"Fallið {m.group(1)} er ekki leyft.")
    if _SELECT_INTO.search(cleaned):
        raise SqlRejected("SELECT … INTO (ný tafla) er ekki leyft.")
    # Return the original text minus comments? No — return the user's SQL with
    # only outer whitespace and one trailing ';' removed, so Postgres sees the
    # exact query (comments are harmless to the executor).
    original = sql.strip()
    if original.endswith(";"):
        original = original[:-1].rstrip()
    return original
```

Note on `_SELECT_INTO`: it matches `INTO` only between `SELECT` and the first `FROM`, which is where `SELECT … INTO newtable FROM …` puts it; `SELECT into_col FROM t` does not match because `\bINTO\b` needs a word boundary after `INTO` (the `_` in `into_col` is a word character).

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q tests/test_mcp_sqlguard.py`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/mcp/__init__.py engine/mcp/sqlguard.py tests/test_mcp_sqlguard.py
git commit -m "feat(mcp): SELECT-only sqlguard with Icelandic refusals"
```

---

### Task 3: `shaping` — response compaction helpers

**Files:**
- Create: `engine/mcp/shaping.py`
- Test: `tests/test_mcp_shaping.py`

**Interfaces:**
- Produces:
  - `def truncate_text(text: str | None, max_chars: int) -> tuple[str | None, bool]` — cuts at the last whitespace before `max_chars`, appends nothing; returns `(text, truncated)`. `max_chars <= 0` → `(None, False)`.
  - `def cell_value(v: Any, max_chars: int = 500) -> tuple[Any, bool]` — JSON-safe cell: `uuid.UUID`/`date`/`datetime`/`Decimal` → `str`; `bytes`/`memoryview` → `"<bytes N>"`; `dict`/`list` → left as-is (JSONB) unless its `json.dumps` exceeds `max_chars`, then the dumped string truncated; `str` longer than `max_chars` truncated with `"…"`; returns `(value, truncated)`.
  - `def compact_search_result(r: dict) -> dict` — maps a `search_documents` result dict to the spec §4.1 shape: keys `doc_id, urlausn, source, court, case_number, date, verdict_type, snippet, passage_id, anchor, section_kind, match_tier, keywords` (keywords list capped at 5; non-list keywords → `[]`; UUIDs/dates to str).
  - `def compact_passage(p: dict) -> dict` — `passage_id, ordinal, layer, section_kind, anchor, text` from a `get_passages` passage dict.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_mcp_shaping.py
import datetime as dt
import uuid
from decimal import Decimal

from engine.mcp.shaping import cell_value, compact_passage, compact_search_result, truncate_text


def test_truncate_text_cuts_at_word_boundary():
    text, cut = truncate_text("alpha beta gamma delta", 12)
    assert text == "alpha beta" and cut is True


def test_truncate_text_no_cut_when_short():
    assert truncate_text("stutt", 100) == ("stutt", False)


def test_truncate_text_zero_means_none():
    assert truncate_text("x", 0) == (None, False)
    assert truncate_text(None, 10) == (None, False)


def test_truncate_text_without_whitespace_hard_cuts():
    text, cut = truncate_text("a" * 50, 10)
    assert text == "a" * 10 and cut


def test_cell_value_types():
    u = uuid.uuid4()
    assert cell_value(u) == (str(u), False)
    assert cell_value(dt.date(2020, 5, 5)) == ("2020-05-05", False)
    assert cell_value(dt.datetime(2020, 5, 5, 12, 0)) == ("2020-05-05T12:00:00", False)
    assert cell_value(Decimal("1.50")) == ("1.50", False)
    assert cell_value(b"\x00\x01\x02") == ("<bytes 3>", False)
    assert cell_value(memoryview(b"abcd")) == ("<bytes 4>", False)
    assert cell_value(None) == (None, False)
    assert cell_value(3) == (3, False)
    assert cell_value(True) == (True, False)


def test_cell_value_truncates_long_str_and_json():
    s, cut = cell_value("x" * 600)
    assert len(s) == 501 and s.endswith("…") and cut
    big = {"k": "y" * 600}
    v, cut = cell_value(big)
    assert isinstance(v, str) and cut and v.endswith("…")
    small = {"k": 1}
    assert cell_value(small) == (small, False)


def test_compact_search_result_shape_and_keyword_cap():
    u = uuid.uuid4(); p = uuid.uuid4()
    r = {"id": u, "urlausn": "Hrd. 1/2020", "source": "haestirettur", "source_display": "Hæstiréttur",
         "court": "Hæstiréttur", "case_number": "1/2020", "document_date": dt.date(2020, 1, 2),
         "verdict_type": "Dómur", "keywords": list("abcdefg"), "plaintiffs": [{"n": 1}], "defendants": [],
         "snippet": "<b>x</b>", "has_appeal_links": True, "passage_id": p, "anchor": "body/II/mgr. 3",
         "section_kind": "nidurstada", "layer": "body", "match_count": 2, "match_tier": 1}
    out = compact_search_result(r)
    assert set(out) == {"doc_id", "urlausn", "source", "court", "case_number", "date", "verdict_type",
                        "snippet", "passage_id", "anchor", "section_kind", "match_tier", "keywords"}
    assert out["doc_id"] == str(u) and out["passage_id"] == str(p) and out["date"] == "2020-01-02"
    assert out["keywords"] == ["a", "b", "c", "d", "e"]
    assert out["match_tier"] == 1


def test_compact_search_result_tolerates_missing_passage_fields():
    out = compact_search_result({"id": "x", "keywords": None})
    assert out["passage_id"] is None and out["anchor"] is None and out["keywords"] == []
    assert out["date"] is None and out["match_tier"] == 0


def test_compact_passage():
    p = {"id": uuid.UUID(int=1), "ordinal": 4, "layer": "body", "anchor": "a", "section_path": "II",
         "section_kind": "malsatvik", "para_from": 3, "para_to": 3, "char_start": 0, "char_end": 9,
         "word_count": 2, "text": "hello there"}
    assert compact_passage(p) == {"passage_id": str(uuid.UUID(int=1)), "ordinal": 4, "layer": "body",
                                  "section_kind": "malsatvik", "anchor": "a", "text": "hello there"}
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest -q tests/test_mcp_shaping.py`
Expected: FAIL, `ModuleNotFoundError: No module named 'engine.mcp.shaping'`.

- [ ] **Step 3: Implement**

```python
# engine/mcp/shaping.py
"""Compaction of core results into the token-frugal shapes the MCP tools return (spec §4)."""
from __future__ import annotations

import datetime as _dt
import json
import uuid
from decimal import Decimal
from typing import Any

MAX_KEYWORDS = 5
CELL_MAX_CHARS = 500


def truncate_text(text: str | None, max_chars: int) -> tuple[str | None, bool]:
    if text is None or max_chars <= 0:
        return None, False
    if len(text) <= max_chars:
        return text, False
    head = text[:max_chars]
    cut = head.rfind(" ")
    if cut > 0:
        head = head[:cut]
    return head.rstrip(), True


def _scalar(v: Any) -> Any:
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, memoryview):
        v = v.tobytes()
    if isinstance(v, (bytes, bytearray)):
        return f"<bytes {len(v)}>"
    return v


def cell_value(v: Any, max_chars: int = CELL_MAX_CHARS) -> tuple[Any, bool]:
    v = _scalar(v)
    if isinstance(v, (dict, list)):
        dumped = json.dumps(v, ensure_ascii=False, default=_scalar)
        if len(dumped) > max_chars:
            return dumped[:max_chars] + "…", True
        return v, False
    if isinstance(v, str) and len(v) > max_chars:
        return v[:max_chars] + "…", True
    return v, False


def compact_search_result(r: dict) -> dict:
    kws = r.get("keywords")
    if not isinstance(kws, list):
        kws = []
    date = r.get("document_date")
    return {
        "doc_id": str(r.get("id")) if r.get("id") is not None else None,
        "urlausn": r.get("urlausn"),
        "source": r.get("source"),
        "court": r.get("court"),
        "case_number": r.get("case_number"),
        "date": date.isoformat() if isinstance(date, (_dt.date, _dt.datetime)) else date,
        "verdict_type": r.get("verdict_type"),
        "snippet": r.get("snippet"),
        "passage_id": str(r["passage_id"]) if r.get("passage_id") else None,
        "anchor": r.get("anchor"),
        "section_kind": r.get("section_kind"),
        "match_tier": r.get("match_tier") or 0,
        "keywords": [str(k) for k in kws[:MAX_KEYWORDS]],
    }


def compact_passage(p: dict) -> dict:
    return {
        "passage_id": str(p["id"]),
        "ordinal": p["ordinal"],
        "layer": p["layer"],
        "section_kind": p["section_kind"],
        "anchor": p.get("anchor"),
        "text": p["text"],
    }
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q tests/test_mcp_shaping.py`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/mcp/shaping.py tests/test_mcp_shaping.py
git commit -m "feat(mcp): response shaping helpers"
```

---

### Task 4: Research tools (`search`, `passage_context`, `get_passages`, `get_document`, `list_sources`, `facets`)

**Files:**
- Create: `engine/mcp/tools.py`
- Modify: `engine/config/source_groups.py` (add `annotate_counts`, moved from `engine/api/app.py::_annotate`)
- Modify: `engine/api/app.py:69-82` (delete `_annotate`, import `annotate_counts`, use it in `/api/sources` and `/api/facets`)
- Test: `tests/test_mcp_tools_db.py` (DB, skip without URL), `tests/test_mcp_tools_unit.py`

**Interfaces:**
- Consumes: `shaping.compact_search_result`, `shaping.compact_passage`, `shaping.truncate_text` (Task 3); `search_documents`, `get_document`, `facet_counts`, `SearchError` from `engine.search.queries`; `get_passages` from `engine.search.passage_search`; `catalog`, `resolve_scope` from `engine.config.source_groups`; `SOURCE_REGISTRY`, `get_config` from `engine.config.sources`; `to_markdown` from `engine.processors.renderer`; `Document` from `engine.database.models`.
- Produces, all `async`, all taking `session: AsyncSession` first, all returning `dict`, all raising `ToolInputError(msg)` (a plain `ValueError` subclass defined in `tools.py`) for user-facing errors — `server.py` (Task 6) converts it to `ToolError`:
  - `search(session, *, q, mode="keyword", scope=None, date_from=None, date_to=None, sort="relevance", section_kind=None, page=1, page_size=10) -> dict`
  - `passage_context(session, *, passage_id, before=2, after=2) -> dict`
  - `get_passages_tool(session, *, doc_id, from_ordinal=0, count=20, section_kind=None, layer=None) -> dict`
  - `get_document_tool(session, *, doc_id, max_chars=0) -> dict`
  - `list_sources(session) -> dict`
  - `facets(session, *, q="", mode="keyword", date_from=None, date_to=None) -> dict`
  - constants `PAGE_SIZE_MAX = 25`, `CONTEXT_MAX = 10`, `PASSAGES_COUNT_MAX = 50`, `DOC_TEXT_MAX_CHARS = 40_000`.
  - `def annotate_counts(node: dict, by_source: dict, by_source_vt: dict) -> dict` in `source_groups.py` (exact body of today's `_annotate`).

- [ ] **Step 1: Move `_annotate` to `source_groups.annotate_counts`**

In `engine/config/source_groups.py`, append:

```python
def annotate_counts(node: dict, by_source: dict, by_source_vt: dict) -> dict:
    """Copy a scope-tree node with a document count attached.

    ``by_source`` maps short_name → count; ``by_source_vt`` maps
    (short_name, verdict_type) → count. Used by /api/sources, /api/facets and
    the MCP tools ``list_sources``/``facets``.
    """
    out = {"key": node["key"], "label": node["label"]}
    if node.get("verdict_types"):
        out["count"] = sum(
            by_source_vt.get((s, vt), 0)
            for s in node["sources"] for vt in node["verdict_types"]
        )
    elif "children" in node:
        out["children"] = [annotate_counts(c, by_source, by_source_vt) for c in node["children"]]
        out["count"] = sum(c["count"] for c in out["children"])
    else:  # plain single-source leaf
        out["count"] = sum(by_source.get(s, 0) for s in node["sources"])
    return out
```

In `engine/api/app.py`: delete `_annotate` (lines 69–82), change the import to `from engine.config.source_groups import annotate_counts, catalog`, and replace both `_annotate(cat, by_source, by_source_vt)` calls with `annotate_counts(cat, by_source, by_source_vt)`.

Run: `set -a; . ./.env; set +a; uv run pytest -q tests/test_api_passages.py tests/test_search_queries.py` → PASS (no behaviour change).

- [ ] **Step 2: Write the failing unit tests (no DB)**

```python
# tests/test_mcp_tools_unit.py
"""Pure-logic checks of the MCP research tools with a stubbed core."""
import datetime as dt
import uuid

import pytest

import engine.mcp.tools as tools
from engine.mcp.tools import ToolInputError
from engine.search.queries import SearchResults


class _Sess:  # never touched by the stubbed calls
    pass


async def test_search_caps_page_size_and_compacts(monkeypatch):
    seen = {}
    async def fake_search(session, **kw):
        seen.update(kw)
        return SearchResults(total=1, page=1, page_size=kw["page_size"], results=[
            {"id": uuid.UUID(int=5), "urlausn": "U", "source": "s", "court": "c", "case_number": "1/1",
             "document_date": dt.date(2020, 1, 1), "verdict_type": "Dómur", "keywords": ["a"] * 9,
             "snippet": "sn", "passage_id": uuid.UUID(int=6), "anchor": "A", "section_kind": "annad",
             "layer": "body", "match_count": 1, "match_tier": 0, "plaintiffs": [], "defendants": [],
             "has_appeal_links": False}], strict_total=1, relaxed=False)
    async def fake_scope(session, scope): return None
    monkeypatch.setattr(tools, "search_documents", fake_search)
    monkeypatch.setattr(tools, "resolve_scope", fake_scope)
    out = await tools.search(_Sess(), q="x", page_size=999)
    assert seen["page_size"] == tools.PAGE_SIZE_MAX == 25
    assert out["total"] == 1 and out["hint"] is None
    assert out["results"][0]["doc_id"] == str(uuid.UUID(int=5))
    assert len(out["results"][0]["keywords"]) == 5
    assert "plaintiffs" not in out["results"][0]


async def test_search_hint_only_when_scope_resolves_empty(monkeypatch):
    class _Empty:
        def is_empty(self): return True
    class _NonEmpty:
        def is_empty(self): return False
    async def fake_search(session, **kw):
        return SearchResults(total=0, page=1, page_size=10, results=[])
    monkeypatch.setattr(tools, "search_documents", fake_search)

    async def empty(session, scope): return _Empty()
    monkeypatch.setattr(tools, "resolve_scope", empty)
    out = await tools.search(_Sess(), q="x", scope=["typo"])
    assert out["total"] == 0 and "Óþekkt scope" in out["hint"] and "typo" in out["hint"]

    async def nonempty(session, scope): return _NonEmpty()
    monkeypatch.setattr(tools, "resolve_scope", nonempty)
    out = await tools.search(_Sess(), q="x", scope=["domstolar", "typo"])
    assert out["total"] == 0 and out["hint"] is None


async def test_search_relaxed_hint(monkeypatch):
    async def fake_search(session, **kw):
        return SearchResults(total=3, page=1, page_size=10, results=[], strict_total=1, relaxed=True)
    async def fake_scope(session, scope): return None
    monkeypatch.setattr(tools, "search_documents", fake_search)
    monkeypatch.setattr(tools, "resolve_scope", fake_scope)
    out = await tools.search(_Sess(), q="x y")
    assert out["relaxed"] is True and "match_tier" in out["hint"]


async def test_search_rejects_bad_date_and_wraps_search_error(monkeypatch):
    with pytest.raises(ToolInputError):
        await tools.search(_Sess(), q="x", date_from="ekki-dagsetning")
    from engine.search.queries import SearchError
    async def boom(session, **kw): raise SearchError("Unknown section_kind 'x'")
    async def fake_scope(session, scope): return None
    monkeypatch.setattr(tools, "search_documents", boom)
    monkeypatch.setattr(tools, "resolve_scope", fake_scope)
    with pytest.raises(ToolInputError) as ei:
        await tools.search(_Sess(), q="x", section_kind=["x"])
    assert "section_kind" in str(ei.value)


async def test_passage_context_and_get_passages_clamp_args():
    with pytest.raises(ToolInputError):
        await tools.passage_context(_Sess(), passage_id="not-a-uuid")
    with pytest.raises(ToolInputError):
        await tools.get_passages_tool(_Sess(), doc_id="not-a-uuid")
    assert tools.CONTEXT_MAX == 10 and tools.PASSAGES_COUNT_MAX == 50 and tools.DOC_TEXT_MAX_CHARS == 40_000
```

- [ ] **Step 3: Write the failing DB tests**

```python
# tests/test_mcp_tools_db.py
"""MCP research tools against the live corpus. Skipped without a DB URL.
Uses DATABASE_URL_READONLY when set (Task 7 onward), else DATABASE_URL, so
Tasks 4-6 can run before the read-only role exists."""
import os

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import engine.mcp.tools as tools

_URL = os.environ.get("DATABASE_URL_READONLY") or os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not _URL, reason="needs DATABASE_URL_READONLY or DATABASE_URL")


async def _session():
    eng = create_async_engine(_URL, connect_args={"server_settings": {"plan_cache_mode": "force_custom_plan"}})
    return eng, AsyncSession(eng, expire_on_commit=False)


async def test_search_then_context_then_document():
    eng, s = await _session()
    try:
        out = await tools.search(s, q="gæsluvarðhald", scope=["domstolar"], page_size=5)
        assert out["total"] > 0 and out["strict_total"] > 0 and len(out["results"]) == 5
        first = out["results"][0]
        assert first["passage_id"] and first["urlausn"] and first["anchor"]

        ctx = await tools.passage_context(s, passage_id=first["passage_id"], before=1, after=1)
        ords = [p["ordinal"] for p in ctx["passages"]]
        assert ctx["focus_ordinal"] in ords and len(ords) <= 3
        assert ctx["urlausn"] == first["urlausn"]

        doc = await tools.get_document_tool(s, doc_id=first["doc_id"], max_chars=2000)
        assert doc["urlausn"] == first["urlausn"]
        assert doc["text"] is not None and len(doc["text"]) <= 2000
        assert doc["text_truncated"] is True             # gæsluvarðhald rulings are longer than 2000 chars
        assert sum(o["passages"] for o in doc["outline"]) == doc["total_passages"] > 0
        assert "raw_api_data" not in doc and "body_text" not in doc

        win = await tools.get_passages_tool(s, doc_id=first["doc_id"], from_ordinal=0, count=3)
        assert [p["ordinal"] for p in win["passages"]] == [0, 1, 2]
        assert win["next_from_ordinal"] == 3
    finally:
        await s.close(); await eng.dispose()


async def test_get_document_no_text_by_default_and_short_text_not_truncated():
    eng, s = await _session()
    try:
        out = await tools.search(s, q="gæsluvarðhald", scope=["domstolar"], page_size=1)
        d = await tools.get_document_tool(s, doc_id=out["results"][0]["doc_id"])
        assert d["text"] is None and d["text_truncated"] is False
        d2 = await tools.get_document_tool(s, doc_id=out["results"][0]["doc_id"], max_chars=40_000)
        assert d2["text"] is not None
        if not d2["text_truncated"]:
            assert len(d2["text"]) <= 40_000
    finally:
        await s.close(); await eng.dispose()


async def test_passage_context_summary_layer():
    eng, s = await _session()
    try:
        out = await tools.search(s, q="gæsluvarðhald", scope=["domstolar"], section_kind=["reifun"], page_size=1)
        if out["total"] == 0:
            pytest.skip("no reifun hit")
        pid = out["results"][0]["passage_id"]
        ctx = await tools.passage_context(s, passage_id=pid, before=2, after=2)
        focus = [p for p in ctx["passages"] if p["passage_id"] == pid]
        assert len(focus) == 1 and focus[0]["ordinal"] == ctx["focus_ordinal"]
    finally:
        await s.close(); await eng.dispose()


async def test_list_sources_and_facets():
    eng, s = await _session()
    try:
        src = await tools.list_sources(s)
        keys = {n["key"] for n in src["catalog"]}
        assert {"domstolar", "stjornsysla"} <= keys
        assert any(x["short_name"] == "haestirettur" for x in src["sources"])
        f = await tools.facets(s, q="gæsluvarðhald")
        assert f["by_group"]["domstolar"] > 0 and f["by_source"]["haestirettur"] > 0
    finally:
        await s.close(); await eng.dispose()


async def test_unknown_document_and_passage():
    eng, s = await _session()
    try:
        import uuid
        with pytest.raises(tools.ToolInputError):
            await tools.get_document_tool(s, doc_id=str(uuid.UUID(int=0)))
        with pytest.raises(tools.ToolInputError):
            await tools.passage_context(s, passage_id=str(uuid.UUID(int=0)))
    finally:
        await s.close(); await eng.dispose()
```

- [ ] **Step 4: Run both files to verify failure**

Run: `set -a; . ./.env; set +a; uv run pytest -q tests/test_mcp_tools_unit.py tests/test_mcp_tools_db.py`
Expected: FAIL, `ModuleNotFoundError: No module named 'engine.mcp.tools'`.

- [ ] **Step 5: Implement `engine/mcp/tools.py`**

```python
"""MCP tool implementations as plain async functions (spec §4).

Every function takes an AsyncSession first, returns a JSON-safe dict, and
raises ToolInputError for anything the LLM should see as a tool error.
server.py wraps these; nothing here imports the mcp SDK, so the logic is
testable without a protocol layer.
"""
from __future__ import annotations

import datetime as _dt
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from engine.config.source_groups import annotate_counts, catalog, resolve_scope
from engine.config.sources import SOURCE_REGISTRY, get_config
from engine.database.models import Document
from engine.mcp.shaping import compact_passage, compact_search_result, truncate_text
from engine.processors.renderer import to_markdown
from engine.search.passage_search import get_passages
from engine.search.queries import SearchError, facet_counts, get_document, search_documents

PAGE_SIZE_MAX = 25
PAGE_SIZE_DEFAULT = 10
CONTEXT_MAX = 10
PASSAGES_COUNT_MAX = 50
DOC_TEXT_MAX_CHARS = 40_000

RELAXED_HINT = ("Færri en 10 skjöl innihalda öll orðin; niðurstöður með match_tier 1–2 "
                "innihalda aðeins hluta þeirra.")


class ToolInputError(ValueError):
    """User-facing error; server.py turns it into an MCP ToolError."""


def _uuid(value: Any, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise ToolInputError(f"Ógilt {what}: {value!r}")


def _date(value: str | None, name: str) -> _dt.date | None:
    if value in (None, ""):
        return None
    try:
        return _dt.date.fromisoformat(str(value))
    except ValueError:
        raise ToolInputError(f"{name} verður að vera ISO-dagsetning (ÁÁÁÁ-MM-DD), fékk {value!r}")


def _clamp(v: int | None, lo: int, hi: int, default: int) -> int:
    if v is None:
        return default
    try:
        v = int(v)
    except (TypeError, ValueError):
        raise ToolInputError(f"Væntanlegt heiltala á bilinu {lo}–{hi}, fékk {v!r}")
    return max(lo, min(v, hi))


# ── search ─────────────────────────────────────────────────────────────────────

async def search(session: AsyncSession, *, q: str, mode: str = "keyword",
                 scope: list[str] | None = None, date_from: str | None = None,
                 date_to: str | None = None, sort: str = "relevance",
                 section_kind: list[str] | None = None, page: int = 1,
                 page_size: int = PAGE_SIZE_DEFAULT) -> dict:
    page = _clamp(page, 1, 10_000, 1)
    page_size = _clamp(page_size, 1, PAGE_SIZE_MAX, PAGE_SIZE_DEFAULT)
    d_from, d_to = _date(date_from, "date_from"), _date(date_to, "date_to")

    hint: str | None = None
    if scope:
        sf = await resolve_scope(session, scope)
        if sf is not None and sf.is_empty():
            hint = f"Óþekkt scope: {', '.join(scope)}. Kallaðu á list_sources til að sjá gild heiti."
    try:
        res = await search_documents(session, q=q or "", mode=mode, scope=scope,
                                     date_from=d_from, date_to=d_to, sort=sort,
                                     page=page, page_size=page_size, section_kind=section_kind)
    except SearchError as exc:
        raise ToolInputError(str(exc))
    if hint is None and res.relaxed:
        hint = RELAXED_HINT
    return {
        "total": res.total, "strict_total": res.strict_total, "relaxed": res.relaxed,
        "page": res.page, "page_size": res.page_size,
        "results": [compact_search_result(r) for r in res.results],
        "hint": hint,
    }


# ── passages ───────────────────────────────────────────────────────────────────

async def _passage_window(session: AsyncSession, doc_id: uuid.UUID, *, from_ordinal: int,
                          to_ordinal: int, section_kinds: list[str] | None, layer: str | None) -> dict:
    try:
        win = await get_passages(session, doc_id, from_ordinal=from_ordinal, to_ordinal=to_ordinal,
                                 section_kinds=section_kinds, layer=layer)
    except SearchError as exc:
        raise ToolInputError(str(exc))
    if win is None:
        raise ToolInputError("Skjal fannst ekki.")
    return win


async def passage_context(session: AsyncSession, *, passage_id: str, before: int = 2,
                          after: int = 2) -> dict:
    pid = _uuid(passage_id, "passage_id")
    before = _clamp(before, 0, CONTEXT_MAX, 2)
    after = _clamp(after, 0, CONTEXT_MAX, 2)
    row = (await session.execute(text(
        "SELECT document_id, ordinal FROM passages WHERE id = :pid"), {"pid": pid})).first()
    if row is None:
        raise ToolInputError("Efnisgrein fannst ekki.")
    doc_id, ordinal = row[0], row[1]
    win = await _passage_window(session, doc_id, from_ordinal=max(0, ordinal - before),
                                to_ordinal=ordinal + after, section_kinds=None, layer=None)
    return {
        "doc_id": str(doc_id), "urlausn": win["urlausn"], "focus_ordinal": ordinal,
        "total_passages": win["total"],
        "passages": [compact_passage(p) for p in win["passages"]],
    }


async def get_passages_tool(session: AsyncSession, *, doc_id: str, from_ordinal: int = 0,
                            count: int = 20, section_kind: list[str] | None = None,
                            layer: str | None = None) -> dict:
    did = _uuid(doc_id, "doc_id")
    from_ordinal = _clamp(from_ordinal, 0, 1_000_000, 0)
    count = _clamp(count, 1, PASSAGES_COUNT_MAX, 20)
    win = await _passage_window(session, did, from_ordinal=from_ordinal,
                                to_ordinal=from_ordinal + count - 1,
                                section_kinds=section_kind, layer=layer)
    passages = [compact_passage(p) for p in win["passages"]]
    last = passages[-1]["ordinal"] if passages else None
    next_from = last + 1 if last is not None and last + 1 < win["total"] else None
    return {"doc_id": str(did), "urlausn": win["urlausn"], "total_passages": win["total"],
            "passages": passages, "next_from_ordinal": next_from}


# ── document ───────────────────────────────────────────────────────────────────

_OUTLINE_SQL = text("""
    SELECT layer, section_kind, min(ordinal) AS from_ordinal, max(ordinal) AS to_ordinal,
           count(*) AS passages
    FROM passages WHERE document_id = :id
    GROUP BY layer, section_kind ORDER BY min(ordinal)
""")


async def get_document_tool(session: AsyncSession, *, doc_id: str, max_chars: int = 0) -> dict:
    did = _uuid(doc_id, "doc_id")
    max_chars = _clamp(max_chars, 0, DOC_TEXT_MAX_CHARS, 0)
    try:
        doc = await get_document(session, did)
    except SearchError as exc:
        raise ToolInputError(str(exc))
    if doc is None:
        raise ToolInputError("Skjal fannst ekki.")
    for k in ("raw_api_data", "body_text", "lower_body_text", "markdown"):
        doc.pop(k, None)
    rows = (await session.execute(_OUTLINE_SQL, {"id": did})).mappings().all()
    doc["outline"] = [dict(r) for r in rows]
    doc["total_passages"] = sum(r["passages"] for r in rows)
    doc["text"], doc["text_truncated"] = None, False
    if max_chars > 0:
        orm = await session.get(Document, did)
        md = None
        if orm is not None:
            try:
                md = to_markdown(orm, get_config(doc["source"]))
            except Exception:
                md = None
        doc["text"], doc["text_truncated"] = truncate_text(md, max_chars)
    return _jsonable(doc)


def _jsonable(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.isoformat()
    return v


# ── sources / facets ───────────────────────────────────────────────────────────

_COUNTS_SQL = text("""
    SELECT s.short_name, d.verdict_type, count(d.id) AS n
    FROM sources s LEFT JOIN documents d ON d.source_id = s.id
    GROUP BY s.short_name, d.verdict_type
""")


async def list_sources(session: AsyncSession) -> dict:
    rows = (await session.execute(_COUNTS_SQL)).mappings().all()
    by_source: dict[str, int] = {}
    by_source_vt: dict[tuple[str, str], int] = {}
    for r in rows:
        by_source[r["short_name"]] = by_source.get(r["short_name"], 0) + (r["n"] or 0)
        if r["verdict_type"] is not None:
            by_source_vt[(r["short_name"], r["verdict_type"])] = r["n"] or 0
    tree = [annotate_counts(cat, by_source, by_source_vt) for cat in catalog()]
    flat = sorted(
        ({"short_name": sn,
          "display_name": (cfg := SOURCE_REGISTRY.get(sn)) and cfg.display_name or sn,
          "abbreviation": cfg.abbreviation if cfg else None,
          "count": n} for sn, n in by_source.items()),
        key=lambda s: -s["count"])
    return {"catalog": tree, "sources": flat, "total": sum(c["count"] for c in tree)}


def _flatten_groups(node: dict, out: dict[str, int]) -> None:
    out[node["key"]] = node["count"]
    for c in node.get("children", []):
        _flatten_groups(c, out)


async def facets(session: AsyncSession, *, q: str = "", mode: str = "keyword",
                 date_from: str | None = None, date_to: str | None = None) -> dict:
    try:
        by_source, by_source_vt = await facet_counts(
            session, q=q or "", mode=mode, date_from=_date(date_from, "date_from"),
            date_to=_date(date_to, "date_to"))
    except SearchError as exc:
        raise ToolInputError(str(exc))
    by_group: dict[str, int] = {}
    for cat in catalog():
        _flatten_groups(annotate_counts(cat, by_source, by_source_vt), by_group)
    return {"by_source": dict(by_source), "by_group": by_group}
```

- [ ] **Step 6: Run the tests**

Run: `set -a; . ./.env; set +a; uv run pytest -q tests/test_mcp_tools_unit.py tests/test_mcp_tools_db.py tests/test_api_passages.py`
Expected: all PASS (DB tests run because `DATABASE_URL` is set in `.env`).

- [ ] **Step 7: Commit**

```bash
git add engine/mcp/tools.py engine/config/source_groups.py engine/api/app.py tests/test_mcp_tools_unit.py tests/test_mcp_tools_db.py
git commit -m "feat(mcp): research tools (search, passage_context, get_passages, get_document, list_sources, facets)"
```

---

### Task 5: `describe_schema` and `sql_query`

**Files:**
- Modify: `engine/mcp/tools.py` (append)
- Test: `tests/test_mcp_sql_db.py` (DB, skip without URL), append to `tests/test_mcp_tools_unit.py`

**Interfaces:**
- Consumes: `sqlguard.validate_sql`, `shaping.cell_value`.
- Produces:
  - `async def describe_schema(session, *, table: str | None = None) -> dict`
  - `async def sql_query(session, *, sql: str, max_rows: int = 200) -> dict`
  - constants `SQL_MAX_ROWS = 1000`, `SQL_DEFAULT_ROWS = 200`, `SQL_TIMEOUT = "15s"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_mcp_tools_unit.py`:

```python
async def test_sql_query_rejects_before_touching_db():
    class _Boom:
        def begin(self): raise AssertionError("must not reach the DB")
    with pytest.raises(ToolInputError) as ei:
        await tools.sql_query(_Boom(), sql="DELETE FROM documents")
    assert "SELECT" in str(ei.value)
    with pytest.raises(ToolInputError):
        await tools.sql_query(_Boom(), sql="SELECT pg_sleep(20)")
```

New `tests/test_mcp_sql_db.py`:

```python
"""sql_query / describe_schema against the live DB. Skipped without a URL.
Uses DATABASE_URL_READONLY when set, else DATABASE_URL."""
import os

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import engine.mcp.tools as tools

_URL = os.environ.get("DATABASE_URL_READONLY") or os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not _URL, reason="needs DATABASE_URL_READONLY or DATABASE_URL")


async def _session():
    eng = create_async_engine(_URL)
    return eng, AsyncSession(eng, expire_on_commit=False)


async def test_sql_query_count():
    eng, s = await _session()
    try:
        out = await tools.sql_query(s, sql="SELECT count(*) AS n FROM documents")
        assert out["columns"] == ["n"] and out["row_count"] == 1 and out["rows"][0][0] > 0
        assert out["truncated_rows"] is False and out["elapsed_ms"] >= 0
    finally:
        await s.close(); await eng.dispose()


async def test_sql_query_row_cap_and_cell_truncation():
    eng, s = await _session()
    try:
        out = await tools.sql_query(s, sql="SELECT id, summary FROM documents WHERE summary IS NOT NULL ORDER BY length(summary) DESC", max_rows=3)
        assert out["row_count"] == 3 and out["truncated_rows"] is True
        assert all(len(r[1]) <= 501 for r in out["rows"])
        assert out["truncated_cells"] >= 1
        assert isinstance(out["rows"][0][0], str)          # UUID → str
    finally:
        await s.close(); await eng.dispose()


async def test_sql_query_serializes_special_types():
    eng, s = await _session()
    try:
        out = await tools.sql_query(s, sql="""
            SELECT id, document_date, keywords, created_at, decode('00ff', 'hex') AS b, 1.5::numeric AS d
            FROM documents WHERE keywords IS NOT NULL LIMIT 1""")
        import json; json.dumps(out)                        # must be JSON-safe end to end
        row = dict(zip(out["columns"], out["rows"][0]))
        assert row["b"] == "<bytes 2>" and row["d"] == "1.5"
    finally:
        await s.close(); await eng.dispose()


async def test_sql_query_timeout_message():
    eng, s = await _session()
    try:
        tools.SQL_TIMEOUT = "200ms"
        try:
            with pytest.raises(tools.ToolInputError) as ei:
                # cross join big enough to exceed 200 ms but cheap to cancel
                await tools.sql_query(s, sql="SELECT count(*) FROM passages a, passages b WHERE a.ordinal = b.ordinal")
            assert "tímamörk" in str(ei.value)
        finally:
            tools.SQL_TIMEOUT = "15s"
        # connection still usable afterwards
        out = await tools.sql_query(s, sql="SELECT 1 AS one")
        assert out["rows"] == [[1]]
    finally:
        await s.close(); await eng.dispose()


async def test_sql_query_sql_error_is_tool_error():
    eng, s = await _session()
    try:
        with pytest.raises(tools.ToolInputError) as ei:
            await tools.sql_query(s, sql="SELECT * FROM table_that_does_not_exist")
        assert "does not exist" in str(ei.value)
    finally:
        await s.close(); await eng.dispose()


async def test_describe_schema():
    eng, s = await _session()
    try:
        all_ = await tools.describe_schema(s)
        names = {t["name"] for t in all_["tables"]}
        assert {"documents", "passages", "sources"} <= names
        docs = next(t for t in all_["tables"] if t["name"] == "documents")
        assert docs["approx_rows"] > 0 and docs["description"]
        one = await tools.describe_schema(s, table="passages")
        cols = {c["name"]: c for c in one["columns"]}
        assert cols["fts_is"]["type"] == "tsvector" and cols["ordinal"]["nullable"] is False
        assert any("ix_passage_doc" in ix["name"] for ix in one["indexes"])
        d = await tools.describe_schema(s, table="documents")
        assert "raw_api_data" in d["notes"]
        with pytest.raises(tools.ToolInputError):
            await tools.describe_schema(s, table="nope")
    finally:
        await s.close(); await eng.dispose()
```

- [ ] **Step 2: Run to verify failure**

Run: `set -a; . ./.env; set +a; uv run pytest -q tests/test_mcp_sql_db.py tests/test_mcp_tools_unit.py -k "sql or schema"`
Expected: FAIL with `AttributeError: module 'engine.mcp.tools' has no attribute 'sql_query'`.

- [ ] **Step 3: Implement (append to `engine/mcp/tools.py`)**

```python
# ── schema / sql ───────────────────────────────────────────────────────────────

import time as _time

from engine.mcp.shaping import cell_value
from engine.mcp.sqlguard import SqlRejected, validate_sql

SQL_MAX_ROWS = 1000
SQL_DEFAULT_ROWS = 200
SQL_TIMEOUT = "15s"

_TABLE_DESCRIPTIONS = {
    "documents": "Eitt skjal á röð (dómur, úrskurður, ákvörðun, lagakafli). NORM-dálkar + raw_api_data (RAW, stórt JSONB).",
    "passages": "Efnisgreinar skjala, leitareining orðaleitar. (document_id, ordinal) einkvæmt; fts_is er GIN-vísað.",
    "sources": "Heimildir (short_name, display_name). documents.source_id → sources.id.",
    "document_links": "Tengingar milli skjala (t.d. málskotsbeiðni → Landsréttardómur), relation/confidence/method.",
    "alembic_version": "Skemaútgáfa (alembic).",
}
_TABLE_NOTES = {
    "documents": "raw_api_data er stórt JSONB (öll API-svörin); veldu einstaka lykla (raw_api_data->>'x') en aldrei dálkinn í heild. body_text/lower_body_text eru fullir textar; notaðu passages fyrir lesanleg brot.",
    "passages": "text getur verið langt; sía fyrst á document_id eða fts_is @@ to_tsquery('simple', …).",
}


async def describe_schema(session: AsyncSession, *, table: str | None = None) -> dict:
    if table is None:
        rows = (await session.execute(text("""
            SELECT c.relname AS name, c.reltuples::bigint AS approx_rows
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY c.relname"""))).mappings().all()
        return {"tables": [{"name": r["name"], "approx_rows": max(0, int(r["approx_rows"])),
                            "description": _TABLE_DESCRIPTIONS.get(r["name"], "")} for r in rows]}
    cols = (await session.execute(text("""
        SELECT column_name AS name, udt_name AS type, is_nullable = 'YES' AS nullable
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = :t ORDER BY ordinal_position"""), {"t": table})).mappings().all()
    if not cols:
        raise ToolInputError(f"Tafla fannst ekki: {table!r}. Kallaðu á describe_schema án table til að sjá lista.")
    idx = (await session.execute(text(
        "SELECT indexname AS name, indexdef AS definition FROM pg_indexes WHERE schemaname = 'public' AND tablename = :t ORDER BY indexname"),
        {"t": table})).mappings().all()
    return {"table": table, "description": _TABLE_DESCRIPTIONS.get(table, ""),
            "columns": [dict(c) for c in cols], "indexes": [dict(i) for i in idx],
            "notes": _TABLE_NOTES.get(table, "")}


async def sql_query(session: AsyncSession, *, sql: str, max_rows: int = SQL_DEFAULT_ROWS) -> dict:
    try:
        clean = validate_sql(sql)
    except SqlRejected as exc:
        raise ToolInputError(str(exc))
    max_rows = _clamp(max_rows, 1, SQL_MAX_ROWS, SQL_DEFAULT_ROWS)
    t0 = _time.perf_counter()
    try:
        async with session.begin():
            await session.execute(text("SET TRANSACTION READ ONLY"))
            await session.execute(text(f"SET LOCAL statement_timeout = '{SQL_TIMEOUT}'"))
            result = await session.execute(text(clean))
            columns = list(result.keys())
            raw = result.fetchmany(max_rows + 1)
    except Exception as exc:  # asyncpg errors arrive wrapped in sqlalchemy DBAPIError
        msg = str(getattr(exc, "orig", exc))
        low = msg.lower()
        if "canceling statement due to statement timeout" in low or "querycancelederror" in low:
            raise ToolInputError(f"Fyrirspurn féll á {SQL_TIMEOUT} tímamörkum.")
        if "permission denied" in low or "read-only transaction" in low or "insufficientprivilege" in low:
            raise ToolInputError("Lesaðgangshlutverkið má ekki gera þetta.")
        raise ToolInputError(f"Villa í fyrirspurn: {msg.splitlines()[0] if msg else exc.__class__.__name__}")
    elapsed = int((_time.perf_counter() - t0) * 1000)
    truncated_rows = len(raw) > max_rows
    raw = raw[:max_rows]
    rows, truncated_cells = [], 0
    for r in raw:
        out_row = []
        for v in r:
            val, cut = cell_value(v)
            truncated_cells += int(cut)
            out_row.append(val)
        rows.append(out_row)
    return {"columns": columns, "rows": rows, "row_count": len(rows),
            "truncated_rows": truncated_rows, "truncated_cells": truncated_cells, "elapsed_ms": elapsed}
```

Move the three `import`s added above (`time`, `cell_value`, `sqlguard`) up to the module's import block — keep all imports at the top of `tools.py`.

- [ ] **Step 4: Run the tests**

Run: `set -a; . ./.env; set +a; uv run pytest -q tests/test_mcp_sql_db.py tests/test_mcp_tools_unit.py tests/test_mcp_sqlguard.py`
Expected: all PASS. If `test_sql_query_timeout_message` does not time out at 200 ms, raise the join to `passages a, passages b, passages c LIMIT 1` — the test must produce a real `statement_timeout` cancel, and the connection must survive it (the trailing `SELECT 1` proves that).

- [ ] **Step 5: Commit**

```bash
git add engine/mcp/tools.py tests/test_mcp_sql_db.py tests/test_mcp_tools_unit.py
git commit -m "feat(mcp): describe_schema and read-only sql_query with timeout and cell truncation"
```

---

### Task 6: `server.py`, `__main__.py`, stdout guard, stdio smoke test

**Files:**
- Create: `engine/mcp/server.py`
- Create: `engine/mcp/__main__.py`
- Test: `tests/test_mcp_server.py`, `tests/test_mcp_stdio.py`

**Interfaces:**
- Consumes: every function in `tools.py`; `init_db(url=..., create_tables=False)` (Task 1); `engine.database.connection.AsyncSessionLocal`.
- Produces: `engine.mcp.server.build_server() -> MCPServer` (no DB access at build time); `engine.mcp.server.main(argv: list[str] | None = None) -> int` (loads `.env` from the repo root via `dotenv.load_dotenv(REPO_ROOT / ".env", override=False)`, requires `DATABASE_URL_READONLY`, runs stdio); `INSTRUCTIONS: str`; `TOOL_NAMES: tuple[str, ...]` with the eight names in spec order.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_mcp_server.py
import asyncio
import subprocess
import sys

import pytest

from engine.mcp import server as srv

EXPECTED = ("search", "passage_context", "get_passages", "get_document",
            "list_sources", "facets", "describe_schema", "sql_query")


def test_import_writes_nothing_to_stdout():
    """Stdout is the MCP channel — any stray print at import time corrupts the
    protocol. Run in a fresh interpreter so module-level side effects count."""
    p = subprocess.run([sys.executable, "-c", "import engine.mcp.server"], capture_output=True, text=True,
                       cwd=str(srv.REPO_ROOT))
    assert p.returncode == 0, p.stderr
    assert p.stdout == ""


async def test_list_tools_names_and_annotations():
    s = srv.build_server()
    tools = await s.list_tools()
    assert tuple(t.name for t in tools) == EXPECTED == srv.TOOL_NAMES
    assert all(t.annotations is not None and t.annotations.read_only_hint is True for t in tools)
    assert all(t.description for t in tools)
    schema = {t.name: t.input_schema for t in tools}
    assert schema["search"]["required"] == ["q"]
    assert schema["sql_query"]["required"] == ["sql"]
    assert schema["list_sources"].get("required", []) == []
    assert srv.INSTRUCTIONS and "urlausn" in srv.INSTRUCTIONS


def test_main_requires_readonly_url(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL_READONLY", raising=False)
    monkeypatch.setattr(srv, "_load_env", lambda: None)
    assert srv.main([]) == 2
    out, err = capsys.readouterr()
    assert out == "" and "DATABASE_URL_READONLY" in err


async def test_tool_error_wrapping(monkeypatch):
    """ToolInputError from tools must surface as an MCP ToolError (is_error), never as a crash."""
    from mcp.server.mcpserver.exceptions import ToolError
    async def fake_search(session, **kw):
        from engine.mcp.tools import ToolInputError
        raise ToolInputError("prófvilla")
    monkeypatch.setattr(srv.tools, "search", fake_search)
    class _S:
        async def __aenter__(self): return object()
        async def __aexit__(self, *a): return False
    monkeypatch.setattr(srv, "_session_factory", lambda: _S())
    s = srv.build_server()
    with pytest.raises(ToolError) as ei:
        await s.call_tool("search", {"q": "x"})
    assert "prófvilla" in str(ei.value)
```

```python
# tests/test_mcp_stdio.py
"""End-to-end over stdio: spawn the server exactly as a client would."""
import os
import sys

import pytest

_URL = os.environ.get("DATABASE_URL_READONLY") or os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not _URL, reason="needs DATABASE_URL_READONLY or DATABASE_URL")


async def test_stdio_roundtrip():
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    env = dict(os.environ)
    env["DATABASE_URL_READONLY"] = _URL      # tests may run before the RO role exists
    params = StdioServerParameters(command=sys.executable, args=["-m", "engine.mcp"], env=env,
                                   cwd=os.getcwd())
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = [t.name for t in (await session.list_tools()).tools]
            assert len(names) == 8 and "search" in names and "sql_query" in names
            res = await session.call_tool("list_sources", {})
            assert res.is_error is False
            text = res.content[0].text
            assert "domstolar" in text
            bad = await session.call_tool("sql_query", {"sql": "DELETE FROM documents"})
            assert bad.is_error is True and "SELECT" in bad.content[0].text
```

- [ ] **Step 2: Run to verify failure**

Run: `set -a; . ./.env; set +a; uv run pytest -q tests/test_mcp_server.py tests/test_mcp_stdio.py`
Expected: FAIL, `ImportError: cannot import name 'server' from 'engine.mcp'`.

- [ ] **Step 3: Implement `engine/mcp/server.py`**

```python
"""MCPServer wiring for the Lausnir read-only server (spec §3, §6).

Build-time (build_server) touches no database, so tests can list tools
offline. main() loads .env, requires DATABASE_URL_READONLY, initialises the
engine with create_tables=False and serves stdio. Logging goes to stderr —
stdout is the protocol channel.
"""
from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

import engine.database.connection as _db
from engine.mcp import tools
from engine.mcp.tools import ToolInputError

REPO_ROOT = Path(__file__).resolve().parents[2]
log = logging.getLogger("lausnir.mcp")

TOOL_NAMES = ("search", "passage_context", "get_passages", "get_document",
              "list_sources", "facets", "describe_schema", "sql_query")

INSTRUCTIONS = (
    "Lausnir er safn íslenskra dóma, úrskurða og stjórnsýsluákvarðana. Byrjaðu á `search` "
    "(orðaleit með BÍN-lemmun; `scope` þrengir að dómstigi eða heimild, sjá `list_sources`). "
    "Hver niðurstaða vísar á bestu efnisgreinina (`passage_id`, `anchor`). Notaðu "
    "`passage_context` til að lesa í kringum treffið og `get_document` fyrir lýsigögn, aðila og "
    "reifun. Vitnaðu alltaf með `urlausn` og `anchor` (t.d. „Hrd. 123/2020, mgr. 14“). "
    "`relaxed: true` þýðir að færri en 10 skjöl innihéldu öll leitarorðin; `match_tier` 1–2 "
    "innihalda aðeins hluta þeirra. `sql_query` er fyrir tölfræði og gagnaathuganir sem "
    "leitarverkfærin svara ekki; það er read-only og skilar mest 1000 röðum.\n\n"
    "English: Lausnir is a corpus of Icelandic court rulings and administrative decisions. Start "
    "with `search` (lemmatised keyword search; `scope` narrows by court tier or source, see "
    "`list_sources`). Each hit points at its best passage (`passage_id`, `anchor`); use "
    "`passage_context` to read around it and `get_document` for metadata, parties and summary. "
    "Always cite with `urlausn` plus `anchor`. `relaxed: true` means fewer than 10 documents "
    "contained all query terms; `match_tier` 1–2 hits contain only some of them. `sql_query` is "
    "read-only SQL for statistics the search tools cannot answer (max 1000 rows)."
)

_RO = ToolAnnotations(read_only_hint=True)


def _session_factory():
    if _db.AsyncSessionLocal is None:
        raise ToolError("Gagnagrunnstenging er ekki tilbúin.")
    return _db.AsyncSessionLocal()


async def _run(fn, **kw):
    try:
        async with _session_factory() as session:
            return await fn(session, **kw)
    except ToolInputError as exc:
        raise ToolError(str(exc))
    except ToolError:
        raise
    except Exception as exc:  # DB down mid-run etc.: readable error, no traceback to the LLM
        log.exception("tool failure")
        raise ToolError(f"Gagnagrunnstenging brást: {exc.__class__.__name__}: {str(exc).splitlines()[0] if str(exc) else ''}")


@asynccontextmanager
async def _lifespan(_server: MCPServer) -> AsyncIterator[None]:
    url = os.environ["DATABASE_URL_READONLY"]
    await _db.init_db(url=url, create_tables=False)
    log.info("lausnir mcp: db ready")
    yield


def build_server() -> MCPServer:
    server = MCPServer("lausnir", instructions=INSTRUCTIONS, lifespan=_lifespan, log_level="WARNING")

    @server.tool(annotations=_RO, description=(
        "Leit í dómasafninu (orðaleit með lemmun, sjálfgefið mode='keyword'). Skilar allt að page_size "
        "(≤25) skjölum með bestu efnisgrein hvers (passage_id, anchor, snippet). scope: heiti úr "
        "list_sources (t.d. 'domstolar', 'haestirettur', 'landsrettur_domar') eða 'all'. section_kind: "
        "reifun, malsmedferd, malsatvik, malsastaedur, nidurstada, domsord, annad. Dagsetningar ISO."))
    async def search(q: str, mode: str = "keyword", scope: list[str] | None = None,
                     date_from: str | None = None, date_to: str | None = None, sort: str = "relevance",
                     section_kind: list[str] | None = None, page: int = 1, page_size: int = 10) -> dict:
        return await _run(tools.search, q=q, mode=mode, scope=scope, date_from=date_from, date_to=date_to,
                          sort=sort, section_kind=section_kind, page=page, page_size=page_size)

    @server.tool(annotations=_RO, description=(
        "Efnisgreinarnar í kringum eina efnisgrein (passage_id úr search). before/after 0–10."))
    async def passage_context(passage_id: str, before: int = 2, after: int = 2) -> dict:
        return await _run(tools.passage_context, passage_id=passage_id, before=before, after=after)

    @server.tool(annotations=_RO, description=(
        "Gluggi af efnisgreinum skjals frá from_ordinal, count ≤ 50. layer: summary|body|lower_body. "
        "next_from_ordinal segir hvar næsti gluggi byrjar."))
    async def get_passages(doc_id: str, from_ordinal: int = 0, count: int = 20,
                           section_kind: list[str] | None = None, layer: str | None = None) -> dict:
        return await _run(tools.get_passages_tool, doc_id=doc_id, from_ordinal=from_ordinal, count=count,
                          section_kind=section_kind, layer=layer)

    @server.tool(annotations=_RO, description=(
        "Lýsigögn skjals: urlausn, aðilar, reifun, lykilorð, áfrýjunartengingar, kaflayfirlit (outline). "
        "max_chars > 0 bætir við markdown-texta styttum að max_chars (≤ 40000); notaðu frekar "
        "passage_context/get_passages fyrir lestur."))
    async def get_document(doc_id: str, max_chars: int = 0) -> dict:
        return await _run(tools.get_document_tool, doc_id=doc_id, max_chars=max_chars)

    @server.tool(annotations=_RO, description=(
        "Heimildatréð með fjölda skjala. Hvert key og hvert short_name er gilt scope í search."))
    async def list_sources() -> dict:
        return await _run(tools.list_sources)

    @server.tool(annotations=_RO, description=(
        "Fjöldi strangra treffa eftir heimild (by_source) og heimildahópi (by_group) fyrir fyrirspurn."))
    async def facets(q: str = "", mode: str = "keyword", date_from: str | None = None,
                     date_to: str | None = None) -> dict:
        return await _run(tools.facets, q=q, mode=mode, date_from=date_from, date_to=date_to)

    @server.tool(annotations=_RO, description=(
        "Töflur gagnagrunnsins (án table) eða dálkar, vísar og athugasemdir einnar töflu (með table)."))
    async def describe_schema(table: str | None = None) -> dict:
        return await _run(tools.describe_schema, table=table)

    @server.tool(annotations=_RO, description=(
        "Read-only SQL (SELECT/WITH/EXPLAIN) gegn lesaðgangshlutverki, 15 s tímamörk, max_rows ≤ 1000, "
        "reitir styttir í 500 stafi. Notaðu describe_schema fyrst."))
    async def sql_query(sql: str, max_rows: int = 200) -> dict:
        return await _run(tools.sql_query, sql=sql, max_rows=max_rows)

    return server


def _load_env() -> None:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env", override=False)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    _load_env()
    if not os.environ.get("DATABASE_URL_READONLY"):
        print("lausnir mcp: DATABASE_URL_READONLY vantar í umhverfi/.env — þjónninn notar aðeins "
              "lesaðgangshlutverkið lausnir_ro (sjá docs/wiki/10-mcp.md).", file=sys.stderr)
        return 2
    build_server().run("stdio")
    return 0
```

`engine/mcp/__main__.py`:

```python
import sys

from engine.mcp.server import main

sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run the tests**

Run: `set -a; . ./.env; set +a; uv run pytest -q tests/test_mcp_server.py tests/test_mcp_stdio.py`
Expected: all PASS. The stdio test sets `DATABASE_URL_READONLY` to whichever URL is available, so it runs before Task 7.

If `test_import_writes_nothing_to_stdout` fails, find the culprit with `python -X importtime`-style bisecting of `engine` imports (`python -c "import engine.search.queries"` etc.) and fix the offending `print` at its source; do not redirect stdout as a workaround.

- [ ] **Step 5: Run the whole suite and commit**

Run: `set -a; . ./.env; set +a; uv run pytest -q` → green.

```bash
git add engine/mcp/server.py engine/mcp/__main__.py tests/test_mcp_server.py tests/test_mcp_stdio.py
git commit -m "feat(mcp): stdio server wiring, instructions, stdout guard and stdio smoke test"
```

---

### Task 7: The `lausnir_ro` role — needs the user's approval before running SQL

**Files:**
- Create: `deploy/sql/create_readonly_role.sql`
- Modify: `.env` (local, git-ignored: add `DATABASE_URL_READONLY`), `.env.example` if it exists (add the key with a placeholder)
- Test: `tests/test_mcp_readonly_role_db.py` (skip unless `DATABASE_URL_READONLY`)

**Interfaces:**
- Consumes: nothing from code. Produces: a working `DATABASE_URL_READONLY`.

- [ ] **Step 1: Write the SQL file (do not run yet)**

`deploy/sql/create_readonly_role.sql`:

```sql
-- Read-only role for the MCP server (docs/superpowers/specs/2026-09-29-mcp-server-design.md §5).
-- Run once as the DB owner:
--   psql "$SYNC_URL" -v ro_password='…' -f deploy/sql/create_readonly_role.sql
-- Re-runnable: CREATE ROLE is guarded, every other statement is idempotent.
-- (\gexec runs the generated CREATE ROLE only when the row exists; a DO block
--  cannot see psql variables inside dollar quotes, hence this form.)
SELECT format('CREATE ROLE lausnir_ro LOGIN PASSWORD %L', :'ro_password') AS stmt
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lausnir_ro') \gexec

GRANT CONNECT ON DATABASE lausnir_v2 TO lausnir_ro;
GRANT USAGE ON SCHEMA public TO lausnir_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO lausnir_ro;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO lausnir_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO lausnir_ro;
REVOKE CREATE ON SCHEMA public FROM lausnir_ro;
REVOKE TEMP ON DATABASE lausnir_v2 FROM lausnir_ro;
ALTER ROLE lausnir_ro SET default_transaction_read_only = on;
ALTER ROLE lausnir_ro SET statement_timeout = '15s';
```

If the database in `DATABASE_URL` is not named `lausnir_v2`, substitute the real name in both `ON DATABASE` lines before running.

- [ ] **Step 2: STOP — ask the user**

Show the user the full SQL file and this exact message, then wait:

> Þarf samþykki: stofna Postgres-hlutverkið `lausnir_ro` (aðeins SELECT, `default_transaction_read_only = on`, 15 s tímamörk) í `lausnir_v2` með SQL-inu í `deploy/sql/create_readonly_role.sql`. Lykilorðið verður búið til með `openssl rand -base64 24`, aldrei prentað, og skrifað í `.env` sem `DATABASE_URL_READONLY`. Segðu „já“ til að keyra.

Do not proceed to Step 3 without a "já". Record the ruling in the ledger.

- [ ] **Step 3: Run it**

```bash
cd /Volumes/RuleOfLaw/Lausnir && set -a; . ./.env; set +a
PW=$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-32)
SYNC_URL=$(printf '%s' "$DATABASE_URL" | sed 's/postgresql+asyncpg/postgresql/')
psql "$SYNC_URL" -v ro_password="$PW" -f deploy/sql/create_readonly_role.sql
psql "$SYNC_URL" -Atc "\du lausnir_ro"
# Derive the RO URL from DATABASE_URL: same host/port/db, user lausnir_ro.
RO_URL=$(printf '%s' "$DATABASE_URL" | sed -E "s#//[^@]+@#//lausnir_ro:${PW}@#")
grep -q '^DATABASE_URL_READONLY=' .env && sed -i '' "s#^DATABASE_URL_READONLY=.*#DATABASE_URL_READONLY=${RO_URL}#" .env || printf '\nDATABASE_URL_READONLY=%s\n' "$RO_URL" >> .env
unset PW RO_URL
```

Never echo `$PW` or `$RO_URL`. Verify with `grep -c '^DATABASE_URL_READONLY=' .env` (expect `1`) and `psql "$(grep '^DATABASE_URL_READONLY=' .env | cut -d= -f2- | sed 's/postgresql+asyncpg/postgresql/')" -Atc "select current_user, current_setting('default_transaction_read_only')"` (expect `lausnir_ro|on`).

- [ ] **Step 4: Write the privilege test**

```python
# tests/test_mcp_readonly_role_db.py
"""Layer 1 of the SELECT guard: the role itself cannot write, independent of sqlguard."""
import os

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

_URL = os.environ.get("DATABASE_URL_READONLY")
pytestmark = pytest.mark.skipif(not _URL, reason="needs DATABASE_URL_READONLY")


async def test_role_cannot_write_even_without_guard():
    eng = create_async_engine(_URL)
    try:
        async with eng.connect() as conn:
            who = (await conn.execute(text("select current_user"))).scalar()
            assert who == "lausnir_ro"
            with pytest.raises(DBAPIError) as ei:
                await conn.execute(text("UPDATE sources SET display_name = display_name WHERE false"))
                await conn.commit()
            msg = str(ei.value).lower()
            assert "read-only" in msg or "permission denied" in msg
    finally:
        await eng.dispose()


async def test_role_cannot_create_table():
    eng = create_async_engine(_URL)
    try:
        async with eng.connect() as conn:
            with pytest.raises(DBAPIError):
                await conn.execute(text("CREATE TABLE zz_should_fail (id int)"))
    finally:
        await eng.dispose()


async def test_role_can_select():
    eng = create_async_engine(_URL)
    try:
        async with eng.connect() as conn:
            assert (await conn.execute(text("select count(*) from documents"))).scalar() > 0
    finally:
        await eng.dispose()
```

- [ ] **Step 5: Run every MCP test against the RO role**

Run: `set -a; . ./.env; set +a; uv run pytest -q tests/test_mcp_readonly_role_db.py tests/test_mcp_tools_db.py tests/test_mcp_sql_db.py tests/test_mcp_stdio.py`
Expected: all PASS with `DATABASE_URL_READONLY` now set (the DB test files prefer it). Then `uv run pytest -q` → green.

- [ ] **Step 6: Commit (SQL file + test only — `.env` is git-ignored; confirm with `git status`)**

```bash
git add deploy/sql/create_readonly_role.sql tests/test_mcp_readonly_role_db.py
git status --short   # must not list .env
git commit -m "feat(mcp): lausnir_ro read-only role script and privilege tests"
```

---

### Task 8: Documentation

**Files:**
- Create: `docs/wiki/10-mcp.md`
- Modify: `docs/wiki/README.md` (add the page to the index), `docs/wiki/08-throun.md` (one paragraph: how to run/attach the MCP server), `docs/wiki/09-gildrur.md` (two new gotchas + resolve the TODO at line 208), `docs/READINESS_PLAYBOOK.md:47,59,105` (mcp-postgres items → done/replaced), `docs/superpowers/specs/2026-09-29-mcp-server-design.md` (status line → "Innleitt <date>, grein feat/mcp-server")
- Test: none (docs); verify links by `grep -n "10-mcp" docs/wiki/README.md`.

- [ ] **Step 1: Write `docs/wiki/10-mcp.md`** (Icelandic, same style as the other wiki pages). Required sections and content:

```markdown
# 10 · MCP-þjónn (read-only)

**Hvað:** `python -m engine.mcp` er stdio MCP-þjónn sem gefur LLM-viðskiptavini á sömu vél aðgang að leitinni og lesaðgang að grunninum. Spec: `docs/superpowers/specs/2026-09-29-mcp-server-design.md`.

## Ræsing og tenging

Krefst `DATABASE_URL_READONLY` í `.env` (hlutverkið `lausnir_ro`, sjá neðar). Þjónninn les `.env` sjálfur.

Claude Code:
    claude mcp add lausnir -- uv run --directory /Volumes/RuleOfLaw/Lausnir python -m engine.mcp

Claude Desktop (`~/Library/Application Support/Claude/claude_desktop_config.json`):
    {"mcpServers": {"lausnir": {"command": "uv", "args": ["run", "--directory", "/Volumes/RuleOfLaw/Lausnir", "python", "-m", "engine.mcp"]}}}

Handvirk prófun án viðskiptavinar: `uv run pytest -q tests/test_mcp_stdio.py`.

## Verkfærin
[table: name | inntak | skilar | mörk — eight rows, values from spec §4/§7]

## Lesaðgangshlutverkið `lausnir_ro`
- Stofnað með `deploy/sql/create_readonly_role.sql` (SELECT-only, `default_transaction_read_only = on`, `statement_timeout = 15s`).
- Þrjú lög varnar: hlutverk → READ ONLY færsla → `engine/mcp/sqlguard.py`.
- `mcp-postgres` (gamla tengingin með fullum skrifaðgangi): fjarlægja úr stillingum viðskiptavinar, eða beina á sömu `DATABASE_URL_READONLY` ef almennt SQL-verkfæri er enn óskað.

## Mörk og hegðun
[page_size ≤ 25, count ≤ 50, max_chars ≤ 40000, max_rows ≤ 1000, reitir ≤ 500 stafir, 15 s; hint-reglur; relaxed]

## Gildrur
- Stdout er MCP-rásin. Allt log á stderr; `tests/test_mcp_server.py::test_import_writes_nothing_to_stdout` ver þetta.
- `init_db(create_tables=False)` er skylda með lesaðgangshlutverki.
- `plan_cache_mode=force_custom_plan` gildir líka hér (sama `connect_args` og API-ið).
```

Fill every bracketed placeholder above with real content from the spec — the page must have no brackets left.

- [ ] **Step 2: Update the other docs**

- `docs/wiki/README.md`: add `- [10 · MCP-þjónn](10-mcp.md) — read-only MCP fyrir LLM-viðskiptavini: leit, efnisgreinar, skjöl, SQL með lesaðgangi` to the index list.
- `docs/wiki/08-throun.md`: add a short "MCP" subsection: how to attach with Claude Code, how to run the MCP tests, that `DATABASE_URL_READONLY` is per machine (like `.env`).
- `docs/wiki/09-gildrur.md`: two new `###` entries — "MCP: stdout er samskiptarásin" and "Lesaðgangshlutverk + `Base.metadata.create_all`" — and change the line-208 bullet to `- ~~Ákveða hvort mcp-postgres …~~ Leyst 2026-09-29: `lausnir_ro` + `engine/mcp` (sjá 10-mcp.md).`
- `docs/READINESS_PLAYBOOK.md`: lines 59 and 105 → `[x]` with a pointer to `docs/wiki/10-mcp.md`; line 47 gets one sentence: "Síðan 2026-09-29 er til aðgreint lesaðgangshlutverk `lausnir_ro` sem MCP-þjónninn notar."
- Spec status line → `**Staða:** Innleitt 2026-09-29 á grein feat/mcp-server.`

- [ ] **Step 3: Verify and commit**

Run: `grep -n "10-mcp" docs/wiki/README.md docs/wiki/08-throun.md docs/wiki/09-gildrur.md docs/READINESS_PLAYBOOK.md | wc -l` → ≥ 4; `grep -c "\[" docs/wiki/10-mcp.md` should only count markdown links, not leftover placeholders (`grep -n "^\[" docs/wiki/10-mcp.md` → nothing).

```bash
git add docs/wiki/10-mcp.md docs/wiki/README.md docs/wiki/08-throun.md docs/wiki/09-gildrur.md docs/READINESS_PLAYBOOK.md docs/superpowers/specs/2026-09-29-mcp-server-design.md
git commit -m "docs: MCP server wiki page, gotchas, readiness playbook update"
```

---

## Self-review notes (written with the spec open)

- Spec §3 → Tasks 1, 6. §4.1–4.6 → Task 4. §4.7–4.8 → Task 5. §5 → Tasks 2, 5, 7. §6 → Task 6 (`INSTRUCTIONS`). §7 → caps as constants in Tasks 4–5, error mapping in Task 5 `sql_query` and Task 6 `_run`. §8 → tests in every task; stdio smoke test in Task 6. §9 → Task 8. §10 risks: SDK pin (Task 1), caps (Tasks 4–5), role test independent of guard (Task 7), no DDL (Task 1 test + Task 6 lifespan).
- Names used consistently: `get_passages_tool` / `get_document_tool` in `tools.py` (to avoid shadowing the core functions they import), registered under the MCP names `get_passages` / `get_document` in `server.py`.
- The DB test files prefer `DATABASE_URL_READONLY` and fall back to `DATABASE_URL` so Tasks 4–6 are testable before Task 7; only `test_mcp_readonly_role_db.py` requires the RO URL, because it tests the role itself.
