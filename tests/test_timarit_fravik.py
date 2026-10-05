# tests/test_timarit_fravik.py
"""Task 23b: fewer false deviation flags (FRÁVIK, R42) in the four categories audit 4 found noisy: signatures, author
conflicts, title search and uncertain ends. Every page is a real one from the archive (tests/fixtures/timarit/pages/
pagekind.json, made by make_pagekind_fixture.py there; running text in mid-page replaced by filler of the same
length); each test names the audited flag it mirrors and what the auditor found."""
import json
from pathlib import Path

from engine.timarit import firstpage as FP
from engine.timarit import pagekind as PK
from engine.timarit import printed as PR
from engine.timarit.firstpage import Line, Page
from engine.timarit.manifest import ArticleEntry
from engine.timarit.pagemap import PageMap

FIX = json.loads((Path(__file__).parent / "fixtures/timarit/pages/pagekind.json").read_text(encoding="utf-8"))


def page(key: str) -> Page:
    d = FIX[key]
    return Page([Line(*ln) for ln in d["lines"]], d["height"], d["width"])


class Reader:
    """A PageReader over fixture pages as if they followed each other (index 0 = the first key)."""

    def __init__(self, keys: list[str]):
        self.keys = keys
        self.pages = [page(k) for k in keys]

    def __call__(self, i: int) -> Page:
        return self.pages[i] if 0 <= i < len(self.pages) else FP.EMPTY

    def picture(self, i: int) -> float:
        return FIX[self.keys[i]]["picture"]

    def drawings(self, i: int) -> int:
        return FIX[self.keys[i]]["drawings"]


def end(keys: list[str], title: str, authors: tuple[str, ...] = ()) -> PK.End:
    r = Reader(keys)
    return PK.article_end(r, 0, len(keys) - 1, title, r.picture, list(authors), r.drawings)


# --- signatures -------------------------------------------------------------------------------------------------------

def test_a_translators_classified_advert_under_the_article_is_no_signature():
    # LMBL 2021-3 idx 10 (also 2020-3 idx 9, 2025-2 idx 5): "Lúðvík Emil Kaaber" in 21 pt over his address and phones
    e = end(["lmbl2021/27", "lmbl2021/28"], "Áminning vegna umfjöllunar um viðkvæmar persónuupplýsingar á opinberum vettvangi")
    assert (e.last, e.signed, e.uncertain) == (1, (), False)


def test_photo_captions_in_the_margin_are_no_signature():
    # LMBL 2012-2 idx 14: three statements, each beside a captioned photo ("Guðrún Helga / Brynleifsdóttir hrl.")
    p = page("lmbl2012/26")
    assert PK.signature(p, PK.typography([p]), "Gegn grundvallarreglum sakamálaréttarfarsins") == []


def test_a_signature_after_the_next_items_heading_ends_the_range_before_that_item():
    # TL 1996 idx 24 (auditor: ends p. 204): the range ran on over the Lögfræðingafélag and Dómarafélag reports, and the
    # Dómarafélag report's signature "Allan V. Magnússon" was flagged against the registry's "Jón L. Arnalds"
    r = Reader(["tl1996/330", "tl1996/335", "tl1996/336", "tl1996/342", "tl1996/348"])
    a = ArticleEntry(title="Hugleiðing um réttarsáttir", authors=["Jón L. Arnalds"], page_start=199, pdf_pages=[0, 4])
    ends = PR.end_ranges([a], r, lambda i, lo=None, hi=None: {0: 199, 1: 204, 2: 205, 3: 211, 4: 217}[i])
    assert a.pdf_pages == [0, 1] and a.page_end == 204 and ends.ends[id(a)].signed == ()
    assert a.analysis_notes == ["FRÁVIK: undirritunin (Allan V. Magnússon) stendur á eftir fyrirsögninni „Á VÍÐ OG DREIF "
                                "— SKÝRSLA STJÓRNAR LÖGFRÆÐINGAFÉLAGS ÍSLANDS Á“ á bls. 205; greinin er talin enda á "
                                "undan henni"]


