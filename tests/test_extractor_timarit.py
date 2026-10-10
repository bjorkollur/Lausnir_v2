from datetime import date

from engine.config.sources import get_config
from engine.processors.extractor import Extractor

RAW = {
    "title": "Hvenær er vanhæfi smitandi?", "authors": ["Kjartan Bjarni Björgvinsson"], "author_titles": [],
    "article_type": "Fræðigrein", "page_start": 7, "page_end": 47, "lang": "is",
    "volume": 60, "issue": "1", "year": 2010, "date": None, "summary": "Ágrip.", "keywords": ["vanhæfi"],
    "pdf_text": "Texti greinar.", "page_offsets": [{"char": 0, "pdf_page": 0, "printed": 7}],
    "source_filename": "Tímarit Lögfræðinga/TL 2010_01.pdf", "pdf_pages": [8, 48],
    "provenance": {"title": "leitir"}, "checks": {}, "confidence": 0.97,
    "leitir_record_id": "991001868109706886", "xlsx_row": None, "ocr_source": "embedded", "date_precision": "year",
}


def test_extract_maps_bibliographic_fields():
    f = Extractor(get_config("timarit_logfraedinga")).extract(RAW)
    assert f["case_number"] == "Hvenær er vanhæfi smitandi?"
    assert f["plaintiffs"] == [{"name": "Kjartan Bjarni Björgvinsson", "lawyer": None}]
    assert f["court"] == "TL" and f["verdict_type"] == "Fræðigrein"
    assert f["document_date"] == date(2010, 1, 1)
    assert (f["volume"], f["issue"], f["page_start"], f["page_end"], f["lang"]) == (60, "1", 7, 47, "is")
    assert f["page_offsets"] == RAW["page_offsets"]
    assert f["body_text"] == "Texti greinar." and f["summary"] == "Ágrip." and f["keywords"] == ["vanhæfi"]
    assert f["instance_tier"] is None and f["defendants"] is None and f["lower_body_text"] is None
    assert "pdf_text" not in f["raw_api_data"] and f["raw_api_data"]["leitir_record_id"] == "991001868109706886"


def test_extract_exact_date_wins_over_year():
    f = Extractor(get_config("ulfljotur_vefrit")).extract({**RAW, "date": "2022-01-21", "year": 2022})
    assert f["document_date"] == date(2022, 1, 21)


def test_extract_unknown_article_type_falls_back_to_source_default():
    f = Extractor(get_config("logmannabladid")).extract({**RAW, "article_type": None})
    assert f["verdict_type"] == "Annað"


def test_an_initial_keeps_its_period_in_the_author():
    # Task 23f: manifests analysed before the fix hold "E A" and "E. A" (TL 1951); the document names "E. A."
    f = Extractor(get_config("timarit_logfraedinga")).extract({**RAW, "authors": ["E A", "E. A", "Sindri M Stephensen"]})
    assert [p["name"] for p in f["plaintiffs"]] == ["E. A.", "E. A.", "Sindri M. Stephensen"]


def test_all_journals_registered():
    from engine.config.sources import JOURNAL_SOURCES
    for s in JOURNAL_SOURCES:
        Extractor(get_config(s)).extract(RAW)   # must not raise NotImplementedError
