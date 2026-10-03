# tests/test_timarit_toc.py
import json
from pathlib import Path

import pytest

from engine.timarit.toc import TocEntry, find_toc_pages, parse_toc, toc_style

FIX = Path(__file__).parent / "fixtures" / "timarit" / "toc"


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _pages(name):
    """First pages of a real PDF, as the text layer returned them (pages.json)."""
    return json.loads((FIX / "pages.json").read_text(encoding="utf-8"))[name]


def test_tl_issue_page_first():
    entries = parse_toc(_load("tl_2010_01.txt"), style="page_first")
    titles = {e.title.split("?")[0].strip(): e for e in entries}
    kb = next(e for e in entries if e.authors == ["Kjartan Bjarni Björgvinsson"])
    assert kb.page_start == 7 and kb.title.startswith("Hvenær er vanhæfi smitandi")
    first = entries[0]
    assert first.page_start == 1 and first.title.startswith("Ný löggjöf um skipun dómara") and first.authors == []
    assert all(e.page_start is not None for e in entries)
    assert [e.page_start for e in entries] == sorted(e.page_start for e in entries)


def test_tl_annual_page_last_with_eftir_authors_and_hefti_sections():
    entries = parse_toc(_load("tl_1985.txt"), style="page_last")
    e = next(x for x in entries if x.title.startswith("Almennar reglur um sameign"))
    assert e.page_start == 7 and e.authors == ["Gaukur Jörundsson"]      # 'eftir Gauk Jörundsson' → nominative via BÍN
    assert e.issue == "1"
    assert next(x for x in entries if x.title.startswith("Markmið lagakennslu")).page_start == 1


def test_logbru_comma_separated_author_then_title():
    entries = parse_toc(_load("logbru_2013_01.txt"), style="page_last")
    e = next(x for x in entries if "sönnunarkröfur" in x.title)
    assert e.authors == ["Pétur Fannar Gíslason"] and e.page_start == 5
    s = next(x for x in entries if "forvirkar" in x.title)
    assert s.authors == ["Snorri Snorrason"]          # 'hdl.' stripped


def test_logfraedingur_dotted_leaders():
    entries = parse_toc(_load("logfraedingur_2010.txt"), style="page_last")
    e = next(x for x in entries if x.title.startswith("Frá ritstjóra"))
    assert e.authors == ["Elín Sif Kjartansdóttir"] and e.page_start == 5


def test_find_toc_pages_picks_pages_with_many_page_numbers():
    pages = ["Kápa", "Efnisyfirlit\nA ... 5\nB ... 17\nC ... 33\nD ... 49", "Meginmál án tölusettra lína."]
    assert find_toc_pages(pages) == [1]
    assert find_toc_pages(["Bara texti", "Meira"]) == []


def test_styles_per_source():
    assert toc_style("timarit_logfraedinga") == "page_first"
    assert toc_style("logbru") == "page_last"


def test_parse_toc_ignores_lines_without_a_page_number():
    text = "RITSTJÓRI: Jón Jónsson\nAðalsteinn: Grein ein 12\nFRAMKVÆMDASTJÓRI: X\n"
    assert [e.title for e in parse_toc(text, style="page_last")] == ["Grein ein"]


# ---------------------------------------------------------------------------------------------
# Added beyond the brief: behaviour the real fixtures force (page number on its own line, wrapped
# titles, description lines, author index, running footers, OCR noise).
# ---------------------------------------------------------------------------------------------

def _by_page(entries):
    return {e.page_start: e for e in entries}


def test_find_toc_pages_counts_page_numbers_standing_alone_on_a_line():
    # In all four real TOC pages the number is on its own line (or the line ends in leaders, number below).
    for name in ("tl_2010_01.txt", "tl_1985.txt", "logbru_2013_01.txt", "logfraedingur_2010.txt"):
        assert find_toc_pages(["Kápa", _load(name)]) == [1], name


def test_find_toc_pages_ignores_year_like_numbers():
    page = "Útgefið 2010\nÁr 2011\nÁr 2012\nÁr 2013\nÁr 2014"
    assert find_toc_pages([page]) == []


