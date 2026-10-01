import json
import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.config.sources import get_config

CONFIG = get_config("haestirettur")
SOURCE_ID = uuid.uuid4()


# ── Checkpoint helpers ────────────────────────────────────────────────────────

def test_load_checkpoint_defaults_on_missing_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from scripts.import_haestirettur import _load_checkpoint
    start, total, imported = _load_checkpoint()
    assert (start, total, imported) == (1, 0, 0)


def test_load_checkpoint_resumes_from_saved_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "checkpoints").mkdir()
    (tmp_path / "checkpoints" / "haestirettur.json").write_text(
        json.dumps({"last_completed_page": 47, "total_pages": 1221, "imported": 470})
    )
    from scripts.import_haestirettur import _load_checkpoint
    start, total, imported = _load_checkpoint()
    assert (start, total, imported) == (48, 1221, 470)


def test_save_checkpoint_writes_correct_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from scripts.import_haestirettur import _save_checkpoint
    _save_checkpoint(10, 1221, 100)
    data = json.loads((tmp_path / "checkpoints" / "haestirettur.json").read_text())
    assert data == {"last_completed_page": 10, "total_pages": 1221, "imported": 100}


def test_save_then_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from scripts.import_haestirettur import _load_checkpoint, _save_checkpoint
    _save_checkpoint(99, 500, 990)
    start, total, imported = _load_checkpoint()
    assert (start, total, imported) == (100, 500, 990)


# ── _get_build_id ─────────────────────────────────────────────────────────────

async def test_get_build_id_extracts_correctly():
    from scripts.import_haestirettur import _get_build_id

    mock_resp = MagicMock()
    mock_resp.text = 'stuff before {"buildId":"abc-123-xyz","page":"/domar"} stuff after'

    with patch("scripts.import_haestirettur.get_with_retry", new_callable=AsyncMock) as m:
        m.return_value = mock_resp
        result = await _get_build_id(MagicMock())

    assert result == "abc-123-xyz"


async def test_get_build_id_raises_when_marker_absent():
    from scripts.import_haestirettur import _get_build_id

    mock_resp = MagicMock()
    mock_resp.text = "no build id here"

    with patch("scripts.import_haestirettur.get_with_retry", new_callable=AsyncMock) as m:
        m.return_value = mock_resp
        with pytest.raises(ValueError, match="buildId"):
            await _get_build_id(MagicMock())


# ── _build_document ───────────────────────────────────────────────────────────

_LIST_ITEM = {
    "id": "haestirettur-domar-100",
    "title": "A ehf. gegn B hf.",
    "caseNumber": "E-42/2023",
    "verdictDate": "2023-06-01T00:00:00Z",
    "keywords": ["Kröfuréttur", "Samningslög"],
    "presentings": "Ágrip málsins.",
    "court": "Hæstiréttur",
}

_DETAIL = {
    "richText": "<p>Dómsorð</p><p>Stefndi greiði.</p>",
    "pdfString": "AAAA==",
    "resolutionLink": None,
}


def test_build_document_basic_fields():
    from scripts.import_haestirettur import _build_document
    from engine.database.models import Document

    doc = _build_document(_LIST_ITEM, _DETAIL, SOURCE_ID, CONFIG)

    assert isinstance(doc, Document)
    assert doc.external_id == "haestirettur-domar-100"
    assert doc.case_number == "E-42/2023"
    assert doc.court == "Hrd."
    assert doc.document_date == date(2023, 6, 1)
    assert doc.url == "https://island.is/domar/haestirettur-domar-100"
    assert doc.source_id == SOURCE_ID


def test_build_document_body_text_is_plain():
    from scripts.import_haestirettur import _build_document

    doc = _build_document(_LIST_ITEM, _DETAIL, SOURCE_ID, CONFIG)
    assert doc.body_text is not None
    assert "<p>" not in doc.body_text
    assert "Dómsorð" in doc.body_text


def test_build_document_parties_parsed():
    from scripts.import_haestirettur import _build_document

    doc = _build_document(_LIST_ITEM, _DETAIL, SOURCE_ID, CONFIG)
    assert doc.plaintiffs == [{"name": "A ehf.", "lawyer": None}]
    assert doc.defendants == [{"name": "B hf.", "lawyer": None}]


def test_build_document_raw_api_data_includes_pdf():
    from scripts.import_haestirettur import _build_document

    doc = _build_document(_LIST_ITEM, _DETAIL, SOURCE_ID, CONFIG)
    assert doc.raw_api_data["pdfString"] == "AAAA=="
    assert doc.raw_api_data["richText"] == _DETAIL["richText"]


def test_build_document_failed_detail_sets_validation_error():
    from scripts.import_haestirettur import _build_document

    err = ConnectionError("timeout")
    doc = _build_document(_LIST_ITEM, err, SOURCE_ID, CONFIG)

    assert doc.external_id == "haestirettur-domar-100"
    assert doc.body_text is None
    assert doc.validation_errors is not None
    assert any(e.get("field") == "detail_fetch" for e in doc.validation_errors)


def test_build_document_failed_detail_raw_is_list_item_only():
    from scripts.import_haestirettur import _build_document

    doc = _build_document(_LIST_ITEM, Exception("fail"), SOURCE_ID, CONFIG)
    assert "richText" not in doc.raw_api_data


# ── resolutionLink → appeal edges ─────────────────────────────────────────────

class _LinkSession:
    """Answers the lower-court lookup and records every INSERT's params."""

    def __init__(self, lower_id):
        self.lower_id = lower_id
        self.inserts: list[dict] = []

    async def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        if sql.startswith("INSERT INTO document_links"):
            relation = "appealed_to" if "'appealed_to'" in sql else "appealed_from"
            self.inserts.append({**params, "relation": relation})
            return MagicMock()
        result = MagicMock()
        result.fetchone.return_value = (self.lower_id,)
        return result


async def test_resolution_link_edges_follow_the_tier_invariant():
    """lower→higher carries 'appealed_to', higher→lower 'appealed_from' — the
    invariant check_link_orientation.py asserts. This writer had it the other
    way round, so every Hæstiréttur import added mis-oriented pairs."""
    from scripts.import_haestirettur import _link_via_resolution_link
    hrd = MagicMock(id=uuid.uuid4(), raw_api_data={"resolutionLink": "/domar/g-lower-1"})
    lower_id = uuid.uuid4()
    session = _LinkSession(lower_id)

    assert await _link_via_resolution_link(session, [hrd]) == 1

    edges = {(e["from_id"], e["to_id"]): e["relation"] for e in session.inserts}
    assert edges == {(lower_id, hrd.id): "appealed_to", (hrd.id, lower_id): "appealed_from"}
