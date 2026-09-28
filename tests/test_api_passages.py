"""Contract tests for the passages endpoint and section_kind param, with a fake session."""
import pytest
from fastapi.testclient import TestClient

import engine.api.app as appmod
from engine.api.app import app, get_session
from engine.search.queries import SearchResults


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


def test_passages_404_when_document_missing():
    c = _client([_Result(first=None)])
    r = c.get("/api/document/00000000-0000-0000-0000-000000000000/passages")
    assert r.status_code == 404


@pytest.mark.parametrize("qs", ["?from=5&to=2", "?from=0&to=300", "?layer=footnotes", "?section_kind=foo"])
def test_passages_400_on_bad_params(qs):
    c = _client([])          # validation must fail before any DB call
    r = c.get("/api/document/00000000-0000-0000-0000-000000000000/passages" + qs)
    assert r.status_code == 400


def test_passages_400_on_bad_uuid():
    c = _client([])
    assert c.get("/api/document/not-a-uuid/passages").status_code == 400


def test_search_section_kind_400_for_non_passage_mode():
    """F4: mode=exact&section_kind=domsord must reject rather than silently
    ignore the filter. No DB call happens before the raise."""
    c = _client([])          # validation must fail before any DB call
    r = c.get("/api/search?q=x&mode=exact&section_kind=domsord")
    assert r.status_code == 400


def test_passages_happy_path_shape():
    import uuid
    from datetime import date
    doc = {"id": uuid.uuid4(), "source": "landsrettur", "court": "Lrd.", "case_number": "1/2024",
           "document_date": date(2024, 3, 6), "verdict_type": "Dómur"}
    row = {"id": uuid.uuid4(), "ordinal": 3, "layer": "body", "section_path": "Niðurstaða",
           "section_kind": "nidurstada", "para_from": 4, "para_to": 7, "char_start": 10,
           "char_end": 50, "word_count": 8, "text": "Texti efnisgreinar."}
    c = _client([_Result(first=doc), _Result(first=1), _Result(rows=[row])])
    r = c.get(f"/api/document/{doc['id']}/passages?from=3&to=3")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1 and body["urlausn"].startswith("Lrd. 1/2024")
    p = body["passages"][0]
    assert p["anchor"] == "4.–7. mgr." and p["section_kind"] == "nidurstada" and p["text"] == "Texti efnisgreinar."


def test_search_rejects_unknown_section_kind():
    c = _client([])
    r = c.get("/api/search?q=x&section_kind=foo")
    assert r.status_code == 400


def _minimal_result(**overrides):
    row = {
        "id": "doc-1", "urlausn": "Hrd. 1/2024", "source": "hrd", "source_display": "Hæstiréttur",
        "court": "Hrd.", "case_number": "1/2024", "document_date": "2024-01-01", "verdict_type": "Dómur",
        "keywords": [], "plaintiffs": [], "defendants": [], "snippet": "...", "has_appeal_links": False,
        "passage_id": "p-1", "anchor": None, "section_kind": None, "layer": None, "match_count": 1,
        "match_tier": 0,
    }
    row.update(overrides)
    return row


def test_search_exposes_strict_total_and_relaxed_when_relaxed(monkeypatch):
    """T4: relaxed keyword search shape — strict_total/relaxed/match_tier pass through."""
    async def _fake_search_documents(*a, **k):
        return SearchResults(
            total=5, page=1, page_size=20,
            results=[_minimal_result(match_tier=1)],
            strict_total=2, relaxed=True,
        )
    monkeypatch.setattr(appmod, "search_documents", _fake_search_documents)
    c = _client([])
    r = c.get("/api/search?q=x")
    assert r.status_code == 200
    body = r.json()
    assert body["strict_total"] == 2
    assert body["relaxed"] is True
    assert body["results"][0]["match_tier"] == 1


def test_search_api_passes_through_strict_total_and_relaxed_shape(monkeypatch):
    """API-shape test only: proves /api/search passes SearchResults.strict_total
    and .relaxed straight through to the JSON body, unmodified. This mocks
    search_documents, so it says nothing about whether strict_total actually
    equals total on any real (non-relaxed) code path — that contract is
    covered against the real search_documents regex path by
    tests/test_search_queries.py::test_regex_mode_strict_total_equals_total."""
    async def _fake_search_documents(*a, **k):
        return SearchResults(
            total=5, page=1, page_size=20,
            results=[_minimal_result()],
            strict_total=5,
        )
    monkeypatch.setattr(appmod, "search_documents", _fake_search_documents)
    c = _client([])
    r = c.get("/api/search?q=x&mode=regex")
    assert r.status_code == 200
    body = r.json()
    assert body["strict_total"] == body["total"]
    assert body["relaxed"] is False