def test_page_first_joins_wrapped_titles_dehyphenates_and_drops_footer():
    entries = parse_toc(_load("tl_2010_01.txt"), style="page_first")
    assert all(isinstance(e, TocEntry) for e in entries)
    assert [e.page_start for e in entries] == [1, 7, 49, 71]
    by = _by_page(entries)
    # "framkvæmdar-<soft hyphen>" at a line end is rejoined with the next line
    assert by[49].title == "Eru valdmörk milli dómsvalds og framkvæmdarvalds á sífelldri hreyfingu?"
    assert by[49].authors == ["Daníel Isebarn Ágústsson"]
    # the page footer "1. hefti • 60. árgangur vor 2010" is not part of the last title
    assert by[71].title.endswith("veitingu opinberra starfa")
    assert by[71].authors == ["Berglind Bára Sigurjónsdóttir"]
    # a title that contains "17. desember 2009 í máli nr. 665/2008" is not split at those numbers
    assert by[7].title.endswith("í máli nr. 665/2008")


def test_tl_annual_stops_at_the_author_index_and_keeps_hefti_in_order():
    entries = parse_toc(_load("tl_1985.txt"), style="page_last")
    pages = [e.page_start for e in entries]
    assert pages == sorted(pages) and pages[-1] == 272          # nothing from HÖFUNDASKRÁ
    by = _by_page(entries)
    assert (by[1].issue, by[73].issue, by[141].issue, by[209].issue) == ("1", "2", "3", "4")


def test_tl_annual_description_lines_do_not_leak_into_the_next_title():
    by = _by_page(parse_toc(_load("tl_1985.txt"), style="page_last"))
    assert by[132].title == "Á víð og dreif"          # preceded by the description "Um starfsemi … 1984-1985"
    assert by[128].title == "Frá Lögmannafélagi íslands"
    assert by[272].title == "Á víð og dreif"          # preceded by "Deildarfréttir — … 28. febrúar 1985"
    assert by[70].title == "Á víð og dreif"           # leaders broken across three lines
    assert by[265].title == "Frá Lagadeild Háskólans"


def test_tl_annual_eftir_names_are_lemmatised_to_nominative():
    by = _by_page(parse_toc(_load("tl_1985.txt"), style="page_last"))
    assert by[28].authors == ["Gylfi Knudsen"]                      # unknown surname kept as printed
    assert by[115].authors == ["Erla S. Árnadóttir"]                # initial kept, -dóttur → -dóttir
    assert by[183].authors == ["Þórunn J. Hafstein"]                # family name Hafstein is not "Hafsteinn"
    assert by[196].authors == ["Páll Sigurðsson"]                   # 'eftir' on the line below the title
    assert by[125].authors == ["Arnljótur Björnsson"]               # gender of the -son surname picks the lemma
    assert by[65].authors == ["Ingibjörg Sólrún Gísladóttir"]       # OCR "lngibjörgu" (l for I)
    assert by[65].title == "Hver eru mannréttindi kvenna?"
    assert by[68].authors == ["Þór Vilhjálmsson"]                   # BÍN lists Þór under the lemma Þórr first
    assert by[148].authors == ["Sigurður Reynir Pétursson"]         # while Sigurð (acc.) is Sigurður, not the rare lemma Sigurð
    assert by[58].title == "Af vettvangi dómsmála: Dýraspítalamálið"      # the colon belongs to the title


def test_tl_annual_entries_without_eftir_have_no_authors():
    by = _by_page(parse_toc(_load("tl_1985.txt"), style="page_last"))
    assert by[5].title == "Kristján Eiríksson" and by[5].authors == []         # obituary, page number on its own line
    assert by[75].title == "Birgir Ásgeirsson — Páll Pálmason" and by[75].authors == []
    assert by[211].title.startswith("Árni Tryggvason —") and by[211].authors == []   # no leaders, bare number


def test_logbru_colophon_and_year_do_not_become_entries():
    entries = parse_toc(_load("logbru_2013_01.txt"), style="page_last")
    assert [e.page_start for e in entries] == [5, 57, 99, 155]
    assert entries[3].title == "Viðtöl við bifrestinga" and entries[3].authors == []
    assert entries[2].authors == ["Kristján Óskar Ásvaldsson"] and entries[2].title == "Skilyrði lögbanns"


