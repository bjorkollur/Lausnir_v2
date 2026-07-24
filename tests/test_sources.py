"""Tests for source registry and scope catalog."""


def test_lagasafn_sources_registered():
    from engine.config.sources import SOURCE_REGISTRY
    for n in range(1, 49):
        key = f"lagasafn_{n:02d}"
        assert key in SOURCE_REGISTRY, f"{key} missing from SOURCE_REGISTRY"
        cfg = SOURCE_REGISTRY[key]
        assert cfg.instance_tier == 0
        assert cfg.parse_parties == "none"
        assert "Lög" in cfg.verdict_types_allowed


def test_catalog_valid_with_lagasafn():
    from engine.config.source_groups import validate_catalog
    validate_catalog()  # should not raise


def test_logfraedibaekur_source_registered():
    from engine.config.sources import SOURCE_REGISTRY
    assert "logfraedibaekur" in SOURCE_REGISTRY
    cfg = SOURCE_REGISTRY["logfraedibaekur"]
    assert cfg.display_name == "Lögfræðibækur"
    assert cfg.abbreviation == "Bók."
    assert cfg.parse_parties == "none"
    assert cfg.verdict_type_default == "Bók"
    assert cfg.verdict_types_allowed == ["Bók"]
    assert cfg.case_number_is_title is True


def test_dropfolder_dir_is_under_data_dir():
    from engine.config.sources import DROPFOLDER_DIR, _DATA_DIR
    assert DROPFOLDER_DIR == f"{_DATA_DIR}/dropfolder"


def test_logfraedibaekur_in_baekur_category():
    from engine.config.source_groups import SCOPE_TREE
    baekur = next(c for c in SCOPE_TREE if c["key"] == "baekur")
    leaf_keys = {leaf["key"] for leaf in baekur["children"]}
    assert "logfraedibaekur" in leaf_keys


def test_logfraedibaekur_is_chunked_scope():
    from engine.search.queries import _scope_is_chunked
    assert _scope_is_chunked(["logfraedibaekur"]) is True
