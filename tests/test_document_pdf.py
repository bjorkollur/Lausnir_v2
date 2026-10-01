"""The reader's PDF view: get_document's has_pdf and /api/document/{id}/pdf.

Both looked only under external_id, but court PDFs are stored under
verdict_filename — so no district-court or Landsréttur document offered its PDF
(24 280 + 6 294 files on disk, measured 2026-10-01).
"""
import uuid

import pytest
from fastapi.testclient import TestClient

import engine.config.sources as sources_config
from engine.api.app import app, get_session
from engine.config.sources import get_config
from engine.search.queries import get_document
from tests.test_api_citations import DOC_ROW, KeyedSession, _Result

VF = "HerdRvk_E-4047-2018_D_27-11-2020"
COURT_ROW = {**DOC_ROW, "source": "heradsdomstolar", "source_display": "Héraðsdómstólar",
             "external_id": "g-9b7fbb27", "verdict_filename": VF}


@pytest.fixture(autouse=True)
def _tmp_raw_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(sources_config, "RAW_DIR", str(tmp_path / "raw"))
    yield
    app.dependency_overrides.clear()


def _put(source: str, name: str):
    path = get_config(source).pdf_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.5 test")
    return path


def _doc_session(row):
    return KeyedSession(doc=_Result(first=row), links=_Result(rows=[]),
                        out=_Result(rows=[]), in_=_Result(rows=[]), unresolved=_Result(first=0))


async def test_has_pdf_finds_a_court_pdf_by_verdict_filename():
    _put("heradsdomstolar", VF)
    doc = await get_document(_doc_session(COURT_ROW), COURT_ROW["id"])
    assert doc["has_pdf"] is True


async def test_has_pdf_is_false_when_nothing_is_stored():
    doc = await get_document(_doc_session(COURT_ROW), COURT_ROW["id"])
    assert doc["has_pdf"] is False


class _PdfRowSession:
    """Answers the single lookup the PDF endpoint needs."""

    def __init__(self, row):
        self._row = row

    async def execute(self, stmt, params=None, *a, **k):
        return _Result(first=self._row)


def _client(row):
    async def _fake():
        yield _PdfRowSession(row)
    app.dependency_overrides[get_session] = _fake
    return TestClient(app)


def test_pdf_endpoint_serves_a_court_pdf_stored_under_verdict_filename():
    _put("heradsdomstolar", VF)
    r = _client(COURT_ROW).get(f"/api/document/{COURT_ROW['id']}/pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content == b"%PDF-1.5 test"


def test_pdf_endpoint_404s_when_nothing_is_stored():
    r = _client(COURT_ROW).get(f"/api/document/{COURT_ROW['id']}/pdf")
    assert r.status_code == 404


def test_pdf_endpoint_404s_for_an_unknown_document():
    r = _client(None).get(f"/api/document/{uuid.uuid4()}/pdf")
    assert r.status_code == 404


def test_pdf_endpoint_400s_for_a_malformed_id():
    assert _client(None).get("/api/document/not-a-uuid/pdf").status_code == 400
