# tests/test_timarit_manifest.py
from pathlib import Path

import pytest
import yaml

from engine.config.sources import get_config
from engine.timarit import config as C
from engine.timarit.manifest import (
    ArticleEntry, IssueManifest, article_confidence, assign_external_ids, date_precision, external_id, issue_label, issue_status,
    load_manifest, manifest_path, manifest_sha256, merge_preserving_user, save_manifest,
)


def _m(**over):
    base = dict(source="timarit_logfraedinga", year=2010, volume=60, issue="1",
                label="Tímarit lögfræðinga, 60. árg. 1. hefti (2010)", file="Tímarit og fræðigreinar/Tímarit Lögfræðinga/TL 2010_01.pdf",
                pages=109, articles=[ArticleEntry(title="A", authors=["X Y"], page_start=7, page_end=47, pdf_pages=[8, 48],
                                                  provenance={"title": "leitir"}, checks={"title_on_page": True, "printed_page_match": True,
                                                  "author_on_page": True, "external_record": True})])
    base.update(over)
    return IssueManifest(**base)


def test_manifest_path_uses_x_for_missing_parts(tmp_path):
    assert manifest_path(tmp_path, "timarit_logfraedinga", 2010, 60, "1") == tmp_path / "timarit_logfraedinga" / "2010_60_1.yaml"
    assert manifest_path(tmp_path, "ulfljotur", 2002, None, "3-4").name == "2002_x_3-4.yaml"
    assert manifest_path(tmp_path, "logfraedingur", 2010, 4, None).name == "2010_4_x.yaml"
    assert manifest_path(tmp_path, "ulfljotur_vefrit", None, None, None).name == "x_x_x.yaml"


def test_issue_label_per_source():
    assert issue_label(get_config("timarit_logfraedinga"), 2010, 60, "1") == "Tímarit lögfræðinga, 60. árg. 1. hefti (2010)"
    assert issue_label(get_config("ulfljotur"), 2004, 57, "2") == "Úlfljótur, 57. árg. 2. tbl. (2004)"
    assert issue_label(get_config("timarit_logfraedinga"), 1985, 35, None) == "Tímarit lögfræðinga, 35. árg. (1985)"
    assert issue_label(get_config("rannsoknir_lagadeild"), 2007, 8, None) == "Rannsóknir í félagsvísindum, 8. árg. (2007)"


def test_issue_label_without_volume_has_no_dangling_comma():
    assert issue_label(get_config("logbru"), 2013, None, None) == "Lögbrú (2013)"


def test_issue_label_issue_without_volume():
    assert issue_label(get_config("ulfljotur"), 2002, None, "3-4") == "Úlfljótur, 3-4. tbl. (2002)"


def test_issue_label_omits_year_when_unknown():
    assert issue_label(get_config("stjornmal_stjornsysla"), None, 1, None) == "Stjórnmál og stjórnsýsla, 1. árg."
    assert issue_label(get_config("ulfljotur_vefrit"), None, None, None) == "Úlfljótur vefrit"


def test_issue_label_ignores_issue_when_source_has_no_issue_term():
    # rannsoknir_lagadeild has no issue level: an issue value must not leak into the label
    assert issue_label(get_config("rannsoknir_lagadeild"), 2007, 8, "2") == "Rannsóknir í félagsvísindum, 8. árg. (2007)"


def test_issue_label_omits_a_non_numeric_issue_key():
    # Task 17a (R32): discovery's non-numeric issue keys are identity only (file name, manifest path, external_id);
    # a label must not read "afmaelisrit. tbl." or "power-and-democracy-in-iceland. tbl.".
    ulf, sogs = get_config("ulfljotur"), get_config("stjornmal_stjornsysla")
    assert issue_label(ulf, 2007, None, "afmaelisrit") == "Úlfljótur (2007)"
    assert issue_label(ulf, 2007, 60, "afmaelisrit") == "Úlfljótur, 60. árg. (2007)"
    assert issue_label(sogs, 2018, 14, "power-and-democracy-in-iceland") == "Stjórnmál og stjórnsýsla, 14. árg. (2018)"
    assert issue_label(get_config("rannsoknir_lagadeild"), 2010, 11, "trausti-fannar") == "Rannsóknir í félagsvísindum, 11. árg. (2010)"
    # numbers and number ranges are still printed
    assert issue_label(ulf, 2002, None, "3-4") == "Úlfljótur, 3-4. tbl. (2002)"
    assert issue_label(ulf, 2004, 57, "2") == "Úlfljótur, 57. árg. 2. tbl. (2004)"
    # R35: "1/2" (a double issue as leitir.is writes it) stays citable
    assert issue_label(ulf, 2004, 57, "1/2") == "Úlfljótur, 57. árg. 1/2. tbl. (2004)"


def test_external_id_and_manifest_path_keep_a_non_numeric_issue_key(tmp_path):
    m = _m(source="ulfljotur", year=2007, volume=None, issue="afmaelisrit", articles=[ArticleEntry(title="T", page_start=9)])
    assert external_id(m, m.articles[0]) == "2007-x-afmaelisrit-9"
    assert manifest_path(tmp_path, "ulfljotur", 2007, None, "afmaelisrit").name == "2007_x_afmaelisrit.yaml"


def test_roundtrip_yaml_keeps_everything(tmp_path):
    p = tmp_path / "m.yaml"
    save_manifest(_m(), p)
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert data["articles"][0]["pdf_pages"] == [8, 48] and data["status"] == "pending"
    assert load_manifest(p) == _m()


def test_a_date_taken_from_the_pdf_metadata_survives_a_save_and_load(tmp_path):
    # Task 22d: provenance "pdf_metadata" is a label of its own; load_manifest refuses labels it does not know, and
    # analyze's own output must load again (the pilot report, import and the review UI all read it)
    a = ArticleEntry(title="Loftslagsváin og dómstólar", date="2020-01-01", provenance={"title": "filename", "date": "pdf_metadata"},
                     analysis_notes=["birtingardagur fannst ekki á fyrstu síðu; ár úr lýsigögnum PDF-skjalsins: 2020"])
    m = _m(source="ulfljotur_vefrit", year=None, volume=None, issue=None, articles=[a])
    p = tmp_path / "x_x_x.yaml"
    save_manifest(m, p)
    back = load_manifest(p)
    assert back.articles[0].date == "2020-01-01" and back.articles[0].provenance["date"] == "pdf_metadata"
    assert date_precision(back.articles[0]) == "year"