def test_logfraedingur_author_line_then_wrapped_title_and_no_footer_entry():
    entries = parse_toc(_load("logfraedingur_2010.txt"), style="page_last")
    assert [e.page_start for e in entries] == [5, 6, 8, 20, 31, 40, 59, 75, 88]   # footer "Lögfræðingur 3" is not one
    by = _by_page(entries)
    assert by[8].authors == ["Alena Ingvarsdóttir"]
    assert by[8].title == "Resolving Iceland‘s Debt Crisis: Causes, Sovereign Debt and Future Prospects"
    assert by[59].authors == ["Mark S. Weiner"]                       # foreign name, initial
    assert by[59].title.endswith("Internet: Lessons from Iceland")     # colon inside the title stays
    assert by[31].authors == ["Jakob Þ. Möller"]
    assert by[88].title == "Lögfræðingatal" and by[88].authors == []


def test_single_line_author_title_forms():
    text = (
        "Pétur Fannar Gíslason, Um sönnunarkröfur og sönnunarmat í sakamálum 5\n"
        "Elín Sif Kjartansdóttir Frá ritstjóra . . . . . . . 9\n"
        "Kjartan Bjarni Björgvinsson: Hvenær er vanhæfi smitandi? ........ 12\n"
    )
    got = [(e.authors, e.title, e.page_start) for e in parse_toc(text, style="page_last")]
    assert got == [
        (["Pétur Fannar Gíslason"], "Um sönnunarkröfur og sönnunarmat í sakamálum", 5),
        (["Elín Sif Kjartansdóttir"], "Frá ritstjóra", 9),
        (["Kjartan Bjarni Björgvinsson"], "Hvenær er vanhæfi smitandi?", 12),
    ]


def test_a_label_before_the_colon_is_not_an_author():
    text = "Viðtal: Jón Jónsson 12\nRitdómur: Bók um sönnun 20\n"
    got = [(e.authors, e.title) for e in parse_toc(text, style="page_last")]
    assert got == [([], "Viðtal: Jón Jónsson"), ([], "Ritdómur: Bók um sönnun")]


def test_eftir_followed_by_a_non_name_is_not_an_author():
    text = "Um rétt eftir fyrri dómum og venjum .......... 7\n"
    (e,) = parse_toc(text, style="page_last")
    assert e.authors == [] and e.title == "Um rétt eftir fyrri dómum og venjum"


def test_section_and_hefti_headings_set_context_and_end_pending_text():
    text = (
        "1. hefti\n"
        "Fræðigreinar\n"
        "Jón Jónsson: Fyrsta grein ......... 3\n"
        "Lýsing sem fylgir engu blaðsíðutali\n"
        "Ritdómar\n"
        "Páll Pálsson: Bókadómur ......... 40\n"
        "2. hefti\n"
        "Anna Önnudóttir: Önnur grein ......... 61\n"
    )
    got = [(e.title, e.page_start, e.section, e.issue) for e in parse_toc(text, style="page_last")]
    assert got == [
        ("Fyrsta grein", 3, "Fræðigreinar", "1"),
        ("Bókadómur", 40, "Ritdómar", "1"),
        ("Önnur grein", 61, "Ritdómar", "2"),
    ]


def test_page_first_ignores_role_words_and_year_like_numbers():
    text = "4 Ritstjóri: Jón Jónsson\n5 Anna Önnudóttir: Grein um árið 2009 Hæstiréttur\n"
    entries = parse_toc(text, style="page_first")
    assert [(e.page_start, e.authors, e.title) for e in entries] == [
        (5, ["Anna Önnudóttir"], "Grein um árið 2009 Hæstiréttur"),
    ]


# ---------------------------------------------------------------------------------------------
# Review round 1: footers/colophons, increasing page marks, the TOC page detector, style by kind.
# ---------------------------------------------------------------------------------------------

def test_page_first_post_2010_cover_footer_does_not_become_an_entry():
    # footer: "1. hefti • 62. árgangur / vor 2012 / Lögfræðingafélag Íslands • Álftamýri 9 • 108 Reykjavík"
    entries = parse_toc(_load("tl_2012_01.txt"), style="page_first")
    assert [e.page_start for e in entries] == [1, 5, 51, 79]
    by = _by_page(entries)
    assert by[1].title == "Hljóðritanir á ríkisstjórnarfundum og dómur Landsdóms" and by[1].authors == []
    assert by[5].authors == ["Eiríkur Elís Þorláksson"]
    assert by[5].title == "Meginreglur um lagaval innan samninga og undantekningar frá þeim"
    assert by[79].authors == ["Eiríkur Jónsson"]
    assert by[79].title == "Skaðabætur vegna kynferðisbrota – Er réttur brotaþola ekki að fullu sóttur?"
    assert not any("Reykjavík" in e.title or "hefti" in e.title for e in entries)


