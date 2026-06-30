"""Test that cited_provisions column exists on Document model."""
from sqlalchemy import inspect as sa_inspect
from engine.database.models import Document


def test_cited_provisions_column_exists():
    mapper = sa_inspect(Document)
    col_names = {c.key for c in mapper.columns}
    assert "cited_provisions" in col_names


def test_cited_provisions_is_jsonb():
    from sqlalchemy.dialects.postgresql import JSONB
    mapper = sa_inspect(Document)
    col = next(c for c in mapper.columns if c.key == "cited_provisions")
    assert isinstance(col.type, JSONB)
