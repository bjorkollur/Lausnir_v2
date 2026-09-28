"""Unit tests for _build_text_filter and _order_clause."""
from engine.search.queries import _build_text_filter, _order_clause


class FakeResult:
    """Stand-in for the SQLAlchemy Result returned by session.execute()."""

    def __init__(self, scalar_value=None, mapping_rows=None):
        self._scalar_value = scalar_value
        self._mapping_rows = mapping_rows or []

    def scalar(self):
        return self._scalar_value

    def mappings(self):
        return self

    def all(self):
        return self._mapping_rows


class FakeSession:
    """Records every session.execute() call (SQL text + params) and returns a
    scripted result. Used to test the relaxation dispatch in queries.py without
    a real database — only .scalar()/.mappings().all() are ever read off the
    result by the code under test here."""

    def __init__(self, scalar_value=None, mapping_rows=None):
        self.scalar_value = scalar_value
        self.mapping_rows = mapping_rows or []
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, stmt, params=None):
        self.calls.append((str(stmt), dict(params or {})))
        return FakeResult(scalar_value=self.scalar_value, mapping_rows=self.mapping_rows)


def test_exact_single_word():
    frags, params = _build_text_filter("exact", ["gæsluvarðhald"], None, 5)
    assert len(frags) == 1
    assert "pat_0" in params
    assert params["pat_0"] == r"\mgæsluvarðhald\M"
    assert "~* :pat_0" in frags[0]


def test_exact_two_words_ands_both():
    frags, params = _build_text_filter("exact", ["a", "b"], None, 5)
    assert len(frags) == 2  # AND of two conditions
    assert "pat_0" in params and "pat_1" in params
    assert params["pat_0"] == r"\ma\M"
    assert params["pat_1"] == r"\mb\M"


def test_prefix():
    frags, params = _build_text_filter("prefix", ["gæslu"], None, 5)
    assert params["pat_0"] == r"\mgæslu"


def test_substring():
    frags, params = _build_text_filter("substring", ["hald"], None, 5)
    assert params["pat_0"] == "hald"


def test_any_two_words():
    frags, params = _build_text_filter("any", ["dómur", "úrskurður"], None, 5)
    assert len(frags) == 1
    assert params["pattern"] == "(dómur|úrskurður)"


def test_proximity_two_words():
    frags, params = _build_text_filter("proximity", ["gæsluvarðhald", "rannsókn"], None, 5)
    assert len(frags) == 1
    assert "prox_q" in params
    # Union of distances 1..N in both directions
    assert "<1>" in params["prox_q"]
    assert "<5>" in params["prox_q"]
    assert "<6>" not in params["prox_q"]  # N=5, so no <6>
    # Both lemmas present
    assert "gæsluvarðhald" in params["prox_q"]
    assert "rannsókn" in params["prox_q"]


def test_proximity_custom_n():
    frags, params = _build_text_filter("proximity", ["a", "b"], None, 10)
    assert "<10>" in params["prox_q"]
    assert "<11>" not in params["prox_q"]  # N=10, so no <11>


def test_proximity_single_word_no_chevron():
    frags, params = _build_text_filter("proximity", ["gæsluvarðhald"], None, 5)
    assert "<" not in params["prox_q"]  # single word, no proximity operator


def test_empty_words_returns_nothing():
    frags, params = _build_text_filter("exact", [], None, 5)
    assert frags == []
    assert params == {}


def test_proximity_hyphenated_word_sanitized():
    """Hyphenated input like 'e-mál' lemmatizes to 'e mál' — internal spaces must become ' & '."""
    import re as re_
    frags, params = _build_text_filter("proximity", ["e-mál"], None, 5)
    assert len(frags) == 1
    prox_q = params["prox_q"]
    # No bare space between word characters (all spaces must be around valid operators)
    assert not re_.search(r'\w \w', prox_q), f"Bare space in tsquery: {prox_q!r}"


def test_order_clause_relevance_falls_back_to_newest():
    """No mode that reaches _order_clause carries an FTS rank (keyword/proximity
    return early via search_by_passages), so relevance always falls back."""
    result = _order_clause("relevance")
    assert result == "d.document_date DESC NULLS LAST, d.id"