def test_a_signature_that_names_the_registrys_author_leaves_the_range_alone():
    r = Reader(["tl1996/330", "tl1996/335", "tl1996/336", "tl1996/342", "tl1996/348"])
    a = ArticleEntry(title="Úr skýrslu stjórnar Dómarafélags Íslands", authors=["Allan V. Magnússon"], pdf_pages=[0, 4])
    PR.end_ranges([a], r, lambda i, lo=None, hi=None: None)
    assert a.pdf_pages == [0, 4] and not any(n.startswith("FRÁVIK") for n in a.analysis_notes)


# --- author conflicts -------------------------------------------------------------------------------------------------

def _byline(key: str, title: str) -> list[str]:
    p = page(key)
    return FP.byline(p, FP.printed_title(p, title), title)


def test_a_lead_a_list_and_a_citation_name_no_author():
    # LMBL 2017-2 idx 6: the lead names the seminar's chair; TL 1964-1 idx 8: graduates with their marks; LMBL 2004-3
    # idx 10: "Hans Gammeltoft-Hansen, segir í" in a column of text
    assert _byline("lmbl2017b/17", "Sjálfstæði lögmannastéttarinnar") == []
    assert _byline("tl1964/63", "Frá Háskólanum") == []
    assert _byline("lmbl2004c/27", "Ritað í tilefni af pistli formanns LMFÍ í Lögmannablaðinu, júníhefti 2004") == []


def test_an_interview_names_the_person_interviewed_without_a_deviation():
    # Lögrétta 2014-2 idx 11: "… viðtal við Pál Hreinsson"; the interview is Þór Jónsson's
    a = ArticleEntry(title="EFTA-dómstóllinn gerir sig gildandi: Viðtal við Pál Hreinsson, dómara við EFTA-dómstólinn",
                     authors=["Þór Jónsson"], article_type="Viðtal", provenance={"title": "llm", "article_type": "llm"})
    PR.read_printed_page(a, page("logretta2014b/131"), None)
    assert a.authors == ["Þór Jónsson"] and not any(n.startswith("FRÁVIK") for n in a.analysis_notes)


def test_an_interviews_printed_credit_is_its_author():
    # LMBL 2005-4 idx 2 (fix round 2): the lead names Örn Clausen; "Viðtal: Eyrún Ingadóttir" credits the registry's
    # author
    a = ArticleEntry(title="Held áfram meðan heilsan leyfir", authors=["Eyrún Ingadóttir"], article_type="Viðtal",
                     provenance={"title": "llm", "article_type": "llm"})
    PR.read_printed_page(a, page("lmbl2005b/9"), None)
    assert a.authors == ["Eyrún Ingadóttir"] and not any(n.startswith("FRÁVIK") for n in a.analysis_notes)


def test_names_in_an_interviews_lead_are_no_conflict():
    # LMBL 2006-2 idx 3 and 4 (fix round 2): the leads "Ástríður Gísladóttir er fulltrúi …", "Hjördís E. Harðardóttir og
    # Arnór Halldórsson eru …" introduce the people interviewed; the credit stands on the series' first page
    for key, title in (("lmbl2006/9", "Ekki erfiðara en hjá fólki í öðrum störfum"),
                       ("lmbl2006/10", "Skilningur á því að menn vinna á daginn")):
        a = ArticleEntry(title=title, authors=["Ásta Sóllilja Sigurbjörnsdóttir", "Eyrún Ingadóttir"],
                         article_type="Viðtal", provenance={"title": "llm", "article_type": "llm"})
        PR.read_printed_page(a, page(key), None)
        assert not any(n.startswith("FRÁVIK") for n in a.analysis_notes), (key, a.analysis_notes)
    # the same page under an article that is no interview keeps the deviation
    a = ArticleEntry(title="Ekki erfiðara en hjá fólki í öðrum störfum", authors=["Eyrún Ingadóttir"],
                     article_type="Fræðigrein", provenance={"title": "llm", "article_type": "llm"})
    PR.read_printed_page(a, page("lmbl2006/9"), None)
    assert any(n.startswith("FRÁVIK: höfundar") for n in a.analysis_notes)


