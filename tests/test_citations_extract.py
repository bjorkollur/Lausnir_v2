import datetime as dt

import pytest

from engine.processors.citations import extract_citations

D = dt.date(2022, 3, 15)


def one(text, **kw):
    out = extract_citations(text, doc_date=kw.pop("doc_date", D))
    assert len(out) == 1, out
    return out[0]


def test_prose_with_date():
    c = one("Með dómi Hæstaréttar 8. nóvember 2017 í máli nr. 700/2017 var því slegið föstu.")
    assert (c.target_court, c.target_case_number, c.target_date, c.target_verdict, c.form) == \
           ("Hrd.", "700/2017", dt.date(2017, 11, 8), "Dómur", "prose")
    assert c.raw_text.startswith("Hæstaréttar") and c.raw_text.endswith("700/2017")


def test_char_span_is_the_number():
    text = "sbr. dóm Hæstaréttar í máli nr. 190/1996. K ehf."
    c = one(text)
    assert text[c.char_start:c.char_end] == "190/1996"


def test_enumeration_yields_distinct_rows_same_court():
    text = ("Í dómum réttarins 21. október 1999 í máli nr. 116/1999, 9. desember sama ár í máli nr. 274/1999 "
            "og 3. febrúar 2000 í máli nr. 3/2000 var …")
    # 'réttarins' inherits the last court named earlier in the sentence; here none precedes, so no court → nothing.
    assert extract_citations(text, doc_date=D) == []
    text2 = "Hæstiréttur vísaði til dóma sinna í málum nr. 116/1999, 274/1999 og 3/2000."
    out = extract_citations(text2, doc_date=D)
    assert [c.target_case_number for c in out] == ["116/1999", "274/1999", "3/2000"]
    assert len({c.char_start for c in out}) == 3 and {c.target_court for c in out} == {"Hrd."}


def test_court_carried_forward_by_rettarins():
    text = ("Í dómi Hæstaréttar 21. október 1999 í máli nr. 116/1999 og dómi réttarins 9. desember sama ár "
            "í máli nr. 274/1999 var …")
    out = extract_citations(text, doc_date=D)
    assert [(c.target_court, c.target_case_number, c.target_date) for c in out] == [
        ("Hrd.", "116/1999", dt.date(1999, 10, 21)), ("Hrd.", "274/1999", dt.date(1999, 12, 9))]


def test_sama_ar_uses_previous_year_in_sentence_not_doc_year():
    c = one("Áfrýjað var dómi Landsréttar 18. nóvember sama ár í máli nr. 308/2021, en Hæstiréttur 2. mars 2021 …"
            .split(", en")[0] + ".")
    # no earlier year in the sentence → date must be None, never doc year
    assert c.target_case_number == "308/2021" and c.target_date is None
    c2 = one("Með áfrýjunarstefnu 25. janúar 2021 var áfrýjað dómi Landsréttar 18. nóvember sama ár í máli nr. 308/2021.")
    assert c2.target_date == dt.date(2021, 11, 18)


def test_s_a_abbreviation():
    c = one("Með áfrýjunarstefnu 25. janúar 2021 var áfrýjað dómi Landsréttar 18. nóvember s.á. í máli nr. 308/2021.")
    assert c.target_date == dt.date(2021, 11, 18)


def test_relative_sl_same_year_and_previous_year():
    c = one("til að kæra úrskurð Landsréttar 26. febrúar sl. í máli nr. 93/2025.", doc_date=dt.date(2025, 4, 1))
    assert c.target_date == dt.date(2025, 2, 26)
    c2 = one("til að kæra úrskurð Landsréttar 26. nóvember sl. í máli nr. 93/2024.", doc_date=dt.date(2025, 4, 1))
    assert c2.target_date == dt.date(2024, 11, 26)
    c3 = one("til að kæra úrskurð Landsréttar 26. nóvember sl. í máli nr. 93/2024.", doc_date=None)
    assert c3.target_date is None


