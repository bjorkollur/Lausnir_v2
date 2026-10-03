# tests/test_models_journal.py
"""Schema for journals (spec 2026-10-03 §4): six document columns, two passage
columns, and the journal_issues table — checked on the ORM, no DB needed."""
from engine.database.models import Base, Document, JournalIssue, Passage


def test_document_has_journal_columns():
    cols = Document.__table__.c
    for name in ("volume", "issue", "page_start", "page_end", "lang", "page_offsets", "journal_issue_id"):
        assert name in cols, name
    assert str(cols["volume"].type).upper() == "SMALLINT"
    assert str(cols["page_start"].type).upper() == "INTEGER"
    assert cols["page_offsets"].type.__class__.__name__ == "JSONB"
    fk = next(iter(cols["journal_issue_id"].foreign_keys))
    assert fk.column.table.name == "journal_issues"
    assert fk.ondelete == "SET NULL"


def test_passage_has_page_columns():
    cols = Passage.__table__.c
    assert "page_from" in cols and "page_to" in cols
    assert str(cols["page_from"].type).upper() == "SMALLINT"


def test_journal_issues_table():
    t = JournalIssue.__table__
    assert t.name == "journal_issues"
    for name in ("id", "source_id", "year", "volume", "issue", "label", "file_path", "sha256",
                 "page_count", "toc_text", "manifest_path", "manifest_sha256", "status",
                 "confidence", "error", "created_at", "updated_at"):
        assert name in t.c, name
    assert t.c["status"].nullable is False
    assert t.c["manifest_path"].nullable is False
    assert "journal_issues" in Base.metadata.tables


def test_journal_issues_manifest_path_is_unique():
    # R4: later tasks upsert journal_issues by manifest_path, so it must be unique.
    t = JournalIssue.__table__
    unique_over_manifest = [
        ix for ix in t.indexes
        if ix.unique and [c.name for c in ix.columns] == ["manifest_path"]
    ]
    assert unique_over_manifest, "no unique index over journal_issues.manifest_path"
    assert any(ix.name == "uq_journal_issue_manifest" for ix in unique_over_manifest)