def test_page_first_three_entry_cover_with_the_issue_marker_above_the_entries():
    # 2019+ covers put "4. hefti • 71. árgangur" on top and the address footer at the bottom
    got = [(e.page_start, e.authors, e.title) for e in parse_toc(_load("tl_2021_04.txt"), style="page_first")]
    assert [g[0] for g in got] == [475, 487, 515]
    assert got[0][1] == [] and got[0][2].startswith("Einkavæðing fjármálafyrirtækja")
    assert got[1][1] == ["Ingvar Sverrisson"] and got[1][2].endswith("almannatrygginga")
    assert got[2][1] == ["Þorgeir Ingi Njálsson"] and got[2][2] == "Um hraða málsmeðferð — málsmeðferð innan hæfilegs tíma"
    got = [(e.page_start, e.authors) for e in parse_toc(_load("tl_2019_02.txt"), style="page_first")]
    assert got == [
        (155, []),
        (157, ["Lára Herborg Ólafsdóttir", "Sindri M. Stephensen"]),
        (183, ["Ásgerður Ragnarsdóttir", "Karl Axelsson"]),
    ]


def test_page_first_page_marks_must_increase():
    # "Mál nr. 3 Hæstaréttar" holds a number that looks like a page mark but is lower than the mark before it
    text = (
        "5 Jón Jónsson: Mál nr. 3 Hæstaréttar\n9 Páll Pálsson: Önnur grein\n"
        "12 Anna Önnudóttir: Þriðja grein\n15 Gunnar Gunnarsson: Fjórða grein\n20 Ólöf Ólafsdóttir: Fimmta grein\n"
    )
    got = [(e.page_start, e.title) for e in parse_toc(text, style="page_first")]
    assert got == [(5, "Mál nr. 3 Hæstaréttar"), (9, "Önnur grein"), (12, "Þriðja grein"), (15, "Fjórða grein"), (20, "Fimmta grein")]
    # the reviewer's phrase on its own: the first mark stays, the lower one is not a boundary
    text = "3 Hæstaréttar og 2 Íslendingar\n" + "".join(
        f"{n} Jón Jónsson: Grein {n}\n" for n in (5, 9, 12, 15, 20))
    got = [(e.page_start, e.title) for e in parse_toc(text, style="page_first")]
    assert got[0] == (3, "Hæstaréttar og 2 Íslendingar") and [g[0] for g in got] == [3, 5, 9, 12, 15, 20]


def test_page_first_scrambled_cover_gives_nothing():
    # TL 2007-2008 covers: the text layer detaches the numbers from the titles ("83 / 5 / 39 / Matthías G. Pálsson: …").
    # A parse of that would pair numbers with the wrong titles, so the caller falls back to leitir.is / the LLM instead.
    assert parse_toc(_load("tl_2007_01_scrambled.txt"), style="page_first") == []
    text = "7 Jón Jónsson: A\n3 Páll Pálsson: B\n9 Anna Önnudóttir: C\n2 Gunnar Gunnarsson: D\n5 Ólöf Ólafsdóttir: E\n"
    assert parse_toc(text, style="page_first") == []


def test_page_first_colophon_before_the_entries_is_not_parsed():
    # Lögmannablaðið: "2 / LÖGMANNABLAÐIÐ / EFNISYFIRLIT / <colophon with 108 Reykjavík, 0800, ISSN> / 4 NAME / title …"
    entries = parse_toc(_load("logmannabladid_2023_01.txt"), style="page_first")
    assert [e.page_start for e in entries] == [4, 6, 8, 11, 13, 14, 16, 18, 23, 24]
    by = _by_page(entries)
    assert by[4].authors == ["Sigurður Örn Hilmarsson"] and by[4].title == "Formannspistill"
    assert by[6].authors == ["Ari Karlsson"] and by[6].title == "Frá ritstjórn"
    assert by[8].authors == ["Eva Hrönn Jónsdóttir"] and by[8].title == "Af þagnarskyldu, hagsmunagæslu og þóknun lögmanna"
    assert by[11].authors == [] and by[11].title == "Í fréttum er þetta helst"
    assert by[14].title == "Róbert Spanó í Hörpu: Sjálfbærni og UFS"
    assert by[18].authors == ["Sveinn Andri Sveinsson"]
    # an interview heading is not a person called "Viðtal Við Jónas Haraldsson"
    assert by[24].authors == [] and by[24].title.startswith("VIÐTAL VIÐ JÓNAS HARALDSSON")
    assert not any("Reykjavík" in e.title or "ISSN" in e.title for e in entries)