def test_law_numbers_are_not_citations():
    assert extract_citations("Í dómi Hæstaréttar kom fram að af lögum nr. 7/1998 leiddi …", doc_date=D) == []
    assert extract_citations("sbr. 2. mgr. 218. gr. laga nr. 19/1940 og reglugerð nr. 12/2001.", doc_date=D) == []


def test_other_adjudicator_nearer_than_court_is_dropped():
    # the court word and the nefnd are in the SAME sentence, so only the
    # 'nearer body wins' rule can reject this — not the sentence boundary
    text = "Í dómi Hæstaréttar var fjallað um úrskurð óbyggðanefndar í máli nr. 4/2005."
    assert extract_citations(text, doc_date=D) == []
    assert extract_citations("Vísað er til úrskurðar nefndarinnar í máli nr. 2/2010.", doc_date=D) == []


def test_sentence_boundary_blocks_court_from_previous_sentence():
    text = "Hæstiréttur staðfesti niðurstöðuna. Í málinu nr. 12/2019 var deilt um …"
    assert extract_citations(text, doc_date=D) == []


@pytest.mark.parametrize("word,abbr", [
    ("Reykjavíkur", "Hérd. Rvk."), ("Reykjaness", "Hérd. Reykn."), ("Suðurlands", "Hérd. Suðl."),
    ("Norðurlands eystra", "Hérd. Norðeyst."), ("Norðurlands vestra", "Hérd. Norðvest."),
    ("Vesturlands", "Hérd. Vestl."), ("Austurlands", "Hérd. Austl."), ("Vestfjarða", "Hérd. Vestfj."),
])
def test_district_courts(word, abbr):
    c = one(f"Kærandi vísar til dóms Héraðsdóms {word} í máli nr. E-351/2022 frá 13. október 2022.")
    assert c.target_court == abbr and c.target_case_number == "E-351/2022"
    assert c.target_date == dt.date(2022, 10, 13)      # 'frá' introduces a date after the number


def test_district_without_place_and_lowercase():
    c = one("sem staðfest var með úrskurði héraðsdóms í máli nr. R-362/2009.")
    assert c.target_court == "Hérd." and c.target_verdict == "Úrskurður"


def test_letter_prefix_overrides_court_word():
    c = one("sbr. dóm Hæstaréttar í máli nr. E-110/2015.")
    assert c.target_court == "Hérd."
    f = one("sbr. dóm Héraðsdóms Reykjavíkur í máli nr. F-3/2015.")
    assert f.target_court == "Féld."
    f2 = one("sbr. dóm Félagsdóms í máli nr. 9/1999.")
    assert f2.target_court == "Féld." and f2.target_case_number == "9/1999"


def test_malskotsbeidni_forms():
    c = one("sbr. ákvörðun Hæstaréttar 21. maí 2019 í máli nr. 2019-155.")
    assert (c.target_court, c.target_case_number, c.target_verdict) == ("Hrd. málsk.", "2019-155", "Ákvörðun")
    c2 = one("með ákvörðun réttarins nr. 2023-68 var beiðninni hafnað.")
    assert c2.target_court == "Hrd. málsk." and c2.target_case_number == "2023-68"


def test_abbreviations_and_reporter():
    a = one("sjá Hrd. 700/2017 og")
    assert (a.target_court, a.target_case_number, a.form) == ("Hrd.", "700/2017", "abbrev")
    b = one("sjá Hérd. Reykn. E-12/2020.")
    assert (b.target_court, b.target_case_number) == ("Hérd. Reykn.", "E-12/2020")
    r = one("en með dómi Hæstaréttar frá árinu 1983, Hrd.1983/1538.")
    assert (r.target_court, r.target_case_number, r.form) == ("Hrd.", None, "reporter")
    h = one("sbr. H 1999:123.")
    assert (h.form, h.target_case_number) == ("reporter", None)


def test_date_after_number_without_introducer_is_ignored():
    c = one("í máli Landsréttar nr. 121/2020, kæra á ákvörðun sýslumanns frá 20. október 2020, var …")
    assert c.target_case_number == "121/2020" and c.target_date is None


