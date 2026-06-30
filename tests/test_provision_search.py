"""Tests for provision filter in search_documents."""
import pytest
from engine.search.queries import _build_provision_filter


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
