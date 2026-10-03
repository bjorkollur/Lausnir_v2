# tests/test_timarit_firstpage.py
"""Task 22b: what an article's own pages print. Every page here is a real first (or last) page of the pilot, as
read_page returns it (text, font size, top), cut to the lines that matter; the comment names the audited article."""
import fitz
import pytest

from engine.timarit.firstpage import (
    Line, Page, byline, choose_title, is_furniture, match_score, merge_authors, names_in, page_heading, page_heads_digest,
    printed_title, read_page, recase, signature, text_quality, title_at_top,
)

_BODY = "Meginmál greinarinnar heldur áfram hér með nægum texta svo að síðan hafi meginmál."


def _page(height, *rows, body=10.0, body_from=None, body_lines=12):
    """rows: (size, y, text[, bold]); `body_lines` lines of body text in `body` points follow from `body_from`."""
    lines = [Line(r[2], r[0], r[1], r[3] if len(r) > 3 else False) for r in rows]
    start = body_from if body_from is not None else max((r[1] for r in rows), default=0) + 30
    lines += [Line(_BODY, body, start + 14 * k) for k in range(body_lines)]
    return Page(sorted(lines, key=lambda ln: ln.y), height)


# Úlfljótur 2010-1, pdf 10 (audit: idx 0, "reflur" for "reglur" in the spreadsheet). The title comes last in the text
# layer; by position it is at the top.
ULF_2010_P10 = _page(657.6, (24.0, 46.2, "Fjármálamarkaður innan"), (24.0, 71.2, "Evrópusambandsins og EES"),
                     (24.0, 96.2, "reglur og eftirlit*"), (12.0, 263.8, "Ágrip", True), body=10.0, body_from=280)
# Úlfljótur 2010-1, pdf 76 (idx 2: the spreadsheet's title is the section heading "Fræðabálkur")
ULF_2010_P76 = _page(657.6, (24.0, 60.4, "FRÆÐABÁLKUR"), (24.0, 107.1, "Er skipulögð glæpastarfsemi"),
                     (24.0, 132.1, "raunveruleg ógn á Íslandi ?"), (12.0, 503.8, "Breytt mynd skipulagðrar glæpastarfsemi", True),
                     body=10.0, body_from=170)
# Lögmannablaðið 2017-1, pdf 3 (idx 0: the TOC says "Leiðari"; the page prints the editorial's own headline)
LMBL_2017_P3 = _page(841.9, (8.0, 807.0, "4 LÖGMANNABLAÐIÐ TBL 01/17"), (38.0, 114.4, "LÖGMENN Í"),
                     (38.0, 147.3, "LANDSRÉTT"), (10.0, 229.9, "EVA HALLDÓRSDÓTTIR HDL."), (10.0, 241.9, "RITSTJÓRI"),
                     body=10.2, body_from=293.7)
# Rannsóknir í félagsvísindum VI, pdf 300 (idx 14: the TOC title is cut short, the author is missing)
RANNS_2005_P300 = _page(842.0, (18.6, 70.9, "Gildissvið meginreglunnar um ne bis in"),
                        (18.6, 90.0, "idem í 1. mgr. 4. gr. 7. viðauka við"), (18.6, 109.2, "mannréttindasáttmála Evrópu"),
                        (15.3, 148.2, "Róbert R. Spanó"), (14.0, 223.3, "I. Umfjöllunarefnið"), body=11.0, body_from=252.6)
# Tímarit lögfræðinga 1985, pdf 209 (idx 23: the OCR'd TOC reads "maf"; the page prints MAÍ, glued to the year)
TL_1985_P209 = _page(622.8, (31.2, 99.4, "Aí vettvangi", True), (32.6, 128.0, "(lóinsinála", True),
                     (14.0, 178.0, "Páll Sigurðsson dósent:"), (14.3, 200.5, "HÆSTARÉTTARDÓMUR FRÁ 31. MAÍ1985", True),
                     body=11.7, body_from=225.2)
# Úlfljótur 1975 (24.pdf, idx 9): the item starts half way down, under the end of the one before it
ULF_1975_24 = _page(1391.0, (51.9, 442.3, "SKRÁ YFIR LÖGFRÆÐIBÆKUR"), (51.2, 500.3, "ÚTGEFNAR 1974 OG 1975"),
                    body=24.3, body_from=41.7, body_lines=20)
# Úlfljótur 1957 (1071.pdf, idx 14): the OCR misreads the heading
ULF_1957_1071 = _page(1401.0, (44.2, 56.6, "Frá aSalfundi"), body=24.7, body_from=132.1)
# Lögmannablaðið 2017-1, pdf 31 (idx 11: the TOC ran three items together)
LMBL_2017_P31 = _page(841.9, (8.0, 807.0, "32 LÖGMANNABLAÐIÐ TBL 01/17"), (52.0, 323.9, "JÓLASNAPSMÓT LMFÍ", True),
                      (22.0, 400.0, "MEISTARAMÓT LMFÍ Í KNATTSPYRNU INNANHÚSS 2016"), body=10.2, body_from=442.0)
# Úlfljótur vefrit, "Samningur um úthafsveiðar.pdf" (idx 14: the file name is a short form of the printed title)
VEFRIT_UTHAF = _page(841.9, (20.0, 257.3, "VEFRIT", True), (28.0, 320.2, "Nýr samningur um að koma í veg fyrir"),
                     (28.0, 354.4, "stjórnlausar úthafsveiðar í miðhluta"), (28.0, 388.6, "Norður-Íshafsins"),
                     (14.0, 446.9, "Eftir Arnór Snæbjörnsson, lögfræðing í sjávarútvegsráðuneytið"),
                     (12.0, 493.0, "Efnisyfirlit", True), body=12.0, body_from=530.2)
# Úlfljótur 2023-3, pdf 8 (idx 0: the TOC names the second of two authors only)
ULF_2023_P8 = _page(666.1, (10.0, 597.6, "Úlfljótur 271"), (24.0, 53.3, "Sérgreiningarregla"), (24.0, 80.8, "höfundaréttar*"),
                    (10.0, 154.5, "Alexander Hafþórsson", True), (10.0, 166.5, "lögmaður hjá Rétti - Aðalsteinssyni & Partners"),
                    (10.0, 184.5, "Sigríður Rut Júlíusdóttir", True), (10.0, 196.5, "héraðsdómari við Héraðsdóm Reykjavíkur"),
                    (12.0, 232.4, "Efnisyfirlit", True), (10.0, 250.5, "1 Inngangur"), body=10.0, body_from=262.5)
# Lögmannablaðið 2020-1, pdf 25 (idx 5: the co-author was glued into the TOC title)
LMBL_2020_P25 = _page(841.9, (8.0, 807.0, "26 LÖGMANNABLAÐIÐ TBL 01/20"), (34.0, 71.9, "VIÐBÓTARGREINARGERÐIR"),
                      (44.0, 112.3, "- Á ÍSLANDI OG", True), (44.0, 162.3, "Í DANMÖRKU", True),
                      (10.0, 220.0, "GUÐRÚN OLSEN LÖGMAÐUR OG BERGÞÓR BERGSSON LÖGFRÆÐINGUR SKRIFA"),
                      (12.0, 277.1, "Í DANMÖRKU ÞURFA AÐILAR MÁLS EKKI AÐ"), body=10.3, body_from=488.0)
# Lögbrú 2013-1, pdf 154 (idx 3: "Texti: Kristján Örvar Sveinsson"; the name under the label is the interviewee)
LOGBRU_2013_P154 = _page(558.4, (9.0, 528.5, "155 / LÖGBRÚ", True), (29.0, 39.3, "VIÐTAL", True),
                         (12.0, 73.3, "Unnar Steinn Bjarndal", True), (12.0, 87.7, "Útskriftarár 2006"),
                         (12.0, 102.1, "Héraðsdómslögmaður LS Legal"),
                         (10.0, 206.6, "viðtölum. Texti: Kristján Örvar Sveinsson - Myndir: Sigurður Steinar Ásgeirsson", True),
                         body=10.0, body_from=239.9)
# Lögfræðingur 2006, pdf 78 (idx 6): "Grein eftir Margréti Steinarsdóttur ráðgjafa í"
LOGFR_2006_P78 = _page(595.2, (35.0, 62.3, "Lögfræðiráðgjöf í Alþjóðahúsi"),
                       (10.4, 115.2, "Grein eftir Margréti Steinarsdóttur ráðgjafa í"),
                       (10.5, 129.6, "Alþjóðahúsi. Margrét er lögfræðingur að mennt"), body=9.4, body_from=187.1)
# Lögfræðingur 2011, pdf 96 (idx 6): "Ritdómur eftir Margréti Heinreksdóttur" as the running head
LOGFR_2011_P96 = _page(595.2, (9.7, 48.8, "Ritdómur eftir Margréti Heinreksdóttur"), (19.8, 121.8, "Mál Mannréttindanefndar SÞ"),
                       (20.3, 145.9, "1977-2008 - Handbók"), (10.1, 168.9, "Jakob Þ. Möller 8 Alfred de Zayas"), body=9.4,
                       body_from=216.0)
# Úlfljótur vefrit, "Gjafa- og séreignakaupmálar á Íslandi.pdf" (idx 37) and "Samtal Hæstaréttar og Endurupptökudóms.pdf"
VEFRIT_GJAFA = _page(841.8, (12.0, 36.1, "Birt 9. janúar 2023"), (19.8, 251.3, "VEFRIT", True),
                     (25.8, 319.9, "Gjafa- og séreignakaupmálar á Íslandi"),
                     (13.8, 367.8, "Björn Ingi L. Jónsson, lögfræðingur"),
                     (13.8, 392.4, "Hrefna Friðriksdóttir, prófessor við lagadeild Háskóla Íslands"), body=10.2, body_from=425)
