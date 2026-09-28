"""SQL shape of the passage search path (no DB)."""
import pytest
from engine.search.queries import SearchError
from engine.search import passage_search
from engine.search.passage_search import (
    PASSAGE_CANDIDATE_DOCS, PASSAGE_RANK_FN, PASSAGE_RANK_STRATEGY, RANK_STRATEGIES,
    build_hits_sql, order_sql, search_by_passages, validate_section_kinds,
)


class _EmptyRowsResult:
    """Stand-in for the rows query result — returns no rows."""
    def mappings(self):
        return self
    def all(self):
        return []
    def scalar(self):
        return None


class _RecordingSession:
    """Records every executed SQL statement; used to prove a query was (or was
    not) issued, without a real database."""
    def __init__(self):
        self.calls: list[str] = []

    async def execute(self, stmt, params=None):
        self.calls.append(str(stmt))
        return _EmptyRowsResult()


def test_validate_section_kinds_accepts_known_rejects_unknown():
    assert validate_section_kinds(None) is None
    assert validate_section_kinds(["nidurstada", "domsord"]) == ["nidurstada", "domsord"]
    with pytest.raises(SearchError):
        validate_section_kinds(["nidurstada", "foo"])


def test_candidate_cap_constant():
    assert PASSAGE_CANDIDATE_DOCS == 2000


def test_hits_sql_prefilters_on_document_fts_and_groups_by_document():
    sql = build_hits_sql(tsq="plainto_tsquery('simple', :lemmas)", or_tsq=None,
                         doc_where=["d.document_date >= :date_from"], section_filter=False)
    assert "LIMIT :cand_limit" in sql
    assert "JOIN cand c ON c.id = p.document_id" in sql
    assert "ORDER BY doc_rank DESC" in sql
    assert "GROUP BY p.document_id" in sql
    assert "count(*) AS match_count" in sql and "best_passage_id" in sql
    assert "d.document_date >= :date_from" in sql


def test_hits_sql_adds_section_filter_when_requested():
    sql = build_hits_sql(tsq="plainto_tsquery('simple', :lemmas)", or_tsq=None,
                         doc_where=[], section_filter=True)
    assert "p.section_kind = ANY(:section_kinds)" in sql


def test_hits_sql_or_fallback_present_only_for_keyword():
    sql_with = build_hits_sql(tsq="plainto_tsquery('simple', :lemmas)",
                              or_tsq="to_tsquery('simple', :lemmas_or)",
                              doc_where=[], section_filter=False)
    assert "hits_or" in sql_with
    assert "NOT EXISTS" in sql_with
    assert "UNION ALL" in sql_with
    assert "colocated" in sql_with

    sql_without = build_hits_sql(tsq="plainto_tsquery('simple', :lemmas)", or_tsq=None,
                                 doc_where=[], section_filter=False)
    assert "hits_or" not in sql_without


@pytest.mark.parametrize("sort,first", [
    ("relevance", "h.tier ASC, (h.doc_rank + h.best_rank * ln(1 + h.match_count) + CASE WHEN h.colocated THEN 0.25 ELSE 0 END) DESC"),
    ("newest", "h.tier ASC, d.document_date DESC NULLS LAST, h.best_rank DESC"),
    ("oldest", "h.tier ASC, d.document_date ASC NULLS LAST, h.best_rank DESC"),
])
def test_order_sql(sort, first):
    assert order_sql(sort).startswith(first)


def test_default_rank_settings():
    assert PASSAGE_RANK_FN == "ts_rank"
    assert PASSAGE_RANK_STRATEGY == "breadth_coloc"


def test_rank_strategies_all_produce_order_sql():
    for strategy in RANK_STRATEGIES:
        sql = order_sql("relevance", strategy)
        assert sql.endswith("d.document_date DESC NULLS LAST, d.id")
    with pytest.raises(ValueError):
        order_sql("relevance", "not-a-strategy")


@pytest.mark.parametrize("sort,expected_cand_order", [
    ("relevance", "ORDER BY doc_rank DESC, d.id"),
    ("newest", "ORDER BY d.document_date DESC NULLS LAST, d.id"),
    ("oldest", "ORDER BY d.document_date ASC NULLS LAST, d.id"),
])
def test_cand_order_by_follows_sort(sort, expected_cand_order):
    """F2: 'cand' must not always rank by doc_rank — newest/oldest need the
    candidate cap itself ordered by date, otherwise the sort only ever picks
    among the best-ranked :cand_limit documents, not the newest/oldest overall."""
    sql = build_hits_sql(tsq="plainto_tsquery('simple', :lemmas)", or_tsq=None,
                         doc_where=[], section_filter=False, sort=sort)
    cand_cte = sql.split("hits_and AS")[0]
    assert expected_cand_order in cand_cte
    # doc_rank is still computed/selected regardless of sort (used as a tiebreaker
    # downstream in order_sql).
    assert "AS doc_rank" in cand_cte