def test_a_heading_under_the_authors_name_is_compared_with_the_registry():
    # TL 2024-4 idx 1 (fix round 2): "Trausti Fannar Valsson:" / "SÉRSTAKT HÆFI Í SVEITARFÉLÖGUM*" under the DOI line;
    # the registry has the cover's wording
    a = ArticleEntry(title="Hæfisreglan í sveitarstjórnarlögunum", authors=["Trausti Fannar Valsson"],
                     article_type="Fræðigrein", provenance={"title": "toc", "article_type": "toc"})
    # the registry has the cover's wording. The heading is read now; it shares "hæfi" and "sveitar-" with the registry's
    # (match 0.59, over _OTHER_TITLE), so it is no other title and raises no deviation: the gate's plain note names it
    assert PR.page_heading_text(page("tl2024d/4")) == "SÉRSTAKT HÆFI Í SVEITARFÉLÖGUM"
    for title, flagged in (("Hæfisreglan í sveitarstjórnarlögunum", False), ("Um lögbann og tryggingu", True)):
        a = ArticleEntry(title=title, authors=["Trausti Fannar Valsson"], article_type="Fræðigrein",
                         provenance={"title": "toc", "article_type": "toc"})
        PR.read_printed_page(a, page("tl2024d/4"), None)
        assert any(n.startswith("FRÁVIK: fyrirsögnin á fyrstu síðu („SÉRSTAKT HÆFI Í SVEITARFÉLÖGUM“)")
                   for n in a.analysis_notes) == flagged


def test_name_forms_are_the_same_person():
    # TL 1984 idx 11: leitir's "Yoran, Aaron" is the printed "Aharon Yoran"; Úlfljótur 2011-3 idx 1: "Róbert R. Spanó";
    # Úlfljótur 1995 idx 18: a section heading "A. Perspectives" is nobody
    assert PR.given_first("Yoran, Aaron") == "Aaron Yoran" and PR.given_first("Páll Hreinsson") == "Páll Hreinsson"
    assert not PR._conflict(["Yoran, Aaron"], ["Aharon Yoran"])
    assert not PR._conflict(["Róbert Ragnar Spanó"], ["Róbert R. Spanó"])
    assert not PR._conflict(["Meinhard Hilf"], ["A. Perspectives"])
    assert PR._conflict(["Eyvindur G. Gunnarsson"], ["Salvör Þórisdóttir"])


# --- title search -----------------------------------------------------------------------------------------------------

def test_the_registrys_page_is_kept_when_it_prints_the_title():
    # TL 1971 idx 10: the map cannot locate p. 51; the title search took Páll Sigurðsson's "Örorkumöt" on p. 37 for
    # Gunnar M. Guðmundsson's "Um örorkumöt", which leitir.is and the contents put on p. 51
    r = Reader(["tl1971/270", "tl1971/284"])
    a = ArticleEntry(title="Um örorkumöt", authors=["Gunnar M. Guðmundsson"], page_start=51,
                     provenance={"title": "toc", "pages": "leitir+toc"})
    pm = PageMap(None, [None, None], [False, False], 0.0)
    PR.relocate_by_title([a], [75], pm, ["x", "x"], r, skip=set(), number=lambda i, lo=None, hi=None: (37, 51)[i])
    assert a.pdf_pages[0] == 1 and a.page_start == 51
    assert not any(n.startswith("FRÁVIK") for n in a.analysis_notes)


# --- the end of an article --------------------------------------------------------------------------------------------

def test_the_articles_last_lines_over_its_chart_are_its_own():
    # TL 1968-2 idx 1 (auditor: 75–78 right): p. 78 starts "þennan hátt er þá …", signs "ThBL." and prints the chart
    e = end(["tl1968/14", "tl1968/15", "tl1968/16", "tl1968/17"], "Ár mannréttinda 1968", ("Theodór B. Líndal",))
    assert (e.last, e.uncertain) == (3, False)


def test_an_english_abstract_run_on_to_the_next_page_is_the_articles():
    # TL 2014-3 idx 1 (auditor: 273–307 right): p. 307 goes on with the abstract; p. 308 holds only a download stamp
    e = end(["tl2014/4", "tl2014/5", "tl2014/37", "tl2014/38", "tl2014/39"], "Samræmd EES-túlkun", ("Páll Hreinsson",))
    assert (e.last, e.uncertain, e.outside) == (3, False, (4,))


