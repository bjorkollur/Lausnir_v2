from datetime import date

from engine.config.sources import get_config
from engine.database.models import Document
from engine.processors.renderer import to_markdown, to_urlausn, verdict_filename

TL = get_config("timarit_logfraedinga")
ULF = get_config("ulfljotur")
VEF = get_config("ulfljotur_vefrit")
YMSAR = get_config("fraedigreinar_ymsar")
RANN = get_config("rannsoknir_lagadeild")
LMB = get_config("logmannabladid")


def _doc(**over):
    base = dict(
        case_number="Hvenær er vanhæfi smitandi?", court="TL", verdict_type="Fræðigrein",
        document_date=date(2010, 1, 1), volume=60, issue="1", page_start=7, page_end=47,
        plaintiffs=[{"name": "Kjartan Bjarni Björgvinsson", "lawyer": None}],
        body_text="Fyrsta síða.\n\nÖnnur síða.", external_id="2010-60-1-7",
        page_offsets=[{"char": 0, "pdf_page": 0, "printed": 7}, {"char": 14, "pdf_page": 1, "printed": 8}],
    )
    base.update(over)
    return Document(**base)


def test_urlausn_full_bibliographic_form():
    assert to_urlausn(_doc(), TL) == (
        "Kjartan Bjarni Björgvinsson: „Hvenær er vanhæfi smitandi?“ "
        "Tímarit lögfræðinga, 60. árg. 1. hefti (2010), bls. 7–47")


def test_urlausn_ulfljotur_uses_tbl_and_three_authors_become_ofl():
    d = _doc(court="Úlfljótur", volume=57, issue="2", page_start=165, page_end=192,
             plaintiffs=[{"name": "A B", "lawyer": None}, {"name": "C D", "lawyer": None}, {"name": "E F", "lawyer": None}],
             case_number="Það sem barninu er fyrir bestu", document_date=date(2004, 1, 1))
    assert to_urlausn(d, ULF) == "A B o.fl.: „Það sem barninu er fyrir bestu“ Úlfljótur, 57. árg. 2. tbl. (2004), bls. 165–192"


def test_urlausn_two_authors_both_named_and_missing_parts_omitted():
    d = _doc(plaintiffs=[{"name": "A B", "lawyer": None}, {"name": "C D", "lawyer": None}], issue=None, page_end=None)
    assert to_urlausn(d, TL) == "A B og C D: „Hvenær er vanhæfi smitandi?“ Tímarit lögfræðinga, 60. árg. (2010), bls. 7"


def test_urlausn_no_author_starts_with_title():
    assert to_urlausn(_doc(plaintiffs=None), TL).startswith("„Hvenær er vanhæfi smitandi?“ Tímarit lögfræðinga")


def test_urlausn_vefrit_uses_full_date_and_no_pages():
    d = _doc(court="Úlfljótur vefrit", volume=None, issue=None, page_start=None, page_end=None,
             document_date=date(2022, 1, 21), case_number="Loftslagsváin og dómstólar",
             plaintiffs=[{"name": "Kári Hólmar Ragnarsson", "lawyer": None}])
    assert to_urlausn(d, VEF) == "Kári Hólmar Ragnarsson: „Loftslagsváin og dómstólar“ Úlfljótur vefrit, 21. janúar 2022"


def _vefrit_doc(precision=None, when=date(2020, 1, 1)):
    return _doc(court="Úlfljótur vefrit", volume=None, issue=None, page_start=None, page_end=None,
                document_date=when, case_number="Loftslagsváin og dómstólar", external_id="2020-loftslagsvain-og-domstolar",
                plaintiffs=[{"name": "Kári Hólmar Ragnarsson", "lawyer": None}],
                raw_api_data={"date_precision": precision} if precision else None)


def test_urlausn_vefrit_with_only_a_year_cites_the_year_never_an_invented_day():
    # Task 22d: no date was printed; the year comes from the PDF's metadata and document_date is 2020-01-01
    d = _vefrit_doc("year")
    assert to_urlausn(d, VEF) == "Kári Hólmar Ragnarsson: „Loftslagsváin og dómstólar“ Úlfljótur vefrit, 2020"
    assert "janúar" not in to_urlausn(d, VEF) and "janúar" not in to_markdown(d, VEF)
    d.plaintiffs = None
    assert to_urlausn(d, VEF) == "„Loftslagsváin og dómstólar“ Úlfljótur vefrit, 2020"