def test_two_courts_in_one_sentence():
    text = ("Með dómi Landsréttar 4. júní 2020 í máli nr. 455/2019 var staðfestur dómur Héraðsdóms Reykjavíkur "
            "12. desember 2019 í máli nr. E-1/2019.")
    out = extract_citations(text, doc_date=D)
    assert [(c.target_court, c.target_case_number) for c in out] == [("Lrd.", "455/2019"), ("Hérd. Rvk.", "E-1/2019")]


def test_empty_and_garbage():
    assert extract_citations("", doc_date=D) == []
    assert extract_citations("nr. nr. máli nr. /2020 Hæstaréttar", doc_date=D) == []


def test_court_beyond_the_window_is_not_used():
    # court word ~423 chars before the number, same sentence → outside the 120-char look-back
    assert extract_citations(f"dómi Hæstaréttar {'sem ' * 100}í máli nr. 1/2020.", doc_date=D) == []


def test_raw_text_capped():
    c = one(f"dómi Hæstaréttar {'sem ' * 20}í máli nr. 1/2020.")
    assert len(c.raw_text) <= 240 and c.raw_text.endswith("1/2020")


# --- extra tests added while implementing (not in the task brief) ------------

def test_extra_semicolon_and_blank_line_break_the_sentence():
    """';' and a blank line are sentence breaks, so the court does not carry over."""
    assert extract_citations("Hæstiréttur staðfesti; í máli nr. 12/2019 var deilt um annað.", doc_date=D) == []
    assert extract_citations("Hæstiréttur staðfesti\n\nÍ máli nr. 12/2019 var deilt um annað.", doc_date=D) == []


def test_extra_ordinal_and_nr_abbreviations_do_not_break_the_sentence():
    """'3. mgr.', '5. gr.' and 'nr. 7/1998' keep the sentence intact; the law number is masked."""
    c = one("Hæstiréttur vísaði til 3. mgr. 5. gr. laga nr. 7/1998 og til dóms síns í máli nr. 12/2019.")
    assert (c.target_court, c.target_case_number) == ("Hrd.", "12/2019")


def test_extra_domstolsins_inherits_and_is_not_an_other_body():
    """'dómstólsins' matches _OTHER_BODY_RX's 'dómstól\\w*' but must not cancel its own court."""
    c = one("Landsréttur féllst ekki á það og í dómi dómstólsins í máli nr. 55/2020 var niðurstaðan staðfest.")
    assert c.target_court == "Lrd."


def test_extra_dags_and_uppkvedinn_introduce_a_date_after_the_number():
    assert one("sbr. dóm Hæstaréttar í máli nr. 5/2021 dags. 3. mars 2021.").target_date == dt.date(2021, 3, 3)
    assert one("sbr. dóm Hæstaréttar í máli nr. 5/2021 uppkveðnum 3. mars 2021.").target_date == dt.date(2021, 3, 3)


def test_extra_reporter_and_abbrev_can_coexist():
    """'Hrd. 15/1983' is a case number, 'Hrd.1983/1538' is a reporter reference."""
    out = extract_citations("sjá Hrd. 15/1983 og Hrd.1983/1538.", doc_date=D)
    assert [(c.form, c.target_case_number) for c in out] == [("abbrev", "15/1983"), ("reporter", None)]


def test_extra_narrowed_other_body_words():
    """'stjórnarskrárinnar' must not cancel a citation; a real 'nefnd' still must."""
    c = one("Með dómi Hæstaréttar um túlkun stjórnarskrárinnar í máli nr. 12/2019 var deilt um það.")
    assert c.target_court == "Hrd."
    assert extract_citations("Vísað er til úrskurðar nefndarinnar í máli nr. 2/2010.", doc_date=D) == []


def test_extra_compound_adjudicator_names_cancel():
    """'nefnd' and 'dómstóll' are compound tails, so neither may be \\b-anchored."""
    for body in ("úrskurð áfrýjunarnefndar samkeppnismála", "úrskurð matsnefndar eignarnámsbóta",
                 "úrskurð kærunefndar útlendingamála", "úrskurð yfirskattanefndar"):
        text = f"Í dómi Hæstaréttar var vísað til {body} í máli nr. 1/2012."
        assert extract_citations(text, doc_date=D) == [], text
    assert extract_citations(
        "Hæstiréttur vísaði til dóms Mannréttindadómstóls Evrópu í máli nr. 22/2011.", doc_date=D) == []


