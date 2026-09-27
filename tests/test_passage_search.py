"""SQL shape of the passage search path (no DB)."""
import pytest
from engine.search.queries import SearchError
from engine.search.passage_search import build_hits_sql, order_sql, validate_section_kinds


def test_validate_section_kinds_accepts_known_rejects_unknown():
    assert validate_section_kinds(None) is None
    assert validate_section_kinds(["nidurstada", "domsord"]) == ["nidurstada", "domsord"]
    with pytest.raises(SearchError):
        validate_section_kinds(["nidurstada", "foo"])


def test_hits_sql_prefilters_on_document_fts_and_groups_by_document():
    sql = build_hits_sql(tsq="plainto_tsquery('simple', :lemmas)",
                         doc_where=["d.document_date >= :date_from"], section_filter=False)
    assert "d.fts_is @@ plainto_tsquery('simple', :lemmas)" in sql
    assert "p.fts_is @@ plainto_tsquery('simple', :lemmas)" in sql
    assert "d.document_date >= :date_from" in sql
    assert "GROUP BY p.document_id" in sql
    assert "count(*) AS match_count" in sql and "best_passage_id" in sql
    assert "section_kind" not in sql


def test_hits_sql_adds_section_filter_when_requested():
    sql = build_hits_sql(tsq="plainto_tsquery('simple', :lemmas)", doc_where=[], section_filter=True)
    assert "p.section_kind = ANY(:section_kinds)" in sql


@pytest.mark.parametrize("sort,first", [
    ("relevance", "h.best_rank DESC, h.match_count DESC"),
    ("newest", "d.document_date DESC NULLS LAST, h.best_rank DESC"),
    ("oldest", "d.document_date ASC NULLS LAST, h.best_rank DESC"),
])
def test_order_sql(sort, first):
    assert order_sql(sort).startswith(first)
