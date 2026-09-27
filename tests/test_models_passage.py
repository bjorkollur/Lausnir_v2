"""Passage ORM model shape (mirrors test_models_chunk.py)."""
from sqlalchemy import inspect as sa_inspect
from engine.database.models import Document, Passage


def test_table_name():
    assert Passage.__tablename__ == "passages"


def test_columns():
    cols = {c.key for c in sa_inspect(Passage).columns}
    assert cols >= {"id", "document_id", "ordinal", "layer", "section_path", "section_kind",
                    "para_from", "para_to", "char_start", "char_end", "text", "word_count", "fts_is"}
    assert "embedding" not in cols          # added by a later spec, on purpose


def test_indexes_and_unique():
    t = Passage.__table__
    assert {i.name for i in t.indexes} >= {"ix_passage_doc", "ix_passage_fts_is", "ix_passage_section_kind"}
    assert "uq_passage_doc_ordinal" in {c.name for c in t.constraints if hasattr(c, "columns")}


def test_document_has_passage_hash():
    assert "passage_hash" in {c.key for c in sa_inspect(Document).columns}
