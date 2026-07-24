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


def test_logfraedibaekur_validates_clean_when_complete():
    from datetime import date
    from engine.config.sources import get_config
    from engine.database.models import Document
    from engine.processors.extractor import Extractor
    from engine.processors.validator import validate
    import uuid

    config = get_config("logfraedibaekur")
    raw = {
        "title": "Kröfuréttur I",
        "author": "Páll Sigurðsson",
        "isbn": "9780306406157",
        "document_date": date(1985, 1, 1),
        "source_filename": "krofurettur.pdf",
        "pdf_text": "x" * 300,  # over the 200-char minimum
    }
    fields = Extractor(config).extract(raw)
    doc = Document(id=uuid.uuid4(), source_id=uuid.uuid4(), external_id="9780306406157", **fields)
    errors = validate(doc, config)
    # Books never have keywords — expected, non-blocking
    fields_with_errors = {e["field"] for e in errors}
    assert fields_with_errors == {"keywords"}


def test_logfraedibaekur_flags_missing_document_date_without_blocking():
    from engine.config.sources import get_config
    from engine.database.models import Document
    from engine.processors.extractor import Extractor
    from engine.processors.validator import validate
    import uuid

    config = get_config("logfraedibaekur")
    raw = {
        "title": "Óþekkt bók",
        "author": None,
        "isbn": None,
        "document_date": None,
        "source_filename": "ohefdbaerabok.pdf",
        "pdf_text": "x" * 300,
    }
    fields = Extractor(config).extract(raw)
    doc = Document(id=uuid.uuid4(), source_id=uuid.uuid4(), external_id="ohefdbaerabok", **fields)
    errors = validate(doc, config)
    fields_with_errors = {e["field"] for e in errors}
    assert "document_date" in fields_with_errors
    assert "keywords" in fields_with_errors  # books never have keywords — expected, non-blocking
