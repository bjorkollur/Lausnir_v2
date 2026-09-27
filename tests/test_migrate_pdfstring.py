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


def test_decode_whitespace_only_returns_empty_bytes():
    # base64.b64decode(..., validate=False) discards non-alphabet chars
    # (including whitespace), so a whitespace-only string decodes to b"".
    assert m.decode_pdf_string("   ") == b""


def test_decode_bare_data_url_prefix_returns_empty_bytes():
    # "data:application/pdf;base64," with nothing after the comma.
    assert m.decode_pdf_string("data:application/pdf;base64,") == b""


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


def test_decide_empty_takes_priority_over_missing():
    assert m.decide(False, None, m.sha256_hex(b""), is_empty=True) == "empty"


def test_decide_empty_takes_priority_even_when_disk_file_matches():
    # A 0-byte file already sitting on disk must NOT be treated as "equal" —
    # empty pdfString has always meant "no PDF", not "a 0-byte PDF".
    empty_sha = m.sha256_hex(b"")
    assert m.decide(True, empty_sha, empty_sha, is_empty=True) == "empty"


# ─── UPDATE_SQL ────────────────────────────────────────────────────────────

def test_update_sql_drops_pdfstring_and_adds_markers():
    sql = m.UPDATE_SQL
    assert "raw_api_data - 'pdfString'" in sql
    # Explicit text casts are required — asyncpg can't infer a bind parameter's
    # type when it's only ever used as a jsonb_build_object() argument
    # (IndeterminateDatatypeError). CAST(:x AS text), not :x::text: SQLAlchemy's
    # text() bind-parameter regex has a negative lookahead on a following ':'
    # specifically so postgres `::type` casts in raw SQL aren't misparsed as
    # bind params — which means `:sha::text` is silently NOT bound at all and
    # asyncpg then chokes on the literal ":sha::text" left in the SQL. Both
    # failure modes were hit running the real migration before this fix.
    assert "jsonb_build_object('pdf_sha256', CAST(:sha AS text), 'pdf_path', CAST(:rel AS text))" in sql
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


def test_write_atomic_still_round_trips_bytes_with_fsync(tmp_path):
    # write_atomic now flushes + fsyncs the temp file and best-effort fsyncs
    # the directory before/after the rename -- confirm it still round-trips
    # exact bytes and leaves no temp file behind.
    target = tmp_path / "sub" / "doc.pdf"
    data = bytes(range(256)) * 100  # non-trivial binary payload
    m.write_atomic(target, data)
    assert target.read_bytes() == data
    assert list(target.parent.glob(".tmp-migrate-*")) == []


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


# ─── process_doc: empty pdfString means "no PDF", never "missing" ─────────

def test_process_doc_empty_string_writes_no_file_and_nulls_markers(tmp_path, monkeypatch):
    _use_tmp_raw_dir(monkeypatch, tmp_path)
    result = m.process_doc("heradsdomstolar", "ext-empty-1", "HerdRvk_E-5-2020_D_01-01-2020", "", dry_run=False)
    assert result.outcome == "empty"
    assert result.sql_sha256 is None
    assert result.sql_rel_path is None
    written_path = tmp_path / "raw" / "heradsdomstolar" / "HerdRvk_E-5-2020_D_01-01-2020.pdf"
    assert not written_path.exists()


def test_process_doc_whitespace_only_string_is_empty_outcome(tmp_path, monkeypatch):
    _use_tmp_raw_dir(monkeypatch, tmp_path)
    result = m.process_doc("heradsdomstolar", "ext-empty-2", "HerdRvk_E-6-2020_D_01-01-2020", "   ", dry_run=False)
    assert result.outcome == "empty"
    written_path = tmp_path / "raw" / "heradsdomstolar" / "HerdRvk_E-6-2020_D_01-01-2020.pdf"
    assert not written_path.exists()


