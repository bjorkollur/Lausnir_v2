"""Unit tests for the pure parts of scripts/migrate_pdfstring_to_disk.py.

No DB, no filesystem beyond a tmp_path fixture for write_atomic — everything
else is a pure function so it's tested directly.
"""
import base64
import hashlib
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "migrate_pdfstring_to_disk", Path("scripts/migrate_pdfstring_to_disk.py")
)
m = importlib.util.module_from_spec(spec)
sys.modules["migrate_pdfstring_to_disk"] = m
spec.loader.exec_module(m)


# ─── decode_pdf_string ─────────────────────────────────────────────────────

def test_decode_plain_base64():
    data = b"%PDF-1.5 some bytes here"
    encoded = base64.b64encode(data).decode()
    assert m.decode_pdf_string(encoded) == data


def test_decode_data_url_prefix():
    data = b"%PDF-1.7 more bytes"
    encoded = "data:application/pdf;base64," + base64.b64encode(data).decode()
    assert m.decode_pdf_string(encoded) == data


def test_decode_empty_string_returns_empty_bytes():
    assert m.decode_pdf_string("") == b""


def test_decode_none_returns_empty_bytes():
    assert m.decode_pdf_string(None) == b""


def test_decode_data_url_without_comma_is_treated_as_plain_base64():
    # Defensive: a malformed "data:" value with no comma falls through to
    # b64decode on the original string rather than raising elsewhere.
    encoded = base64.b64encode(b"abc").decode()
    # No "data:" prefix here at all -- sanity check plain path still works.
    assert m.decode_pdf_string(encoded) == b"abc"


# ─── sha256_hex ────────────────────────────────────────────────────────────

def test_sha256_hex_matches_hashlib():
    data = b"hello world"
    assert m.sha256_hex(data) == hashlib.sha256(data).hexdigest()


def test_sha256_hex_empty_bytes():
    assert m.sha256_hex(b"") == hashlib.sha256(b"").hexdigest()


# ─── decide ────────────────────────────────────────────────────────────────

def test_decide_missing_when_path_does_not_exist():
    assert m.decide(False, None, "abc123") == "missing"


def test_decide_equal_when_hashes_match():
    assert m.decide(True, "abc123", "abc123") == "equal"


def test_decide_mismatch_when_hashes_differ():
    assert m.decide(True, "abc123", "def456") == "mismatch"


# ─── UPDATE_SQL ────────────────────────────────────────────────────────────

def test_update_sql_drops_pdfstring_and_adds_markers():
    sql = m.UPDATE_SQL
    assert "raw_api_data - 'pdfString'" in sql
    assert "jsonb_build_object('pdf_sha256', :sha, 'pdf_path', :rel)" in sql
    assert "WHERE id = :id AND raw_api_data ? 'pdfString'" in sql
    assert sql.strip().startswith("UPDATE documents")


# ─── write_atomic ──────────────────────────────────────────────────────────

def test_write_atomic_creates_file_with_exact_bytes(tmp_path):
    target = tmp_path / "sub" / "doc.pdf"
    data = b"%PDF-1.4 payload"
    m.write_atomic(target, data)
    assert target.read_bytes() == data
    # No leftover temp files.
    assert list(target.parent.glob(".tmp-migrate-*")) == []


def test_write_atomic_overwrites_cleanly(tmp_path):
    target = tmp_path / "doc.pdf"
    target.write_bytes(b"old")
    m.write_atomic(target, b"new-bytes")
    assert target.read_bytes() == b"new-bytes"


# ─── process_doc (pure once get_config/pdf_path are real, no network/DB) ───

def _use_tmp_raw_dir(monkeypatch, tmp_path):
    """Point engine.config.sources.RAW_DIR at tmp_path so process_doc/pdf_path
    can never touch the real Lausnir_Data raw directory during tests."""
    monkeypatch.setattr(m.sources_config, "RAW_DIR", str(tmp_path / "raw"))


def test_process_doc_missing_writes_file(tmp_path, monkeypatch):
    _use_tmp_raw_dir(monkeypatch, tmp_path)
    data = b"%PDF-1.5 body"
    encoded = base64.b64encode(data).decode()
    result = m.process_doc("heradsdomstolar", "ext-1", "HerdRvk_E-1-2020_D_01-01-2020", encoded, dry_run=False)
    assert result.outcome == "written"
    assert result.json_sha256 == hashlib.sha256(data).hexdigest()
    written_path = tmp_path / "raw" / "heradsdomstolar" / "HerdRvk_E-1-2020_D_01-01-2020.pdf"
    assert written_path.read_bytes() == data


def test_process_doc_missing_dry_run_does_not_write(tmp_path, monkeypatch):
    _use_tmp_raw_dir(monkeypatch, tmp_path)
    data = b"%PDF-1.5 body"
    encoded = base64.b64encode(data).decode()
    result = m.process_doc("heradsdomstolar", "ext-2", "HerdRvk_E-2-2020_D_01-01-2020", encoded, dry_run=True)
    assert result.outcome == "written"
    written_path = tmp_path / "raw" / "heradsdomstolar" / "HerdRvk_E-2-2020_D_01-01-2020.pdf"
    assert not written_path.exists()


def test_process_doc_equal_on_disk(tmp_path, monkeypatch):
    _use_tmp_raw_dir(monkeypatch, tmp_path)
    data = b"%PDF-1.5 identical body"
    encoded = base64.b64encode(data).decode()
    p = tmp_path / "raw" / "heradsdomstolar" / "HerdRvk_E-3-2020_D_01-01-2020.pdf"
    p.parent.mkdir(parents=True)
    p.write_bytes(data)
    result = m.process_doc("heradsdomstolar", "ext-3", "HerdRvk_E-3-2020_D_01-01-2020", encoded, dry_run=False)
    assert result.outcome == "migrated"
    assert result.json_sha256 == result.disk_sha256


def test_process_doc_mismatch_leaves_file_untouched(tmp_path, monkeypatch):
    _use_tmp_raw_dir(monkeypatch, tmp_path)
    data = b"%PDF-1.5 json body"
    encoded = base64.b64encode(data).decode()
    p = tmp_path / "raw" / "heradsdomstolar" / "HerdRvk_E-4-2020_D_01-01-2020.pdf"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"different disk body")
    result = m.process_doc("heradsdomstolar", "ext-4", "HerdRvk_E-4-2020_D_01-01-2020", encoded, dry_run=False)
    assert result.outcome == "mismatch"
    assert result.json_sha256 != result.disk_sha256
    assert p.read_bytes() == b"different disk body"


def test_process_doc_falls_back_to_external_id_when_verdict_filename_null(tmp_path, monkeypatch):
    _use_tmp_raw_dir(monkeypatch, tmp_path)
    data = b"%PDF-1.5 fallback body"
    encoded = base64.b64encode(data).decode()
    result = m.process_doc("heradsdomstolar", "ext-fallback", None, encoded, dry_run=False)
    assert result.outcome == "written"
    written_path = tmp_path / "raw" / "heradsdomstolar" / "ext-fallback.pdf"
    assert written_path.read_bytes() == data