def test_page_first_issn_after_the_entries_does_not_swallow_them():
    text = "4 Jón Jónsson: Grein ein\n6 Páll Pálsson: Grein tvö\n9 Anna Önnudóttir: Grein þrjú\nISSN 1670-2689\n"
    assert [e.page_start for e in parse_toc(text, style="page_first")] == [4, 6, 9]


def test_toc_style_depends_on_the_kind_of_file():
    assert toc_style("timarit_logfraedinga") == "page_first"                 # default kind: issue
    assert toc_style("timarit_logfraedinga", "issue") == "page_first"
    assert toc_style("timarit_logfraedinga", "volume") == "page_last"        # annual volumes: title, leaders, page
    assert toc_style("logmannabladid") == "page_first"                       # page, NAME, title
    assert toc_style("logmannabladid", "issue") == "page_first"
    assert toc_style("logbru", "issue") == "page_last" and toc_style("logbru", "volume") == "page_last"
    with pytest.raises(ValueError):
        toc_style("logbru", "article")


def test_tl_annual_volume_parses_with_the_style_chosen_for_a_volume():
    style = toc_style("timarit_logfraedinga", "volume")
    e = next(x for x in parse_toc(_load("tl_1985.txt"), style=style) if x.title.startswith("Almennar reglur um sameign"))
    assert e.page_start == 7 and e.issue == "1"


def test_find_toc_pages_three_entry_covers():
    # three articles: 155 / 157 / 183 and 475 / 487 / 515 (the old threshold of four lines missed them)
    assert find_toc_pages(["Kápa", _load("tl_2019_02.txt")]) == [1]
    assert find_toc_pages([_load("tl_2021_04.txt")]) == [0]


def test_find_toc_pages_skips_body_pages_with_footnote_markers():
    # TL 2021_04: cover with three entries, then article pages whose footnotes are "1\t", "2\t" … on their own lines
    assert find_toc_pages(_pages("tl_2021_04")) == [0]
    # TL 2025_02 starts in the middle of an article; its footnote markers must not make it a TOC
    assert find_toc_pages(_pages("tl_2025_02")) == []
    footnotes = "Texti með tilvísun.1\n" + "".join(f"{n}\t\nSjá nánar í heimild númer {n} og samanburð.\n" for n in range(1, 8))
    assert find_toc_pages([footnotes]) == []


def test_find_toc_pages_ignores_tables_of_figures():
    table = "Dæmd mál\n" + "\n".join(str(n) for n in (566, 419, 417, 490, 440, 485, 487, 558, 590))
    assert find_toc_pages([table]) == []


def test_find_toc_pages_stops_at_the_first_page_after_the_toc():
    # Lögbrú 2013-01: TOC on page index 2; the article that follows prints its own outline (1. Inngangur … 6) on pages 4-5
    pages = _pages("logbru_2013_01")
    assert find_toc_pages(pages) == [2]
    text = "\n".join(pages[i] for i in find_toc_pages(pages))
    assert [e.page_start for e in parse_toc(text, style="page_last")] == [5, 57, 99, 155]


def test_find_toc_pages_finds_the_logmannabladid_toc_page_and_not_the_cover():
    pages = _pages("logmannabladid_2023_01")
    assert find_toc_pages(pages) == [1]


