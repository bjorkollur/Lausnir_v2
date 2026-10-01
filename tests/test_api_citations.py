"""Contract tests for the citations endpoint, get_citations and the new
get_document fields. No DB: a keyed fake session dispatches on the SQL that was
actually issued, so a swapped direction or a dropped filter fails loudly rather
than quietly consuming the next scripted result.
"""
import inspect
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient

from engine.api.app import app, get_session
from engine.search import passage_search
from engine.search import queries as q
from engine.search.queries import (
    APPEAL_RELATIONS, CITATION_LAYERS, SearchError, get_citations, get_document,
)


class _Result:
    def __init__(self, first=None, rows=()):
        self._first, self._rows = first, list(rows)
    def mappings(self):
        return self
    def first(self):
        return self._first
    def all(self):
        return self._rows
    def scalar(self):
        return self._first


# Distinguishing fragment of each statement get_document / get_citations issues.
# Order matters: the count statement embeds the page statement's WHERE clause, so
# it has to be tried first.
K_DOC = "d.raw_api_data"                              # the document row
K_LINKS = "dl.relation <> 'cites'"                    # appeal links (never 'cites')
K_ME = "SELECT court, case_number FROM documents"     # court/case for same_case
K_UNRESOLVED = "status = ANY(:statuses)"              # citations_unresolved_total
K_COUNT = "SELECT count(*) AS total FROM c"           # total for an empty page
K_OUT = "c.from_doc_id = :id"                         # out page  (I cite these)
K_IN = "c.to_doc_id = :id"                            # in page   (these cite me)
_ORDER = (K_DOC, K_LINKS, K_ME, K_UNRESOLVED, K_COUNT, K_OUT, K_IN)


class KeyedSession:
    """execute() picks its result by what the SQL says, and records the params."""

    def __init__(self, **scripted):
        self._by_key = {
            K_DOC: scripted.pop("doc", None), K_LINKS: scripted.pop("links", None),
            K_ME: scripted.pop("me", None), K_UNRESOLVED: scripted.pop("unresolved", None),
            K_COUNT: scripted.pop("count", None), K_OUT: scripted.pop("out", None),
            K_IN: scripted.pop("in_", None),
        }
        assert not scripted, f"unknown scripted queries: {sorted(scripted)}"
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, stmt, params=None, *a, **k):
        sql = " ".join(str(stmt).split())
        for key in _ORDER:
            if key in sql:
                res = self._by_key[key]
                assert res is not None, f"unscripted query matched {key!r}: {sql[:160]}"
                self.calls.append((key, params or {}))
                return res
        raise AssertionError(f"no key matched this SQL: {sql[:200]}")

    def params_for(self, key):
        return next(p for k, p in self.calls if k == key)

    def keys(self):
        return [k for k, _ in self.calls]


def _client(**scripted):
    session = KeyedSession(**scripted)

    async def _fake():
        yield session
    app.dependency_overrides[get_session] = _fake
    return TestClient(app), session


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


DOC_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")

ME = {"court": "Hrd.", "case_number": "055/2001"}


def _row(**over):
    row = {
        "layer": "body", "raw_text": "dómi Hæstaréttar í máli nr. 12/2020", "confidence": 1.0,
        "other_id": OTHER_ID, "other_case": "12/2020", "other_court": "Lrd.",
        "other_date": date(2020, 5, 5), "other_verdict": "Dómur", "other_source": "landsrettur",
        "passage_id": uuid.UUID("33333333-3333-3333-3333-333333333333"),
        "p_layer": "body", "para_from": 4, "para_to": 4, "section_path": "Niðurstaða", "ordinal": 7,
        "also_appeal": False, "total": 1,
    }
    row.update(over)
    return row


CITATION_REF_KEYS = {
    "document_id", "urlausn", "source", "document_date", "layer", "passage_id",
    "anchor", "raw_text", "confidence", "also_appeal", "same_case",
}


# ── get_citations: item shape and direction ───────────────────────────────────

async def test_get_citations_out_item_shape_and_bound_params():
    s = KeyedSession(me=_Result(first=ME), out=_Result(rows=[_row()]))
    res = await get_citations(s, DOC_ID, direction="out")

    assert s.keys() == [K_ME, K_OUT], "out must issue the from_doc_id page query"
    p = s.params_for(K_OUT)
    assert p["id"] == DOC_ID and p["layers"] == list(CITATION_LAYERS)
    assert p["appeal_rels"] == list(APPEAL_RELATIONS)
    assert p["limit"] == 50 and p["offset"] == 0

    assert res["direction"] == "out" and res["total"] == 1
    assert res["page"] == 1 and res["page_size"] == 50
    item = res["items"][0]
    assert set(item) == CITATION_REF_KEYS
    assert item["document_id"] == str(OTHER_ID)
    assert item["urlausn"].startswith("Lrd. 12/2020")
    assert item["source"] == "landsrettur"
    assert item["document_date"] == "2020-05-05"
    assert item["layer"] == "body"
    assert item["passage_id"] == "33333333-3333-3333-3333-333333333333"
    assert item["anchor"] == "4. mgr."
    assert item["raw_text"].endswith("12/2020")
    assert item["confidence"] == 1.0
    assert item["also_appeal"] is False
    assert item["same_case"] is False