VEFRIT_SAMTAL = _page(841.9, (11.0, 22.0, "Birt 7. nóvember 2022"), (20.0, 236.8, "VEFRIT", True),
                      (26.0, 297.4, "Samtal Hæstaréttar og Endurupptökudóms"),
                      (14.0, 341.8, "Sindri M. Stephensen, dósent við lagadeild Háskólans í Reykjavík"),
                      (14.0, 365.9, "Víðir Smári Petersen, dósent við lagadeild Háskóla Íslands"),
                      (16.0, 418.2, "Efnisyfirlit:", True), body=11.0, body_from=445.6)
# Tímarit lögfræðinga 2007-1, pdf 5 (idx 1): "Páll Hreinsson:" above the title in capitals
TL_2007_P5 = _page(682.0, (13.0, 604.3, "5"), (11.5, 254.9, "Páll Hreinsson:"),
                   (11.5, 272.9, "LEIÐBEININGARSKYLDA STJÓRNVALDS VIÐ MEÐFERÐ"), (11.5, 285.9, "STJÓRNSÝSLUMÁLS"),
                   (10.6, 310.8, "EFNISYFIRLIT:"), (10.6, 322.8, "1. INNGANGUR"), body=10.6, body_from=340)
# Úlfljótur 2004-2 (3240.pdf, idx 4): an obituary; the name it heads is not its author
ULF_2004_3240 = _page(1410.0, (81.7, 63.0, "t MINNING t"), (52.1, 355.9, "Arnljótur Björnsson"), body=24.3,
                      body_from=477.9, body_lines=20)


def test_printed_heading_corrects_a_registry_typo():
    c = choose_title("Fjármálamarkaður innan Evrópusambandsins og EES reflur og eftirlit", ULF_2010_P10)
    assert c == ("Fjármálamarkaður innan Evrópusambandsins og EES reglur og eftirlit", None, True)


def test_a_section_label_as_title_takes_the_pages_heading():
    c = choose_title("Fræðabálkur", ULF_2010_P76)
    assert c.title == "Er skipulögð glæpastarfsemi raunveruleg ógn á Íslandi?" and c.label == "Fræðabálkur"
    assert c.from_page
    c = choose_title("Leiðari", LMBL_2017_P3)
    assert c.title.lower() == "lögmenn í landsrétt" and c.label == "Leiðari" and c.from_page


def test_a_truncated_toc_title_gets_its_whole_printed_heading_and_the_byline_under_it():
    c = choose_title("4. gr. 7. viðauka við mannréttindasáttmála Evrópu", RANNS_2005_P300)
    assert c.title == ("Gildissvið meginreglunnar um ne bis in idem í 1. mgr. 4. gr. 7. viðauka við "
                       "mannréttindasáttmála Evrópu")
    h = printed_title(RANNS_2005_P300, c.title)
    assert byline(RANNS_2005_P300, h, c.title) == ["Róbert R. Spanó"]


def test_a_heading_in_capitals_takes_the_registry_casing_and_fixes_its_ocr_typo():
    c = choose_title("Af vettvangi dómsmála: Hæstaréttardómur frá 31. maf 1985", TL_1985_P209)
    assert c.title == "Hæstaréttardómur frá 31. maí 1985" and c.from_page
    h = printed_title(TL_1985_P209, "Hæstaréttardómur frá 31. maí 1985")
    assert byline(TL_1985_P209, h, c.title) == ["Páll Sigurðsson"]      # "Name dósent:" above the title


def test_printed_wording_outranks_the_spreadsheet():
    c = choose_title("Skrá yfir lögfræðilegar bækur útgefnar 1974 og 1975", ULF_1975_24)
    assert c.title == "Skrá yfir lögfræðibækur útgefnar 1974 og 1975" and c.from_page


def test_an_ocr_garbled_heading_does_not_replace_a_clean_registry_title():
    assert choose_title("Frá aðalfundi", ULF_1957_1071) == ("Frá aðalfundi", None, False)
    assert text_quality("Frá aSalfundi") < text_quality("Frá aðalfundi")


def test_a_toc_entry_that_ran_three_items_together_is_cut_to_the_printed_one():
    c = choose_title("Jólasnapsmót LMFÍ Hinn nýi Landsréttur. Eiríkur Tómasson.", LMBL_2017_P31)
    assert c.title == "Jólasnapsmót LMFÍ" and c.from_page


def test_a_file_name_title_is_a_short_form_of_the_printed_one():
    assert choose_title("Samningur um úthafsveiðar", VEFRIT_UTHAF).from_page is False      # strict: no match
    c = choose_title("Samningur um úthafsveiðar", VEFRIT_UTHAF, loose=True)
    assert c.title == "Nýr samningur um að koma í veg fyrir stjórnlausar úthafsveiðar í miðhluta Norður-Íshafsins"
    h = printed_title(VEFRIT_UTHAF, c.title)
    assert byline(VEFRIT_UTHAF, h, c.title) == ["Arnór Snæbjörnsson"]


def test_bylines_under_the_title():
    h = printed_title(ULF_2023_P8, "Sérgreiningarregla höfundaréttar")
    assert byline(ULF_2023_P8, h, "Sérgreiningarregla höfundaréttar") == ["Alexander Hafþórsson", "Sigríður Rut Júlíusdóttir"]
    title = "Viðbótargreinargerðir á Íslandi og í Danmörku"
    assert choose_title(title, LMBL_2020_P25) == (title, None, False)                     # the same title in capitals
    assert byline(LMBL_2020_P25, printed_title(LMBL_2020_P25, title), title) == ["Guðrún Olsen", "Bergþór Bergsson"]
    h = page_heading(LMBL_2017_P3)
    assert byline(LMBL_2017_P3, h, h.text) == ["Eva Halldórsdóttir"]
    for page, title, names in [(VEFRIT_GJAFA, "Gjafa- og séreignakaupmálar á Íslandi", ["Björn Ingi L. Jónsson", "Hrefna Friðriksdóttir"]),
                               (VEFRIT_SAMTAL, "Samtal Hæstaréttar og Endurupptökudóms", ["Sindri M. Stephensen", "Víðir Smári Petersen"]),
                               (TL_2007_P5, "Leiðbeiningarskylda stjórnvalds við meðferð stjórnsýslumáls", ["Páll Hreinsson"])]:
        assert byline(page, printed_title(page, title), title) == names


def test_credits_name_the_author_in_the_nominative():
    assert byline(LOGBRU_2013_P154, None, "Viðtöl við bifrestinga") == ["Kristján Örvar Sveinsson"]
    assert byline(LOGFR_2006_P78, printed_title(LOGFR_2006_P78, "Lögfræðiráðgjöf í Alþjóðahúsi"),
                  "Lögfræðiráðgjöf í Alþjóðahúsi") == ["Margrét Steinarsdóttir"]
    title = "Mál Mannréttindanefndar SÞ 1977-2008 — Handbók"
    assert byline(LOGFR_2011_P96, printed_title(LOGFR_2011_P96, title), title) == ["Margrét Heinreksdóttir"]


def test_an_obituary_keeps_its_label_and_the_person_it_heads_is_not_its_author():
    h = page_heading(ULF_2004_3240)
    assert h.text == "Minning: Arnljótur Björnsson"
    assert byline(ULF_2004_3240, h, h.text) == []
    assert choose_title(None, ULF_2004_3240) == ("Minning: Arnljótur Björnsson", None, True)


def test_editorials_signed_at_the_end():
    # TL 2010-1 pdf 6 (idx 0), Lögfræðingur 2006 pdf 5 and 7 (idx 0 and 1): the last lines of the last page
    tl = _page(637.8, (10.5, 476.2, "málefnaleg og afar traust. Það er raunar mergurinn máls­ins!"),
               (10.5, 512.2, "Róbert R. Spanó"), (12.5, 584.7, "5"), body=10.5, body_from=56.2, body_lines=10)
    assert signature(tl) == ["Róbert R. Spanó"]
    lf5 = _page(595.2, (9.9, 501.4, "Kristjánsdóttir kemur að þessum málum"), (10.9, 514.5, "Guðmundur Egill Erlendsson"),
                (8.6, 527.6, "Ritstjóri"), (9.0, 563.0, "Lögfræðingur 5"), body=9.5, body_from=60, body_lines=10)
    assert signature(lf5) == ["Guðmundur Egill Erlendsson"]
    lf7 = _page(595.2, (9.9, 497.9, "Vigdís Ósk Sveinsdóttir"), (9.7, 498.3, "þeir geta treyst og muni standa við"),
                (10.1, 511.1, "Formaður Þemis, nemendafélags"), (9.9, 511.5, "sefin loforð um aukin fjárútlát"),
                (10.5, 524.2, "laganema við HA"), (9.4, 524.8, "háskólans."), (9.0, 561.4, "Lögfræðingur 7"),
                body=9.5, body_from=60, body_lines=10)
    assert signature(lf7) == ["Vigdís Ósk Sveinsdóttir"]
    assert signature(ULF_2010_P10) == []