def test_urlausn_vefrit_with_a_printed_day_is_cited_by_day_even_on_the_first_of_january():
    for precision in ("day", None):                  # a row without date_precision is a day-precision one
        d = _vefrit_doc(precision)
        assert to_urlausn(d, VEF).endswith("Úlfljótur vefrit, 1. janúar 2020")
    d = _vefrit_doc("day", date(2022, 1, 21))
    assert to_urlausn(d, VEF).endswith("Úlfljótur vefrit, 21. janúar 2022")
    assert "*Kári Hólmar Ragnarsson: „Loftslagsváin og dómstólar“ Úlfljótur vefrit, 21. janúar 2022*" in to_markdown(d, VEF)


def test_year_precision_markdown_and_filename_for_a_vefrit_article():
    md = to_markdown(_vefrit_doc("year"), VEF)
    assert "*Kári Hólmar Ragnarsson: „Loftslagsváin og dómstólar“ Úlfljótur vefrit, 2020*" in md
    assert "##### Úlfljótur vefrit · Fræðigrein" in md and "2020-01-01" not in md
    # the file name carries the year only, for either precision (R6)
    assert verdict_filename(_vefrit_doc("year"), VEF) == "UlfljoturVefrit_x-x_2020_loftslagsvain-og-domstolar"
    assert verdict_filename(_vefrit_doc("day"), VEF) == "UlfljoturVefrit_x-x_2020_loftslagsvain-og-domstolar"


def test_date_precision_changes_nothing_for_a_source_that_cites_by_volume_and_pages():
    assert to_urlausn(_doc(raw_api_data={"date_precision": "year"}), TL) == to_urlausn(_doc(), TL)


def test_urlausn_standalone_work_omits_journal_name():
    # fraedigreinar_ymsar has citation_name=None: there is no journal to cite (ruling R11).
    d = _doc(court="Fræðigrein", case_number="Titill", volume=None, issue=None,
             page_start=None, page_end=None, document_date=date(2016, 1, 1),
             plaintiffs=[{"name": "Höfundur", "lawyer": None}])
    assert to_urlausn(d, YMSAR) == "Höfundur: „Titill“ (2016)"
    d.page_start, d.page_end = 3, 9
    assert to_urlausn(d, YMSAR) == "Höfundur: „Titill“ (2016), bls. 3–9"


def test_urlausn_no_volume_and_no_issue_has_no_stray_comma():
    d = _doc(volume=None, issue=None)
    assert to_urlausn(d, TL) == (
        "Kjartan Bjarni Björgvinsson: „Hvenær er vanhæfi smitandi?“ Tímarit lögfræðinga (2010), bls. 7–47")


def test_urlausn_issue_without_volume():
    d = _doc(court="Lögmannablaðið", volume=None, issue="3", document_date=date(2005, 1, 1),
             page_start=None, page_end=None, plaintiffs=None, case_number="Titill")
    assert to_urlausn(d, LMB) == "„Titill“ Lögmannablaðið, 3. tbl. (2005)"


def test_urlausn_page_end_equal_to_page_start_is_a_single_page():
    assert to_urlausn(_doc(page_start=7, page_end=7), TL).endswith("(2010), bls. 7")


def test_urlausn_rannsoknir_cites_bibliographically_not_by_date():
    # rannsoknir_lagadeild has no issue_term but is not a web journal (R13): pages are kept.
    d = _doc(court="Rannsóknir í félagsvísindum", volume=8, issue=None, page_start=33, page_end=50,
             document_date=date(2007, 1, 1), case_number="Titill", plaintiffs=[{"name": "Höfundur", "lawyer": None}])
    assert to_urlausn(d, RANN) == "Höfundur: „Titill“ Rannsóknir í félagsvísindum, 8. árg. (2007), bls. 33–50"
    d.volume = None
    assert to_urlausn(d, RANN) == "Höfundur: „Titill“ Rannsóknir í félagsvísindum (2007), bls. 33–50"


def test_verdict_filename_journal_and_vefrit():
    assert verdict_filename(_doc(), TL) == "TL_60-1_2010_bls-7-47"
    assert verdict_filename(_doc(issue="3-4", page_end=None), TL) == "TL_60-3-4_2010_bls-7"
    d = _doc(court="Úlfljótur vefrit", volume=None, issue=None, page_start=None, page_end=None,
             document_date=date(2022, 1, 21), case_number="Loftslagsváin og dómstólar")
    assert verdict_filename(d, VEF) == "UlfljoturVefrit_x-x_2022_loftslagsvain-og-domstolar"


