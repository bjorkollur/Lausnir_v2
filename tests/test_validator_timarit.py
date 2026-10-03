from datetime import date

from engine.config.sources import get_config
from engine.database.models import Document
from engine.processors.validator import validate

TL = get_config("timarit_logfraedinga")


def _doc(**over):
    d = dict(case_number="Titill", court="TL", verdict_type="Fræðigrein", document_date=date(2010, 1, 1),
             plaintiffs=[{"name": "A B", "lawyer": None}], body_text="x" * 300, page_start=7, page_end=47, keywords=["a"])
    d.update(over)
    return Document(**d)


def _fields(errors):
    return {e["field"] for e in errors}


def test_clean_article_validates():
    assert validate(_doc(), TL) == []


def test_missing_author_is_an_error_for_fraedigrein_but_not_for_frett():
    assert "plaintiffs" in _fields(validate(_doc(plaintiffs=None), TL))
    assert "plaintiffs" not in _fields(validate(_doc(plaintiffs=None, verdict_type="Frétt"), TL))


def test_page_order_and_title_checked():
    assert "page_start" in _fields(validate(_doc(page_start=50, page_end=47), TL))
    assert "case_number" in _fields(validate(_doc(case_number=None), TL))


def test_no_case_number_shape_or_keyword_rules_for_journals():
    errs = validate(_doc(case_number="37/1993 og annað", keywords=None), TL)
    assert "keywords" not in _fields(errs) and "case_number" not in _fields(errs)