@pytest.mark.parametrize("registry, printed, authors, from_page", [
    (["Sigríður Rut Júlíusdóttir"], ["Alexander Hafþórsson", "Sigríður Rut Júlíusdóttir"],
     ["Alexander Hafþórsson", "Sigríður Rut Júlíusdóttir"], True),                     # Úlfljótur 2023-3 idx 0
    (["Viðar Már Matthíassson"], ["Viðar Már Matthíasson"], ["Viðar Már Matthíasson"], True),    # Rannsóknir 2003 idx 11
    (["Jóhannes Rúnar Jóhannesson"], ["Jóhannes Rúnar Jóhannsson"], ["Jóhannes Rúnar Jóhannsson"], True),  # idx 7
    ([], ["Róbert R. Spanó"], ["Róbert R. Spanó"], True),
    (["Eva Halldórsdóttir"], [], ["Eva Halldórsdóttir"], False),
    (["Eva Halldórsdóttir"], ["Eva Halldórsdóttir"], ["Eva Halldórsdóttir"], False),
])
def test_merge_authors(registry, printed, authors, from_page):
    c = merge_authors(registry, printed)
    assert (c.authors, c.from_page, c.note) == (authors, from_page, None)


def test_merge_authors_keeps_the_registry_when_the_page_names_someone_else():
    c = merge_authors(["Jóhann J. Ragnarsson"], ["Gaukur Jörundsson"])
    assert c.authors == ["Jóhann J. Ragnarsson"] and not c.from_page and "Gaukur Jörundsson" in c.note


def test_names_in_bylines():
    assert names_in("GUÐRÚN OLSEN LÖGMAÐUR OG BERGÞÓR BERGSSON LÖGFRÆÐINGUR SKRIFA") == ["Guðrún Olsen", "Bergþór Bergsson"]
    assert names_in("Lára V. Júlíusdóttir hdl.:") == ["Lára V. Júlíusdóttir"]
    assert names_in("ÁRMANN SNÆVARR, CAND. JUR.:") == ["Ármann Snævarr"]
    assert names_in("Héraðsdómslögmaður LS Legal") == []
    assert names_in("Formaður Þemis, nemendafélags") == []
    assert names_in("Útskriftarár 2006") == []


def test_match_score_and_title_at_top():
    assert match_score("Bætur fyrir lífs- og líkamstjón", "Bætur fyrir lífs- og líkamstjórn").score >= 0.85
    assert match_score("Lög", "Lög um dómstóla").score < 0.85
    assert title_at_top(ULF_2010_P10, "Fjármálamarkaður innan Evrópusambandsins og EES reglur og eftirlit") == 1.0
    assert title_at_top(ULF_2010_P76, "Fjármálamarkaður innan Evrópusambandsins og EES reglur og eftirlit") == 0.0


def test_recase():
    assert recase("UM RÉTT TIL LAUNA í VEIKINDUM", "Af vettvangi dómsmála: Um rétt til launa f veikindum") == \
        "Um rétt til launa í veikindum"
    assert recase("JÓLASNAPSMÓT LMFÍ") == "Jólasnapsmót LMFÍ"
    assert recase("Frá aðalfundi") == "Frá aðalfundi"


def test_read_page_joins_spans_on_a_line_and_keeps_columns_apart():
    d = fitz.open()
    p = d.new_page(width=420, height=595)
    p.insert_text((40, 60), "Lögfræðiráðgjöf", fontsize=30)
    p.insert_text((40 + fitz.get_text_length("Lögfræðiráðgjöf", fontsize=30) + 8, 60), "í Alþjóðahúsi", fontsize=22)
    p.insert_text((40, 300), "vinstri dálkur", fontsize=10)
    p.insert_text((250, 300), "hægri dálkur", fontsize=10)
    page = read_page(d[0])
    assert [ln.text for ln in page.lines] == ["Lögfræðiráðgjöf í Alþjóðahúsi", "vinstri dálkur", "hægri dálkur"]
    assert page.lines[0].size == 30 and page.height == 595


def test_page_heads_digest():
    # Lögrétta 2010: no issue TOC; every article starts on a page of its own under the article's letter and number
    pages = [Page([Line("a", 9, 20), Line("9", 9, 32), Line("Úrræði lánveitanda við", 20, 80),
                   Line("vanefndir lántaka I – Gjaldfelling lánasamninga", 20, 105), Line("Stefán A. Svensson", 11, 140)], 600),
             Page([Line("a", 9, 20), Line("10", 9, 32), Line("1. Inngangur", 11, 60), Line(_BODY, 10, 80)], 600),
             Page([Line("b", 9, 20), Line("25", 9, 32), Line("Forgangsáhrif réttilega", 20, 80),
                   Line("innleiddra EES-reglna", 20, 105)], 600)]
    assert page_heads_digest(pages, [9, 10, None]).splitlines() == [
        "[0] p.9: Úrræði lánveitanda við | vanefndir lántaka I – Gjaldfelling lánasamninga",
        f"[1] p.10: 1. Inngangur | {_BODY[:70]}",
        "[2] p.?: Forgangsáhrif réttilega | innleiddra EES-reglna",
    ]


# --- the decision against OCR'd pages (pilot pages beyond the audited sample) ----------------------------------------

def test_an_ocr_split_or_misread_heading_does_not_replace_the_spreadsheet_title():
    # Úlfljótur 1947 211.pdf and 231.pdf: "V iðskiptabréf sreglur" / "um hlutabréf.", "FJÁRTJÓN OG MISKL"
    p211 = _page(1388.0, (28.8, 364.8, "ÓLAFUR LÁRUSSON:"), (32.4, 432.1, "V iðskiptabréf sreglur"),
                 (32.5, 476.4, "um hlutabréf."), body=23.5, body_from=568.7)
    assert choose_title("Viðskiptabréfsreglur um hlutabréf", p211) == ("Viðskiptabréfsreglur um hlutabréf", None, False)
    p231 = _page(1388.0, (25.0, 404.1, "ÓLAFUR JÓHANNESSON:"), (38.9, 467.0, "FJÁRTJÓN OG MISKL"), body=23.4, body_from=601.7)
    assert choose_title("Fjártjón og miski", p231) == ("Fjártjón og miski", None, False)
    # Úlfljótur 1975 357.pdf: a Danish heading in capitals, OCR'd ("H0JESTE-", "(§67)!)")
    p357 = _page(1391.0, (23.1, 60.0, "Professor dr. juris Gaukur Jörundsson:"), (31.0, 110.0, "ISLANDSKE H0JESTE-"),
                 (31.6, 150.0, "RETSDOMME DER HAR"), (31.9, 190.0, "BETYDNING FOR"), (32.4, 230.0, "FORTOLKNING AF DEN"),
                 (31.5, 270.0, "ISLANDSKE GRUNDLOVS"), (31.7, 310.0, "EKSPROPRIATIONS-"), (31.2, 350.0, "BESTEMMELSE (§67)!)"),
                 body=24.2, body_from=420)
    title = ("Islandske höjesteretsdomme der har betydning for fortolkning af den islandske grundlovs "
             "ekspropriationsbestemmelse (67)")
    assert choose_title(title, p357) == (title, None, False)


def test_a_heading_that_lacks_a_word_printed_elsewhere_on_the_page_keeps_the_registry_title():
    # Úlfljótur 1957 1041.pdf: the drop cap "Um" is not in the text layer: "störf og samskipti" / "lögmanna."
    p = _page(1401.0, (23.7, 280.6, "EINAR B. GUÐMUNDSSON:"), (42.9, 353.7, "störf og samskipti"), (42.9, 434.3, "lögmanna."),
              (23.7, 537.7, "Ég hygg að fáir eða engir kynnist eins vel um einkahögum manna"), body=23.7, body_from=562.7)
    assert choose_title("Um störf og samskipti lögmanna", p) == ("Um störf og samskipti lögmanna", None, False)


def test_a_double_obituary_keeps_both_names_and_an_office_after_a_name_is_not_title():
    # TL 1985 pdf 84 ("BIRGIR ÁSGEIRSSON"; "PÁLL PÁLMASSON" on the next page); Úlfljótur 1975 1750.pdf
    p84 = _page(631.0, (69.6, 41.0, "t"), (13.2, 98.7, "BIRGIR ÁSGEIRSSON", True), body=9.6, body_from=115.2)
    t = "Birgir Ásgeirsson — Páll Pálmason"
    assert choose_title(t, p84) == (t, None, False)
    p1750 = _page(1391.0, (38.0, 300.0, "TÖNNES MADSSON ANDENÆS, stórþingsmaöur."), body=24.2, body_from=427.1)
    assert choose_title("Tönnes Madsson Andenæs", p1750) == ("Tönnes Madsson Andenæs", None, False)


def test_a_section_label_over_two_lines_and_misread_by_the_ocr():
    # TL 1985 pdf 75 and 215: "Ávíð" / "02 dreif", "Ávíð" / "og dreif", then the item's own headline; the TOC has
    # "Á víð og dreif" or, misread, "Á vfð og dreif"
    p75 = _page(626.0, (31.8, 60.0, "Ávíð", True), (30.1, 88.0, "02 dreif", True),
                (13.3, 138.7, "NORRÆNT LAGANEMAMÓT Á ÍSLANDI", True), body=9.5, body_from=154.1)
    c = choose_title("Á víð og dreif", p75)
    assert c.title == "Norrænt laganemamót á Íslandi" and c.label == "Á víð og dreif" and c.from_page
    p215 = _page(632.0, (30.1, 73.7, "Ávíð", True), (32.4, 99.6, "og dreif", True),
                 (13.3, 162.4, "EMBÆTTISSKIPANIR OG LAUSNIR", True), (12.6, 177.6, "FRÁ 1. MAÍ 1983 TIL 1. DES. 1985", True),
                 body=9.6, body_from=205.6)
    assert choose_title("Á vfð og dreif", p215).title == "Embættisskipanir og lausnir frá 1. maí 1983 til 1. des. 1985"


