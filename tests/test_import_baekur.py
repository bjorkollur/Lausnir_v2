from unittest.mock import patch


def test_extract_text_uses_parse_pdf_when_text_layer_present():
    from scripts.import_baekur import extract_text
    with patch("scripts.import_baekur.parse_pdf", return_value="Alvöru texti") as mock_parse:
        with patch("scripts.import_baekur.docling_ocr_pdf") as mock_ocr:
            result = extract_text(b"%PDF-fake")
    mock_parse.assert_called_once_with(b"%PDF-fake")
    mock_ocr.assert_not_called()
    assert result == "Alvöru texti"


def test_extract_text_falls_back_to_ocr_when_text_layer_empty():
    from scripts.import_baekur import extract_text
    with patch("scripts.import_baekur.parse_pdf", return_value=""):
        with patch("scripts.import_baekur.docling_ocr_pdf", return_value="OCR texti") as mock_ocr:
            result = extract_text(b"%PDF-fake")
    mock_ocr.assert_called_once_with(b"%PDF-fake", timeout=1800)
    assert result == "OCR texti"


def test_extract_text_returns_empty_string_when_both_fail():
    from scripts.import_baekur import extract_text
    with patch("scripts.import_baekur.parse_pdf", return_value=""):
        with patch("scripts.import_baekur.docling_ocr_pdf", return_value=None):
            result = extract_text(b"%PDF-fake")
    assert result == ""


def test_book_stem_transliterates_and_caps_length():
    from scripts.import_baekur import book_stem
    stem = book_stem("Skaðabótaréttur á Íslandi og nágrannalöndum, ítarleg umfjöllun")
    assert stem == stem.encode("ascii", "ignore").decode("ascii")  # pure ASCII
    assert len(stem) <= 40
    assert stem.startswith("Skadabotarettur")


def test_book_stem_empty_title_returns_book_fallback():
    from scripts.import_baekur import book_stem
    assert book_stem("") == "book"


def test_build_document_maps_metadata_and_body():
    from datetime import date
    from engine.config.sources import get_config
    from scripts.import_baekur import build_document

    config = get_config("logfraedibaekur")
    meta = {
        "title": "Kröfuréttur I",
        "author": "Páll Sigurðsson",
        "isbn": "9780306406157",
        "external_id": "9780306406157",
        "document_date": date(1985, 1, 1),
    }
    doc = build_document(meta, "Meginmál bókarinnar.", __import__("uuid").uuid4(), config)

    assert doc.external_id == "9780306406157"
    assert doc.case_number == "Kröfuréttur I"
    assert doc.plaintiffs == [{"name": "Páll Sigurðsson", "lawyer": None}]
    assert doc.body_text == "Meginmál bókarinnar."
    assert doc.court == "Bók."
    assert doc.document_date == date(1985, 1, 1)


def test_build_document_no_author_gives_none_plaintiffs():
    from scripts.import_baekur import build_document
    from engine.config.sources import get_config

    config = get_config("logfraedibaekur")
    meta = {
        "title": "Ónefnd bók",
        "author": None,
        "isbn": None,
        "external_id": "onefnd_bok",
        "document_date": None,
    }
    doc = build_document(meta, "texti", __import__("uuid").uuid4(), config)
    assert doc.plaintiffs is None