def test_roundtrip_with_every_field_filled(tmp_path):
    full = ArticleEntry(title="Ö", authors=["A B", "C D"], author_titles=["prófessor", "lögmaður"], article_type="Ritdómur",
                        page_start=3, page_end=9, pdf_pages=[4, 10], lang="en", date="2022-01-21", summary="s", keywords=["k1", "k2"],
                        provenance={"title": "user"}, checks={"title_on_page": False}, confidence=0.35,
                        leitir_record_id="rec1", xlsx_row=12, source_file="Tímarit og fræðigreinar/X/y.pdf", note="n")
    m = _m(sha256="ab" * 32, year_end=2011, page_map={"offset": 1, "quality": 0.9}, status="review", confidence=0.5,
           error="e", files=["Tímarit og fræðigreinar/a.pdf"], articles=[full])
    p = tmp_path / "m.yaml"
    save_manifest(m, p)
    assert load_manifest(p) == m


def test_save_creates_parent_dirs_and_writes_utf8(tmp_path):
    p = tmp_path / "a" / "b" / "m.yaml"
    save_manifest(_m(), p)
    assert "Tímarit lögfræðinga" in p.read_text(encoding="utf-8")


def test_a_failing_write_leaves_the_old_manifest_intact_and_no_temp_file(tmp_path, monkeypatch):
    """analyze rewrites hand-edited files in bulk: a crash or a full disk mid-write must not truncate one."""
    import os
    p = tmp_path / "m.yaml"
    save_manifest(_m(), p)
    before = p.read_bytes()
    changed = _m(label="Annað heiti")
    for target in ("fsync", "replace"):
        with monkeypatch.context() as mp:
            def boom(*a, **k):
                raise OSError(28, "No space left on device")
            mp.setattr(os, target, boom)
            with pytest.raises(OSError):
                save_manifest(changed, p)
        assert p.read_bytes() == before
        assert [q.name for q in tmp_path.iterdir()] == ["m.yaml"]          # the temp file is cleaned up
    save_manifest(changed, p)                                              # and the next save works
    assert load_manifest(p).label == "Annað heiti" and [q.name for q in tmp_path.iterdir()] == ["m.yaml"]


def test_save_keeps_the_mode_of_an_existing_file(tmp_path):
    p = tmp_path / "m.yaml"
    save_manifest(_m(), p)
    p.chmod(0o640)
    save_manifest(_m(label="Annað"), p)
    assert p.stat().st_mode & 0o777 == 0o640
    fresh = tmp_path / "n.yaml"
    save_manifest(_m(), fresh)
    assert fresh.stat().st_mode & 0o777 == 0o644                           # not mkstemp's private 0600


def test_save_writes_dates_as_quoted_strings(tmp_path):
    m = _m(articles=[ArticleEntry(title="T", date="2022-01-21")])
    p = tmp_path / "m.yaml"
    save_manifest(m, p)
    assert "date: '2022-01-21'" in p.read_text(encoding="utf-8")
    assert load_manifest(p).articles[0].date == "2022-01-21"