def test_bylines_that_are_not():
    # Lögfræðingur 2006 pdf 14: the author under the title in its own type; the epigraph's author is no author
    p14 = _page(595.0, (25.7, 68.2, "THE EVOLVING"), (25.7, 98.4, "CONSTITUTION OF"), (26.9, 128.3, "NORTHERN IRELAND“*"),
                (11.9, 158.5, "Tim Murphy"), (9.3, 191.4, "from the latter part of the twelfth"),
                (9.6, 191.7, "History, Stephen said, is a nightmare"), (9.5, 220.0, "James Joyce, Ulysses"),
                body=9.6, body_from=240)
    t = "The evolving constitution of Northern Ireland"
    assert byline(p14, printed_title(p14, t), t) == ["Tim Murphy"]
    # Lögfræðingur 2011 pdf 151: a list of new lawyers under "Lögfræðingatal"
    p151 = _page(595.0, (20.4, 52.2, "Lögfræðingatal"),
                 (9.2, 101.7, "Í júní 2010 brautskráðust 11 meistaranemar með M.L.-próf í lögfræði frá"),
                 (10.2, 184.4, "Birna Ágústsdóttir"), (9.4, 200.2, "f. 10. nóvember 1981"), body=9.2, body_from=230)
    assert byline(p151, printed_title(p151, "Lögfræðingatal"), "Lögfræðingatal") == []
    # Úlfljótur 1957 1059.pdf: the body names people in the dative ("Ármanni Snævarr boðið")
    p1059 = _page(1401.0, (25.7, 79.6, "GUDRÚN ERLENDSDÓTTIR, stud. jur.:"), (33.9, 153.8, "Laganemamót í Danmörku í ágúst 1958."),
                  (24.6, 256.3, "Fyrsta norræna laganemamótið var haldið árið 1918. Síðan hafa þau verið"),
                  (24.9, 338.1, "Magnús Thoroddsen, stud. jur. Benedikt Blöndal, hdl. Þorvaldur Ari Ara-"),
                  (24.3, 400.6, "Ármanni Snævarr boðið sem fyrirlesara."), body=24.7, body_from=430)
    t = "Laganemamót í Danmörku í ágúst 1958"
    names = byline(p1059, printed_title(p1059, t), t)
    assert names == ["Gudrún Erlendsdóttir"]
    assert merge_authors(["Guðrún Erlendsdóttir"], names).authors == ["Guðrún Erlendsdóttir"]   # the OCR lost the ð
    # Stjórnmál og stjórnsýsla 10 (387-398): two authors in the same type; "Social and Human Sciences" is affiliation
    pal = _page(680.0, (12.0, 13.9, "STJÓRNMÁL"), (20.0, 106.9, "The Palermo Protocol: Trafficking Takes it All"),
                (12.0, 154.9, "Jónína Einarsdóttir, Professor, Faculty of Social and Human Sciences,", True),
                (12.0, 169.4, "University of Iceland"),
                (12.0, 183.7, "Hamadou Boiro, Researcher, Instituto Nacional de Estudos e Pesquisa", True),
                (12.0, 198.2, "(INEP), Guinea-Bissau."), (13.0, 236.1, "Abstract", True), body=10.6, body_from=251.8)
    t = "The Palermo Protocol: Trafficking Takes it All"
    assert byline(pal, printed_title(pal, t), t) == ["Jónína Einarsdóttir", "Hamadou Boiro"]


@pytest.mark.parametrize("registry, printed, authors", [
    (["Ólaíur Jóhannesson"], ["Ólafur Jóliannesson"], ["Ólafur Jóhannesson"]),       # TL 1955: each side misreads one
    (["Benedikt Sigurjónsson"], ["Benedilct Sigurjónsson"], ["Benedikt Sigurjónsson"]),
    (["Ísleifur Árnason"], ["Ísleifur Arnason"], ["Ísleifur Árnason"]),               # Úlfljótur 1947: accents lost
    (["Viðar Lúðvílcsson"], ["Viðar Lúðvíksson"], ["Viðar Lúðvíksson"]),             # TL 2004: the TOC misreads
])
def test_merge_authors_spells_each_name_part_as_bin_knows_it(registry, printed, authors):
    assert merge_authors(registry, printed).authors == authors


def test_a_drop_cap_does_not_make_a_body_line_a_heading_and_a_year_range_is_no_page_number():
    d = fitz.open()
    p = d.new_page(width=420, height=700)
    p.insert_text((40, 120), "Þ", fontsize=40)
    p.insert_text((40 + fitz.get_text_length("Þ", fontsize=40) + 2, 120), "inn. Þá var ný stjórn kjörin en í henni sitja",
                  fontsize=10)
    p.insert_text((40, 60), "2003-2005", fontsize=20)
    page = read_page(d[0])
    body = next(ln for ln in page.lines if "stjórn" in ln.text)
    assert body.size == 10
    from engine.timarit.firstpage import is_furniture
    assert not is_furniture(next(ln for ln in page.lines if ln.text == "2003-2005"))
    assert is_furniture(Line("271", 10, 600)) and is_furniture(Line("Úlfljótur 271", 10, 600))


@pytest.mark.parametrize("lines, title", [
    (["Skipting á skattlagningarréttinum milli", "heimilisfestarríkis og keldulands1)2"],     # Lögrétta 2010 (39)
     "Skipting á skattlagningarréttinum milli heimilisfestarríkis og keldulands"),
    (["EFTA-dómstóllinn í 10 ár: Hvernig er samskiptum", "við Evrópudómstólinn háttað?1"],      # TL 2004
     "EFTA-dómstóllinn í 10 ár: Hvernig er samskiptum við Evrópudómstólinn háttað?"),
    (["Framkvæmd skráningar EES-", "launþega hér á landi"], "Framkvæmd skráningar EES-launþega hér á landi"),
    (["BÆTUR FYRIR LÍFS-", "OG LÍKAMSTJÓN"], "BÆTUR FYRIR LÍFS- OG LÍKAMSTJÓN"),
    (["Stúdentaskiptin við Þýzkaland."], "Stúdentaskiptin við Þýzkaland"),
    (["Frá Félagsdómi o.fl."], "Frá Félagsdómi o.fl."),
])
def test_join_title(lines, title):
    from engine.timarit.firstpage import join_title
    assert join_title(lines) == title


# --- review round 1 (Task 22b) --------------------------------------------------------------------------------------

def test_a_running_head_on_the_folios_baseline_is_not_a_heading():
    # Rannsóknir VIII (2007) pdf 10: the odd page repeats the article's title beside the folio ("…Grágásar" / "11")
    p10 = Page([Line("Nokkur orð um eignarhugtakið í Landabrigðisþætti Grágásar", 11.9, 35.5), Line("11", 11.9, 35.5)]
               + [Line(_BODY, 11.0, 71.5 + 13 * k) for k in range(30)], 842.0)
    from engine.timarit.firstpage import top_lines
    assert "Grágásar" not in " ".join(ln.text for ln in top_lines(p10))
    assert printed_title(p10, "Nokkur orð um eignarhugtakið í Landabrigðisþætti Grágásar") is None
    # a lone letter on a heading's line is no folio (vefrit: "Um" "tilkynningarskyldu" "lögmanna" "í" on one baseline)
    vef = Page([Line("Um", 26.0, 286.2), Line("tilkynningarskyldu", 26.0, 286.2), Line("lögmanna", 26.0, 286.2),
                Line("í", 26.0, 286.2), Line("tengslum við aðgerðir", 26.0, 315.9)], 842.0)
    assert [ln.text for ln in top_lines(vef)][:3] == ["Um", "tilkynningarskyldu", "lögmanna"]


@pytest.mark.parametrize("text, size, body, names", [
    ("Í Sjálfstæðu fólki eftir Halldór Kiljan Laxness segir frá því, þegar sveitungar", 11.0, 11.0, []),   # Ranns. 2005
    ("„Retskilder\" heitir bók eftir Per Aucjdahl, sem kom út", 11.5, 11.3, []),                         # TL 1955
    ("CJt er komin bókin Okutæki og tjónbætur eftir Arnljót Björnsson, sem lengi", 23.8, 23.5, []),       # Úlfljótur 2004
    ("eftir Hæstarétti Íslands", 10.0, 10.0, []),
    ("Ritdómur eftir Margréti Heinreksdóttur", 9.7, 9.4, ["Margrét Heinreksdóttir"]),
    ("Eftir Arnór Snæbjörnsson, lögfræðing í sjávarútvegsráðuneytið", 14.0, 12.0, ["Arnór Snæbjörnsson"]),
    ("viðtölum. Texti: Kristján Örvar Sveinsson - Myndir: Sigurður Steinar Ásgeirsson", 10.0, 10.0,
     ["Kristján Örvar Sveinsson"]),
])
def test_only_credit_lines_name_authors_never_running_text(text, size, body, names):
    from engine.timarit.firstpage import _credit_names
    assert _credit_names(Line(text, size, 300.0), body) == names