def test_order_clause_newest():
    result = _order_clause("newest")
    assert result == "d.document_date DESC NULLS LAST, d.id"


def test_order_clause_oldest():
    result = _order_clause("oldest")
    assert result == "d.document_date ASC NULLS LAST, d.id"


# ── Provision query parser ────────────────────────────────────────────────────

def test_parse_provision_query_gr_first():
    from engine.search.queries import parse_provision_query
    assert parse_provision_query("3. gr. 33/1944") == ("33/1944", 3, None, None)
    assert parse_provision_query("12. gr. laga nr. 91/1991") == ("91/1991", 12, None, None)


def test_parse_provision_query_law_first():
    from engine.search.queries import parse_provision_query
    assert parse_provision_query("33/1944, 3. gr.") == ("33/1944", 3, None, None)
    assert parse_provision_query("nr. 91/1991 12. gr.") == ("91/1991", 12, None, None)


def test_parse_provision_query_with_mgr():
    from engine.search.queries import parse_provision_query
    assert parse_provision_query("218. gr. 1. mgr. 19/1940") == ("19/1940", 218, None, 1)
    assert parse_provision_query("218. gr. 2. mgr. 19/1940") == ("19/1940", 218, None, 2)
    assert parse_provision_query("19/1940 218. gr. 1. mgr.") == ("19/1940", 218, None, 1)


def test_parse_provision_query_with_suffix():
    from engine.search.queries import parse_provision_query
    assert parse_provision_query("218. gr. a. 19/1940") == ("19/1940", 218, "a", None)
    assert parse_provision_query("218. gr. a. 1. mgr. 19/1940") == ("19/1940", 218, "a", 1)
    assert parse_provision_query("19/1940 218. gr. b. 2. mgr.") == ("19/1940", 218, "b", 2)
    assert parse_provision_query("218. gr. c. 19/1940") == ("19/1940", 218, "c", None)


def test_parse_provision_query_no_match():
    from engine.search.queries import parse_provision_query
    assert parse_provision_query("samningur") is None
    assert parse_provision_query("kaupsamningur 2024") is None


def test_build_keyword_filter_basic():
    from engine.search.queries import _build_keyword_filter
    frag, params = _build_keyword_filter("skaðabætur")
    assert frag == "d.keywords::text ILIKE :keyword_pattern"
    assert params == {"keyword_pattern": "%skaðabætur%"}


def test_build_keyword_filter_uses_named_param():
    from engine.search.queries import _build_keyword_filter
    frag, params = _build_keyword_filter("forsjá")
    assert ":keyword_pattern" in frag
    assert "keyword_pattern" in params
    assert params["keyword_pattern"] == "%forsjá%"


def test_provision_noise_query_is_filter_only_in_both_impls():
    """'2. mgr. 218. gr. laga nr. 19/1940' lemmatises to noise only; with a provision
    filter present the text part must be dropped, whichever impl is active."""
    from engine.search.queries import _text_is_noise_for_provision
    assert _text_is_noise_for_provision("2. mgr. 218. gr. laga nr. 19/1940", provision="218. gr. 19/1940")
    assert not _text_is_noise_for_provision("líkamsárás 218. gr.", provision="218. gr. 19/1940")
    assert not _text_is_noise_for_provision("2. mgr. 218. gr.", provision=None)


# ── F4: section_kind rejected outside passage modes ──────────────────────────

async def test_section_kind_rejected_for_non_passage_mode():
    """mode=exact&section_kind=domsord must 400, not silently ignore the filter.
    scope=None means resolve_scope never touches the session, so this raises
    before any DB access."""
    import pytest
    from engine.search.queries import SearchError, search_documents
    with pytest.raises(SearchError):
        await search_documents(None, q="x", mode="exact", section_kind=["domsord"])


