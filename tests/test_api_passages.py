"""Contract tests for the passages endpoint and section_kind param, with a fake session."""
import pytest
from fastapi.testclient import TestClient

import engine.api.app as appmod
from engine.api.app import app, get_session


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
