"""Unit tests for _build_text_filter and _order_clause."""
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


def test_or_query_joins_lemmas_with_pipe():
    from engine.search.queries import _or_query
    assert _or_query("gæsluvarðhald rannsókn") == "gæsluvarðhald | rannsókn"
    assert _or_query("gæsluvarðhald") == "gæsluvarðhald"