async def test_section_kind_rejected_when_keyword_query_is_provision_noise():
    """A keyword query that lemmatizes to only provision noise ('mgr','gr','nr')
    with a provision filter present degenerates into a document-level browse
    (never reaches search_by_passages) — section_kind can't be honored there."""
    import pytest
    from engine.search.queries import SearchError, search_documents
    with pytest.raises(SearchError):
        await search_documents(
            None, q="2. mgr. 218. gr. laga nr. 19/1940", mode="keyword",
            provision="218. gr. 19/1940", section_kind=["domsord"])


async def test_section_kind_rejected_when_keyword_query_is_empty():
    """No query text at all also degenerates keyword into a plain browse."""
    import pytest
    from engine.search.queries import SearchError, search_documents
    with pytest.raises(SearchError):
        await search_documents(None, q="", mode="keyword", section_kind=["domsord"])


async def test_section_kind_allowed_for_real_keyword_query():
    """Sanity check: a real keyword query with section_kind must reach
    search_by_passages (and NOT raise) — proves the F4 guard doesn't
    over-trigger on the normal path."""
    from engine.search import queries as queries_mod

    called = {}

    async def _fake_search_by_passages(session, **kwargs):
        called.update(kwargs)
        return queries_mod.SearchResults(total=0, page=1, page_size=20, results=[])

    orig = queries_mod.search_by_passages
    queries_mod.search_by_passages = _fake_search_by_passages
    try:
        session = FakeSession(scalar_value=0)  # strict count for the dispatch decision
        result = await queries_mod.search_documents(
            session, q="gæsluvarðhald", mode="keyword", section_kind=["domsord"])
        assert result.total == 0
        assert called["section_kinds"] == ["domsord"]
    finally:
        queries_mod.search_by_passages = orig


# ── Relaxation dispatch (spec 2026-09-28) ─────────────────────────────────────

async def test_keyword_dispatch_relaxes_when_strict_count_below_threshold(monkeypatch):
    """Strict count (3) below RELAX_BELOW (10) with 3 lemmas: relax_params carries
    both the any- and all-but-one query params, and search_by_passages' params
    contain all three tsquery strings built from the lemmas."""
    from engine.search import queries as queries_mod, relaxation

    monkeypatch.setattr(relaxation, "RELAX_BELOW", 10)
    monkeypatch.setattr(queries_mod, "lemmatize_query", lambda q: "a b c")

    captured = {}

    async def fake_search_by_passages(session, **kwargs):
        captured.update(kwargs)
        return queries_mod.SearchResults(total=0, page=1, page_size=20, results=[])

    monkeypatch.setattr(queries_mod, "search_by_passages", fake_search_by_passages)

    session = FakeSession(scalar_value=3)
    await queries_mod.search_documents(session, q="ignored", mode="keyword")

    assert captured["strict_total"] == 3
    assert captured["or_tsq_param"] == "q_any"
    assert captured["relax_params"] == ("q_any", "q_nminus1")
    params = captured["params"]
    assert params["q_strict"] == "a & b & c"
    assert params["q_any"] == "a | b | c"
    assert params["q_nminus1"] == "(a & b) | (a & c) | (b & c)"
    # The strict count itself was queried against to_tsquery, not plainto_tsquery.
    assert "to_tsquery" in session.calls[0][0]
    assert session.calls[0][1]["q_strict"] == "a & b & c"


async def test_keyword_dispatch_no_relax_when_strict_count_at_threshold(monkeypatch):
    """Strict count exactly at RELAX_BELOW must NOT relax (should_relax is a
    strict '<'), but the OR-fallback stage is still wired up for n >= 2."""
    from engine.search import queries as queries_mod, relaxation

    monkeypatch.setattr(relaxation, "RELAX_BELOW", 10)
    monkeypatch.setattr(queries_mod, "lemmatize_query", lambda q: "a b c")

    captured = {}

    async def fake_search_by_passages(session, **kwargs):
        captured.update(kwargs)
        return queries_mod.SearchResults(total=0, page=1, page_size=20, results=[])

    monkeypatch.setattr(queries_mod, "search_by_passages", fake_search_by_passages)

    session = FakeSession(scalar_value=10)
    await queries_mod.search_documents(session, q="ignored", mode="keyword")

    assert captured["strict_total"] == 10
    assert captured["relax_params"] is None
    assert captured["or_tsq_param"] == "q_any"
    assert "q_nminus1" not in captured["params"]


