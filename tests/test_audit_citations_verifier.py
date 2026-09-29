"""The precision audit's independent verifier, on six fixed examples.

Four are correct citations the verifier must wave through (one of them with an
inherited court word, where the court cannot be checked at all), two are
deliberately wrong and must be caught. The verifier is a pure function: no DB,
and none of the extractor's own code.
"""
import datetime as dt

from scripts.audit_citations import norm_num, verify


def _t(court, case, date, verdict="Dómur"):
    return {"court": court, "case_number": case,
            "document_date": date, "verdict_type": verdict}


CITING = {"document_date": dt.date(2021, 6, 1)}


# ---------------------------------------------------------------- correct ---

def test_hrd_prose_with_date_is_ok():
    raw = "Hæstaréttar 8. nóvember 2017 í máli nr. 700/2017"
    v = verify(raw, "700/2017", _t("Hrd.", "700/2017", dt.date(2017, 11, 8)),
               CITING, prefix="Með dómi ")
    assert v["verdict"] == "mech_ok"
    assert v["checks"] == {"number": "pass", "court": "pass", "date": "pass",
                           "verdict": "pass", "order": "pass", "span": "pass"}


def test_district_court_with_e_number_and_leading_zeros_is_ok():
    raw = "Héraðsdóms Reykjavíkur 3. maí 2019 í máli nr. E-0012/2019"
    v = verify(raw, "E-0012/2019",
               _t("Hérd. Rvk.", "E-12/2019", dt.date(2019, 5, 3)),
               CITING, prefix="sbr. dóm ")
    assert v["verdict"] == "mech_ok", v
    assert v["checks"]["number"] == "pass"
    assert v["checks"]["court"] == "pass"


def test_felagsdomur_bare_number_against_stored_f_prefix_is_ok():
    raw = "Félagsdóms í máli nr. 5/2011"
    v = verify(raw, "5/2011", _t("Féld.", "F-5/2011", dt.date(2011, 3, 2)),
               CITING, prefix="sbr. dóm ")
    assert v["verdict"] == "mech_ok", v
    assert v["checks"]["number"] == "pass"
    assert v["checks"]["date"] == "n/a"        # no full date in raw_text
    assert any("F- prefix" in n for n in v["notes"])


def test_inherited_court_word_cannot_be_verified_but_number_can():
    raw = "réttarins 9. desember 1999 í máli nr. 274/1999"
    v = verify(raw, "274/1999", _t("Hrd.", "274/1999", dt.date(1999, 12, 9)),
               CITING, prefix="dóma ")
    assert v["verdict"] == "mech_ok", v
    assert v["checks"]["court"] == "n/a"
    assert v["checks"]["number"] == "pass"
    assert v["checks"]["date"] == "pass"
    assert v["checks"]["verdict"] == "pass"


def test_malskotsbeidni_needs_no_court_word_at_all():
    # 'ákvörðun nr. 2022-1': the YYYY-N shape is always a málskotsbeiðni (§5.3),
    # so the citation is complete without a court word.
    raw = "ákvörðun nr. 2022-1"
    v = verify(raw, "2022-1", _t("Hrd. málsk.", "2022-1", dt.date(2022, 3, 4), "Ákvörðun"),
               {"document_date": dt.date(2022, 9, 28)})
    assert v["verdict"] == "mech_ok", v
    assert v["checks"]["court"] == "pass"


def test_letter_prefix_overrides_a_wrong_court_word():
    # The citing text misattributes an E- number to Hæstiréttur; a letter prefix
    # other than F- is always a district court (§5.3), so the target is right.
    raw = "Hæstaréttar Íslands í máli nr. E-1871/2007"
    v = verify(raw, "E-1871/2007",
               _t("Hérd. Rvk.", "E-1871/2007", dt.date(2007, 10, 5)),
               {"document_date": dt.date(2007, 12, 12)}, prefix="dómur ")
    assert v["verdict"] == "mech_ok", v
    assert v["checks"]["court"] == "pass"
    assert any("letter-prefix rule" in n for n in v["notes"])


# ------------------------------------------------------------------ wrong ---

def test_number_mismatch_fails():
    raw = "Hæstaréttar í máli nr. 701/2017"
    v = verify(raw, "701/2017", _t("Hrd.", "700/2017", dt.date(2017, 11, 8)),
               CITING, prefix="Með dómi ")
    assert v["verdict"] == "mech_fail"
    assert v["failed"] == ["number"]


def test_date_mismatch_fails():
    raw = "Hæstaréttar 8. nóvember 2017 í máli nr. 700/2017"
    v = verify(raw, "700/2017", _t("Hrd.", "700/2017", dt.date(2017, 11, 9)),
               CITING, prefix="Með dómi ")
    assert v["verdict"] == "mech_fail"
    assert v["failed"] == ["date"]


# ------------------------------------------------- the supporting machinery ---

def test_norm_num_drops_spaces_zeros_and_upcases():
    assert norm_num("055/2001") == "55/2001"
    assert norm_num("243 /2002") == "243/2002"
    assert norm_num("e-0012/2020") == "E-12/2020"
    assert norm_num("2023-65") == "2023-65"
    assert norm_num(None) is None


def test_verdict_mismatch_is_reported_but_not_scored():
    # 'dómi Hæstaréttar' against a target stored as 'Úrskurður' (a Hæstiréttur
    # kærumál): flagged for the reviewer, but it is not a link error.
    raw = "Hæstaréttar 29. ágúst 2017 í máli nr. 533/2017"
    v = verify(raw, "533/2017",
               _t("Hrd.", "533/2017", dt.date(2017, 8, 29), "Úrskurður"),
               CITING, prefix="Með dómi ")
    assert v["checks"]["verdict"] == "fail"
    assert v["verdict_mismatch"] is True
    assert v["failed"] == []
    assert v["verdict"] == "mech_ok"


def test_later_target_and_broken_span_are_caught():
    raw = "Hæstaréttar í máli nr. 700/2017"
    v = verify(raw, "nr. 700", _t("Hrd.", "700/2017", dt.date(2022, 1, 1)), CITING)
    assert v["failed"] == ["order", "span"]