def test_running_text_with_eftir_does_not_hide_the_byline():
    # Rannsóknir 2005 pdf 270 and Úlfljótur 2004 3238.pdf (a review: the book's author above the title)
    p270 = _page(842.0, (18.6, 71.0, "Slægð eða ofbeldi?"), (18.6, 92.1, "Um ákvæði 196. gr. hgl."),
                 (14.0, 132.1, "Ragnheiður Bragadóttir"), (14.0, 207.3, "I. Inngangur"),
                 (11.0, 236.7, "Í Sjálfstæðu fólki eftir Halldór Kiljan Laxness segir frá því, þegar sveitungar"),
                 body=11.0, body_from=249.7)
    t = "Slægð eða ofbeldi? Um ákvæði 196. gr. hgl."
    assert byline(p270, printed_title(p270, t), t) == ["Ragnheiður Bragadóttir"]
    p3238 = _page(1410.0, (67.1, 173.7, "BOKABALKUR"), (32.9, 334.7, "Arnljótur Björnsson"),
                  (58.3, 373.5, "Ökutæki og tjónbætur"), (28.4, 446.8, "Hið íslenzka bókmenntafélag, 2003"),
                  (21.3, 656.6, "Viðar Már Matthíasson,", True), (20.6, 680.3, "prófessor"), (30.2, 744.0, "1. Inngangur"),
                  (23.8, 776.0, "CJt er komin bókin Okutæki og tjónbætur eftir Arnljót Björnsson, sem lengi"),
                  body=23.5, body_from=800.9)
    t = "Ökutæki og tjónbætur"
    assert byline(p3238, printed_title(p3238, t), t) == ["Viðar Már Matthíasson"]


def test_page_heading_takes_one_heading_not_its_author_byline_or_lead():
    # TL 2004 pdf 202: "Þorgeir Örlygsson:" above "RÉTTARREGLUR UM GALLA í" / "LAUSAFJÁRKAUPUM"
    p202 = _page(625.0, (9.2, 180.1, "Þorgeir Örlygsson er dómari Islands", True), (13.8, 290.0, "Þorgeir Örlygsson:"),
                 (13.9, 313.3, "RÉTTARREGLUR UM GALLA í", True), (13.9, 331.9, "LAUSAFJÁRKAUPUM", True),
                 (10.5, 358.7, "1. INNGANGUR"), body=10.5, body_from=380)
    assert page_heading(p202).text == "Réttarreglur um galla í lausafjárkaupum"
    # Rannsóknir 2003 pdf 92: the byline under the title in smaller type is not part of it
    p92 = _page(652.0, (18.6, 81.4, "Ákvörðun um saksókn"), (18.6, 101.8, "frá sjónarhorni verjanda"),
                (15.3, 142.7, "Jóhannes Rúnar Jóhannsson"), (14.0, 194.7, "I. Inngangur."), body=11.0, body_from=227.8)
    assert page_heading(p92).text == "Ákvörðun um saksókn frá sjónarhorni verjanda"
    # Lögmannablaðið 2017 pdf 31: the 22 pt subtitle follows the 52 pt title; the 14 pt lead stays out (round 2)
    p31 = _page(841.9, (52.0, 323.9, "JÓLASNAPSMÓT LMFÍ", True), (22.0, 400.0, "MEISTARAMÓT LMFÍ Í KNATTSPYRNU INNANHÚSS 2016"),
                (14.0, 442.0, "Skömmu fyrir hátíð ljóss og friðar, eða fimmtudaginn 22. desember, fór fram"),
                (14.0, 458.8, "meistaramót LMFÍ í knattspyrnu innandyra, svokallað Jólasnapsmót. Að venju fór"),
                body=10.2, body_from=532.5)
    assert page_heading(p31).text == "Jólasnapsmót LMFÍ meistaramót LMFÍ í knattspyrnu innanhúss 2016"
    # TL 1985 pdf 209: the column logo "Af vettvangi" / "dómsmála" as the OCR read it is a label, not the heading
    assert page_heading(TL_1985_P209) is None or "lóinsinála" not in page_heading(TL_1985_P209).text
    assert choose_title("Af vettvangi dómsmála: Hæstaréttardómur frá 31. maf 1985", TL_1985_P209).label == \
        "Af vettvangi dómsmála"


