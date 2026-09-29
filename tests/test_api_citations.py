"""Contract tests for the citations endpoint, get_citations and the new
get_document fields — scripted fake session, same style as
tests/test_api_passages.py (no DB).
"""
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient

from engine.api.app import app, get_session
from engine.search.queries import SearchError, get_citations, get_document


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


class FakeSession:
    """Scripted execute(): returns the next _Result in the queue."""
    def __init__(self, results):
        self._q = list(results)
    async def execute(self, *a, **k):
        return self._q.pop(0)


def _client(results):
    async def _fake():
        yield FakeSession(results)
    app.dependency_overrides[get_session] = _fake
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


DOC_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")

# The citing document, as get_citations fetches it to build same_case.
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


# ── get_citations ─────────────────────────────────────────────────────────────

async def test_get_citations_out_item_shape():
    s = FakeSession([_Result(first=ME), _Result(rows=[_row()])])
    res = await get_citations(s, DOC_ID, direction="out")
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


async def test_get_citations_in_direction_and_also_appeal():
    s = FakeSession([_Result(first=ME), _Result(rows=[_row(also_appeal=True, total=3)])])
    res = await get_citations(s, DOC_ID, direction="in")
    assert res["direction"] == "in" and res["total"] == 3
    assert res["items"][0]["also_appeal"] is True


async def test_same_case_true_when_other_matches_normalised_number_and_court():
    """'055/2001' and '55/2001' are the same case number (norm_case_number)."""
    s = FakeSession([_Result(first=ME),
                     _Result(rows=[_row(other_court="Hrd.", other_case="55/2001")])])
    res = await get_citations(s, DOC_ID, direction="out")
    assert res["items"][0]["same_case"] is True


async def test_same_case_false_when_court_differs():
    s = FakeSession([_Result(first=ME),
                     _Result(rows=[_row(other_court="Lrd.", other_case="55/2001")])])
    res = await get_citations(s, DOC_ID, direction="out")
    assert res["items"][0]["same_case"] is False


async def test_passage_id_and_anchor_null_when_no_passage_found():
    s = FakeSession([_Result(first=ME), _Result(rows=[_row(passage_id=None, p_layer=None,
                                                           para_from=None, para_to=None,
                                                           section_path=None, ordinal=None)])])
    item = (await get_citations(s, DOC_ID, direction="out"))["items"][0]
    assert item["passage_id"] is None and item["anchor"] is None


async def test_get_citations_rejects_bad_direction():
    with pytest.raises(SearchError):
        await get_citations(FakeSession([]), DOC_ID, direction="sideways")


async def test_get_citations_rejects_bad_uuid():
    with pytest.raises(SearchError):
        await get_citations(FakeSession([]), "not-a-uuid", direction="out")


async def test_get_citations_clamps_page_size():
    s = FakeSession([_Result(first=ME), _Result(rows=[])])
    res = await get_citations(s, DOC_ID, direction="out", page_size=500)
    assert res["page_size"] == 100 and res["items"] == [] and res["total"] == 0


async def test_get_citations_returns_none_for_missing_document():
    s = FakeSession([_Result(first=None)])
    assert await get_citations(s, DOC_ID, direction="out") is None


# ── route ─────────────────────────────────────────────────────────────────────

def test_route_out_returns_items():
    c = _client([_Result(first=ME), _Result(rows=[_row()])])
    r = c.get(f"/api/document/{DOC_ID}/citations")
    assert r.status_code == 200
    body = r.json()
    assert body["direction"] == "out" and body["total"] == 1
    assert set(body["items"][0]) == CITATION_REF_KEYS


def test_route_in_direction():
    c = _client([_Result(first=ME), _Result(rows=[_row(total=2)])])
    r = c.get(f"/api/document/{DOC_ID}/citations?direction=in&page=1&page_size=10")
    assert r.status_code == 200
    body = r.json()
    assert body["direction"] == "in" and body["page_size"] == 10 and body["total"] == 2


def test_route_404_when_document_missing():
    c = _client([_Result(first=None)])
    assert c.get(f"/api/document/{DOC_ID}/citations").status_code == 404


def test_route_rejects_bad_direction():
    c = _client([])          # validation must fail before any DB call
    r = c.get(f"/api/document/{DOC_ID}/citations?direction=sideways")
    assert r.status_code in (400, 422)


def test_route_rejects_page_size_above_100():
    c = _client([])
    assert c.get(f"/api/document/{DOC_ID}/citations?page_size=500").status_code in (400, 422)


def test_route_400_on_bad_uuid():
    c = _client([])
    assert c.get("/api/document/not-a-uuid/citations").status_code == 400


# ── get_document ──────────────────────────────────────────────────────────────

DOC_ROW = {
    "id": DOC_ID, "source": "haestirettur", "source_display": "Hæstiréttur",
    "external_id": "x1", "url": "http://e", "court": "Hrd.", "case_number": "055/2001",
    "document_date": date(2001, 3, 1), "verdict_type": "Dómur", "instance_tier": 3,
    "case_type": None, "plaintiffs": [], "defendants": [], "keywords": [],
    "summary": "reifun", "body_text": "texti", "lower_body_text": None, "raw_api_data": {},
}


async def test_get_document_has_empty_citation_fields_when_nothing_found():
    """doc row, appeal links, citations out, citations in, unresolved count."""
    s = FakeSession([_Result(first=DOC_ROW), _Result(rows=[]),
                     _Result(rows=[]), _Result(rows=[]), _Result(first=0)])
    doc = await get_document(s, DOC_ID)
    assert doc["citations_out"] == [] and doc["citations_out_total"] == 0
    assert doc["cited_by"] == [] and doc["cited_by_total"] == 0
    assert doc["citations_unresolved_total"] == 0


async def test_get_document_fills_citation_fields():
    s = FakeSession([_Result(first=DOC_ROW), _Result(rows=[]),
                     _Result(rows=[_row(total=1)]), _Result(rows=[_row(total=7)]),
                     _Result(first=4)])
    doc = await get_document(s, DOC_ID)
    assert doc["citations_out_total"] == 1 and len(doc["citations_out"]) == 1
    assert doc["cited_by_total"] == 7 and len(doc["cited_by"]) == 1
    assert doc["citations_unresolved_total"] == 4
    assert set(doc["citations_out"][0]) == CITATION_REF_KEYS
