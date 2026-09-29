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