# --- review round 2: a title block in steps of size, "Main title:" lines, subtitles ------------------------------------
# (case, page height, body size, the page's content lines around the heading as read_page gives them, heading)
_TITLE_BLOCKS = [
    ('logmannabladid/2017_x_1#11', 842, 14.0, [(78.0, 357.0, 'MEGINREGLAN', True), (44.0, 452.4, 'UM LÖGVARÐA HAGSMUNI\xad', False), (14.0, 551.9, 'Þann 19. janúar sl. stóðu Lögmannafélag Íslands og Dómarafélag Íslands fyrir', False), (14.0, 568.7, 'hádegisverðarfundi um inntak meginreglu íslensks réttarfars um lögvarða', False), (14.0, 585.5, 'hagsmuni. Meginreglan fjallar um það grundvallar skilyrði íslensks réttarfars að', False)],
     'Meginreglan um lögvarða hagsmuni'),
    ('logmannabladid/2017_x_1#24', 842, 10.2, [(80.0, 117.8, 'ÖFLUN', False), (60.0, 183.9, 'MÁLFLUTNINGS\xad', False), (60.0, 231.9, 'RÉTTINDA FYRIR', False), (60.0, 279.9, 'ÆÐRA DÓMSTIGI', False), (14.0, 376.8, 'Margir lögmenn reyna nú hvað þeir geta til að fá prófmál fyrir Hæstarétti áður en', False), (14.0, 393.6, 'Landsréttur tekur til starfa 1. janúar 2018. Að sögn Þorsteins Jónssonar, skrifstofustjóra', False), (14.0, 410.4, 'Hæstaréttar, hafa 48 lögmenn tilkynnt að þeir hyggist flytja prófmál fyrir Hæstarétti á', False)],
     'Öflun málflutningsréttinda fyrir æðra dómstigi'),
    ('logmannabladid/2017_x_1#27', 842, 10.2, [(66.0, 139.3, 'SAMSKIPTI', False), (45.0, 198.9, 'LÖGMANNA ÚR BÖNDUM', False), (22.0, 274.4, 'AF ÁHUGAVERÐUM ÚRSKURÐI', False), (22.0, 300.8, 'ÚRSKURÐARNEFNDAR LÖGMANNA', False), (12.0, 367.1, 'Hæstaréttarlögmaðurinn Hermóður sætti áminningu úrskurðarnefndar lögmanna í máli nr.', False), (12.0, 381.5, '16/2016 vegna brota gegn 2. gr., 2. mgr. 8. gr., 25. gr., 27. gr. og 30. gr. siðareglna lö', False), (12.0, 395.9, 'sem fjalla um bann við því að lögmenn samsami sig umbjóðendum sínum og um samskipti', False)],
     'Samskipti lögmanna úr böndum af áhugaverðum úrskurði úrskurðarnefndar lögmanna'),
    ('logmannabladid/2017_x_1#29', 842, 10.2, [(53.0, 95.2, 'ÁNÆGJA OG', True), (53.0, 150.2, 'TRAUST TIL', True), (46.0, 214.7, 'DÓMSTÓLANNA', False), (22.0, 271.7, 'KÖNNUN DÓMSTÓLARÁÐS', False), (13.2, 324.6, 'Um síðustu áramót lét dómstólaráð framkvæma könnun með það að', False), (13.2, 339.5, 'markmiði að kanna ýmsa þjónustuþætti og traust til dómstólanna.', False), (10.2, 387.9, 'Að sögn Ólafar Finnsdóttur framkvæmdastjóra dómstóla\xad', False)],
     'Ánægja og traust til dómstólanna könnun Dómstólaráðs'),
    ('logfraedingur/2006_1_x#57', 595, 9.3, [(22.2, 133.2, 'Er virkt þingræði', False), (19.9, 165.4, 'á Íslandi?', False), (9.9, 207.4, 'Húni Heiðar Hallsson', False), (10.2, 237.3, '1. Inngangur', False), (9.1, 237.5, 'tilvitnuninni gerir Bjarni sig sekan', False)],
     'Er virkt þingræði á Íslandi?'),
    ('logfraedingur/2006_1_x#14', 595, 9.3, [(25.7, 68.2, 'THE EVOLVING', False), (25.7, 98.4, 'CONSTITUTION OF', False), (23.9, 128.3, 'NORTHERN IRELAND“*', False), (11.9, 158.5, 'Tim Murphy', False), (8.2, 191.4, 'from the latter part of the twelfth', False), (9.6, 191.7, 'History, Stephen said, is a nightmare', False)],
     'THE evolving constitution of northern ireland“'),
    ('logfraedingur/2012_6_x#8', 595, 8.7, [(9.5, 53.2, 'Hjördís Olga Guðbrandsdóttir', False), (21.9, 157.3, 'Ávarp formanns', False), (20.5, 183.5, 'Málfundafélags Þemis', False), (8.8, 228.1, 'Telders, alþjóðleg málflutningskeppni, sem haldin er ár hvert í Haag, fór', False), (7.7, 241.6, 'fram dagana 26.-28. apríl síðasliðinn en tilefni þessa pistils, sem nú birtist í', False), (8.4, 255.2, 'Lögfræðingi er það ánægjuefni að í fyrsta skipti átti lagadeild Háskólans á', False)],
     'Ávarp formanns Málfundafélags Þemis'),
    ('timarit_logfraedinga/1985_x_x#137', 622, 9.6, [(31.6, 58.0, 'Frá', True), (32.0, 85.7, 'Lögmannafélagi', True), (29.5, 113.9, 'íslanfls', True), (13.2, 163.1, 'UM STARFSEMI LÖGMANNAFÉLAGS ÍSLANDS', True), (13.2, 178.9, 'STARFSÁRIÐ 1984-1985', True), (9.6, 197.3, 'Aðalfundur L.M.F.Í. var haldinn 29. mars s.l. Formaður, Jón Steinar Gunn-', False), (9.5, 209.4, 'laugsson hrl., minntist í upphafi látinna félagsmanna, þeirra Birgis Ásgeirsson-', False), (9.6, 218.1, 'ar hdl., Einars Viðars hrl., Héðins Finnbogasonar hdl., Kristjáns Eiríkssonar', False)],
     'Frá Lögmannafélagi íslanfls UM STARFSEMI LÖGMANNAFÉLAGS ÍSLANDS STARFSÁRIÐ 1984-1985'),
    ('logmannabladid/2020_x_1#23', 842, 10.2, [(34.0, 98.1, 'STARFSÁBYRGÐAR\xad', False), (34.0, 135.1, 'TRYGGINGAR', False), (45.0, 172.6, 'LÖGMANNA', True), (10.0, 239.8, 'ARNA PÁLSDÓTTIR LÖGMAÐUR SKRIFAR', False), (14.0, 328.8, 'Viðarsdóttur lögmann og', False), (14.0, 345.6, 'framkvæmdastjóra tjónasviðs TM og', False)],
     'Starfsábyrgðartryggingar lögmanna'),
    ('logmannabladid/2020_x_1#33', 842, 10.2, [(37.0, 112.0, 'LÖGMENNSKA', False), (42.0, 155.9, 'Á LANDS\xad', True), (42.0, 204.9, 'BYGGÐINNI', True), (10.0, 259.6, 'ARI KARLSSON LÖGMAÐUR SKRIFAR', False), (10.2, 341.2, 'Hverjir eru helstu kostir við að reka stofu á landsbyggðinni?', False), (10.1, 355.2, 'Helsti kosturinn að mínu mati er að verkefnin sem við fáum', False)],
     'Lögmennska á landsbyggðinni'),
    ('logmannabladid/2020_x_1#25', 842, 10.2, [(34.0, 71.9, 'VIÐBÓTARGREINARGERÐIR', False), (44.0, 112.3, '- Á ÍSLANDI OG', True), (44.0, 162.3, 'Í DANMÖRKU', True), (10.0, 220.0, 'GUÐRÚN OLSEN LÖGMAÐUR OG BERGÞÓR BERGSSON LÖGFRÆÐINGUR SKRIFA', False), (14.0, 271.2, 'Hæstiréttur hefur staðfest í dóma\xad', False), (12.0, 277.1, 'Í DANMÖRKU ÞURFA AÐILAR MÁLS EKKI AÐ', False)],
     'Viðbótargreinargerðir - á Íslandi og í Danmörku'),
    ('logfraedingur/2010_4_x#7', 595, 10.0, [(8.0, 144.7, 'rently pursuing an LL.M degree in Commercial Law at University College', False), (8.0, 154.7, 'London.', False), (20.0, 179.8, 'Resolving Iceland‘s Debt Crisis:', False), (20.0, 202.8, 'Causes, Sovereign', False), (20.0, 225.8, 'Debt and Future Prospects', False), (10.0, 266.9, 'Introduction', True), (10.0, 280.4, '2008 will enter the annals of financial history alongside the Great Depression, Black', False), (10.0, 293.9, 'Monday of 1987 and the dotcom bubble of the early 2000s as one of the most sig-', False)],
     'Resolving Iceland‘s Debt Crisis: Causes, Sovereign Debt and Future Prospects'),
    ('logfraedingur/2011_5_x#27', 595, 8.7, [(5.8, 191.8, 'mannsins á Blönduósi. Greinin er byggð á lokaritgerð hennar til M.L. prófs', False), (5.8, 200.6, 'í lögfræði við Háskólann á Akureyri.', False), (18.6, 238.5, 'ICESAVE: Ber íslenska ríkið', False), (18.7, 260.6, 'ábyrgð á grundvelli megin-', False), (19.5, 284.8, 'reglna Evrópuréttar um bann', False), (18.7, 308.9, 'við mismunun á grundvelli ríkis-', False), (20.3, 331.9, 'fangs og bótaskyldu ríkis?', False), (10.4, 374.1, '1. Inngangur', False), (8.7, 390.3, 'Icesave-deila íslenska ríkisins við breska og hollenska ríkið hefur vart farið', False), (8.3, 405.2, 'framhjá mörgum Íslendingum. Deilan er sprottin af tilskipun Evrópusam-', False)],
     'ICESAVE: Ber íslenska ríkið ábyrgð á grundvelli meginreglna Evrópuréttar um bann við mismunun á grundvelli ríkisfangs og bótaskyldu ríkis?'),
    ('ulfljotur/2023_x_3#44', 666, 10.0, [(24.0, 53.3, 'Vímuefna- og sviptingarakstur:', False), (24.0, 80.8, 'Reglur og viðurlög*', False), (10.0, 184.5, 'Karitas Rán Garðarsdóttir', True), (10.0, 196.5, 'aðstoðarmaður dómara við Héraðsdóm Reykjavíkur', False), (12.0, 232.4, 'Efnisyfirlit', True)],
     'Vímuefna- og sviptingarakstur: Reglur og viðurlög'),
    ('logretta/2010_x_1#0', 667, 11.0, [(10.0, 166.4, 'Stefán A. Svensson', True), (10.0, 178.7, 'hrl., LL.M.', False), (30.0, 208.4, 'Úrræði lánveitanda við', False), (30.0, 244.4, 'vanefndir lántaka I', False), (26.0, 278.3, '- Gjaldfelling lánasamninga -', False), (11.0, 390.0, '1. Inngangur', False), (11.0, 403.0, '2. Vanefndir samkvæmt lánasamningum', False), (11.0, 415.8, '2.1. Almennt', False)],
     'Úrræði lánveitanda við vanefndir lántaka I - Gjaldfelling lánasamninga'),
    ('logretta/2005_x_1#0', 666, 9.5, [(11.0, 121.9, 'héraðsdómari og dósent', False), (11.0, 135.1, 'við lagadeild H.Í.', False), (30.0, 179.2, '„Hvers vegna eruð Þið', False), (30.0, 215.2, 'alltaf að Þessu Þrasi?“', False), (15.0, 260.4, 'Hugleiðingar um lög og lagalegan ágreining', False), (9.5, 319.1, 'Deilur um lagaleg efni eru núorðið nánast einfaldlega ekki líklegur til að ná sínu fram.', False), (9.5, 331.1, 'fastur liður í umræðum um öll meiri háttar', False), (9.5, 331.1, 'Það kemur því vart á óvart að', False)],
     '„Hvers vegna eruð Þið alltaf að Þessu Þrasi?“ Hugleiðingar um lög og lagalegan ágreining'),
    ('logretta/2005_x_1#31', 666, 9.5, [(11.0, 113.1, 'Jóhannes Karl Sveinsson', True), (11.0, 133.3, 'hrl', False), (30.0, 178.9, 'Félagsform íslensku', False), (30.0, 214.9, 'sparisjóðanna', False), (15.0, 261.7, 'hlutafélagavæðing og SPRON-málin', False), (9.5, 460.5, 'Í lögum um fjármálafyrirtæki nr.', False), (13.0, 460.6, '1. Umfjöllunarefnið', False), (9.5, 472.5, '161/2002 eru ýmis sérákvæði um sparisjóði', False)],
     'Félagsform íslensku sparisjóðanna hlutafélagavæðing og SPRON-málin'),
    ('rannsoknir_lagadeild/2005_6_x#388', 842, 11.0, [(18.6, 70.9, 'Aðgangur almennings að upplýsingum í', False), (18.6, 90.0, 'starfsmannamálum', False), (15.3, 129.0, 'Ákvæði upplýsingalaga og réttarframkvæmd', False), (15.3, 164.6, 'Þórhallur Vilhjálmsson', False), (14.0, 239.8, 'I. Inngangur', False), (11.0, 269.1, 'Opinber starfsmannaréttur fæst við það að lýsa vinnusambandi opinberra', False)],
     'Aðgangur almennings að upplýsingum í starfsmannamálum Ákvæði upplýsingalaga og réttarframkvæmd'),
    ('logfraedingur/2008_2_x#8', 595, 8.6, [(10.4, 50.4, 'Aðalheiður Ánundadóttir:', False), (6.2, 64.4, 'Lögfræðinemi á 3. ári við Háskólann á Akureyri.', False), (17.2, 110.0, 'Um íslenska', False), (17.9, 133.8, 'fiskveiðistjórnunarkerfið', False), (10.1, 162.6, 'Og skyldu íslenska ríkisins til að virða', False), (11.5, 177.5, 'álit Mannréttindanefndar Sameinuðu', False), (11.4, 192.7, 'þjóðanna', False), (10.4, 226.2, 'Inngangur', False), (9.1, 242.2, 'Mannréttindanefnd Sameinuðu þjóðanna var stofnuð skv. 28. gr. Alþjóða-', False), (8.7, 257.4, 'samnings um borgaraleg og stjórnmálaleg réttindi (hér eftir „mannréttinda-', False)],
     'Um íslenska fiskveiðistjórnunarkerfið Og skyldu íslenska ríkisins til að virða álit Mannréttindanefndar Sameinuðu þjóðanna'),
    ('logmannabladid/2017_x_1#7', 842, 14.0, [(57.0, 514.2, 'HÆGIR Á FJÖLGUN', True), (36.0, 584.7, 'FÉLAGSMANNA Í', False), (36.0, 634.7, 'LÖGMANNAFÉLAGI ÍSLANDS', False), (14.0, 692.9, 'Í byrjun mars 2017 voru félagsmenn í Lögmannafélagi Íslands 1087 talsins og', False), (14.0, 709.7, 'hefur þeim því aðeins fjölgað um þrjá frá sama tíma árið 2016 eða um 0,3%.', False), (14.0, 726.5, 'Eru héraðsdómslögmenn nú 776 talsins og hæstaréttarlögmenn 311.', False)],
     'Hægir á fjölgun félagsmanna í Lögmannafélagi Íslands'),
    ('logmannabladid/2020_x_1#5', 842, 10.2, [(32.0, 115.4, 'AF VETTVANGI', False), (32.0, 147.4, 'FÉLAGSINS', False), (10.0, 210.0, 'BERGLIND SVAVARSDÓTTIR LÖGMAÐUR', False), (10.0, 222.0, 'FORMAÐUR', False), (10.3, 259.0, 'Um langa hríð hafa tafir á afgreiðslu fjölskyldumála hafa nokkrar tillögur þess efnis veri', False)],
     'Af vettvangi félagsins'),
    # review round 3: a byline with a footnote star ends the block; a lead with a sentence in it is no subtitle; a
    # title the OCR starts in lower case is still a title (no byline above it)
    ('rannsoknir_lagadeild/2010_11_ritstrynt#25', 842, 11.0, [(18.7, 77.3, 'Um afmörkun og endurmat á stjórnskipulegri', False), (18.7, 96.4, 'stöðu og hlutverki forseta Íslands', False), (15.4, 135.6, 'Björg Thorarensen*', False), (14.0, 193.3, '1. Inngangur', False), (11.0, 223.2, 'Stefnt hefur verið að endurskoðun stjórnarskrár lýðveldisins Íslands nr. 33/1944 allt', False)],
     'Um afmörkun og endurmat á stjórnskipulegri stöðu og hlutverki forseta Íslands'),
    ('rannsoknir_lagadeild/2010_11_ritstrynt#48', 842, 11.0, [(18.7, 77.3, 'Hvernig má tryggja hæfilega valddreifingu í', False), (18.7, 96.4, 'íslenskri stjórnskipun?', False), (15.4, 135.6, 'Eiríkur Tómasson*', False), (11.0, 194.4, 'Í fyrri hluta þessarar greinar mun ég leitast við að svara tveimur spurningum: Í fyrsta', False), (11.0, 207.4, 'lagi hvort hæfileg dreifing ríkisvaldsins milli ólíkra valdhafa, þannig að þeir hafi visst', False)],
     'Hvernig má tryggja hæfilega valddreifingu í íslenskri stjórnskipun?'),
    ('logmannabladid/2005_x_2#21', 700, 9.0, [(42.9, 44.4, 'Heyrt og séð', True), (22.5, 94.5, 'á hinni umtöluðu árshátíð LMFÍ', False), (13.0, 133.5, 'Árshátíð LMFÍ var haldin laugardaginn 12. mars. Að', False), (13.0, 150.5, 'sjálfsögðu voru fulltrúar hins vinsæla Heyrt og séð á', False), (13.0, 167.5, 'staðnum eins og alls staðar þar sem prúðbúið fólk', False)],
     'Heyrt og séð á hinni umtöluðu árshátíð LMFÍ'),
    ('logretta/2014_x_1#68', 668, 11.0, [(10.0, 154.9, 'og Svala Ísfeld Ólafsdóttir,', True), (10.0, 167.2, 'cand. jur. og MA í félagsfræði, dósent við', False), (10.0, 179.2, 'lagadeild Háskólans í Reykjavík.', False), (30.0, 251.7, 'áhrif sérfræðigagna á', False), (30.0, 287.7, 'fjárhæð miskabóta til', False), (30.0, 323.7, 'Þolenda kynferðisbrota í', False), (30.0, 359.7, 'æsku', False), (11.0, 431.0, '1. Inngangur', False), (11.0, 444.0, '2. Rannsókn á miskabótum til Þolenda kynferðisbrota í æsku', False), (11.0, 456.7, '2.1 Rannsóknargögn og rannsóknaraðferð', False)],
     'Áhrif sérfræðigagna á fjárhæð miskabóta til Þolenda kynferðisbrota í æsku'),
    ('logfraedingur/2012_6_x#23', 595, 6.5, [(5.6, 181.8, 'Háskólanum á Akureyri vorið 2011. Bjarni hefur starfað sem lögreglumað-', False), (5.9, 190.9, 'ur síðan 1997, en er nú í leyfi frá embætti Ríkislögreglustjórans til að fylgja', False), (6.0, 199.8, 'eftir sérhæfingu sinni á sviði skipulagðrar glæpastarfsemi.', False), (21.3, 233.3, 'samræmist bann við skipu-', False), (22.3, 257.2, 'lögðum glæpasamfökum gild-', False), (21.4, 281.4, 'andi mannéttindum á lslandi?*', False), (8.9, 325.2, 'Umræða um skipulagða glæpastarfsemi hefur komið ítrekað í íslenskri um-', False), (8.5, 338.7, 'ræðu og verið einkar lífleg á þessu vormisseri. Umræðan hefur meðal ann-', False), (8.0, 352.3, 'ars snúist um hvort banna eigi tiltekin félög í þeim tilgangi að sporna gegn', False)],
     'Samræmist bann við skipulögðum glæpasamfökum gildandi mannéttindum á lslandi?'),
    ('logfraedingur/2011_5_x#69', 595, 6.1, [(6.1, 230.4, "Afdrif efnahagslegra og félagslegra réttinda í efnahagskreppunni' (Human Rights in Crisis:", False), (5.7, 239.4, "and Cultural Rights in the Shadow of Iceland's Economic Collapse), Rannsóknir í félagsvísi", False), (6.1, 248.4, 'lagadeild, University of Iceland, October 2010. Online Access: http://skemman.is/item/view', False), (19.5, 281.1, 'lceland s Renaissance:', False), (20.5, 305.3, 'Time for Comparative Law“', False), (10.2, 348.8, 'I Introduction', False), (8.5, 364.8, 'In 2011, Iceland looks to a future beyond the crisis in which it must self-', False), (8.4, 379.8, 'critically reflect on its legal framework, engage in substantial law reform', False)],
     'Lceland s Renaissance: Time for Comparative Law“'),
    ('timarit_logfraedinga/1985_x_x#6', 633, 9.5, [(37.4, 107.7, 'itmaiut— —', True), (41.0, 142.3, 'LÖI.I It I IH M.A', True), (9.3, 168.2, '1. HEFTI 35. ÁRGANGUR', False), (9.0, 185.1, 'MAÍ 1985', False), (13.1, 215.7, 'MARKMIÐ LAGAKENNSLU', True), (9.6, 234.7, 'Öllum er hollt að hugleiða markmiðin með því sem þeir eru að gera. Það', False), (9.5, 246.3, 'er nauðsynlegur undanfari allra umræðna um árangur þeirra leiða sem farnar', False)],
     'Markmið lagakennslu'),
    ('timarit_logfraedinga/1985_x_x#82', 636, 9.5, [(50.4, 105.8, 'rniAiíii—', True), (45.9, 147.7, 'TÖI.IITTIHV.A', True), (9.8, 188.5, '2. HEFTI___________ 35. ÁRGANGUR___________ OKTÓBER 1985', False), (13.2, 215.9, 'STJÓRNVALD EÐA DÓMSTÓLL', True), (13.3, 232.5, 'í BARNARÉTTARMÁLUM?', True), (9.6, 250.6, 'Þróunin í barnarétti hin síðari ár hefur orðið á þann veg ao auka réttindi', False), (9.5, 260.9, 'barnsins í flestum málum, sem það snerta. Lögin gera ráð fyrir því, að for-', False)],
     'Stjórnvald eða dómstóll í barnaréttarmálum?'),
    ('timarit_logfraedinga/2004_x_x#482', 620, 10.5, [(59.0, 119.3, 'löqfræðinqa', False), (12.6, 169.3, '4. hefti • 54. árgangur', False), (12.6, 184.2, 'desember 2004', False), (13.5, 253.4, 'EIN GREIN í FRUMVARPI TIL SAMKEPPNISLAGA', True), (10.5, 275.1, 'Nýverið var lagt fram á Alþingi frumvarp til samkeppnislaga og er nýjum', False), (10.5, 285.7, 'lögum ætlað að taka gildi 1. júlí á þessu ári. Fyrsta umræða um frumvarpið', False)],
     'Ein grein í frumvarpi til samkeppnislaga'),
    ('timarit_logfraedinga/2004_x_x#6', 625, 10.5, [(103.0, 50.6, 'Tímarit', False), (59.6, 138.1, 'löqfræðinqa', False), (12.5, 181.8, '1. hefti • 54. árgangur', True), (13.0, 199.0, 'mars 2004', True), (13.3, 246.3, 'GRÆÐGI VALDSINS', True), (10.5, 262.4, 'Nýlegur leiðari í Tíund, fréttablaði ríkisskattstjóra, vakti nokkra athygli.', False), (10.5, 274.7, 'Fjallað var um græðgina, eina af dauðasyndunum sjö. Reyndar um það sem', False)],
     'Græðgi valdsins'),
    ('logmannabladid/2005_x_2#16', 700, 8.0, [(10.0, 45.6, 'þau eru rekin fyrir dómstólum og einnig eftir að', False), (10.0, 45.6, 'sem þjónar gjarnan eingöngu þeim tilgangi að', False), (10.0, 57.1, 'dómur hefur verið upp kveðinn. Stöku sinnum', False), (10.0, 57.1, 'fegra málstað viðkomandi umbjóðanda lögmanns', False), (10.0, 68.5, 'hafa þó örfáir lögmenn kosið að tjá sig frjálslega', False), (10.0, 68.5, 'á kostnað gagnaðila eða rýra að ástæðulausu álit', False), (10.0, 80.0, 'um mál meðan þau eru til meðferðar fyrir dóm-', False), (10.0, 80.0, 'almennings á dómstólum. Dómarar eru þannig í', False), (10.0, 91.4, 'stólum og einnig eftir að dómur hefur verið kveð-', False), (10.0, 91.4, 'sveit settir að þeir eiga óhægt um vik að tjá sig um', False), (10.0, 102.9, 'inn upp. Enda þótt ekki verði fullyrt hvaða hvatir', False), (10.0, 102.9, 'einstök mál og er því auðvelt að koma höggi á þá', False), (10.0, 114.3, 'kunna að búa þar að baki er nærtækasta skýringin', False), (10.0, 114.3, 'hafi lögmenn geð í sér til þess.', False), (10.0, 125.8, 'sú að með þessu móti telji lögmennirnir sig fá', False), (10.0, 125.8, 'Í 10. gr. siðareglna lögmanna er kveðið á um að', False), (10.0, 137.2, 'góða og ókeypis auglýsingu. Framsetning af þessu', False), (10.0, 137.2, 'lögmaður skuli sýna dómstólum fulla tillitssemi', False), (10.0, 148.7, 'tagi er oftast vafasöm og er hætt við að hún sé sé', False), (10.0, 148.7, 'og virðingu í ræðu, riti og framkomu. Er mikil-', False), (10.0, 160.1, 'bæði einhliða og villandi, sérstaklega í þeim til-', False), (10.0, 160.1, 'vægt að þetta ákvæði sé virt af lögmönnum enda', False), (10.0, 171.6, 'vikum þegar ekki er rætt við gagnaðila og hans', False), (10.0, 171.6, 'þjónar það engan veginn hagsmunum lögmanna', False), (10.0, 183.0, 'sjónarmið fengin en oftast er það svo. Almennt', False), (10.0, 183.0, 'að grafa undan trausti almennings á dómstólum.', False), (10.0, 194.5, 'hljóta bæði lögmenn og dómarar að vera sammála', False), (10.0, 194.5, 'Málefnaleg gagnrýni á hins vegar að sjálfsögðu', False), (10.0, 205.9, 'um að æskilegt sé að forðast slíkan fréttaflutning', False), (10.0, 205.9, 'fullan rétt á sér.', False), (16.4, 233.5, 'Breytingar á félagatali', True), (8.1, 251.8, 'Allar breytingar s.s. ný símanúmer, vefföng, aðsetur og fleira er uppfært á heimasíðu félagsins, www.lmfi.is', True), (8.0, 273.1, 'Einar Kristján Jónsson hdl.', True), (10.0, 272.9, 'Ný málflutningsréttindi', True), (10.0, 272.9, 'Nýtt aðsetur:', True), (8.0, 282.1, 'Landsbanki Íslands', False)],
     'Breytingar á félagatali'),
]


