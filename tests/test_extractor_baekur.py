from datetime import date

from engine.config.sources import get_config
from engine.processors.extractor import Extractor

CONFIG = get_config("logfraedibaekur")


def _raw(**overrides) -> dict:
    base = {
        "title": "Kröfuréttur I",
        "author": "Páll Sigurðsson",
        "isbn": "9780306406157",
        "document_date": date(1985, 1, 1),
        "source_filename": "krofurettur.pdf",
        "pdf_text": "Meginmál bókarinnar hér.",
    }
    return {**base, **overrides}


def test_extract_maps_title_to_case_number():
    result = Extractor(CONFIG).extract(_raw())
    assert result["case_number"] == "Kröfuréttur I"


def test_extract_maps_author_to_plaintiffs():
    result = Extractor(CONFIG).extract(_raw())
    assert result["plaintiffs"] == [{"name": "Páll Sigurðsson", "lawyer": None}]


def test_extract_no_author_gives_none_plaintiffs():
    result = Extractor(CONFIG).extract(_raw(author=None))
    assert result["plaintiffs"] is None


def test_extract_uses_config_court_and_verdict_type():
    result = Extractor(CONFIG).extract(_raw())
    assert result["court"] == "Bók."
    assert result["verdict_type"] == "Bók"


def test_extract_passes_through_document_date():
    result = Extractor(CONFIG).extract(_raw())
    assert result["document_date"] == date(1985, 1, 1)


def test_extract_body_text_from_pdf_text():
    result = Extractor(CONFIG).extract(_raw())
    assert result["body_text"] == "Meginmál bókarinnar hér."


def test_extract_no_body_text_gives_none():
    result = Extractor(CONFIG).extract(_raw(pdf_text=None))
    assert result["body_text"] is None


def test_extract_raw_api_data_excludes_pdf_text():
    result = Extractor(CONFIG).extract(_raw())
    assert "pdf_text" not in result["raw_api_data"]
    assert result["raw_api_data"]["isbn"] == "9780306406157"


def test_extract_instance_tier_and_defendants_are_none():
    result = Extractor(CONFIG).extract(_raw())
    assert result["instance_tier"] is None
    assert result["defendants"] is None