def test_page_first_author_lines_that_wrap_or_continue():
    text = (
        "4\nSIGURÐUR ÖRN HILMARSSON\nFormannspistill\n"
        "6\nHRAFNHILDUR SÆBERG\nÞORSTEINSDÓTTIR\nII. Peningaþvætti og alþjóðleg regluverk\n"          # surname wrapped onto its own line
        "9\nKRISTRÚN ELSA HARÐARDÓTTIR\nOG ÞORBJÖRG INGA JÓNSDÓTTIR\n20 ára afmælishátíð\n"        # list goes on after a line starting "OG"
        "12\nEYRÚN INGADÓTTIR OG\nKATRÍN SMÁRI ÓLAFSDÓTTIR\nRéttarsöguferð\n"                       # … or after a line ending in "OG"
        "15\nÚRSKURÐARNEFND LÖGMANNA:\nKvörtunum fjölgar\n"                                         # a body, not a person
        "18\nMÁLÞING FKL\nJafnréttisparadísin Ísland?\n"                                            # "Málþing" is an ordinary word
        "21\nBrynhildur G. Flóvenz og Sigurlaug Soffía\nFriðþjófsdóttir: Réttarvernd fatlaðs fólks\n"  # list wraps before the colon
    )
    got = [(e.page_start, e.authors, e.title) for e in parse_toc(text, style="page_first")]
    assert got == [
        (4, ["Sigurður Örn Hilmarsson"], "Formannspistill"),
        (6, ["Hrafnhildur Sæberg Þorsteinsdóttir"], "II. Peningaþvætti og alþjóðleg regluverk"),
        (9, ["Kristrún Elsa Harðardóttir", "Þorbjörg Inga Jónsdóttir"], "20 ára afmælishátíð"),
        (12, ["Eyrún Ingadóttir", "Katrín Smári Ólafsdóttir"], "Réttarsöguferð"),
        (15, [], "ÚRSKURÐARNEFND LÖGMANNA: Kvörtunum fjölgar"),
        (18, [], "MÁLÞING FKL Jafnréttisparadísin Ísland?"),
        (21, ["Brynhildur G. Flóvenz", "Sigurlaug Soffía Friðþjófsdóttir"], "Réttarvernd fatlaðs fólks"),
    ]


def test_page_first_a_single_mark_is_not_a_toc():
    # TL 2004_03: the scrambled cover leaves only the colophon's "108 Reykjavík, sími …"
    assert parse_toc("5 Jón Jónsson: Ein grein\n", style="page_first") == []


def test_find_toc_pages_ignores_address_and_phone_numbers_of_ad_pages():
    # Lögmannablaðið 2017_04, page 3: "Bæjarflöt 4 / 112 Reykjavík / Sími: 568 9095"
    ad = "Bæjarflöt 4\n112 Reykjavík\nSími: 568 9095\nwww.gagnaeyding.is\nVarðveislu persónuupplýsinga lýkur með eyðingu þeirra\n"
    assert find_toc_pages([ad]) == []
    toc_page = _load("logmannabladid_2023_01.txt")
    assert find_toc_pages(["Kápa", toc_page, ad]) == [1]
    # each guard on its own: phone numbers ("568 9095"), postal codes before a town
    assert find_toc_pages(["Sími 568 9095\nFax 568 9300\nGsm 899 9400\nwww.x.is"]) == []
    assert find_toc_pages(["101 Reykjavík\n200 Kópavogur\n600 Akureyri\nwww.x.is"]) == []


def test_page_first_colophon_without_issn_before_the_entries():
    # Lögmannablaðið 2018: no ISSN; the page's own number "2" opens a long stretch (header + colophon) before "4 …"
    colophon = "".join(f"Starfsmaður númer {n} lögmaður hjá félaginu\n" for n in range(12))
    text = (
        "2\nLÖGMANNABLAÐIÐ TBL 01/18\nEFNISYFIRLIT\nLögmannafélag Íslands\nÁlftamýri 9, 108 Reykjavík\n" + colophon
        + "4\nEVA HALLDÓRSDÓTTIR\nLeiðari\n6\nREIMAR PÉTURSSON\nÁskoranir nútímans\n8\nINGIMAR INGASON\nFélagsmönnum LMFÍ fækkar\n"
    )
    got = [(e.page_start, e.authors, e.title) for e in parse_toc(text, style="page_first")]
    assert got == [
        (4, ["Eva Halldórsdóttir"], "Leiðari"),
        (6, ["Reimar Pétursson"], "Áskoranir nútímans"),
        (8, ["Ingimar Ingason"], "Félagsmönnum LMFÍ fækkar"),
    ]