async def test_single_lemma_never_relaxes(monkeypatch):
    """A single-lemma query never relaxes and never gets an OR-fallback stage,
    regardless of how low the strict count is."""
    from engine.search import queries as queries_mod, relaxation

    monkeypatch.setattr(relaxation, "RELAX_BELOW", 10)
    monkeypatch.setattr(queries_mod, "lemmatize_query", lambda q: "a")

    captured = {}

    async def fake_search_by_passages(session, **kwargs):
        captured.update(kwargs)
        return queries_mod.SearchResults(total=0, page=1, page_size=20, results=[])

    monkeypatch.setattr(queries_mod, "search_by_passages", fake_search_by_passages)

    session = FakeSession(scalar_value=0)
    await queries_mod.search_documents(session, q="ignored", mode="keyword")

    assert captured["or_tsq_param"] is None
    assert captured["relax_params"] is None
    assert "q_any" not in captured["params"]


async def test_facets_always_count_strict(monkeypatch):
    """I2 ruling (2026-09-28, spec decision 6 amended): facet_counts no longer
    relaxes, ever — it always filters with the strict (all-lemmas) query,
    exactly like before relaxed search existed. Even a strict count of 2 (well
    below RELAX_BELOW) must not switch to the any-lemma query, and no separate
    strict-count query is run any more (one call total: the facets query
    itself)."""
    from engine.search import queries as queries_mod, relaxation

    monkeypatch.setattr(relaxation, "RELAX_BELOW", 10)
    monkeypatch.setattr(queries_mod, "lemmatize_query", lambda q: "a b")

    session = FakeSession(scalar_value=2)
    await queries_mod.facet_counts(session, q="ignored", mode="keyword")

    assert len(session.calls) == 1
    facets_sql = session.calls[-1][0]
    assert ":q_strict" in facets_sql
    assert ":q_any" not in facets_sql
    assert session.calls[-1][1]["q_strict"] == "a & b"


async def test_facets_use_strict_query_regardless_of_count(monkeypatch):
    """Same as above with a large strict count — behaviour is identical either
    way now, since facets never relax."""
    from engine.search import queries as queries_mod, relaxation

    monkeypatch.setattr(relaxation, "RELAX_BELOW", 10)
    monkeypatch.setattr(queries_mod, "lemmatize_query", lambda q: "a b")

    session = FakeSession(scalar_value=50)
    await queries_mod.facet_counts(session, q="ignored", mode="keyword")

    facets_sql = session.calls[-1][0]
    assert ":q_strict" in facets_sql
    assert ":q_any" not in facets_sql
    assert session.calls[-1][1]["q_strict"] == "a & b"


# ── I1: strict_total == total on every non-relaxed path ──────────────────────

async def test_regex_mode_strict_total_equals_total():
    """I1: regex/exact/prefix/substring/any/proximity and filter-only browse are
    all non-relaxed paths — strict_total must equal total and relaxed must be
    False. Exercised against the real search_documents regex path (not a mock)
    with a FakeSession returning a count and an empty page."""
    from engine.search.queries import search_documents

    session = FakeSession(scalar_value=3, mapping_rows=[])
    res = await search_documents(session, q="gæsluvarðhald", mode="regex")
    assert res.total == 3
    assert res.strict_total == res.total
    assert res.relaxed is False


# ── M6: token hygiene — search_documents falls into filter-only browse ───────

async def test_keyword_search_falls_back_to_browse_when_lemmas_are_all_operators(monkeypatch):
    """build_keyword_queries raises ValueError when every token strips down to
    nothing; search_documents must catch it and degrade to a filter-only
    browse, exactly like an empty/non-lemmatisable query — not propagate."""
    from engine.search import queries as queries_mod

    monkeypatch.setattr(queries_mod, "lemmatize_query", lambda q: "& | (")

    session = FakeSession(scalar_value=0, mapping_rows=[])
    res = await queries_mod.search_documents(session, q="ignored", mode="keyword")
    assert res.total == 0
    assert res.strict_total == 0
    assert res.relaxed is False