def test_a_scan_page_measured_a_little_larger_is_still_text():
    # TL 1970-1 idx 9 (auditor: ending at 39 right): p. 39 reads 12.1 pt over the article's 11.2; p. 40 is a photo
    e = end(["tl1970/38", "tl1970/39", "tl1970/40", "tl1970/41"], "Húsnæði Hæstaréttar 1920–1970")
    assert (e.last, e.uncertain, e.outside) == (2, False, (3,))


def test_a_full_page_advert_after_the_text_ends_the_article():
    # LMBL 2004-4 idx 0 (auditor: ends p. 4, p. 5 is an advert drawn without text); Úlfljótur 1983 idx 1 (p. 27 the
    # journal's own advert in the article's file)
    e = end(["lmbl2004d/3", "lmbl2004d/4"], "Frá ritstjórn", ("Guðrún Björg Birgisdóttir",))
    assert (e.last, e.uncertain, e.outside) == (0, False, (1,))
    e = end(["ulf1983/0", "ulf1983/1", "ulf1983/7", "ulf1983/8"],
            "Nokkur orð um fornan rétt varðandi manntjón af völdum dýra", ("Páll Sigurðsson",))
    assert (e.last, e.uncertain, e.outside) == (2, False, (3,))


def test_a_rubric_glued_before_the_authors_name_gives_way_to_the_printed_name():
    # LMBL 2018-4 idx 2 (audit 4): the contents list and the page read "Jólahugvekja Svanhildur Hólm Valsdóttir"
    assert FP.names_in("JÓLAHUGVEKJA SVANHILDUR HÓLM VALSDÓTTIR", styled=True) == ["Svanhildur Hólm Valsdóttir"]
    choice = FP.merge_authors(["Jólahugvekja Svanhildur Hólm Valsdóttir"], ["Svanhildur Hólm Valsdóttir"])
    assert choice == FP.AuthorChoice(["Svanhildur Hólm Valsdóttir"], True, None)
    assert FP.merge_authors(["Jón Jónsson"], ["Páll Jónsson"]).authors == ["Jón Jónsson"]


def test_a_title_found_on_a_page_whose_running_head_prints_the_registrys_number_is_no_deviation():
    # Úlfljótur 2017-3 idx 3 (audit 4: true but trivial): a spread prints "Afmælisrit  379" over the title; the map
    # reads no number in the file, so the article is found by its title on the very page the spreadsheet names
    assert PR._margin_prints("Afmælisrit  379\nDómar Mannréttindadómstóls\nEvrópu …\n", 379)
    assert PR._margin_prints("Texti\nmeira\n12\n", 12) and PR._margin_prints("Texti\nmeira\n155 / LÖGBRÚ\n", 155)
    assert not PR._margin_prints("Afmælisrit  381\nTexti greinarinnar um 379 mál\n", 379)
    assert not PR._margin_prints("12. árg. 2. tbl.\nTexti greinarinnar\n", 12)        # (review of 23b)


def test_a_picture_page_inside_the_article_is_kept_when_its_text_goes_on():
    # LMBL 2004-4 idx 1: p. 11 is a full-page photograph between two pages of the article (p. 12 ends it)
    e = end(["lmbl2004d/8", "lmbl2004d/9", "lmbl2004d/10", "lmbl2004d/11"],
            "„Þar sem peningar eru, þar eru konur ekki“ – Konur í lögmannastéttinni", ("Eyrún Ingadóttir",))
    assert (e.last, e.uncertain) == (3, False)


def test_a_signature_over_a_download_stamp_in_the_margin_stands():
    # TL 2016-1 idx 0 (bulk): "Hafsteinn Þór Hauksson ritstjóri" ends the editorial; the PDF's download stamp
    # "logfraedingafelag.is - Hlaðið niður …" runs up the margin, read after it
    body = [Line("Meginmál greinarinnar heldur hér áfram með nægum texta svo að úr verði lína.", 10.5, 300 + 12 * k,
                 False, 42, 360) for k in range(9)]
    stamp = Line("logfraedingafelag.is - Hlaðið niður af aðgangi Háskólinn á Akureyri", 9.0, 152.1, False, 3, 16)
    lines = body + [Line("greiðslan fór fram.", 10.5, 404.2, False, 42, 130),
                    Line("Hafsteinn Þór Hauksson ritstjóri", 10.5, 436.1, False, 213, 356), stamp]
    assert PK.signature(Page(lines, 595.0, 420.0), PK.Typography((10.5,), 64.0), "Í tilefni af Brexit") == \
        ["Hafsteinn Þór Hauksson"]
    # TL 2014-2 idx 0: the stamp a few points under the name, in the margin beside it
    near = lines[:-1] + [Line(stamp.text, 9.0, 452.0, False, 3, 16)]
    assert PK.signature(Page(near, 595.0, 420.0), PK.Typography((10.5,), 64.0), "Í tilefni af Brexit") == \
        ["Hafsteinn Þór Hauksson"]