@pytest.mark.parametrize("case, height, body, rows, heading", _TITLE_BLOCKS, ids=[c[0] for c in _TITLE_BLOCKS])
def test_page_heading_is_the_whole_title_block(case, height, body, rows, heading):
    # magazine titles set in steps of size, "Main title:" lines, subtitles in other type (Lögrétta, Rannsóknir), a title
    # block that runs below the top part (LMBL 2017 pdf 7); never a byline, a "Name:" line, a lead or the body
    from engine.timarit.ulfljotur_xlsx import _fold
    last = max(r[1] for r in rows)
    filler = [Line(_BODY + " og meira af meginmáli greinarinnar", body, last + 20 + 13 * k) for k in range(60)]
    page = Page(sorted([Line(t, sz, y, b) for sz, y, t, b in rows] + filler, key=lambda ln: ln.y), float(height))
    h = page_heading(page)
    assert h is not None and _fold(h.text) == _fold(heading)


def test_page_heading_skips_the_rest_of_an_outline_heading_between_body_columns():
    # Lögrétta 2005 pdf 35: "4. Nokkur grundvallaratriði" / "í samanburði stofnfjárhluta" / "og hlutabréfa" is a section
    # heading in the left column, read between the right column's body lines; its lower-case rest is no page heading
    rows = [(9.5, 333.0, 'greiðslu vaxta á innborgað stofnfé. Jafnframt', False),
            (9.5, 345.0, 'skyldu ganga í séreignasjóðinn þær fjárhæðir', False),
            (13.0, 353.8, '4. Nokkur grundvallaratriði', False), (9.5, 357.0, 'sem greiddar voru umfram nafnverð fyrir', False),
            (13.0, 364.6, 'í samanburði stofnfjárhluta', False), (9.5, 369.0, 'nýja stofnfjárhluti.', False),
            (13.0, 375.4, 'og hlutabréfa', False), (9.5, 381.0, 'Í athugasemdum með 1. gr. frumvarpsins', False),
            (9.5, 393.0, 'sem varð að lögum nr. 87/1985 segir um', False)]
    filler = [Line(_BODY, 9.5, 69.7 + 12 * k) for k in range(22)] + [Line(_BODY, 9.5, 405 + 12 * k) for k in range(20)]
    page = Page(sorted([Line(t, sz, y, b) for sz, y, t, b in rows] + filler, key=lambda ln: ln.y), 666.0)
    assert page_heading(page) is None