def test_extra_nefnd_adjectives_do_not_cancel():
    """'áðurnefndu máli' is an adjective, not an adjudicator."""
    c = one("sbr. dóm Hæstaréttar í áðurnefndu máli nr. 12/2019.")
    assert c.target_court == "Hrd."
    for adj in ("fyrrnefnda", "síðarnefnda", "svonefnda", "margnefnda"):
        out = extract_citations(f"sbr. dóm Hæstaréttar í {adj} máli nr. 12/2019.", doc_date=D)
        assert [c.target_court for c in out] == ["Hrd."], adj


def test_extra_date_is_claimed_by_the_first_number_only():
    out = extract_citations(
        "Í dómi Hæstaréttar 8. nóvember 2017 í máli nr. 700/2017, sbr. einnig mál nr. 701/2017.", doc_date=D)
    assert [(c.target_case_number, c.target_date) for c in out] == [
        ("700/2017", dt.date(2017, 11, 8)), ("701/2017", None)]


def test_extra_comma_before_the_date_introducer():
    assert one("sbr. dóm Hæstaréttar í máli nr. 5/2021, dags. 3. mars 2021.").target_date == dt.date(2021, 3, 3)
    assert one("sbr. dóm Hæstaréttar í máli nr. 5/2021, frá 3. mars 2021.").target_date == dt.date(2021, 3, 3)


def test_extra_year_range_is_not_a_malskotsbeidni():
    assert extract_citations("sbr. dóm Hæstaréttar í máli nr. 2010-2015.", doc_date=D) == []


def test_extra_relative_year_ignores_law_and_case_numbers():
    """'sama ár' must resolve to a standalone year token, never the year inside 91/1991."""
    # (1) only a law number precedes → no year at all, and never the law's year
    c = one("Samkvæmt 1. mgr. 175. gr. laga nr. 91/1991 var dómi Landsréttar 18. nóvember sama ár "
            "í máli nr. 308/2021 áfrýjað.", doc_date=dt.date(2022, 3, 1))
    assert c.target_date is None
    # (2) a real standalone year still resolves
    c2 = one("Með áfrýjunarstefnu 25. janúar 2022 var dómi Landsréttar 18. nóvember sama ár "
             "í máli nr. 308/2021 áfrýjað.")
    assert c2.target_date == dt.date(2022, 11, 18)
    # (3) the real year wins even with a law number earlier in the sentence
    c3 = one("Samkvæmt 175. gr. laga nr. 91/1991 og áfrýjunarstefnu 25. janúar 2022 var dómi "
             "Landsréttar 18. nóvember sama ár í máli nr. 308/2021 áfrýjað.")
    assert c3.target_date == dt.date(2022, 11, 18)
    # (4) a bare 'árið 2021' is a standalone year
    c4 = one("Málið var rekið árið 2021 og dómi Landsréttar 18. nóvember sama ár "
             "í máli nr. 308/2021 var áfrýjað.")
    assert c4.target_date == dt.date(2021, 11, 18)
    # (5) a málskotsbeiðni number is not a year either — '2019-155' must not give 2019
    out = extract_citations("Í máli nr. 2019-155 var beiðni hafnað og dómi Landsréttar "
                            "18. nóvember sama ár í máli nr. 308/2021 áfrýjað.", doc_date=D)
    assert [(c.target_case_number, c.target_date) for c in out] == [("2019-155", None), ("308/2021", None)]
    # …unless a real year precedes it
    out2 = extract_citations("Árið 2021 var máli nr. 2019-155 vísað frá og dómi Landsréttar "
                             "18. nóvember sama ár í máli nr. 308/2021 áfrýjað.", doc_date=D)
    assert out2[-1].target_date == dt.date(2021, 11, 18)