# ---------------------------------------------------------------------------------------------
# Review round 2: a surname BÍN stores only as a compound noun (Líndal = lín-dalur) is still a surname.
# ---------------------------------------------------------------------------------------------

def test_surnames_that_bin_knows_only_as_ordinary_words_are_still_authors():
    # TL annual volumes 1982-2002: "… eftir Sigurð Líndal"
    (e,) = parse_toc("Hvers vegna var Staðarhólsbók Grágásar skrifuð? eftir Sigurð Líndal .......... 12\n", style="page_last")
    assert e.authors == ["Sigurður Líndal"] and e.title == "Hvers vegna var Staðarhólsbók Grágásar skrifuð?"
    (e,) = parse_toc("Um skýringu laga eftir Vilhjálm Ámason .......... 30\n", style="page_last")   # OCR "Ámason", alm-only in BÍN
    assert e.authors == ["Vilhjálmur Ámason"]
    # Lögmannablaðið 2016_02 and a page_first cover: "Þórhildur Líndal: Málstofa: …"
    (e, _) = parse_toc("18 Þórhildur Líndal: Málstofa: Eftirlitshlutverk stjórnvalda\n20 Jón Jónsson: Grein\n", style="page_first")
    assert e.authors == ["Þórhildur Líndal"] and e.title == "Málstofa: Eftirlitshlutverk stjórnvalda"
    # the author line above the title, and the comma form
    got = parse_toc("Sigurður Líndal,\nStjórnskipun Íslands ...... 7\n", style="page_last")
    assert [(x.authors, x.title) for x in got] == [(["Sigurður Líndal"], "Stjórnskipun Íslands")]


def test_label_words_before_a_name_are_still_not_authors():
    text = (
        "4 MÁLÞING FKL: Jafnréttisparadísin Ísland?\n"
        "6 ÚRSKURÐARNEFND LÖGMANNA: Kvörtunum fjölgar\n"
        "8 FRÆÐSLUFUNDUR MEÐ JÓLAÍVAFI: Nauðsyn upplýsingatæknikunnáttu\n"
        "10 VIÐTAL VIÐ JÓNAS HARALDSSON: Hef virkilega reynt að vera til vandræða\n"
    )
    got = [(e.authors, e.title) for e in parse_toc(text, style="page_first")]
    assert got == [
        ([], "MÁLÞING FKL: Jafnréttisparadísin Ísland?"),
        ([], "ÚRSKURÐARNEFND LÖGMANNA: Kvörtunum fjölgar"),
        ([], "FRÆÐSLUFUNDUR MEÐ JÓLAÍVAFI: Nauðsyn upplýsingatæknikunnáttu"),
        ([], "VIÐTAL VIÐ JÓNAS HARALDSSON: Hef virkilega reynt að vera til vandræða"),
    ]


def test_a_heading_in_capitals_over_two_lines_is_not_an_author():
    # Lögmannablaðið 2017_04: "SAGA ANNARS STOFNANDA / MÁLFLUTNINGSMANNAFÉLAGS ÍSLANDS / Ævisaga og aldarspegill"
    # (BÍN cannot tell the compound noun "Stofnanda" from a surname like "Líndal", the layout can)
    text = (
        "20\nSKÝRSLA OG FÉLAGSFUNDUR\nLMFÍ skorar á dómsmálaráðherra\n"
        "22\nSAGA ANNARS STOFNANDA\nMÁLFLUTNINGSMANNAFÉLAGS ÍSLANDS\nÆvisaga og aldarspegill\n"
        "28\nKOLBRÚN GARÐARSDÓTTIR\nHugleiðingar um #METOO byltinguna\n"
    )
    got = [(e.page_start, e.authors, e.title) for e in parse_toc(text, style="page_first")]
    assert got == [
        (20, [], "SKÝRSLA OG FÉLAGSFUNDUR LMFÍ skorar á dómsmálaráðherra"),
        (22, [], "SAGA ANNARS STOFNANDA MÁLFLUTNINGSMANNAFÉLAGS ÍSLANDS Ævisaga og aldarspegill"),
        (28, ["Kolbrún Garðarsdóttir"], "Hugleiðingar um #METOO byltinguna"),
    ]