def test_rank_fn_ts_rank_cd_applied(monkeypatch):
    monkeypatch.setattr(passage_search, "PASSAGE_RANK_FN", "ts_rank_cd")
    sql = build_hits_sql(tsq="plainto_tsquery('simple', :lemmas)", or_tsq=None,
                         doc_where=[], section_filter=False)
    assert "ts_rank_cd(" in sql
    assert "ts_rank(p.fts_is" not in sql


def test_rank_fn_rejects_unknown_value(monkeypatch):
    """F6: PASSAGE_RANK_FN must be validated inside build_hits_sql rather than
    silently interpolated into SQL — a typo or bad config flip should fail loud."""
    monkeypatch.setattr(passage_search, "PASSAGE_RANK_FN", "drop table")
    with pytest.raises(ValueError):
        build_hits_sql(tsq="plainto_tsquery('simple', :lemmas)", or_tsq=None,
                       doc_where=[], section_filter=False)


def test_hits_sql_unrelaxed_has_constant_tier_and_strict_prefilter():
    sql = build_hits_sql(tsq="to_tsquery('simple', :q_strict)", or_tsq=None, doc_where=[], section_filter=False)
    assert "0 AS tier" in sql and "CASE WHEN d.fts_is" not in sql
    assert "WHERE d.fts_is @@ to_tsquery('simple', :q_strict)" in sql
    assert "max(c.tier) AS tier" in sql


def test_hits_sql_relaxed_uses_any_prefilter_and_tier_case_before_limit():
    sql = build_hits_sql(tsq="to_tsquery('simple', :q_strict)", or_tsq="to_tsquery('simple', :q_any)",
                         doc_where=["d.document_date >= :date_from"], section_filter=False,
                         relax=("to_tsquery('simple', :q_any)", "to_tsquery('simple', :q_nminus1)"))
    assert "WHERE d.fts_is @@ to_tsquery('simple', :q_any) AND d.document_date >= :date_from" in sql
    assert "WHEN d.fts_is @@ to_tsquery('simple', :q_strict) THEN 0" in sql
    assert "WHEN d.fts_is @@ to_tsquery('simple', :q_nminus1) THEN 1" in sql
    cand = sql.split("hits_and")[0]
    assert cand.index("ORDER BY tier ASC") < cand.index("LIMIT :cand_limit")


def test_hits_sql_relaxed_without_nminus1_has_two_tiers():
    sql = build_hits_sql(tsq="T", or_tsq="A", doc_where=[], section_filter=False, relax=("A", None))
    assert "THEN 1" not in sql and "ELSE 2 END AS tier" in sql


@pytest.mark.parametrize("sort", ["relevance", "newest", "oldest"])
def test_order_sql_puts_tier_first(sort):
    assert order_sql(sort).startswith("h.tier ASC, ")


# ── Duplicate strict-count elision (spec 2026-09-28, Task 3 review) ──────────

async def test_unrelaxed_strict_total_skips_duplicate_count_query():
    """When strict_total is already known (unrelaxed keyword dispatch always
    computes it up front) and there's no section_kind filter, search_by_passages
    must reuse it as 'total' instead of re-running the identical strict count
    query against documents.fts_is."""
    session = _RecordingSession()
    res = await search_by_passages(
        session, tsq_fn="to_tsquery", tsq_param="q_strict",
        where=[], params={"q_strict": "a & b"},
        sort="relevance", page=1, page_size=20, section_kinds=None,
        strict_total=7,
    )
    assert len(session.calls) == 1  # only the page/rows query — no count query
    assert not any("FROM documents d WHERE d.fts_is @@" in c for c in session.calls)
    assert res.total == 7
    assert res.strict_total == 7
    assert res.relaxed is False


async def test_relaxed_path_still_runs_its_own_total_query():
    """Sanity check that the elision is specific to the unrelaxed case: with
    relax_params set, the any-lemma total query still runs."""
    session = _RecordingSession()
    await search_by_passages(
        session, tsq_fn="to_tsquery", tsq_param="q_strict",
        where=[], params={"q_strict": "a & b", "q_any": "a | b"},
        sort="relevance", page=1, page_size=20, section_kinds=None,
        or_tsq_param="q_any", relax_params=("q_any", None), strict_total=2,
    )
    assert any("FROM documents d WHERE d.fts_is @@" in c for c in session.calls)


async def test_section_kind_path_still_runs_its_own_total_query():
    """Sanity check that the elision doesn't apply when a section_kind filter
    is present, even with strict_total given — that count is capped by the
    candidate set and can't be assumed equal to strict_total."""
    session = _RecordingSession()
    await search_by_passages(
        session, tsq_fn="to_tsquery", tsq_param="q_strict",
        where=[], params={"q_strict": "a & b"},
        sort="relevance", page=1, page_size=20, section_kinds=["nidurstada"],
        strict_total=7,
    )
    assert any("SELECT count(*) FROM hits" in c for c in session.calls)