def test_process_doc_bare_data_url_prefix_is_empty_outcome(tmp_path, monkeypatch):
    _use_tmp_raw_dir(monkeypatch, tmp_path)
    result = m.process_doc("heradsdomstolar", "ext-empty-3", "HerdRvk_E-7-2020_D_01-01-2020",
                            "data:application/pdf;base64,", dry_run=False)
    assert result.outcome == "empty"
    written_path = tmp_path / "raw" / "heradsdomstolar" / "HerdRvk_E-7-2020_D_01-01-2020.pdf"
    assert not written_path.exists()


def test_process_doc_empty_string_with_existing_zero_byte_disk_file_is_still_empty(tmp_path, monkeypatch):
    # A 0-byte file already on disk must not be treated as "equal" (it would
    # match the empty json hash) -- it's still the "empty" outcome, and the
    # migration script must not delete the file itself.
    _use_tmp_raw_dir(monkeypatch, tmp_path)
    p = tmp_path / "raw" / "heradsdomstolar" / "HerdRvk_E-8-2020_D_01-01-2020.pdf"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"")
    result = m.process_doc("heradsdomstolar", "ext-empty-4", "HerdRvk_E-8-2020_D_01-01-2020", "", dry_run=False)
    assert result.outcome == "empty"
    assert result.zero_byte_disk_file is True
    assert p.exists()  # migration script never deletes


def test_process_doc_empty_dry_run_matches_non_dry_run_outcome(tmp_path, monkeypatch):
    _use_tmp_raw_dir(monkeypatch, tmp_path)
    result = m.process_doc("heradsdomstolar", "ext-empty-5", "HerdRvk_E-9-2020_D_01-01-2020", "", dry_run=True)
    assert result.outcome == "empty"


def test_process_doc_json_null_pdf_string_is_empty_outcome(tmp_path, monkeypatch):
    # raw_api_data->>'pdfString' yields Python None (not "") when the stored
    # JSON value is literally `null` -- the importer's "no PDF" case. This
    # must take the exact same "empty" outcome as "" (no file written,
    # pdfString key stripped, pdf_sha256/pdf_path left None), not "missing"
    # (which would write a bogus 0-byte file). This is the driver's actual
    # SELECT-reachable code path: `d.raw_api_data->>'pdfString' AS pdf_string`
    # in migrate() hands process_doc exactly this None value for such rows.
    _use_tmp_raw_dir(monkeypatch, tmp_path)
    result = m.process_doc("heradsdomstolar", "ext-empty-6", "HerdRvk_E-11-2020_D_01-01-2020", None, dry_run=False)
    assert result.outcome == "empty"
    assert result.sql_sha256 is None
    assert result.sql_rel_path is None
    written_path = tmp_path / "raw" / "heradsdomstolar" / "HerdRvk_E-11-2020_D_01-01-2020.pdf"
    assert not written_path.exists()


# ─── process_doc: read-back verification on the "missing" (write) path ────

def test_process_doc_write_verify_failed_when_disk_read_back_is_corrupted(tmp_path, monkeypatch):
    _use_tmp_raw_dir(monkeypatch, tmp_path)
    data = b"%PDF-1.5 real body"
    encoded = base64.b64encode(data).decode()

    # Simulate a write that silently corrupts the bytes on disk (e.g. a flaky
    # filesystem) by monkeypatching write_atomic to write something else.
    def corrupting_write(path, _data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"corrupted-on-disk")

    monkeypatch.setattr(m, "write_atomic", corrupting_write)

    result = m.process_doc("heradsdomstolar", "ext-corrupt", "HerdRvk_E-10-2020_D_01-01-2020",
                            encoded, dry_run=False)
    assert result.outcome == "error"
    assert "WRITE_VERIFY_FAILED" in result.error
    # The row must be left untouched -- caller only UPDATEs on non-error outcomes.
    written_path = tmp_path / "raw" / "heradsdomstolar" / "HerdRvk_E-10-2020_D_01-01-2020.pdf"
    assert written_path.read_bytes() == b"corrupted-on-disk"  # script never "fixes" it either
