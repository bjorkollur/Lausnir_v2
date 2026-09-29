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
    # ``is_empty`` mirrors the real ScopeFilter, where it is a property.
    class _Empty:
        is_empty = True
    class _NonEmpty:
        is_empty = False
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
    assert str(ei.value).startswith("Ógilt inntak")


async def test_get_passages_rejects_unknown_layer_and_section_kind():
    with pytest.raises(ToolInputError) as ei:
        await tools.get_passages_tool(_Sess(), doc_id=str(uuid.UUID(int=1)), layer="miðja")
    assert str(ei.value).startswith("Ógilt inntak") and "layer" in str(ei.value)
    with pytest.raises(ToolInputError) as ei:
        await tools.get_passages_tool(_Sess(), doc_id=str(uuid.UUID(int=1)), section_kind=["ekki-til"])
    assert str(ei.value).startswith("Ógilt inntak") and "section_kind" in str(ei.value)


async def test_passage_context_and_get_passages_clamp_args():
    with pytest.raises(ToolInputError):
        await tools.passage_context(_Sess(), passage_id="not-a-uuid")
    with pytest.raises(ToolInputError):
        await tools.get_passages_tool(_Sess(), doc_id="not-a-uuid")
    assert tools.CONTEXT_MAX == 10 and tools.PASSAGES_COUNT_MAX == 50 and tools.DOC_TEXT_MAX_CHARS == 40_000


def _fake_citation_ref(n: int) -> dict:
    return {
        "document_id": str(uuid.UUID(int=n)), "urlausn": f"U{n}", "source": "haestirettur",
        "document_date": dt.date(2020, 1, n), "layer": "body",
        "passage_id": str(uuid.UUID(int=n + 100)), "anchor": f"A{n}",
        "raw_text": f"vitnað í mál {n}", "confidence": 0.9, "also_appeal": n % 2 == 0,
        "same_case": False,
    }


async def test_citations_tool_rejects_bad_direction():
    with pytest.raises(ToolInputError) as ei:
        await tools.citations_tool(_Sess(), doc_id=str(uuid.UUID(int=1)), direction="sideways")
    assert "direction" in str(ei.value)


async def test_citations_tool_clamps_page_size(monkeypatch):
    seen = {}

    async def fake_get_citations(session, did, *, direction, page, page_size):
        seen["page_size"] = page_size
        return {"direction": direction, "total": 0, "page": page, "page_size": page_size, "items": []}

    monkeypatch.setattr(tools, "get_citations", fake_get_citations)
    await tools.citations_tool(_Sess(), doc_id=str(uuid.UUID(int=1)), page_size=999)
    assert seen["page_size"] == tools.PAGE_SIZE_MAX == 25


async def test_citations_tool_compacts_items_and_unknown_doc(monkeypatch):
    async def fake_get_citations(session, did, *, direction, page, page_size):
        return {"direction": direction, "total": 1, "page": page, "page_size": page_size,
                "items": [_fake_citation_ref(1)]}

    monkeypatch.setattr(tools, "get_citations", fake_get_citations)
    out = await tools.citations_tool(_Sess(), doc_id=str(uuid.UUID(int=1)))
    assert out["doc_id"] == str(uuid.UUID(int=1))
    assert out["direction"] == "out" and out["total"] == 1
    item = out["items"][0]
    assert set(item) == {"document_id", "urlausn", "date", "layer", "passage_id", "anchor",
                         "raw_text", "also_appeal", "same_case"}
    assert item["date"] == "2020-01-01"
    assert "source" not in item and "confidence" not in item

    async def fake_missing(session, did, *, direction, page, page_size):
        return None

    monkeypatch.setattr(tools, "get_citations", fake_missing)
    with pytest.raises(ToolInputError) as ei:
        await tools.citations_tool(_Sess(), doc_id=str(uuid.UUID(int=1)))
    assert "Skjal fannst ekki" in str(ei.value)


async def test_citations_tool_wraps_search_error(monkeypatch):
    from engine.search.queries import SearchError

    async def boom(session, did, *, direction, page, page_size):
        raise SearchError("boom")

    monkeypatch.setattr(tools, "get_citations", boom)
    with pytest.raises(ToolInputError) as ei:
        await tools.citations_tool(_Sess(), doc_id=str(uuid.UUID(int=1)))
    assert str(ei.value).startswith("Ógilt inntak")


async def test_get_document_tool_truncates_and_compacts_citations(monkeypatch):
    items_out = [_fake_citation_ref(n) for n in range(1, 8)]

    async def fake_get_document(session, did):
        return {
            "id": str(did), "source": "haestirettur", "raw_api_data": {}, "body_text": "x",
            "lower_body_text": None, "citations_out": items_out, "citations_out_total": 7,
            "cited_by": [], "cited_by_total": 0, "citations_unresolved_total": 0,
        }

    async def fake_execute(*a, **kw):
        class _R:
            def mappings(self):
                return self
            def all(self):
                return []
        return _R()

    monkeypatch.setattr(tools, "get_document", fake_get_document)
    session = _Sess()
    session.execute = fake_execute
    session.get = None
    out = await tools.get_document_tool(session, doc_id=str(uuid.UUID(int=1)))
    assert len(out["citations_out"]) == tools.CITATIONS_TOP_N == 5
    assert out["citations_out_total"] == 7
    assert out["cited_by"] == [] and out["cited_by_total"] == 0
    assert set(out["citations_out"][0]) == {"document_id", "urlausn", "date", "layer", "passage_id",
                                            "anchor", "raw_text", "also_appeal", "same_case"}


async def test_sql_query_rejects_before_touching_db():
    class _Boom:
        def begin(self): raise AssertionError("must not reach the DB")
    with pytest.raises(ToolInputError) as ei:
        await tools.sql_query(_Boom(), sql="DELETE FROM documents")
    assert "SELECT" in str(ei.value)
    with pytest.raises(ToolInputError):
        await tools.sql_query(_Boom(), sql="SELECT pg_sleep(20)")
