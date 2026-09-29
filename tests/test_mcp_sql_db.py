"""sql_query / describe_schema against the live DB.

Requires DATABASE_URL_READONLY: these tests are about what the SELECT-only
role can and cannot do, so running them as the read-write role would be a lie."""
import os
import tracemalloc

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import engine.mcp.tools as tools

_URL = os.environ.get("DATABASE_URL_READONLY")
pytestmark = pytest.mark.skipif(not _URL, reason="needs DATABASE_URL_READONLY")


async def _session():
    eng = create_async_engine(_URL)
    return eng, AsyncSession(eng, expire_on_commit=False)


async def test_tests_run_as_the_read_only_role():
    """Honesty guard: no silent fallback to the read-write URL."""
    eng, s = await _session()
    try:
        out = await tools.sql_query(s, sql="SELECT current_user AS u")
        assert out["rows"] == [["lausnir_ro"]]
    finally:
        await s.close(); await eng.dispose()


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


async def test_sql_query_timeout_message(monkeypatch):
    monkeypatch.setattr(tools, "SQL_TIMEOUT", "200ms")
    eng, s = await _session()
    try:
        with pytest.raises(tools.ToolInputError) as ei:
            # cross join big enough to exceed 200 ms but cheap to cancel
            await tools.sql_query(s, sql="SELECT count(*) FROM passages a, passages b WHERE a.ordinal = b.ordinal")
        assert "tímamörk" in str(ei.value)
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


async def test_sql_query_error_message_has_no_class_repr():
    """MINOR 4: the wrapped asyncpg exception's own message, not the wrapper's
    class-repr str() (`"<class '...'>: column ..."`)."""
    eng, s = await _session()
    try:
        with pytest.raises(tools.ToolInputError) as ei:
            await tools.sql_query(s, sql="SELECT nope_col FROM sources")
        msg = str(ei.value)
        assert msg.startswith("Villa í fyrirspurn: column")
        assert "<class" not in msg
    finally:
        await s.close(); await eng.dispose()


async def test_sql_query_streams_large_result_with_low_memory():
    """The old `.execute().fetchmany()` path buffered the whole result
    client-side before slicing it; streaming must not. Measured with
    tracemalloc: an unbounded `SELECT text FROM passages` should stay well
    under buffering ~100k passage texts (~208 MB observed with the old
    code)."""
    eng, s = await _session()
    try:
        tracemalloc.start()
        try:
            out = await tools.sql_query(s, sql="SELECT text FROM passages", max_rows=5)
        finally:
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
        assert out["row_count"] == 5 and out["truncated_rows"] is True
        assert peak < 30 * 1024 * 1024, f"peak allocation {peak / 1e6:.1f} MB, expected < 30 MB"
    finally:
        await s.close(); await eng.dispose()


@pytest.mark.parametrize("sql", [
    "SHOW server_version",
    "EXPLAIN SELECT 1",
    "VALUES (1, 2)",
    "TABLE sources",
])
async def test_sql_query_non_select_statement_forms_still_work(sql):
    """These statement forms go through the same server-side-cursor path as
    SELECT; if a given form can't be streamed, sql_query falls back to a
    buffered execute for that statement only (inside the same transaction) —
    either way it must still return columns/rows."""
    eng, s = await _session()
    try:
        out = await tools.sql_query(s, sql=sql, max_rows=3)
        assert out["columns"] and out["rows"]
    finally:
        await s.close(); await eng.dispose()


async def test_sql_query_after_describe_schema_same_session():
    """MINOR/IMPORTANT 2: describe_schema then sql_query on the SAME session
    must not raise 'A transaction is already begun on this Session'."""
    eng, s = await _session()
    try:
        await tools.describe_schema(s)
        out = await tools.sql_query(s, sql="SELECT 1 AS one")
        assert out["rows"] == [[1]]
    finally:
        await s.close(); await eng.dispose()


async def test_sql_query_after_bare_session_execute_same_session():
    eng, s = await _session()
    try:
        await s.execute(text("SELECT 1"))
        out = await tools.sql_query(s, sql="SELECT 1 AS one")
        assert out["rows"] == [[1]]
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
        assert docs["kind"] == "table"
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


async def test_sql_query_savepoint_fallback_when_cursor_unsupported(monkeypatch):
    """The server-side cursor path is wrapped in a SAVEPOINT so a statement
    form that cannot be streamed (SQLSTATE 0A000) can be retried with a plain
    buffered execute without aborting the outer read-only transaction. Forced
    here by making `session.stream` raise that error for every call."""
    from sqlalchemy.exc import DBAPIError

    class _Orig(Exception):
        sqlstate = "0A000"

    async def boom(self, *a, **kw):
        raise DBAPIError("SELECT 1", {}, _Orig("cursor not supported for this statement"))

    monkeypatch.setattr(AsyncSession, "stream", boom)

    eng, s = await _session()
    try:
        out = await tools.sql_query(s, sql="SELECT short_name FROM sources ORDER BY short_name",
                                    max_rows=2)
        assert out["row_count"] == 2 and out["truncated_rows"] is True
        # The savepoint rollback must leave the session usable for the next call.
        again = await tools.sql_query(s, sql="SELECT 1 AS one")
        assert again["rows"] == [[1]]
    finally:
        await s.close(); await eng.dispose()
