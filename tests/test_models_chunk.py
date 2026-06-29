"""Test DocumentChunk model exists with expected columns."""
from engine.database.models import DocumentChunk
from sqlalchemy import inspect as sa_inspect


def test_chunk_table_name():
    assert DocumentChunk.__tablename__ == "document_chunks"


def test_chunk_columns():
    mapper = sa_inspect(DocumentChunk)
    cols = {c.key for c in mapper.columns}
    assert "id" in cols
    assert "document_id" in cols
    assert "chunk_index" in cols
    assert "chunk_text" in cols
    assert "fts_is" in cols


def test_chunk_indexes():
    table = DocumentChunk.__table__
    index_names = {idx.name for idx in table.indexes}
    assert "ix_chunk_doc_id" in index_names
    assert "ix_chunk_fts_is" in index_names


def test_chunk_unique_constraint():
    table = DocumentChunk.__table__
    uq_names = {c.name for c in table.constraints if hasattr(c, 'columns')}
    assert "uq_chunk_doc_idx" in uq_names
