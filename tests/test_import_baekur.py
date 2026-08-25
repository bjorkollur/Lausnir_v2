from unittest.mock import patch


def test_extract_text_uses_parse_pdf_when_text_layer_present():
    from scripts.import_baekur import extract_text
    with patch("scripts.import_baekur.parse_pdf", return_value="Alvöru texti") as mock_parse:
        with patch("scripts.import_baekur.docling_ocr_pdf") as mock_ocr:
            result = extract_text(b"%PDF-fake")
    mock_parse.assert_called_once_with(b"%PDF-fake", footnotes=False)
    mock_ocr.assert_not_called()
    assert result == "Alvöru texti"


def test_extract_text_passes_footnotes_flag_through():
    """Books carry academic footnotes; the flag must reach parse_pdf or they are
    extracted as detached number columns."""
    from scripts.import_baekur import extract_text
    with patch("scripts.import_baekur.parse_pdf", return_value="texti") as mock_parse:
        with patch("scripts.import_baekur.docling_ocr_pdf"):
            extract_text(b"%PDF-fake", footnotes=True)
    assert mock_parse.call_args_list[0].kwargs == {"footnotes": True}


def test_extract_text_keeps_plain_pass_when_footnote_pass_loses_text():
    """The footnote pass drops note lines it can't attach to a number; on some
    books that eats real text. Losing footnotes beats losing the book."""
    from scripts.import_baekur import extract_text
    full, truncated = "x" * 1000, "x" * 800
    with patch("scripts.import_baekur.parse_pdf",
               side_effect=[truncated, full]) as mock_parse:
        with patch("scripts.import_baekur.docling_ocr_pdf"):
            result = extract_text(b"%PDF-fake", footnotes=True)
    assert result == full
    assert mock_parse.call_count == 2


def test_extract_text_keeps_footnote_pass_when_length_holds():
    from scripts.import_baekur import extract_text
    with_fn, plain = "y" * 1000, "y" * 995
    with patch("scripts.import_baekur.parse_pdf", side_effect=[with_fn, plain]):
        with patch("scripts.import_baekur.docling_ocr_pdf"):
            result = extract_text(b"%PDF-fake", footnotes=True)
    assert result == with_fn


def test_extract_text_skips_the_comparison_pass_when_footnotes_are_off():
    """Court-style documents shouldn't pay for a second parse they never use."""
    from scripts.import_baekur import extract_text
    with patch("scripts.import_baekur.parse_pdf", return_value="texti") as mock_parse:
        with patch("scripts.import_baekur.docling_ocr_pdf"):
            extract_text(b"%PDF-fake")
    assert mock_parse.call_count == 1


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
        "authors": ["Páll Sigurðsson"],
        "isbn": "9780306406157",
        "publisher": "Bókaútgáfan Codex",
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
    assert doc.isbn == "9780306406157"
    assert doc.publisher == "Bókaútgáfan Codex"


def test_build_document_multiple_authors_gives_one_plaintiff_each():
    from scripts.import_baekur import build_document
    from engine.config.sources import get_config

    config = get_config("logfraedibaekur")
    meta = {
        "title": "Afmælisrit",
        "authors": ["Höfundur Einn", "Höfundur Tveir"],
        "isbn": "9789979825968",
        "publisher": None,
        "external_id": "9789979825968",
        "document_date": None,
    }
    doc = build_document(meta, "texti", __import__("uuid").uuid4(), config)
    assert doc.plaintiffs == [
        {"name": "Höfundur Einn", "lawyer": None},
        {"name": "Höfundur Tveir", "lawyer": None},
    ]


def test_build_document_no_author_gives_none_plaintiffs():
    from scripts.import_baekur import build_document
    from engine.config.sources import get_config

    config = get_config("logfraedibaekur")
    meta = {
        "title": "Ónefnd bók",
        "authors": None,
        "isbn": None,
        "publisher": None,
        "external_id": "onefnd_bok",
        "document_date": None,
    }
    doc = build_document(meta, "texti", __import__("uuid").uuid4(), config)
    assert doc.plaintiffs is None
