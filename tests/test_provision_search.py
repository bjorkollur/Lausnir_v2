"""Tests for provision filter in search_documents."""
import pytest
from engine.search.queries import _build_provision_filter, _PROVISION_NOISE


def test_provision_noise_set_exists():
    """_PROVISION_NOISE must contain the key noise tokens."""
    assert "mgr" in _PROVISION_NOISE
    assert "gr" in _PROVISION_NOISE
    assert "nr" in _PROVISION_NOISE


def test_full_provision_filter():
    frag, params = _build_provision_filter("19/1940", gr=218, sfx=None, mgr=1)
    assert "cited_provisions @>" in frag
    assert "prov_filter" in params
    import json
    obj = json.loads(params["prov_filter"])
    assert obj == [{"law": "19/1940", "gr": 218, "mgr": 1}]


def test_provision_filter_no_mgr():
    frag, params = _build_provision_filter("19/1940", gr=218, sfx=None, mgr=None)
    import json
    obj = json.loads(params["prov_filter"])
    assert obj == [{"law": "19/1940", "gr": 218}]


def test_provision_filter_law_only():
    frag, params = _build_provision_filter("19/1940", gr=None, sfx=None, mgr=None)
    import json
    obj = json.loads(params["prov_filter"])
    assert obj == [{"law": "19/1940"}]


def test_provision_filter_with_suffix():
    frag, params = _build_provision_filter("19/1940", gr=218, sfx="a", mgr=1)
    import json
    obj = json.loads(params["prov_filter"])
    assert obj == [{"law": "19/1940", "gr": 218, "mgr": 1, "sfx": "a"}]


def test_returns_sql_fragment():
    frag, params = _build_provision_filter("19/1940", gr=218, sfx=None, mgr=1)
    assert "d.cited_provisions" in frag
    assert ":prov_filter" in frag


def test_bare_law_number_parses():
    """Bare law number '91/1991' should produce a law-only filter."""
    import re
    provision = "91/1991"
    m = re.search(r'\b(\d+/\d{4})\b', provision)
    assert m is not None
    frag, params = _build_provision_filter(m.group(1), None, None, None)
    import json
    obj = json.loads(params["prov_filter"])
    assert obj == [{"law": "91/1991"}]


# ── parse_provision_query tests ───────────────────────────────────────────────

from engine.search.queries import parse_provision_query


def test_parse_mgr_before_gr():
    result = parse_provision_query("2. mgr. 218. gr. laga nr. 19/1940")
    assert result == ("19/1940", 218, None, 2)


def test_parse_gr_only():
    result = parse_provision_query("218. gr. laga nr. 19/1940")
    assert result == ("19/1940", 218, None, None)


def test_parse_suffix():
    result = parse_provision_query("218. gr. a. laga nr. 19/1940")
    assert result == ("19/1940", 218, "a", None)


def test_parse_mgr_suffix():
    result = parse_provision_query("1. mgr. 218. gr. a. laga nr. 19/1940")
    assert result == ("19/1940", 218, "a", 1)


def test_parse_compound_law_name():
    result = parse_provision_query("3. mgr. 70. gr. almennra hegningarlaga nr. 19/1940")
    assert result == ("19/1940", 70, None, 3)


def test_parse_bare_law_returns_none():
    assert parse_provision_query("19/1940") is None


def test_parse_empty_returns_none():
    assert parse_provision_query("") is None
