"""Pure query construction and the relaxation decision."""
import pytest
from engine.search import relaxation
from engine.search.relaxation import KeywordQueries, build_keyword_queries, should_relax


def test_single_lemma():
    kq = build_keyword_queries("krafa")
    assert kq == KeywordQueries(strict="krafa", nminus1=None, any="krafa", n=1)


def test_two_lemmas_nminus1_equals_any():
    """Ruling 2026-09-28 (M5): for n == 2, tier 1 (all-but-one) is identical to
    tier 2 (any) — nminus1 is None so there is no separate tier-1 stage; a
    two-lemma relaxed result only ever shows tier 0/2 ('sum orðin')."""
    kq = build_keyword_queries("gæsluvarðhald rannsókn")
    assert kq.strict == "gæsluvarðhald & rannsókn"
    assert kq.nminus1 is None
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


# ── M4: RELAX_BELOW / RELAX_CAND_LIMIT guard ──────────────────────────────────

def test_should_relax_raises_when_relax_below_not_less_than_cand_limit(monkeypatch):
    """t0 (strict matches) is uncapped by design; relaxation must imply
    strict_total < RELAX_CAND_LIMIT, which only holds if RELAX_BELOW is
    strictly less than RELAX_CAND_LIMIT. A misconfiguration (or an eval-sweep
    override that only flips one of the two) must fail loud, not silently
    relax past the cap."""
    monkeypatch.setattr(relaxation, "RELAX_BELOW", 300)
    monkeypatch.setattr(relaxation, "RELAX_CAND_LIMIT", 300)
    with pytest.raises(ValueError):
        should_relax(5, 2)


def test_should_relax_guard_passes_for_default_configuration():
    assert relaxation.RELAX_BELOW < relaxation.RELAX_CAND_LIMIT
    should_relax(5, 2)  # must not raise


# ── M6: token hygiene ──────────────────────────────────────────────────────

def test_build_keyword_queries_strips_tsquery_operator_chars():
    kq = build_keyword_queries("kr(a)fa d&óm|ur")
    assert kq.strict == "krafa & dómur"
    assert kq.n == 2


def test_build_keyword_queries_drops_tokens_that_become_empty():
    kq = build_keyword_queries("krafa & dómur")
    assert kq.strict == "krafa & dómur"
    assert kq.n == 2


def test_build_keyword_queries_raises_when_nothing_remains_after_stripping():
    with pytest.raises(ValueError):
        build_keyword_queries("&&& |||")