def test_an_article_of_a_bound_volume_cites_its_hefti_and_its_initials_as_printed():
    # Task 23f: TL 1980 bls. 248 (4. hefti), TL 1951 bls. 46 signed "E. A."; without its hefti the article was
    # "TL_30-x_1980_…" and "30. árg. (1980)"
    d = _doc(document_date=date(1980, 1, 1), volume=30, issue="4", page_start=248, page_end=262,
             case_number="Grein", plaintiffs=[{"name": "E. A.", "lawyer": None}], external_id="1980-30-4-248")
    assert to_urlausn(d, TL) == "E. A.: „Grein“ Tímarit lögfræðinga, 30. árg. 4. hefti (1980), bls. 248–262"
    assert verdict_filename(d, TL) == "TL_30-4_1980_bls-248-262"
    d.issue = None
    assert to_urlausn(d, TL).endswith("Tímarit lögfræðinga, 30. árg. (1980), bls. 248–262")
    assert verdict_filename(d, TL) == "TL_30-x_1980_bls-248-262"


def test_urlausn_omits_a_non_numeric_issue_key_but_the_file_name_keeps_it():
    # Task 17a (R32): "afmaelisrit" (Úlfljótur's anniversary issue) and slugs such as "power-and-democracy-in-iceland"
    # are identity only: no "afmaelisrit. tbl." in the citation; verdict_filename (and external_id) keep the key.
    d = _doc(court="Úlfljótur", volume=None, issue="afmaelisrit", page_start=9, page_end=20,
             document_date=date(2007, 1, 1), external_id="2007-x-afmaelisrit-9")
    assert to_urlausn(d, ULF) == "Kjartan Bjarni Björgvinsson: „Hvenær er vanhæfi smitandi?“ Úlfljótur (2007), bls. 9–20"
    assert verdict_filename(d, ULF) == "Ulfljotur_x-afmaelisrit_2007_bls-9-20"
    d.volume = 60
    assert to_urlausn(d, ULF).endswith("Úlfljótur, 60. árg. (2007), bls. 9–20")
    # numeric issues and ranges are cited as before
    d.issue = "3-4"
    assert to_urlausn(d, ULF).endswith("Úlfljótur, 60. árg. 3.–4. tbl. (2007), bls. 9–20")
    # R35: a double issue written "1/2" (leitir.is keeps that form) is a number too
    d.issue = "1/2"
    assert to_urlausn(d, ULF).endswith("Úlfljótur, 60. árg. 1.–2. tbl. (2007), bls. 9–20")
    assert verdict_filename(d, ULF) == "Ulfljotur_60-1-2_2007_bls-9-20"


def test_markdown_header_and_page_markers():
    md = to_markdown(_doc(), TL)
    assert md.startswith("# Hvenær er vanhæfi smitandi?\n")
    assert "Kjartan Bjarni Björgvinsson" in md.split("\n## ")[0]
    assert "Tímarit lögfræðinga, 60. árg. 1. hefti (2010), bls. 7–47" in md
    assert "<!-- bls. 7 -->\nFyrsta síða." in md
    assert "<!-- bls. 8 -->\nÖnnur síða." in md


def test_markdown_standalone_work_labels_fall_back_to_display_name():
    d = _doc(court="Fræðigrein", case_number="Titill", volume=None, issue=None,
             page_start=None, page_end=None, document_date=date(2016, 1, 1),
             plaintiffs=[{"name": "Höfundur", "lawyer": None}], page_offsets=None)
    md = to_markdown(d, YMSAR)
    assert "*Höfundur: „Titill“ (2016)*" in md
    assert "##### Ýmsar fræðigreinar · Fræðigrein" in md


def test_ruling_urlausn_and_filename_unchanged():
    from engine.config.sources import get_config as gc
    d = Document(court="Lrd.", case_number="177/2024", verdict_type="Dómur", document_date=date(2024, 3, 6))
    assert to_urlausn(d, gc("landsrettur")) == "Lrd. 177/2024 6. mars 2024 – Dómur"
    assert verdict_filename(d, gc("landsrettur")) == "Lrd_177-2024_D_06-03-2024"
