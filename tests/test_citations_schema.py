"""Schema contract for citations (alembic 0004) and case-number normalisation."""
import importlib.util
import pathlib

from sqlalchemy import inspect

from engine.database.models import Base, Citation, Document, DocumentLink
from engine.processors.citation_resolver import norm_case_number


def test_citation_model_columns():
    cols = {c.name: c for c in Citation.__table__.columns}
    assert set(cols) == {"id", "from_doc_id", "layer", "char_start", "char_end", "raw_text",
                         "target_court", "target_case_number", "target_date", "target_verdict",
                         "to_doc_id", "status", "method", "confidence", "created_at"}
    assert cols["to_doc_id"].nullable and cols["target_case_number"].nullable
    assert not cols["raw_text"].nullable and not cols["status"].nullable
    uniques = [tuple(c.name for c in u.columns) for u in Citation.__table__.constraints
               if u.__class__.__name__ == "UniqueConstraint"]
    assert ("from_doc_id", "layer", "char_start") in uniques
    fks = {fk.column.table.name: fk.ondelete for fk in Citation.__table__.foreign_keys}
    assert fks == {"documents": "CASCADE"} or set(fks) == {"documents"}


def test_document_has_citation_hash_and_link_index():
    assert "citation_hash" in {c.name for c in Document.__table__.columns}
    idx = {i.name: tuple(c.name for c in i.columns) for i in DocumentLink.__table__.indexes}
    assert idx["ix_link_to_rel"] == ("to_doc_id", "relation")


def test_migration_0004_declares_objects():
    p = pathlib.Path("alembic/versions/0004_citations.py")
    src = p.read_text(encoding="utf-8")
    assert 'revision = "0004"' in src and 'down_revision = "0003"' in src
    for needle in ('"citations"', "citation_hash", "ix_link_to_rel", "ix_cit_to", "ix_cit_target", "uq_cit_doc_layer_start"):
        assert needle in src, needle
    assert "timezone=True" not in src
    assert '"created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False' in src
    spec = importlib.util.spec_from_file_location("m0004", p)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    assert callable(mod.upgrade) and callable(mod.downgrade)


def test_norm_case_number():
    assert norm_case_number("055/2001") == "55/2001"
    assert norm_case_number("243 /2002") == "243/2002"
    assert norm_case_number("E-0012/2020") == "E-12/2020"
    assert norm_case_number("e-12/2020") == "E-12/2020"
    assert norm_case_number(" 7/2022 ") == "7/2022"
    assert norm_case_number("2023-65") == "2023-65"
    assert norm_case_number("0/2020") == "0/2020"
    assert norm_case_number(None) is None
    assert norm_case_number("") is None