def test_page_heading_a_label_alone_in_its_type_is_no_heading():
    # TL 1985 pdf 63: "Af vettvangi" (OCR "Ai' vettvangi") alone in large type is the section label; the title below it
    # is set near the body size, so the page has no heading
    rows = [(31.7, 97.7, "Ai' vettvangi", True), (13.9, 178.7, 'Eiríkur Tómasson hrl.:', False),
            (13.2, 202.8, 'DÝR ASPÍT ALAMÁLIÐ', True),
            (11.4, 226.2, 'Hugleiðingar um dóm Hæstaréttar í máli því sem Dýraspítali Wat-', False),
            (11.3, 237.7, 'sons og Erik Ramskov Garbus höfðuðu gegn Páli A. Pálssyni, yfir-', False),
            (11.3, 254.7, 'dýralækni.', False)]
    filler = [Line(_BODY, 11.4, 276.3 + 13.8 * k) for k in range(25)]
    page = Page([Line(t, sz, y, b) for sz, y, t, b in rows] + filler, 625.0)
    assert page_heading(page) is None


def test_page_heading_stops_at_a_reviewed_books_editor():
    # Lögfræðingur 2011 pdf 106: "Polar Law lextDook" / "Natalia Loukacheva (ed.)" — the editor is no part of the title
    rows = [(9.4, 50.6, 'Reviewed by Rachael Lorna Johnstone', False), (18.5, 83.4, 'Polar Law lextDook', False),
            (10.5, 105.6, 'Natalia Loukacheva (ed.)', False), (8.6, 121.4, 'Nordic Council of Ministers 2010', False),
            (8.9, 163.3, 'This book review was first published in 6(1) Nordicum Mediterranean:', False)]
    filler = [Line(_BODY, 8.6, 178.3 + 15 * k) for k in range(30)]
    page = Page([Line(t, sz, y, b) for sz, y, t, b in rows] + filler, 595.0)
    assert page_heading(page).text == "Polar Law lextDook"


def test_ocr_masthead_and_issue_date_are_no_heading():
    # TL 2004's masthead as the OCR reads it ("löqfræðinqa") is furniture; "Lögfræðingatal" is a word, no masthead;
    # TL 1985's garbled masthead ("itmaiut— —") and the date line ("desember 2004") start no title block
    from engine.timarit.firstpage import _garbled
    assert is_furniture(Line("löqfræðinqa", 59.0, 119.3)) and is_furniture(Line("Tímarit", 103.0, 50.6))
    assert not is_furniture(Line("Lögfræðingatal", 14.0, 100.0)) and not is_furniture(Line("desember 2004", 12.6, 184.2))
    assert _garbled("itmaiut— —") and _garbled("LÖI.I It I IH M.A") and _garbled("TÖI.IITTIHV.A")
    assert not _garbled("EFTA-dómstóllinn í 10 ár") and not _garbled("Polar Law Textbook") and not _garbled("SÞ")
    assert not _garbled("SamMmsömun")             # Lögfræðingur 2012 pdf 73: the title "Samsömun", misread


def test_recase_takes_each_part_of_a_hyphenated_word():
    assert recase("EFTA-DÓMSTÓLLINN Í ÁRATUG") == "EFTA-dómstóllinn í áratug"


def test_page_heading_recases_abbreviations_in_a_capital_heading():
    assert recase("MEÐFERÐ SAMKEPPNISMÁLA OG 6. GR. MANNRÉTTINDASÁTTMÁLA EVRÓPU") == \
        "Meðferð samkeppnismála og 6. gr. mannréttindasáttmála Evrópu"


def test_a_title_set_low_on_the_page_keeps_its_registry_form():
    # LMBL 2017 pdf 7: the title's last line stands below the top part; the page shows the TOC's missing words there
    rows = [(57.0, 514.2, "HÆGIR Á FJÖLGUN", True), (36.0, 584.7, "FÉLAGSMANNA Í", False),
            (36.0, 634.7, "LÖGMANNAFÉLAGI ÍSLANDS", False)]
    page = Page([Line(_BODY, 14.0, 60 + 14 * k) for k in range(20)] + [Line(t, sz, y, b) for sz, y, t, b in rows]
                + [Line(_BODY, 14.0, 692.9)], 842.0)
    t = "Hægir á fjölgun félagsmanna í Lögmannafélagi Íslands"
    assert choose_title(t, page) == (t, None, False)
