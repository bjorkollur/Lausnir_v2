"""Guard tests for the hierarchical search scope catalog.

Ensures every configured source lands in exactly one category, that the tree
resolves cleanly, and that the verdict-type drill-down keys are wired correctly.
"""
from engine.config.source_groups import (
    SCOPE_TREE,
    _RESOLUTIONS,
    catalog,
    validate_catalog,
)
from engine.config.sources import SOURCE_REGISTRY


def test_categories_partition_registry():
    validate_catalog()  # raises on duplicate / unknown / missing


def test_tree_has_four_categories():
    keys = [c["key"] for c in catalog()]
    assert keys == ["domstolar", "stjornsysla", "nefndir", "baekur"]


def test_every_source_reachable_from_tree():
    reached = set()
    for res in _RESOLUTIONS.values():
        reached.update(res[1])  # short_names
    assert set(SOURCE_REGISTRY) <= reached


def test_court_drilldown_keys_present():
    for key in (
        "haestirettur_domar", "haestirettur_urskurdir", "malskotsbeidnir",
        "landsrettur_domar", "landsrettur_urskurdir",
        "heradsdomar_domar", "heradsdomar_urskurdir",
    ):
        assert key in _RESOLUTIONS, f"missing scope key {key}"


def test_verdict_drilldown_resolution():
    kind, sources, vts = _RESOLUTIONS["haestirettur_domar"]
    assert kind == "vt"
    assert sources == ["haestirettur"]
    assert vts == ["Dómur"]


def test_hugverkastofa_in_stjornsysla():
    stjornsysla = next(c for c in SCOPE_TREE if c["key"] == "stjornsysla")
    leaf_keys = {leaf["key"] for leaf in stjornsysla["children"]}
    assert "hugverkastofa" in leaf_keys


def test_skemman_in_baekur():
    baekur = next(c for c in SCOPE_TREE if c["key"] == "baekur")
    assert [leaf["key"] for leaf in baekur["children"]] == ["logfraediritgerdir"]
