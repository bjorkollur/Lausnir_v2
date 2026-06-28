"""Unit tests for _build_text_filter and _order_clause."""
import pytest
from engine.search.queries import _build_text_filter, _order_clause


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


def test_order_clause_proximity_allows_relevance():
    result = _order_clause("proximity", True, "relevance", "ts_rank(x,y)")
    assert "ts_rank" in result


def test_order_clause_exact_overrides_relevance_to_newest():
    result = _order_clause("exact", True, "relevance", "0::real")
    assert "document_date DESC" in result
    assert "0::real" not in result


def test_order_clause_newest():
    result = _order_clause("exact", True, "newest", "0::real")
    assert "document_date DESC" in result


def test_order_clause_oldest():
    result = _order_clause("keyword", True, "oldest", "ts_rank(x,y)")
    assert "document_date ASC" in result


# ── Provision query parser ────────────────────────────────────────────────────

def test_parse_provision_query_gr_first():
    from engine.search.queries import parse_provision_query
    assert parse_provision_query("3. gr. 33/1944") == ("33/1944", 3, None)
    assert parse_provision_query("12. gr. laga nr. 91/1991") == ("91/1991", 12, None)


def test_parse_provision_query_law_first():
    from engine.search.queries import parse_provision_query
    assert parse_provision_query("33/1944, 3. gr.") == ("33/1944", 3, None)
    assert parse_provision_query("nr. 91/1991 12. gr.") == ("91/1991", 12, None)


def test_parse_provision_query_with_mgr():
    from engine.search.queries import parse_provision_query
    assert parse_provision_query("218. gr. 1. mgr. 19/1940") == ("19/1940", 218, 1)
    assert parse_provision_query("218. gr. 2. mgr. 19/1940") == ("19/1940", 218, 2)
    assert parse_provision_query("19/1940 218. gr. 1. mgr.") == ("19/1940", 218, 1)


def test_parse_provision_query_no_match():
    from engine.search.queries import parse_provision_query
    assert parse_provision_query("samningur") is None
    assert parse_provision_query("kaupsamningur 2024") is None