async def test_get_citations_in_issues_the_to_doc_id_query():
    s = KeyedSession(me=_Result(first=ME), in_=_Result(rows=[_row(also_appeal=True, total=3)]))
    res = await get_citations(s, DOC_ID, direction="in")

    assert s.keys() == [K_ME, K_IN], "in must issue the to_doc_id page query"
    assert s.params_for(K_IN)["id"] == DOC_ID
    assert res["direction"] == "in" and res["total"] == 3
    assert res["items"][0]["also_appeal"] is True


async def test_paging_binds_limit_and_offset():
    s = KeyedSession(me=_Result(first=ME), out=_Result(rows=[_row(total=120)]))
    res = await get_citations(s, DOC_ID, direction="out", page=3, page_size=20)
    p = s.params_for(K_OUT)
    assert (p["limit"], p["offset"]) == (20, 40)
    assert res["page"] == 3 and res["page_size"] == 20 and res["total"] == 120


# ── same_case / anchor ────────────────────────────────────────────────────────

async def test_same_case_true_when_other_matches_normalised_number_and_court():
    """'055/2001' and '55/2001' are the same case number (norm_case_number)."""
    s = KeyedSession(me=_Result(first=ME),
                     out=_Result(rows=[_row(other_court="Hrd.", other_case="55/2001")]))
    res = await get_citations(s, DOC_ID, direction="out")
    assert res["items"][0]["same_case"] is True


async def test_same_case_false_when_court_differs():
    s = KeyedSession(me=_Result(first=ME),
                     out=_Result(rows=[_row(other_court="Lrd.", other_case="55/2001")]))
    res = await get_citations(s, DOC_ID, direction="out")
    assert res["items"][0]["same_case"] is False


async def test_passage_id_and_anchor_null_when_no_passage_found():
    s = KeyedSession(me=_Result(first=ME),
                     out=_Result(rows=[_row(passage_id=None, p_layer=None, para_from=None,
                                            para_to=None, section_path=None, ordinal=None)]))
    item = (await get_citations(s, DOC_ID, direction="out"))["items"][0]
    assert item["passage_id"] is None and item["anchor"] is None


# ── validation, clamping, totals ──────────────────────────────────────────────

async def test_get_citations_rejects_bad_direction():
    with pytest.raises(SearchError):
        await get_citations(KeyedSession(), DOC_ID, direction="sideways")


async def test_get_citations_rejects_bad_uuid():
    with pytest.raises(SearchError):
        await get_citations(KeyedSession(), "not-a-uuid", direction="out")


async def test_get_citations_clamps_page_size():
    s = KeyedSession(me=_Result(first=ME), out=_Result(rows=[]))
    res = await get_citations(s, DOC_ID, direction="out", page_size=500)
    assert res["page_size"] == 100 and s.params_for(K_OUT)["limit"] == 100
    assert res["items"] == [] and res["total"] == 0


async def test_empty_first_page_reports_zero_without_a_count_query():
    s = KeyedSession(me=_Result(first=ME), out=_Result(rows=[]))
    res = await get_citations(s, DOC_ID, direction="out")
    assert res["total"] == 0
    assert s.keys() == [K_ME, K_OUT], "page 1 must not pay for a second count query"


async def test_empty_page_past_the_end_still_reports_the_true_total():
    s = KeyedSession(me=_Result(first=ME), out=_Result(rows=[]), count=_Result(first=7))
    res = await get_citations(s, DOC_ID, direction="out", page=4, page_size=2)
    assert res["total"] == 7 and res["items"] == []
    # The empty page came first; the count statement (which embeds the same CTE,
    # hence the _ORDER precedence) only runs because the page came back empty.
    assert s.keys() == [K_ME, K_OUT, K_COUNT]
    assert s.params_for(K_COUNT)["id"] == DOC_ID


async def test_empty_page_past_the_end_counts_the_in_direction_too():
    s = KeyedSession(me=_Result(first=ME), in_=_Result(rows=[]), count=_Result(first=3))
    res = await get_citations(s, DOC_ID, direction="in", page=2)
    assert res["total"] == 3


async def test_get_citations_returns_none_for_missing_document():
    s = KeyedSession(me=_Result(first=None))
    assert await get_citations(s, DOC_ID, direction="out") is None


# ── route ─────────────────────────────────────────────────────────────────────

def test_route_out_returns_items():
    c, _ = _client(me=_Result(first=ME), out=_Result(rows=[_row()]))
    r = c.get(f"/api/document/{DOC_ID}/citations")
    assert r.status_code == 200
    body = r.json()
    assert body["direction"] == "out" and body["total"] == 1
    assert set(body["items"][0]) == CITATION_REF_KEYS


def test_route_in_direction():
    c, s = _client(me=_Result(first=ME), in_=_Result(rows=[_row(total=2)]))
    r = c.get(f"/api/document/{DOC_ID}/citations?direction=in&page=1&page_size=10")
    assert r.status_code == 200
    body = r.json()
    assert body["direction"] == "in" and body["page_size"] == 10 and body["total"] == 2
    assert s.params_for(K_IN)["limit"] == 10


