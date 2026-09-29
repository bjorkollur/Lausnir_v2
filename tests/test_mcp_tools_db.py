"""MCP research tools against the live corpus. Skipped without a DB URL.
Uses DATABASE_URL_READONLY when set (Task 7 onward), else DATABASE_URL, so
Tasks 4-6 can run before the read-only role exists."""
import os
import uuid

import pytest
from sqlalchemy import text
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
        assert doc["text_error"] is None

        win = await tools.get_passages_tool(s, doc_id=first["doc_id"], from_ordinal=0, count=3)
        assert [p["ordinal"] for p in win["passages"]] == [0, 1, 2]
        assert win["next_from_ordinal"] == 3
        # Unfiltered paging is unchanged: the window starts where asked and the
        # filtered total equals the document total.
        assert win["from_ordinal"] == 0
        assert win["matching_passages"] == win["total_passages"] == doc["total_passages"]
    finally:
        await s.close(); await eng.dispose()


async def test_get_document_no_text_by_default_and_short_text_not_truncated():
    eng, s = await _session()
    try:
        out = await tools.search(s, q="gæsluvarðhald", scope=["domstolar"], page_size=1)
        d = await tools.get_document_tool(s, doc_id=out["results"][0]["doc_id"])
        assert d["text"] is None and d["text_truncated"] is False and d["text_error"] is None
        d2 = await tools.get_document_tool(s, doc_id=out["results"][0]["doc_id"], max_chars=40_000)
        assert d2["text"] is not None
        if not d2["text_truncated"]:
            assert len(d2["text"]) <= 40_000
    finally:
        await s.close(); await eng.dispose()


_REVIEWER_DOC = "00c77db4-a01c-44c9-962d-9470044b11f5"


async def test_get_passages_lower_body_filter_reviewer_case():
    """Regression: a layer filter used to return an empty window with a non-zero
    total, because the window was in global-ordinal space and the total was not."""
    eng, s = await _session()
    try:
        exists = (await s.execute(
            text("SELECT 1 FROM documents WHERE id = :id"),
            {"id": uuid.UUID(_REVIEWER_DOC)})).scalar()
        if not exists:
            pytest.skip("reviewer document no longer in the corpus")
        win = await tools.get_passages_tool(s, doc_id=_REVIEWER_DOC, layer="lower_body")
        assert win["passages"], "layer filter must not return an empty first page"
        assert all(p["layer"] == "lower_body" for p in win["passages"])
        assert win["from_ordinal"] >= 33
        assert win["passages"][0]["ordinal"] == win["from_ordinal"]
        assert win["matching_passages"] == 23
        assert win["total_passages"] > win["matching_passages"]
    finally:
        await s.close(); await eng.dispose()


async def test_get_passages_pages_through_a_filtered_layer():
    eng, s = await _session()
    try:
        row = (await s.execute(text("""
            SELECT document_id FROM passages
            GROUP BY document_id
            HAVING count(*) FILTER (WHERE layer = 'lower_body') > 7
               AND count(*) FILTER (WHERE layer = 'body') > 0
            LIMIT 1"""))).first()
        if row is None:
            pytest.skip("no document with both body and lower_body passages")
        did = row[0]
        expected = [r[0] for r in (await s.execute(text(
            "SELECT ordinal FROM passages WHERE document_id = :id AND layer = 'lower_body' "
            "ORDER BY ordinal"), {"id": did})).all()]
        did = str(did)

        seen, cursor, pages = [], 0, 0
        while cursor is not None and pages < 200:
            win = await tools.get_passages_tool(s, doc_id=did, from_ordinal=cursor,
                                                count=5, layer="lower_body")
            assert all(p["layer"] == "lower_body" for p in win["passages"])
            assert win["matching_passages"] == len(expected)
            seen.extend(p["ordinal"] for p in win["passages"])
            cursor, pages = win["next_from_ordinal"], pages + 1
        assert seen == expected

        # Past the end of the filtered range: empty page, no next cursor.
        tail = await tools.get_passages_tool(s, doc_id=did, from_ordinal=expected[-1] + 1,
                                             count=5, layer="lower_body")
        assert tail["passages"] == [] and tail["next_from_ordinal"] is None
        assert tail["matching_passages"] == len(expected)
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
        with pytest.raises(tools.ToolInputError):
            await tools.get_document_tool(s, doc_id=str(uuid.UUID(int=0)))
        with pytest.raises(tools.ToolInputError):
            await tools.passage_context(s, passage_id=str(uuid.UUID(int=0)))
        with pytest.raises(tools.ToolInputError):
            await tools.get_passages_tool(s, doc_id=str(uuid.UUID(int=0)))
        with pytest.raises(tools.ToolInputError):
            await tools.get_passages_tool(s, doc_id=str(uuid.UUID(int=0)), layer="body")
    finally:
        await s.close(); await eng.dispose()