def test_load_normalises_unquoted_yaml_date_to_iso_string(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text("source: ulfljotur_vefrit\nyear: 2022\nvolume: null\nissue: null\nlabel: L\n"
                 "articles:\n- title: T\n  date: 2022-01-21\n", encoding="utf-8")
    assert yaml.safe_load(p.read_text(encoding="utf-8"))["articles"][0]["date"].__class__.__name__ == "date"   # the trap
    d = load_manifest(p).articles[0].date
    assert d == "2022-01-21" and isinstance(d, str)


def test_load_tolerates_missing_articles_and_optional_fields(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text("source: logbru\nyear: 2013\nvolume: null\nissue: null\nlabel: Lögbrú (2013)\n", encoding="utf-8")
    m = load_manifest(p)
    assert m.articles == [] and m.status == "pending" and m.files == [] and m.page_map == {}
    p.write_text("source: logbru\nyear: 2013\nvolume: null\nissue: null\nlabel: L\narticles:\n- title: Bara titill\n", encoding="utf-8")
    a = load_manifest(p).articles[0]
    assert a == ArticleEntry(title="Bara titill")
    p.write_text("source: logbru\nyear: 2013\nvolume: null\nissue: null\nlabel: L\narticles:\n", encoding="utf-8")
    assert load_manifest(p).articles == []


def test_load_turns_blank_list_fields_into_empty_lists(tmp_path):
    # a hand-edited file where the user cleared a list leaves "authors:" (null)
    p = tmp_path / "m.yaml"
    p.write_text("source: logbru\nyear: 2013\nvolume: null\nissue: null\nlabel: L\nfiles:\n"
                 "articles:\n- title: T\n  authors:\n  keywords:\n  provenance:\n  checks:\n  note:\n", encoding="utf-8")
    m = load_manifest(p)
    a = m.articles[0]
    assert m.files == [] and a.authors == [] and a.keywords == [] and a.provenance == {} and a.checks == {} and a.note == ""


def test_load_rejects_unknown_article_key_naming_key_and_file(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text("source: logbru\nyear: 2013\nvolume: null\nissue: null\nlabel: L\narticles:\n- title: T\n  titel: typo\n", encoding="utf-8")
    with pytest.raises(ValueError) as e:
        load_manifest(p)
    assert "titel" in str(e.value) and "m.yaml" in str(e.value)


def test_load_rejects_unknown_issue_key_naming_key_and_file(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text("source: logbru\nyear: 2013\nvolume: null\nissue: null\nlabel: L\nstatsu: approved\n", encoding="utf-8")
    with pytest.raises(ValueError) as e:
        load_manifest(p)
    assert "statsu" in str(e.value) and "m.yaml" in str(e.value)


def test_load_reports_missing_required_issue_keys(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text("source: logbru\nyear: 2013\n", encoding="utf-8")
    with pytest.raises(ValueError) as e:
        load_manifest(p)
    assert "label" in str(e.value) and "m.yaml" in str(e.value)


def test_load_rejects_non_mapping_article(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text("source: logbru\nyear: 2013\nvolume: null\nissue: null\nlabel: L\narticles:\n- just a string\n", encoding="utf-8")
    with pytest.raises(ValueError, match="m.yaml"):
        load_manifest(p)


def test_article_confidence_weights_and_llm_cap():
    all_true = {"title_on_page": True, "printed_page_match": True, "author_on_page": True, "external_record": True}
    assert abs(article_confidence(all_true) - 1.0) < 1e-9
    assert abs(article_confidence({**all_true, "external_record": False}) - 0.80) < 1e-9
    assert article_confidence(all_true, llm=True) == C.LLM_CONFIDENCE_CAP
    assert article_confidence({}) == 0.0


def test_article_confidence_exactly_at_threshold_is_not_lost_to_float_error(monkeypatch):
    # 0.35 + 0.30 + 0.20 is 0.8499999999999999 in floating point; an article with no author but everything else
    # confirmed sits exactly on a threshold of 0.85 (the plan's default; the user's pilot choice, 0.9, only the
    # article with all four checks reaches) and must pass it.
    monkeypatch.setattr(C, "APPROVE_THRESHOLD", 0.85)
    checks = {"title_on_page": True, "printed_page_match": True, "author_on_page": False, "external_record": True}
    assert article_confidence(checks) >= C.APPROVE_THRESHOLD


def test_the_pilot_thresholds_are_the_users_choices():
    # Task 22d: chosen by the user after the pilot audits (2026-10-04)
    assert (C.APPROVE_THRESHOLD, C.OCR_BIN_RATIO_THRESHOLD, C.COVERAGE_MAX_GAP_PAGES, C.PAGE_MAP_MIN_QUALITY) == (0.9, None, 6, 0.5)
    every = {"title_on_page": True, "printed_page_match": True, "author_on_page": True, "external_record": True}
    assert article_confidence(every) >= C.APPROVE_THRESHOLD
    assert article_confidence({**every, "author_on_page": False}) < C.APPROVE_THRESHOLD      # 0.85: to review
    m = _m(pages=72, articles=[ArticleEntry(title="A", page_start=1, pdf_pages=[3, 71], confidence=0.85)])
    assert issue_status(m) == "review"
    m.articles[0].confidence = 0.9
    assert issue_status(m) == "approved"


def test_article_confidence_is_rounded_so_pilot_weights_cannot_miss_a_threshold_on_float_error(monkeypatch):
    # Task 22 replaces the weights; sum([0.05, 0.35]) is 0.39999999999999997 in floating point.
    monkeypatch.setattr(C, "WEIGHTS", {"title_on_page": 0.35, "printed_page_match": 0.05})
    assert article_confidence({"title_on_page": True, "printed_page_match": True}) == 0.4


def _covering(**over):
    """An issue whose single article spans its whole 72-page PDF but for a short cover/TOC head."""
    m = _m(**{"pages": 72, **over})
    m.articles[0].pdf_pages = [3, 71]
    m.articles[0].confidence = 0.95
    return m


def test_issue_status_requires_every_article_above_threshold_and_coverage():
    m = _m(pages=72, articles=[ArticleEntry(title="A", page_start=1, pdf_pages=[3, 48], confidence=0.95),
                                ArticleEntry(title="B", page_start=49, page_end=70, pdf_pages=[50, 71], confidence=0.6)])
    assert issue_status(m) == "review"                       # B is below the threshold
    m.articles[1].confidence = 0.95
    assert issue_status(m) == "approved"
    m.articles[1].pdf_pages = [60, 71]      # gap of 11 pages between 48 and 60
    assert issue_status(m) == "review"


def test_issue_status_empty_issue_is_review():
    assert issue_status(_m(articles=[])) == "review"


def test_issue_status_ignores_coverage_when_there_is_no_issue_file():
    m = _m(file=None, articles=[ArticleEntry(title="A", pdf_pages=[0, 5], confidence=0.9),
                                ArticleEntry(title="B", pdf_pages=[100, 105], confidence=0.9)])
    assert issue_status(m) == "approved"


def test_external_id_forms():
    assert external_id(_m(), _m().articles[0]) == "2010-60-1-7"
    v = _m(source="ulfljotur_vefrit", year=2022, volume=None, issue=None,
           articles=[ArticleEntry(title="Loftslagsváin og dómstólar", date="2022-01-21")])
    assert external_id(v, v.articles[0]) == "2022-01-21-loftslagsvain-og-domstolar"
    r = _m(source="rannsoknir_lagadeild", year=2007, volume=8, issue=None, articles=[ArticleEntry(title="T", page_start=33)])
    assert external_id(r, r.articles[0]) == "2007-8-x-33"


def test_external_id_of_a_vefrit_article_with_only_a_year_is_the_year_and_the_slug():
    # Task 22d: the year came from the PDF's metadata, so the id carries the year, not an invented day
    v = _m(source="ulfljotur_vefrit", year=None, volume=None, issue=None,
           articles=[ArticleEntry(title="Loftslagsváin og dómstólar", date="2020-01-01", provenance={"date": "pdf_metadata"}),
                     ArticleEntry(title="Loftslagsváin og dómstólar", date="2022-01-21", provenance={"date": "page"})])
    assert assign_external_ids(v) == ["2020-loftslagsvain-og-domstolar", "2022-01-21-loftslagsvain-og-domstolar"]


@pytest.mark.parametrize("date, prov, precision", [
    ("2022-01-21", {"date": "page"}, "day"), ("2022-01-21", {}, "day"),                  # no provenance: as before
    ("2022-01-21", {"title": "filename"}, "day"),
    ("2020-01-01", {"date": "pdf_metadata"}, "year"), ("2020-01-01", {"date": "leitir"}, "year"),
    (None, {}, "year"), (None, {"date": "page"}, "year"),
    # R44: the user confirming or correcting an undated article's year writes YYYY-01-01 with provenance user: still a
    # year; a user's date that is not the 1st of January is a whole date; labels combine with "+"
    ("2019-01-01", {"date": "user"}, "year"), ("2019-03-22", {"date": "user"}, "day"),
    ("2019-01-01", {"date": "pdf_metadata+user"}, "year"), ("2019-03-22", {"date": "pdf_metadata+user"}, "day"),
    ("2019-01-01", {"date": "page+user"}, "day"), ("2019-01-01", {"date": "page"}, "day"),   # printed on the page: a day
    ("2019-01-01", {"date": " User "}, "year")])
def test_date_precision_is_day_for_a_printed_date_or_a_users_whole_date(date, prov, precision):
    assert date_precision(ArticleEntry(title="T", date=date, provenance=prov)) == precision


def test_external_id_of_a_vefrit_year_the_user_confirmed_is_still_year_and_slug():
    v = _m(source="ulfljotur_vefrit", year=None, volume=None, issue=None,
           articles=[ArticleEntry(title="Hugtakid Nauðgun", date="2019-01-01", provenance={"date": "user"}),
                     ArticleEntry(title="Hugtakið nauðgun", date="2019-03-22", provenance={"date": "user"})])
    assert assign_external_ids(v) == ["2019-hugtakid-naudgun", "2019-03-22-hugtakid-naudgun"]


def test_external_id_standalone_work_and_unknown_year():
    s = _m(source="fraedigreinar_ymsar", year=None, volume=None, issue=None, file=None,
           articles=[ArticleEntry(title="Réttarríki og kreppa")])
    assert external_id(s, s.articles[0]) == "rettarriki-og-kreppa"
    n = _m(source="stjornmal_stjornsysla", year=None, volume=1, issue="2", articles=[ArticleEntry(title="T", page_start=5)])
    assert external_id(n, n.articles[0]) == "x-1-2-5"


def test_merge_keeps_user_values_and_approved_status():
    old = _m(status="approved")
    old.articles[0].title = "Titill lagaður af notanda"
    old.articles[0].provenance["title"] = "user"
    old.articles[0].note = "lagað 2.10."
    new = _m(status="review")
    new.articles[0].title = "Titill úr nýrri greiningu"
    merged = merge_preserving_user(old, new)
    assert merged.articles[0].title == "Titill lagaður af notanda" and merged.articles[0].provenance["title"] == "user"
    assert merged.status == "approved" and merged.articles[0].note == "lagað 2.10."
    assert merge_preserving_user(None, new) == new


def test_merge_matches_articles_by_page_start_when_titles_differ():
    old = _m(); old.articles[0].authors = ["Notandi Lagaði"]; old.articles[0].provenance["authors"] = "user"
    new = _m(); new.articles[0].title = "Annar titill"; new.articles[0].authors = ["Vél"]
    assert merge_preserving_user(old, new).articles[0].authors == ["Notandi Lagaði"]


def test_merge_does_not_keep_non_user_values_or_demote_pending_status():
    old = _m(status="review")
    old.articles[0].title = "Gamall titill"                # provenance 'leitir' → the new analysis may replace it
    new = _m(status="pending")
    new.articles[0].title = "Nýr titill"
    merged = merge_preserving_user(old, new)
    assert merged.articles[0].title == "Nýr titill" and merged.status == "pending"


def test_merge_keeps_imported_status():
    assert merge_preserving_user(_m(status="imported"), _m(status="review")).status == "imported"


def test_manifest_sha256_changes_with_content(tmp_path):
    p = tmp_path / "m.yaml"; save_manifest(_m(), p); h1 = manifest_sha256(p)
    m = _m(); m.articles[0].note = "x"; save_manifest(m, p)
    assert manifest_sha256(p) != h1


# ---------------------------------------------------------------------------------------------
# Fix round 1 (rulings R27-R30)
# ---------------------------------------------------------------------------------------------

# --- R27: merge -------------------------------------------------------------------------------

def _art(title, page=None, **kw):
    return ArticleEntry(title=title, page_start=page, **kw)


def _mm(*articles, **over):
    return _m(articles=list(articles), **over)


def test_merge_keeps_article_the_user_added_by_hand():
    added = _art("Grein sem notandinn bætti við", 20, provenance={"title": "user", "pages": "user"})
    old = _mm(_art("A", 7), added)
    new = _mm(_art("A", 7))
    assert [a.title for a in merge_preserving_user(old, new).articles] == ["A", "Grein sem notandinn bætti við"]


def test_merge_keeps_article_whose_title_and_page_start_the_user_both_corrected():
    old = _mm(_art("Réttur titill", 9, provenance={"title": "user", "pages": "user"}, note="lagað"))
    new = _mm(_art("Rangur titill", 7))
    merged = merge_preserving_user(old, new)
    assert [(a.title, a.page_start) for a in merged.articles] == [("Rangur titill", 7), ("Réttur titill", 9)]
    assert merged.articles[1].note == "lagað"


def test_merge_puts_user_added_article_at_its_page_position():
    added = _art("Mið", 20, provenance={"title": "user"})
    nameless = _art("Án blaðsíðu", None, provenance={"title": "user"})
    old = _mm(_art("A", 7), added, nameless, _art("C", 30))
    new = _mm(_art("A", 7), _art("C", 30))
    assert [a.title for a in merge_preserving_user(old, new).articles] == ["A", "Mið", "C", "Án blaðsíðu"]


def test_merge_keeps_user_added_articles_in_their_old_order_when_pages_tie():
    old = _mm(_art("U1", 20, provenance={"title": "user"}), _art("U2", 20, provenance={"title": "user"}))
    new = _mm(_art("N", 7))
    assert [a.title for a in merge_preserving_user(old, new).articles] == ["N", "U1", "U2"]


def test_merge_drops_unmatched_old_article_without_user_provenance():
    old = _mm(_art("A", 7), _art("Vélargrein sem hvarf", 20, provenance={"title": "toc"}))
    new = _mm(_art("A", 7))
    assert [a.title for a in merge_preserving_user(old, new).articles] == ["A"]


def test_merge_same_start_page_matches_one_to_one_so_a_user_title_stays_on_its_own_article():
    old = _mm(_art("Fréttir", 5, provenance={"title": "user"}), _art("B vél", 5))
    new = _mm(_art("A vél", 5), _art("B ný", 5))
    merged = merge_preserving_user(old, new)
    assert [(a.title, a.provenance.get("title")) for a in merged.articles] == [("Fréttir", "user"), ("B ný", None)]


def test_merge_one_old_article_is_never_matched_to_two_new_ones():
    old = _mm(_art("Fréttir", 5, provenance={"title": "user"}, note="aðeins ein"))
    new = _mm(_art("A", 5), _art("B", 5))
    merged = merge_preserving_user(old, new)
    assert [a.note for a in merged.articles] == ["aðeins ein", ""]
    assert len(merged.articles) == 2


def test_merge_prefers_page_and_title_over_page_alone():
    old = _mm(_art("Alfa", 5, note="alfa"), _art("Beta", 5, note="beta"))
    new = _mm(_art("Beta", 5), _art("Alfa", 5))          # same page, machine order differs
    assert [(a.title, a.note) for a in merge_preserving_user(old, new).articles] == [("Beta", "beta"), ("Alfa", "alfa")]


def test_merge_matches_by_source_file_before_page_or_title():
    old = _mm(_art("Önnur", None, source_file="x/b.pdf", provenance={"title": "user"}),
              _art("Fyrsta", None, source_file="x/a.pdf"), file=None)
    new = _mm(_art("Fyrsta", None, source_file="x/a.pdf"), _art("Titill vélar", None, source_file="x/b.pdf"), file=None)
    merged = merge_preserving_user(old, new)
    assert [(a.source_file, a.title) for a in merged.articles] == [("x/a.pdf", "Fyrsta"), ("x/b.pdf", "Önnur")]


def test_merge_matches_by_xlsx_row_then_leitir_record_id():
    old = _mm(_art("Notandi 1", 9, xlsx_row=4, provenance={"title": "user"}),
              _art("Notandi 2", 15, leitir_record_id="rec2", provenance={"title": "user"}))
    new = _mm(_art("Vél 1", 7, xlsx_row=4), _art("Vél 2", 8, leitir_record_id="rec2"))
    assert [a.title for a in merge_preserving_user(old, new).articles] == ["Notandi 1", "Notandi 2"]


def test_merge_matches_by_title_alone_ignoring_case_and_punctuation():
    old = _mm(_art("„Hvað er Réttur?“", None, note="n"))
    new = _mm(_art("hvað er réttur", 12))
    assert merge_preserving_user(old, new).articles[0].note == "n"


def test_merge_pages_provenance_covers_page_start_page_end_and_pdf_pages():
    old = _mm(_art("A", 9, page_end=20, pdf_pages=[10, 21], provenance={"pages": "user"}, xlsx_row=3))
    new = _mm(_art("A", 7, page_end=47, pdf_pages=[8, 48], provenance={"pages": "toc"}, xlsx_row=3))
    a = merge_preserving_user(old, new).articles[0]
    assert (a.page_start, a.page_end, a.pdf_pages) == (9, 20, [10, 21]) and a.provenance["pages"] == "user"


def test_merge_user_flag_inside_a_combined_provenance_value_counts():
    old = _mm(_art("Notandi", 7, provenance={"title": "user+toc"}))
    new = _mm(_art("Vél", 7))
    assert merge_preserving_user(old, new).articles[0].title == "Notandi"


# --- R27: provenance validation on load -------------------------------------------------------

_HEAD = "source: logbru\nyear: 2013\nvolume: null\nissue: null\nlabel: L\n"


def _load(tmp_path, text, name="m.yaml"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return load_manifest(p)


def _one_article(*lines):
    return _HEAD + "articles:\n- title: T\n" + "".join(f"  {ln}\n" for ln in lines)


def test_load_accepts_combined_and_field_name_provenance(tmp_path):
    a = _load(tmp_path, _one_article("provenance: {title: toc, pages: leitir+toc, page_start: user, date: page}")).articles[0]
    assert a.provenance == {"title": "toc", "pages": "leitir+toc", "page_start": "user", "date": "page"}


def test_load_normalises_provenance_values(tmp_path):
    a = _load(tmp_path, _one_article("provenance: {title: ' User ', pages: Leitir + TOC}")).articles[0]
    assert a.provenance == {"title": "user", "pages": "leitir+toc"}


@pytest.mark.parametrize("prov", ["{titel: user}", "{title: tco}", "{title: user+nothing}", "{title: ''}", "{title: true}", "{title: [user]}"])
def test_load_rejects_bad_provenance_naming_file_and_article(tmp_path, prov):
    with pytest.raises(ValueError) as e:
        _load(tmp_path, _one_article(f"provenance: {prov}"))
    assert "m.yaml" in str(e.value) and "#1" in str(e.value) and "provenance" in str(e.value)


def test_load_rejects_provenance_that_is_not_a_mapping(tmp_path):
    with pytest.raises(ValueError, match="provenance"):
        _load(tmp_path, _one_article("provenance: user"))


# --- R28: external_id --------------------------------------------------------------------------

def test_external_id_without_page_start_or_date_uses_title_slug_so_articles_do_not_collide():
    m = _mm(_art("Fyrri grein"), _art("Seinni grein"))
    assert external_id(m, m.articles[0]) == "2010-60-1-fyrri-grein"
    assert external_id(m, m.articles[1]) == "2010-60-1-seinni-grein"


def test_external_id_without_page_start_falls_back_to_source_file_stem_not_the_whole_path():
    m = _mm(ArticleEntry(source_file="Tímarit og fræðigreinar/Lögbrú/Lögbrú 2013 grein 4.pdf"), file=None, year=2013, volume=None, issue=None)
    assert external_id(m, m.articles[0]) == "2013-x-x-logbru-2013-grein-4"


def test_external_id_standalone_work_is_the_file_stem_slug_whatever_the_year():
    f = "Tímarit og fræðigreinar/Ýmsar fræðigreinar/Fræðigrein ýmsar 2015.pdf"
    for year in (None, 2015):
        m = _m(source="fraedigreinar_ymsar", year=year, volume=None, issue=None, file=None,
               articles=[ArticleEntry(title="Annar titill", source_file=f)])
        assert external_id(m, m.articles[0]) == "fraedigrein-ymsar-2015"
    m = _m(source="fraedigreinar_ymsar", year=None, volume=None, issue=None, file=None, articles=[ArticleEntry(title="Réttarríki og kreppa")])
    assert external_id(m, m.articles[0]) == "rettarriki-og-kreppa"


def test_external_id_standalone_works_starting_with_the_same_letter_get_different_ids():
    a = ArticleEntry(source_file="Tímarit og fræðigreinar/Ýmsar fræðigreinar/Foo.pdf")
    b = ArticleEntry(source_file="Tímarit og fræðigreinar/Ýmsar fræðigreinar/Fum.pdf")
    m = _m(source="fraedigreinar_ymsar", year=None, volume=None, issue=None, file=None, articles=[a, b])
    assert [external_id(m, x) for x in (a, b)] == ["foo", "fum"]


def test_external_id_standalone_work_ignores_page_start():
    m = _m(source="fraedigreinar_ymsar", year=None, volume=None, issue=None, file=None,
           articles=[ArticleEntry(title="Ritgerð", page_start=5)])
    assert external_id(m, m.articles[0]) == "ritgerd"


def test_external_id_vefrit_keeps_date_and_slug():
    v = _m(source="ulfljotur_vefrit", year=None, volume=None, issue=None, file=None,
           articles=[ArticleEntry(title="Loftslagsváin og dómstólar", date="2022-01-21")])
    assert external_id(v, v.articles[0]) == "2022-01-21-loftslagsvain-og-domstolar"


def test_slug_transliterates_icelandic_letters():
    v = _m(source="ulfljotur_vefrit", year=None, volume=None, issue=None, file=None,
           articles=[ArticleEntry(title="Þingræði, Ægir og Óðinn", date="2022-01-21")])
    assert external_id(v, v.articles[0]) == "2022-01-21-thingraedi-aegir-og-odinn"
    v.articles[0].title = "þórður æðri ðð"
    assert external_id(v, v.articles[0]) == "2022-01-21-thordur-aedri-dd"


def test_assign_external_ids_suffixes_duplicates_in_article_order():
    m = _mm(_art("A", 5), _art("B", 5), _art("C", 5), _art("D", 9), _art("E"), _art("E"))
    assert assign_external_ids(m) == ["2010-60-1-5", "2010-60-1-5-2", "2010-60-1-5-3", "2010-60-1-9", "2010-60-1-e", "2010-60-1-e-2"]


def test_assign_external_ids_unique_input_equals_external_id_and_empty_manifest_gives_empty_list():
    m = _mm(_art("A", 5), _art("B", 9))
    assert assign_external_ids(m) == [external_id(m, a) for a in m.articles]
    assert assign_external_ids(_mm()) == []


def test_assign_external_ids_a_suffix_never_collides_with_a_natural_id():
    m = _m(source="fraedigreinar_ymsar", year=None, volume=None, issue=None, file=None,
           articles=[ArticleEntry(title="a"), ArticleEntry(title="a"), ArticleEntry(title="a 2")])
    ids = assign_external_ids(m)
    assert len(set(ids)) == 3 and ids[0] == "a" and ids[1] == "a-2"


# --- R29: coverage -----------------------------------------------------------------------------

def test_issue_status_approves_an_issue_its_articles_cover():
    assert issue_status(_covering()) == "approved"


def test_issue_status_partial_coverage_of_the_file_is_review():
    m = _m()                                    # 109-page file, one article on pdf pages 8-48
    m.articles[0].confidence = 0.95
    assert issue_status(m) == "review"


@pytest.mark.parametrize("pdf_pages,expected", [([6, 71], "approved"), ([7, 71], "review"),      # head gap: pages before the first article
                                                ([0, 65], "approved"), ([0, 64], "review")])     # tail gap: pages after the last article, up to pages-1
def test_issue_status_limits_head_and_tail_gap(pdf_pages, expected):
    m = _covering()
    m.articles[0].pdf_pages = pdf_pages
    assert issue_status(m) == expected


def test_issue_status_file_backed_article_without_pdf_pages_is_review():
    m = _covering()
    m.articles.append(ArticleEntry(title="Ófundin", confidence=0.95))
    assert issue_status(m) == "review"


def test_issue_status_article_granularity_has_no_coverage_rule():
    m = _m(file=None, pages=None, articles=[ArticleEntry(title="A", confidence=0.9), ArticleEntry(title="B", pdf_pages=[40, 41], confidence=0.9)])
    assert issue_status(m) == "approved"


def test_issue_status_unknown_page_count_skips_head_and_tail_check():
    m = _covering(pages=None)
    m.articles[0].pdf_pages = [3, 10]
    assert issue_status(m) == "approved"


def test_pages_analysis_found_to_be_no_articles_are_no_gap():
    # Task 22c: articles end on their own text; the adverts and blank pages after them (page_map["non_article"]) are
    # covered, an unlisted item's pages are not
    m = _covering()
    m.articles[0].pdf_pages = [3, 40]
    m.articles.append(ArticleEntry(title="B", pdf_pages=[52, 71], confidence=0.95))
    assert issue_status(m) == "review"                                  # pages 41-51: a gap of 11
    m.page_map["non_article"] = list(range(41, 47))
    assert issue_status(m) == "approved"                                # 47-51: 5 pages, within the limit
    m.page_map["non_article"] = [41, 42]
    assert issue_status(m) == "review"
    m.articles[1].pdf_pages = [52, 60]
    m.page_map["non_article"] = list(range(41, 52)) + list(range(61, 72))
    assert issue_status(m) == "approved"                                # the tail after the last article too


def test_coverage_gap_limit_is_a_config_tunable(monkeypatch):
    assert C.COVERAGE_MAX_GAP_PAGES == 6
    m = _m()
    m.articles[0].confidence = 0.95
    monkeypatch.setattr(C, "COVERAGE_MAX_GAP_PAGES", 100)
    assert issue_status(m) == "approved"


# --- R30: load is the validation boundary --------------------------------------------------------

def test_load_coerces_issue_level_scalars(tmp_path):
    m = _load(tmp_path, "source: logbru\nyear: '2010'\nvolume: '60'\nissue: 1\nlabel: 2013\npages: '109'\nyear_end: '2011'\n"
                        "confidence: '0.5'\nstatus: ' Approved '\nfiles:\n- a.pdf\n")
    assert (m.year, m.volume, m.issue, m.label, m.pages, m.year_end, m.confidence, m.status) == (2010, 60, "1", "2013", 109, 2011, 0.5, "approved")
    assert all(isinstance(x, int) for x in (m.year, m.volume, m.pages, m.year_end)) and isinstance(m.issue, str)


def test_load_keeps_a_compound_issue_as_written(tmp_path):
    assert _load(tmp_path, "source: ulfljotur\nyear: 2002\nvolume: null\nissue: 3-4\nlabel: L\n").issue == "3-4"


def test_load_scalar_files_becomes_a_list(tmp_path):
    assert _load(tmp_path, _HEAD + "files: a.pdf\n").files == ["a.pdf"]


def _issue_text(**over):
    base = dict(source="logbru", year="2013", volume="null", issue="null", label="L")
    base.update(over)
    return "".join(f"{k}: {v}\n" for k, v in base.items())


@pytest.mark.parametrize("key,val", [("year", "abc"), ("volume", "6.5"), ("pages", "[1]"), ("year_end", "true"), ("confidence", "high"),
                                     ("status", "gone"), ("page_map", "3"), ("files", "{a: b}")])
def test_load_rejects_unparseable_issue_values_naming_file_and_key(tmp_path, key, val):
    with pytest.raises(ValueError) as e:
        _load(tmp_path, _issue_text(**{key: val}))
    assert "m.yaml" in str(e.value) and key in str(e.value)


def test_load_missing_status_value_is_pending(tmp_path):
    assert _load(tmp_path, _HEAD + "status:\n").status == "pending"


def test_load_coerces_article_numbers(tmp_path):
    a = _load(tmp_path, _one_article("page_start: '7'", "page_end: 47.0", "xlsx_row: '12'", "confidence: '0.9'")).articles[0]
    assert (a.page_start, a.page_end, a.xlsx_row, a.confidence) == (7, 47, 12, 0.9)
    assert isinstance(a.page_start, int) and isinstance(a.page_end, int) and isinstance(a.confidence, float)


@pytest.mark.parametrize("bad", ["page_start: 7a", "page_start: 7.5", "page_start: true", "page_end: [1, 2]", "xlsx_row: x", "confidence: high", "confidence: .nan"])
def test_load_rejects_unparseable_article_numbers_naming_file_article_and_key(tmp_path, bad):
    with pytest.raises(ValueError) as e:
        _load(tmp_path, _one_article(bad))
    assert "m.yaml" in str(e.value) and "#1" in str(e.value) and bad.split(":")[0] in str(e.value)


def test_load_reports_the_index_of_the_bad_article(tmp_path):
    text = _HEAD + "articles:\n- title: ok\n- title: bad\n  page_start: nope\n"
    with pytest.raises(ValueError) as e:
        _load(tmp_path, text)
    assert "#2" in str(e.value)


def test_load_wraps_scalar_list_fields(tmp_path):
    a = _load(tmp_path, _one_article("authors: Jón Jónsson", "author_titles: lögmaður", "keywords: vanhæfi")).articles[0]
    assert (a.authors, a.author_titles, a.keywords) == (["Jón Jónsson"], ["lögmaður"], ["vanhæfi"])


def test_load_list_elements_become_text(tmp_path):
    a = _load(tmp_path, _one_article("keywords: [2010, vanhæfi]", "authors: ['A B', 'C D']")).articles[0]
    assert a.keywords == ["2010", "vanhæfi"] and a.authors == ["A B", "C D"]


@pytest.mark.parametrize("bad", ["authors: [[a]]", "authors: [null]", "keywords: {a: b}", "authors: [No]"])
def test_load_rejects_bad_list_elements(tmp_path, bad):
    with pytest.raises(ValueError) as e:
        _load(tmp_path, _one_article(bad))
    assert "m.yaml" in str(e.value) and "#1" in str(e.value)


def test_load_text_fields_become_str(tmp_path):
    a = _load(tmp_path, _one_article("summary: 1984", "note: 42", "leitir_record_id: 991001868109706886", "lang: is")).articles[0]
    assert (a.summary, a.note, a.leitir_record_id, a.lang) == ("1984", "42", "991001868109706886", "is")


@pytest.mark.parametrize("field", ["title", "summary", "note", "source_file"])
def test_load_refuses_a_yaml_boolean_in_a_text_field_and_says_to_quote_it(tmp_path, field):
    # `title: No` is False to YAML; str(False) would silently store "False"
    with pytest.raises(ValueError) as e:
        _load(tmp_path, _HEAD + f"articles:\n- {field}: No\n")
    assert "m.yaml" in str(e.value) and field in str(e.value) and "quote" in str(e.value)


def test_load_quoted_no_is_a_title(tmp_path):
    assert _load(tmp_path, _HEAD + "articles:\n- title: 'No'\n").articles[0].title == "No"


def test_load_pdf_pages_must_be_a_pair_of_ints(tmp_path):
    assert _load(tmp_path, _one_article("pdf_pages: ['8', 48]")).articles[0].pdf_pages == [8, 48]
    assert _load(tmp_path, _one_article("pdf_pages: null")).articles[0].pdf_pages is None
    assert _load(tmp_path, _one_article("pdf_pages: [5, 5]")).articles[0].pdf_pages == [5, 5]
    for bad in ("8", "[8]", "[8, 9, 10]", "[48, 8]", "[-1, 3]", "[a, b]", "[1, null]", "{a: 1}"):
        with pytest.raises(ValueError) as e:
            _load(tmp_path, _one_article(f"pdf_pages: {bad}"))
        assert "pdf_pages" in str(e.value) and "#1" in str(e.value) and "m.yaml" in str(e.value)


def test_load_checks_values_become_bool(tmp_path):
    a = _load(tmp_path, _one_article("checks: {title_on_page: 1, printed_page_match: 'false', author_on_page: yes, external_record: 0}")).articles[0]
    assert a.checks == {"title_on_page": True, "printed_page_match": False, "author_on_page": True, "external_record": False}
    with pytest.raises(ValueError, match="checks"):
        _load(tmp_path, _one_article("checks: {title_on_page: maybe}"))
    with pytest.raises(ValueError, match="checks"):
        _load(tmp_path, _one_article("checks: [title_on_page]"))


def test_load_date_still_becomes_an_iso_string(tmp_path):
    assert _load(tmp_path, _one_article("date: 2022-01-21")).articles[0].date == "2022-01-21"


def test_load_yaml_syntax_error_is_a_value_error_naming_the_file(tmp_path):
    with pytest.raises(ValueError) as e:
        _load(tmp_path, "source: [unclosed\nyear: 2010\n", name="bilad.yaml")
    assert "bilad.yaml" in str(e.value)


def test_load_hand_edited_status_cannot_bypass_the_approved_check(tmp_path):
    old = _load(tmp_path, _HEAD + "status: Approved\n")
    merged = merge_preserving_user(old, _m(status="review"))
    assert merged.status == "approved"


# --- R31 (revised): an article may carry its own issue -------------------------------------------------

def test_article_issue_round_trips_with_and_without_a_value(tmp_path):
    m = _m(source="ulfljotur", year=1947, volume=1, issue=None, file=None,
           articles=[ArticleEntry(title="A", page_start=3, issue="4", provenance={"issue": "xlsx"}), ArticleEntry(title="B", page_start=5)])
    p = tmp_path / "m.yaml"
    save_manifest(m, p)
    back = load_manifest(p)
    assert back == m and back.articles[0].issue == "4" and back.articles[1].issue is None


def test_load_coerces_an_unquoted_article_issue_to_text(tmp_path):
    assert _load(tmp_path, _one_article("issue: 4")).articles[0].issue == "4"
    assert _load(tmp_path, _one_article("issue: 3-4")).articles[0].issue == "3-4"
    assert _load(tmp_path, _one_article("issue:")).articles[0].issue is None
    with pytest.raises(ValueError, match="issue"):
        _load(tmp_path, _one_article("issue: [4]"))


def test_external_id_uses_the_article_issue_over_the_manifest_issue():
    m = _m(source="ulfljotur", year=1947, volume=1, issue=None, file=None,
           articles=[ArticleEntry(title="A", page_start=3, issue="4"), ArticleEntry(title="B", page_start=3, issue="3"),
                     ArticleEntry(title="C", page_start=9)])
    assert [external_id(m, a) for a in m.articles] == ["1947-1-4-3", "1947-1-3-3", "1947-1-x-9"]
    assert assign_external_ids(m) == ["1947-1-4-3", "1947-1-3-3", "1947-1-x-9"]      # no suffix needed
    n = _m(articles=[ArticleEntry(title="A", page_start=7, issue="3-4")])
    assert external_id(n, n.articles[0]) == "2010-60-3-4-7"


def test_merge_does_not_pair_articles_of_different_issues_by_page_alone():
    """Bls restarts per issue in Úlfljótur vols 1–12: page 3 of issue 3 is not page 3 of issue 4."""
    old = _mm(_art("Notandi", 3, issue="3", provenance={"title": "user"}), file=None)
    new = _mm(_art("Vél 4", 3, issue="4"), _art("Vél 3", 3, issue="3"), file=None)
    merged = merge_preserving_user(old, new)
    assert [(a.title, a.issue) for a in merged.articles] == [("Vél 4", "4"), ("Notandi", "3")]


def test_merge_keeps_an_issue_the_user_set():
    old = _mm(_art("A", 3, issue="2", provenance={"issue": "user"}), file=None)
    new = _mm(_art("A", 3, issue="4", provenance={"issue": "xlsx"}), file=None)
    merged = merge_preserving_user(old, new)
    assert merged.articles[0].issue == "2" and merged.articles[0].provenance["issue"] == "user"


# --- R33: machine notes apart from the user's note ------------------------------------------------------

def test_analysis_notes_round_trip_and_load_as_a_list(tmp_path):
    m = _mm(_art("A", 3, analysis_notes=["upphafsbls. 3 er utan skjals", "leitir.is segir bls. 5"]), _art("B", 9))
    p = tmp_path / "m.yaml"
    save_manifest(m, p)
    assert load_manifest(p) == m and load_manifest(p).articles[1].analysis_notes == []
    assert _load(tmp_path, _one_article("analysis_notes: ein athugasemd")).articles[0].analysis_notes == ["ein athugasemd"]


def test_analysis_notes_is_not_a_provenance_key(tmp_path):
    with pytest.raises(ValueError, match="analysis_notes"):
        _load(tmp_path, _one_article("provenance: {analysis_notes: user}"))


def test_merge_keeps_the_users_note_even_when_the_new_analysis_has_notes():
    old = _mm(_art("A", 7, note="Athugað: titill réttur", analysis_notes=["gömul vélarathugasemd"]))
    new = _mm(_art("A", 7, analysis_notes=["ný vélarathugasemd"]))
    a = merge_preserving_user(old, new).articles[0]
    assert a.note == "Athugað: titill réttur" and a.analysis_notes == ["ný vélarathugasemd"]


def test_merge_takes_analysis_notes_from_the_new_analysis_only():
    old = _mm(_art("A", 7, analysis_notes=["a", "b"]))
    assert merge_preserving_user(old, _mm(_art("A", 7))).articles[0].analysis_notes == []
    assert merge_preserving_user(old, _mm(_art("A", 7, analysis_notes=["c"]))).articles[0].analysis_notes == ["c"]


def test_merge_the_old_note_wins_over_a_note_in_the_new_manifest():
    """`note` is the user's field: a non-empty old note is kept whatever the new manifest carries there."""
    old = _mm(_art("A", 7, note="notandi"))
    assert merge_preserving_user(old, _mm(_art("A", 7, note="annað"))).articles[0].note == "notandi"
    assert merge_preserving_user(_mm(_art("A", 7)), _mm(_art("A", 7, note="annað"))).articles[0].note == "annað"


# --- Task 23e: a page window of a PDF that holds more than one volume ------------------------------------

def test_a_page_window_survives_save_and_load_and_an_absent_one_is_not_written(tmp_path):
    p = tmp_path / "m.yaml"
    save_manifest(_m(), p)
    assert "pdf_window" not in p.read_text(encoding="utf-8")       # every other manifest is saved as before
    assert load_manifest(p).pdf_window is None
    save_manifest(_m(pdf_window=[204, 375]), p)
    assert yaml.safe_load(p.read_text(encoding="utf-8"))["pdf_window"] == [204, 375]
    assert load_manifest(p) == _m(pdf_window=[204, 375])


@pytest.mark.parametrize("val", ["[3]", "[5, 2]", "[-1, 4]", "x", "[a, 4]"])
def test_load_rejects_a_page_window_that_is_not_two_page_indexes(tmp_path, val):
    with pytest.raises(ValueError) as e:
        _load(tmp_path, _HEAD + f"pdf_window: {val}\n")
    assert "m.yaml" in str(e.value) and "pdf_window" in str(e.value)


@pytest.mark.parametrize("pdf_pages,expected", [
    ([210, 369], "approved"), ([211, 369], "review"),     # head: counted from the window's first page
    ([204, 369], "approved"), ([204, 368], "review")])    # tail: up to the window's last page
def test_coverage_of_a_page_window_is_measured_from_the_window_not_from_the_whole_file(pdf_pages, expected):
    m = _covering(pages=376, pdf_window=[204, 375])
    m.articles[0].pdf_pages = pdf_pages
    assert issue_status(m) == expected