def test_route_404_when_document_missing():
    c, _ = _client(me=_Result(first=None))
    assert c.get(f"/api/document/{DOC_ID}/citations").status_code == 404


def test_route_rejects_bad_direction_with_422():
    """FastAPI's pattern rejects it before the handler — the spec's '400' is
    superseded by the Query(pattern=...) the brief prescribes."""
    c, _ = _client()          # validation must fail before any DB call
    assert c.get(f"/api/document/{DOC_ID}/citations?direction=sideways").status_code == 422


def test_route_rejects_page_size_above_100_with_422():
    c, _ = _client()
    assert c.get(f"/api/document/{DOC_ID}/citations?page_size=500").status_code == 422


def test_route_rejects_page_above_10000_with_422():
    """Upper bound on `page` matches the MCP tool's clamp: an absurd page is a
    422 rather than a full-table OFFSET scan."""
    c, _ = _client()          # validation must fail before any DB call
    assert c.get(f"/api/document/{DOC_ID}/citations?page=10001").status_code == 422
    # …and the boundary itself is still allowed through to the handler.
    c, _ = _client(me=_Result(first=ME), out=_Result(rows=[]), count=_Result(first=7))
    r = c.get(f"/api/document/{DOC_ID}/citations?page=10000")
    assert r.status_code == 200 and r.json()["page"] == 10_000


def test_route_400_on_bad_uuid():
    c, _ = _client()
    assert c.get("/api/document/not-a-uuid/citations").status_code == 400


# ── get_document ──────────────────────────────────────────────────────────────

DOC_ROW = {
    "id": DOC_ID, "source": "haestirettur", "source_display": "Hæstiréttur",
    "external_id": "x1", "url": "http://e", "court": "Hrd.", "case_number": "055/2001",
    "document_date": date(2001, 3, 1), "verdict_type": "Dómur", "instance_tier": 3,
    "case_type": None, "plaintiffs": [], "defendants": [], "keywords": [],
    "summary": "reifun", "body_text": "texti", "lower_body_text": None, "raw_api_data": {},
    "verdict_filename": None,
}


async def test_get_document_has_empty_citation_fields_when_nothing_found():
    s = KeyedSession(doc=_Result(first=DOC_ROW), links=_Result(rows=[]),
                     out=_Result(rows=[]), in_=_Result(rows=[]), unresolved=_Result(first=0))
    doc = await get_document(s, DOC_ID)
    assert doc["citations_out"] == [] and doc["citations_out_total"] == 0
    assert doc["cited_by"] == [] and doc["cited_by_total"] == 0
    assert doc["citations_unresolved_total"] == 0
    # Both directions ran, and neither re-read court/case_number (no K_ME).
    assert s.keys() == [K_DOC, K_LINKS, K_OUT, K_IN, K_UNRESOLVED]


async def test_get_document_fills_citation_fields():
    s = KeyedSession(doc=_Result(first=DOC_ROW), links=_Result(rows=[]),
                     out=_Result(rows=[_row(total=1)]), in_=_Result(rows=[_row(total=7)]),
                     unresolved=_Result(first=4))
    doc = await get_document(s, DOC_ID)
    assert doc["citations_out_total"] == 1 and len(doc["citations_out"]) == 1
    assert doc["cited_by_total"] == 7 and len(doc["cited_by"]) == 1
    assert doc["citations_unresolved_total"] == 4
    assert set(doc["citations_out"][0]) == CITATION_REF_KEYS


# ── 'cites' must never leak into the appeal-link surfaces ─────────────────────

async def test_appeal_links_query_excludes_cites():
    """Once 'cites' edges exist, every out-citation would otherwise show up in
    appeal_links under a raw 'cites' label. The keyed fake proves the filter is
    in the statement: without it, K_LINKS matches nothing and execute() raises."""
    s = KeyedSession(doc=_Result(first=DOC_ROW), links=_Result(rows=[]),
                     out=_Result(rows=[]), in_=_Result(rows=[]), unresolved=_Result(first=0))
    doc = await get_document(s, DOC_ID)
    assert doc["appeal_links"] == []
    # The links statement matched on the filter itself; drop it and execute()
    # would fall through to "no key matched this SQL".
    assert K_LINKS in s.keys()
    src = " ".join(inspect.getsource(q.get_document).split())
    assert "WHERE dl.from_doc_id = :id AND dl.relation <> 'cites'" in src


def test_search_projections_exclude_cites_from_has_appeal_links():
    """Both result projections: a cited (or citing) document is not thereby
    'tengt' — has_appeal_links stays about appeal relations only."""
    for fn in (q.search_documents, passage_search.search_by_passages):
        src = " ".join(inspect.getsource(fn).split())
        start = src.index("EXISTS (SELECT 1 FROM document_links dl")
        clause = src[start:src.index("AS has_appeal_links", start)]
        assert "dl.relation <> 'cites'" in clause, f"{fn.__name__}: cites leaks into has_appeal_links"
        assert "AS cited_by_count" in src, f"{fn.__name__}: missing cited_by_count"
