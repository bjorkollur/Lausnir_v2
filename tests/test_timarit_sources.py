from engine.config.sources import (
    JOURNAL_SOURCES, SOURCE_REGISTRY, TIMARIT_DROPFOLDER_DIR, _DATA_DIR, get_config,
)
from engine.config.source_groups import SCOPE_TREE, _leaf_sources, validate_catalog

EXPECTED = {
    "timarit_logfraedinga": ("TL", "hefti", 1951),
    "ulfljotur": ("Úlfljótur", "tbl.", 1947),
    "ulfljotur_vefrit": ("Úlfljótur vefrit", None, None),
    "logmannabladid": ("Lögmannablaðið", "tbl.", 1995),
    "logretta": ("Lögrétta", "tbl.", 2004),
    "logfraedingur": ("Lögfræðingur", "tbl.", None),
    "logbru": ("Lögbrú", "tbl.", None),
    "stjornmal_stjornsysla": ("Stjórnmál og stjórnsýsla", "tbl.", 2005),
    "rannsoknir_lagadeild": ("Rannsóknir í félagsvísindum", None, None),
    "fraedigreinar_ymsar": ("Fræðigrein", None, None),
}


def test_ten_journal_sources_registered_as_journals():
    assert tuple(EXPECTED) == JOURNAL_SOURCES
    for short, (abbr, term, base) in EXPECTED.items():
        cfg = get_config(short)
        assert cfg.kind == "journal", short
        assert cfg.abbreviation == abbr
        assert cfg.issue_term == term
        assert cfg.volume_base_year == base
        assert cfg.case_number_is_title is True
        assert cfg.has_footnotes is True
        assert cfg.parse_parties == "none"
        assert cfg.lang_default == "is"
        assert set(cfg.verdict_types_allowed) == {
            "Fræðigrein", "Ritstjórnargrein", "Ritdómur", "Viðtal", "Frétt", "Dómareifun", "Annað"}


def test_defaults_for_existing_kinds():
    assert get_config("haestirettur").kind == "ruling"
    assert get_config("logfraedibaekur").kind == "book"
    assert get_config("logfraediritgerdir").kind == "book"
    assert get_config("logmannabladid").verdict_type_default == "Annað"
    assert get_config("timarit_logfraedinga").verdict_type_default == "Fræðigrein"
    assert get_config("timarit_logfraedinga").citation_name == "Tímarit lögfræðinga"


def test_dropfolder_under_data_dir():
    assert TIMARIT_DROPFOLDER_DIR == f"{_DATA_DIR}/dropfolder_timarit"


def test_timarit_group_in_tree_and_catalog_valid():
    node = next(c for c in SCOPE_TREE if c["key"] == "timarit")
    assert node["label"] == "Tímarit og fræðigreinar"
    assert {leaf["key"] for leaf in node["children"]} == set(JOURNAL_SOURCES)
    validate_catalog()
    # resolve_scope_keys does not exist in source_groups; _leaf_sources is the
    # module's key -> sources helper (the "timarit" node resolves to its leaves).
    assert set(_leaf_sources(node)) == set(JOURNAL_SOURCES)
    assert len(_leaf_sources(node)) == 10
    assert set(JOURNAL_SOURCES) <= set(SOURCE_REGISTRY)
