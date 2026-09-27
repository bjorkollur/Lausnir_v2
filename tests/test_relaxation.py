"""Pure query construction and the relaxation decision."""
import pytest
from engine.search import relaxation
from engine.search.relaxation import KeywordQueries, build_keyword_queries, should_relax


def test_single_lemma():
    kq = build_keyword_queries("krafa")
    assert kq == KeywordQueries(strict="krafa", nminus1=None, any="krafa", n=1)


def test_two_lemmas_nminus1_equals_any():
    kq = build_keyword_queries("gæsluvarðhald rannsókn")
    assert kq.strict == "gæsluvarðhald & rannsókn"
    assert kq.nminus1 == "gæsluvarðhald | rannsókn"
    assert kq.any == "gæsluvarðhald | rannsókn"
    assert kq.n == 2


def test_three_lemmas():
    kq = build_keyword_queries("a b c")
    assert kq.strict == "a & b & c"
    assert kq.nminus1 == "(a & b) | (a & c) | (b & c)"
    assert kq.any == "a | b | c"


def test_four_lemmas_has_four_subsets():
    kq = build_keyword_queries("a b c d")
    assert kq.nminus1.count("|") == 3 and kq.nminus1.count("&") == 8


def test_nine_lemmas_skips_nminus1():
    kq = build_keyword_queries(" ".join(f"l{i}" for i in range(9)))
    assert kq.n == 9 and kq.nminus1 is None and kq.any.count("|") == 8


def test_multi_token_lemma_and_extra_whitespace():
    kq = build_keyword_queries("  e mál   krafa ")
    assert kq.strict == "e & mál & krafa" and kq.n == 3


def test_empty_raises():
    with pytest.raises(ValueError):
        build_keyword_queries("   ")


def test_should_relax_boundaries(monkeypatch):
    monkeypatch.setattr(relaxation, "RELAX_BELOW", 10)
    assert should_relax(9, 2) is True
    assert should_relax(10, 2) is False
    assert should_relax(0, 1) is False
    assert should_relax(0, 2, threshold=0) is False
    assert should_relax(3, 3, threshold=5) is True