def test_a_number_two_pages_print_is_taken_in_registry_order():
    # TL 2000 idx 15 (bulk): p. 261 is printed on two pages of the volume; the one between its neighbours is its start
    blank = Page([Line("Meginmál.", 10.0, 100.0)], 600.0, 400.0)
    reader = lambda i: blank                                                        # noqa: E731
    a = ArticleEntry(title="Á 50 ára afmæli", page_start=259, pdf_pages=[2, 6], provenance={"title": "toc"})
    b = ArticleEntry(title="Tímarit lögfræðinga 50 ára", page_start=261, provenance={"title": "toc"})
    c = ArticleEntry(title="Efndir in natura", page_start=285, pdf_pages=[7, 9], provenance={"title": "toc"})
    printed = {0: 261, 2: 259, 3: 260, 4: 261, 5: 262, 6: 263, 7: 285}
    pm = PageMap(None, [None] * 10, [False] * 10, 0.0, contested=[261])
    PR.relocate_by_title([a, b, c], [None, None, None], pm, ["x"] * 10, reader, skip=set(),
                         number=lambda i, lo=None, hi=None: printed.get(i))
    assert (a.pdf_pages, b.pdf_pages, b.page_start) == ([2, 3], [4, 6], 261)
    assert not any(n.startswith("FRÁVIK") for n in b.analysis_notes)


def test_only_the_person_an_interviews_title_names_is_exempt_from_the_author_conflict():
    # review of 23b: the exemption covers the interviewee the title names, not any name on an interview's page
    assert PR.interviewees("EFTA-dómstóllinn gerir sig gildandi: Viðtal við Pál Hreinsson, dómara") == ["Páll Hreinsson"]
    assert PR._interviewees_only("Aðalvopn þjóðaréttar er samstaða: Viðtal við Guðmund Eiríksson", ["Þór Jónsson"],
                                 ["Guðmundur Eiríksson"])
    assert not PR._interviewees_only("Faglegur draumur sem rætist", ["Eyrún Ingadóttir"], ["Róbert R. Spanó"])
    assert len(PR.interviewees("VIÐTAL VIÐ HELGU KRISTÍNU AUÐUNSDÓTTUR Samningar")[0].split()) == 3


def test_a_lead_that_starts_with_a_name_is_no_byline():
    # LMBL 2013-3 idx 4: the lead "Róbert R. Spanó var kjörinn" / "dómari við Mannréttinda-" names the interviewee
    lead = Line("Róbert R. Spanó var kjörinn", 11.5, 308.3)
    assert FP._in_sentence(lead, Line("dómari við Mannréttinda­", 11.5, 321.5))
    assert not FP._in_sentence(Line("Ásmundur G. Vilhjálmsson lögmaður", 10.0, 200.0),
                               Line("og stundakennari við lagadeild", 10.0, 212.0))
    assert not FP._in_sentence(Line("Gunnar Jónsson doktorsnemi við", 10.0, 200.0), Line("lagadeild HÍ", 10.0, 212.0))


def test_a_lead_in_its_own_column_is_read_on_in_that_column():
    # LMBL 2013-3 idx 4 (fix round 1): the lead's next line in reading order is the interview's first answer, in the
    # next column; its own next line, "dómari við Mannréttinda-", shows the name is the subject of a sentence
    p = page("lmbl2013/13")
    heading = FP.printed_title(p, "Faglegur draumur sem rætist")
    assert FP.byline(p, heading, "Faglegur draumur sem rætist") == []
