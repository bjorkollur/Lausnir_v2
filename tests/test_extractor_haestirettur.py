from datetime import date
import pytest
from engine.config.sources import get_config
from engine.processors.extractor import Extractor, _html_to_plain, _detect_verdict_type

CONFIG = get_config("haestirettur")


# ── _html_to_plain ────────────────────────────────────────────────────────────

def test_html_to_plain_strips_tags():
    result = _html_to_plain("<p>Hello <b>world</b></p>")
    assert "<p>" not in result and "<b>" not in result
    assert "Hello" in result and "world" in result


def test_html_to_plain_content_across_paragraphs():
    result = _html_to_plain("<p>First</p><p>Second</p>")
    assert "First" in result and "Second" in result


def test_html_to_plain_none_input():
    assert _html_to_plain(None) is None


def test_html_to_plain_blank_input():
    assert _html_to_plain("") is None
    assert _html_to_plain("   ") is None


# ── _detect_verdict_type ─────────────────────────────────────────────────────

def test_detect_urskurdaford_heading():
    assert _detect_verdict_type("Málsatvik\n\nÚrskurðarorð\n\nHafnað.", []) == "Úrskurður"


def test_detect_urskurdar_verb():
    assert _detect_verdict_type("Dómurinn úrskurðar að kröfunni sé hafnað.", []) == "Úrskurður"


def test_detect_case_insensitive():
    assert _detect_verdict_type("úrskurðarorð\n\nHafnað.", []) == "Úrskurður"


def test_detect_returns_none_for_domur():
    assert _detect_verdict_type("Dómsorð\n\nStefndi greiði 500.000 kr.", []) is None


def test_detect_returns_none_for_empty():
    assert _detect_verdict_type(None, []) is None
    assert _detect_verdict_type("", []) is None


# ── _extract_haestirettur ─────────────────────────────────────────────────────

def _raw(**overrides) -> dict:
    base = {
        "id": "haestirettur-domar-test-1",
        "title": "Jón Jónsson gegn Sigríður Sigurðardóttir",
        "caseNumber": "E-123/2024",
        "verdictDate": "2024-05-05T00:00:00Z",
        "keywords": ["Kröfuréttur", "Skaðabótamál"],
        "presentings": "Reifun málsins.",
        "court": "Hæstiréttur",
    }
    return {**base, **overrides}


def test_extract_strips_html_from_rich_text():
    result = Extractor(CONFIG).extract(_raw(richText="<p>Dómsorð</p><p>Stefndi greiði.</p>"))
    assert "<p>" not in result["body_text"]
    assert "Dómsorð" in result["body_text"]


def test_extract_detects_urskurdur_from_rich_text():
    result = Extractor(CONFIG).extract(_raw(richText="<p>Úrskurðarorð</p><p>Hafnað.</p>"))
    assert result["verdict_type"] == "Úrskurður"


def test_extract_defaults_to_domur():
    result = Extractor(CONFIG).extract(_raw(richText="<p>Dómsorð</p><p>Stefndi greiði.</p>"))
    assert result["verdict_type"] == "Dómur"


def test_extract_fallback_to_text_key():
    result = Extractor(CONFIG).extract(_raw(text="Dómsorð\nStefndi greiði."))
    assert result["body_text"] == "Dómsorð\nStefndi greiði."


def test_extract_parses_parties_from_title():
    result = Extractor(CONFIG).extract(_raw(richText="<p>body</p>"))
    assert result["plaintiffs"] == [{"name": "Jón Jónsson", "lawyer": None}]
    assert result["defendants"] == [{"name": "Sigríður Sigurðardóttir", "lawyer": None}]


def test_extract_parses_verdict_date():
    result = Extractor(CONFIG).extract(_raw(richText="<p>body</p>"))
    assert result["document_date"] == date(2024, 5, 5)


def test_extract_uses_presentings_as_summary():
    result = Extractor(CONFIG).extract(_raw(richText="<p>body</p>"))
    assert result["summary"] == "Reifun málsins."


def test_extract_raw_api_data_includes_pdf_string():
    result = Extractor(CONFIG).extract(_raw(richText="<p>b</p>", pdfString="AAAA=="))
    assert result["raw_api_data"]["pdfString"] == "AAAA=="
