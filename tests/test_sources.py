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
