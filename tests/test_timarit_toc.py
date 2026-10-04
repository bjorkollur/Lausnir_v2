# tests/test_timarit_toc.py
import json
from pathlib import Path

import pytest

from engine.timarit.toc import (
    TocEntry, clean_title, find_toc_pages, is_label, label_type, parse_toc, split_label, style_of_text, toc_style,
)

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
        ("Önnur grein", 61, None, "2"),                 # a new hefti ends the heading's reach
    ]


def test_a_contents_row_whose_title_is_a_label_word_is_an_entry_not_a_heading():
    """Task 22e, Lögbrú 2015-1: the interview is listed as "Guðfinnur Stefánsson, / Viðtal / 9". "Viðtal" has an author
    above it and its page below it, so it is the title of a row. As a heading it dropped the row and typed the four
    articles after it as interviews."""
    es = parse_toc(_load("logbru_2015_01.txt"), style="page_last")
    assert [(e.page_start, e.section) for e in es] == [(5, None), (9, None), (16, None), (29, None), (46, None), (64, None)]
    assert [e.title.split()[0] for e in es] == ["Kynning", "Viðtal", "Túlkun", "Hvaða", "Nýsköpun", "Fordæmi"]
    assert es[1].title == "Viðtal"
    assert es[1].authors == ["Guðfinnur Stefánsson"]


def test_a_label_word_with_its_page_on_the_next_line_is_an_entry_whatever_stands_above():
    text = "Jón Jónsson: Fyrsta grein ..... 3\nFréttir\n12\nPáll Pálsson: Önnur grein ..... 20\n"
    assert [(e.title, e.page_start, e.section) for e in parse_toc(text, style="page_last")] == [
        ("Fyrsta grein", 3, None), ("Fréttir", 12, None), ("Önnur grein", 20, None)]


_SECTIONS_ONE_PER_LINE = (        # Lögbrú's layout: author line, title, page on a line of its own
    "Fræðigreinar\nJón Jónsson,\nFyrsta grein\n3\nPáll Pálsson,\nÖnnur grein\n20\n"
    "Ritdómar\nAnna Önnudóttir,\nBók A\n40\nGuðrún Gunnarsdóttir,\nBók B\n44\n")


def test_a_heading_over_several_rows_types_all_of_them_until_the_next_heading():
    got = [(e.title, e.page_start, e.section) for e in parse_toc(_SECTIONS_ONE_PER_LINE, style="page_last")]
    assert got == [("Fyrsta grein", 3, "Fræðigreinar"), ("Önnur grein", 20, "Fræðigreinar"),
                   ("Bók A", 40, "Ritdómar"), ("Bók B", 44, "Ritdómar")]


def test_a_heading_in_the_singular_heads_one_row():
    """A heading "Viðtal" or "Ritstjórnargrein" names one piece, so the row after it is the only one it types."""
    text = ("Jón Jónsson: Fyrsta grein ..... 3\nViðtal\nGuðfinnur Stefánsson: Um laganám ..... 9\n"
            "Páll Pálsson: Önnur grein ..... 20\nRitstjórnargrein\nAnna Önnudóttir: Leiðari ..... 22\n"
            "Karl Karlsson: Þriðja grein ..... 30\n")
    assert [(e.title, e.section) for e in parse_toc(text, style="page_last")] == [
        ("Fyrsta grein", None), ("Um laganám", "Viðtal"), ("Önnur grein", None), ("Leiðari", "Ritstjórnargrein"),
        ("Þriðja grein", None)]
    plural = "Viðtöl\nJón Jónsson: Fyrra ..... 3\nPáll Pálsson: Síðara ..... 9\n"
    assert [e.section for e in parse_toc(plural, style="page_last")] == ["Viðtöl", "Viðtöl"]


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


# --- Task 17a (R32): an article's own outline is not the issue's TOC -----------------------------------------

def test_an_articles_own_outline_is_not_an_issue_toc():
    # Lögrétta 2010 1.tbl starts with the first article: "a" / "9", title, author, then its outline
    # "1. Inngangur\t" / "10", "2. Vanefndir samkvæmt lánasamningum\t" / "10", "2.1. Almennt\t" / "10" … "4. Nokkur lokaorð\t" / "21".
    # Task 17's dry run parsed that outline as the issue TOC ("a" 9, "1. Inngangur" 10, …).
    pages = _pages("logretta_2010_1")
    assert "1. Inngangur" in pages[0] and "2. Vanefndir samkvæmt lánasamningum" in pages[0]
    assert find_toc_pages(pages) == []


def test_an_outline_with_section_numbers_on_lines_of_their_own_is_not_a_toc():
    # Lögrétta 2019 4.tbl: "1. Almennt um reglur … 3", then "2.1 " alone on a line above "Almennt …… 5"
    assert find_toc_pages(_pages("logretta_2019_4")) == []


def test_the_scan_stops_at_an_article_outline():
    # Lögrétta 2019 3.tbl: the outline on page 0, then article pages whose footnotes end in page references
    # ("Press 2012). 19", "… 2/2008. 140"). Once the article has begun no issue TOC follows: before this rule the
    # body page 1 was returned with the outline.
    pages = _pages("logretta_2019_3")
    assert find_toc_pages(pages[1:]) == [0]          # the body page alone does look like a TOC page …
    assert find_toc_pages(pages) == []               # … but not after the article's outline


def test_numbered_dates_and_volume_lines_do_not_make_a_toc_an_outline():
    # TL 1992, cover of 1. hefti: "1.HEFTI", "42. ÁRGANGUR", "1. júlí 1992", "15. febr. 1991-19. rebr. 1992" start with
    # "N." but are no section headings; the page is the hefti's TOC (1, 3, 15, 19, 58).
    pages = _pages("tl_1992_hefti_1_cover")
    assert "42. ÁRGANGUR" in pages[0] and "1. júlí 1992" in pages[0]
    assert find_toc_pages(pages) == [0]


def test_a_real_toc_after_an_outline_free_cover_is_unchanged():
    # Lögbrú 2013-01 prints its first article's outline on pages 4-5, after the TOC on page 2 (unchanged: [2])
    assert find_toc_pages(_pages("logbru_2013_01")) == [2]


# --- Task 22b: TOC titles and names as the pilot audit found them -------------------------------------------------

@pytest.mark.parametrize("raw, clean", [
    # Lögfræðingur 2011 / 2012 / 2008: OCR'd dotted leaders and their page-number tails (audit, manifest + idx in the report)
    ("Lýðveldi, stjórnarskrá og þjóðaratkvæði ...........eeeeeeeee00eeeeeeeeeee er", "Lýðveldi, stjórnarskrá og þjóðaratkvæði"),
    ("Ávarp formanns Þemis ..............ee0eeeeaeenenenenenenenerreeenenenrnenerrrr rr", "Ávarp formanns Þemis"),
    ("Samræmist bann við skipulögðum glæpasamtökum gildandi mannéttindum á Íslandi? "
     ".................eeeeeeeeeezeeneneneneeeereeenenenenenrrrrrrnna",
     "Samræmist bann við skipulögðum glæpasamtökum gildandi mannéttindum á Íslandi?"),
    ("Öryggisráð SÞ frá sjónarhóli þjóðaréttar eee.", "Öryggisráð SÞ frá sjónarhóli þjóðaréttar"),
    ("Laganám við Háskólann á Akureyri .....................00000000000 nn nenna", "Laganám við Háskólann á Akureyri"),
    ("Mál Mannréttindanefndar SÞ 1977-2008 — Handbók...............00000ee.....0",
     "Mál Mannréttindanefndar SÞ 1977-2008 — Handbók"),
    ("Iceland's Renaissance: Time for Comparative Law eee", "Iceland's Renaissance: Time for Comparative Law"),
    # TL 1955: a page range after a short leader
    ("Þróun íslenzkra réttarreglna um hjónavígslutálma frá siðaskiptum til vorra daga .. 65—",
     "Þróun íslenzkra réttarreglna um hjónavígslutálma frá siðaskiptum til vorra daga"),
    # titles that end in words, numbers or years stay as they are
    ("Hæstaréttardómur frá 31. maí 1985", "Hæstaréttardómur frá 31. maí 1985"),
    ("Takmörk markmiðsskýringar – hugleiðingar um hrd. 1993, bls. 2230 (Helga Kress)",
     "Takmörk markmiðsskýringar – hugleiðingar um hrd. 1993, bls. 2230 (Helga Kress)"),
    ("Hvað er?", "Hvað er?"),
    ("Störf Þemis síðastliðið ár", "Störf Þemis síðastliðið ár"),
    ("Sérgreiningarregla höfundaréttar*", "Sérgreiningarregla höfundaréttar"),
])
def test_clean_title_strips_ocr_leaders_and_page_tails(raw, clean):
    assert clean_title(raw) == clean


@pytest.mark.parametrize("raw, label, rest", [
    ("GESTABÁLKUR Viðtal við Markús Sigurbjörnsson, fyrrverandi hæstaréttardómara",      # Úlfljótur 2023-3 idx 3
     "Gestabálkur", "Viðtal við Markús Sigurbjörnsson, fyrrverandi hæstaréttardómara"),
    ("RITSTJÓRNARGREIN: Ný löggjöf um skipun dómara", "Ritstjórnargrein", "Ný löggjöf um skipun dómara"),
    ("Fræðabálkur", "Fræðabálkur", ""),                                                  # Úlfljótur 2010-1 idx 2
    ("Leiðari", "Leiðari", ""),                                                          # LMBL 2017-1 idx 0
    ("Fréttir af aðalfundi", None, "Fréttir af aðalfundi"),                               # a title that starts with a label word
    ("FRÉTTIR AF AÐALFUNDI", None, "FRÉTTIR AF AÐALFUNDI"),
    ("Viðtöl við bifrestinga", None, "Viðtöl við bifrestinga"),
])
def test_split_label(raw, label, rest):
    assert split_label(raw) == (label, rest)


def test_section_label_types():
    assert label_type("Leiðari") == "Ritstjórnargrein" and label_type("FRÆÐABÁLKUR") == "Fræðigrein"
    assert label_type("Gestabálkur") is None and label_type("Ný löggjöf") is None


def test_page_first_author_list_wrapped_inside_a_name():
    # LMBL 2020-1 idx 5 and 2017-1 idx 7: "GUÐRÚN OLSEN OG BERGÞÓR" / "BERGSSON" and "EYRÚN INGADÓTTIR OG VÍÐIR" /
    # "SMÁRI PETERSEN": the second name wraps; it was glued into the title ("OG BERGÞÓR BERGSSON Viðbótargreinargerðir …")
    e20 = {e.page_start: e for e in parse_toc(_load("logmannabladid_2020_01.txt"), style="page_first")}
    assert e20[26].authors == ["Guðrún Olsen", "Bergþór Bergsson"]
    assert e20[26].title == "Viðbótargreinargerðir á Íslandi og í Danmörku"
    e17 = {e.page_start: e for e in parse_toc(_load("logmannabladid_2017_01.txt"), style="page_first")}
    assert e17[24].authors == ["Eyrún Ingadóttir", "Víðir Smári Petersen"] and e17[24].title == "Hin nýja dómstólasýsla"
    assert e17[16].title == "Hinn nýi Landsréttur" and e17[4].title == "Leiðari"


def test_page_last_ocr_junk_lines_credits_and_backward_numbers():
    # Lögfræðingur 2011 (idx 0, 1, 6, 7, 9 of the pilot manifest)
    es = parse_toc(_load("logfraedingur_2011.txt"), style="page_last")
    by_title = {e.title: e for e in es}
    assert by_title["Ávarp formanns Þemis"].page_start == 6
    assert by_title["Lýðveldi, stjórnarskrá og þjóðaratkvæði"].page_start == 8
    rd = by_title["Mál Mannréttindanefndar SÞ 1977-2008 — Handbók"]
    assert rd.authors == ["Margrét Heinreksdóttir"] and rd.section == "Ritdómar" and rd.page_start == 96
    pl = by_title["Polar Law Textbook"]
    assert pl.authors == ["Rachael Lorna Johnstone"] and pl.section == "Ritdómar" and pl.page_start == 106
    assert by_title["The Yearbook of Polar Law"].authors == ["Rachael Lorna Johnstone"]
    assert by_title["Laganám við Háskólann á Akureyri"].page_start == 112
    assert by_title["Millidómstig — tálsýn eða raunveruleiki?"].authors == ["Símon Sigvaldason"]
    # "2" printed for 27 after page 8: a number that goes back is no start page; the title is located on the pages
    icesave = next(e for e in es if e.title.startswith("ICESAVE"))
    assert icesave.page_start is None and icesave.authors == ["Birna Ágústsdóttir"]
    assert not any(e.title in ("00", "er", "rr") for e in es)


def test_page_last_page_ranges_and_sami():
    # TL 1955 1. hefti (idx 7, 8, 9): "… 12— 15", "65— 86" on a line of its own, "87—103"; "Sami:" = the author above
    es = parse_toc(_load("tl_1955_01.txt"), style="page_last")
    starts = {e.page_start: e for e in es}
    assert starts[1].title == "Einar Arnórsson — In memoriam" and starts[1].authors == ["Theodór B. Líndal"]
    assert starts[12].title == "Ólafur Lárusson sjötugur" and starts[12].authors == ["Bjarni Benediktsson"]
    assert starts[16].title == "Þjóðréttarnefnd Sameinuðu þjóðanna"
    assert starts[60].title == "Erlendar bækur" and starts[60].authors == ["Theodór B. Líndal"]
    assert starts[65].title == "Þróun íslenzkra réttarreglna um hjónavígslutálma frá siðaskiptum til vorra daga"
    assert starts[65].authors == ["Ármann Snævarr"]
    assert starts[87].title.endswith("Helztu drættir i danskri löggjöf um vörumerki")
    assert [e.page_start for e in es if e.page_start] == sorted(e.page_start for e in es if e.page_start)


def test_a_page_first_source_with_a_page_last_toc_is_read_page_last():
    # TL 1951–1970 issue files print "Author: Title ...... 12— 15" (TL 1955 1. hefti idx 7–9 were off by one entry)
    text = _load("tl_1955_01.txt")
    assert style_of_text(text, "page_first") == "page_last"
    assert style_of_text(_load("tl_2010_01.txt"), "page_first") == "page_first"
    assert style_of_text(text, "page_last") == "page_last"


# --- Task 22f: a heading outside _SECTION ends a heading's reach (Úlfljótur 2010–2017 contents) ---------------------

def _rows(text):
    return [(e.title, e.page_start, e.section) for e in parse_toc(text, style="page_last")]


def test_ulfljotur_column_headings_end_the_reach_of_greinar():
    """Ulfljotur_2015_2_tbl.pdf, the contents page: "GREINAR" heads four articles; "RÖKSTÓLAR" (a debate column) and
    "REKABÁLKUR" are headings of their own. They were taken for text: GREINAR typed every row after them as a
    scholarly article and the heading was glued onto the next title ("RÖKSTÓLAR Starfsnám laganema …")."""
    got = _rows(_load("ulfljotur_2015_2.txt"))
    assert got[:5] == [
        ("Frá ritstjóra", 129, None),
        ("Um heimildir endurupptökunefndar í ljósi stjórnarskrárákvæða um dómsvald", 131, "GREINAR"),
        ("Stjórnsýslukæra", 141, "GREINAR"),
        ("Umboðsreglur hlutafélagaréttar", 179, "GREINAR"),
        ("Skilyrði reglunnar um brostnar forsendur í samningarétti", 215, "GREINAR"),
    ]
    assert [(page, section) for _, page, section in got[5:]] == [(295, None), (297, None), (299, None), (301, None),
                                                                (305, None), (308, None)]
    assert got[5][0].startswith("Starfsnám laganema") and got[9][0] == "Alþjóðlega Philip C. Jessup málflutningskeppnin 2015"
    assert not any("RÖKSTÓLAR" in t or "REKABÁLKUR" in t for t, _, _ in got)


def test_ulfljotur_interview_and_news_columns_are_not_scholarly_articles():
    """Ulfljotur_2017_1_tbl.pdf (one of the twelve pages of the Task 22e review): RÖKSTÓLAR, GESTABÁLKUR and
    FRÉTTABÁLKUR each end GREINAR's reach; the news column types its row as news, the guest column leaves the type to
    the title ("Viðtal við …"), the debate column types nothing."""
    es = parse_toc(_load("ulfljotur_2017_1.txt"), style="page_last")
    got = [(e.page_start, e.section) for e in es]
    assert got == [(3, None), (5, "GREINAR"), (41, "GREINAR"), (87, "GREINAR"), (107, None), (108, None), (115, None),
                   (120, None), (126, "Frétt")]
    by_page = {e.page_start: e for e in es}
    assert by_page[107].title == "Endurskoðun stjórnarskrárinnar"
    assert by_page[108].authors == ["Salvör Nordal"] and by_page[115].authors == ["Skúli Magnússon"]
    assert by_page[120].title == "Viðtal við Kristínu Edwald"
    assert not any(e.title.startswith(("RÖKSTÓLAR", "GESTABÁLKUR", "FRÉTTABÁLKUR")) for e in es)


def test_a_cover_listing_with_label_headings_in_lower_case():
    """The cover of Ulfljotur_2011_3_tbl.pdf lists "Gestabálkur" / "Viðtal við Gest Jónsson, hrl." / "489": the label
    is a heading (it has no page of its own) and is not part of the title."""
    text = ("Þorgeir Örlygsson\nEfndabætur\n393\nGestabálkur\nViðtal við Gest Jónsson, hrl.\n489\n"
            "Rekabálkur\nLögfræðiaðstoð Orators 30 ára\n495\n")
    assert _rows(text) == [("Efndabætur", 393, None), ("Viðtal við Gest Jónsson, hrl.", 489, None),
                           ("Lögfræðiaðstoð Orators 30 ára", 495, None)]


def test_a_label_heading_with_a_colon_sets_its_type():
    text = ("Greinar:\nJón Jónsson: Fyrsta grein ..... 3\nBókabálkur:\nPáll Pálsson: Um nýja bók ..... 40\n"
            "Fréttabálkur\nAðalfundur Orators ..... 50\n")
    assert _rows(text) == [("Fyrsta grein", 3, "Fræðigrein"), ("Um nýja bók", 40, "Ritdómur"),
                           ("Aðalfundur Orators", 50, "Frétt")]


def test_an_unknown_heading_in_capitals_types_nothing_and_ends_the_reach():
    text = "GREINAR\nJón Jónsson: Fyrsta grein ..... 3\nMÁLSTOFA\nPáll Pálsson: Erindi um dóma ..... 20\n"
    assert _rows(text) == [("Fyrsta grein", 3, "GREINAR"), ("Erindi um dóma", 20, None)]


def test_lines_in_capitals_that_are_not_headings():
    # an author line in capitals stays the author; a title in capitals over two lines stays whole; a title in capitals
    # with its page on the next line is a row
    text = ("GREINAR\nJÓN JÓNSSON\nFyrsta grein ..... 3\nUM STJÓRNARSKRÁ\nÍSLANDS ..... 20\nLOKAORÐ\n30\n")
    es = parse_toc(text, style="page_last")
    assert [(e.title, e.page_start, e.section) for e in es] == [
        ("Fyrsta grein", 3, "GREINAR"), ("UM STJÓRNARSKRÁ ÍSLANDS", 20, "GREINAR"), ("LOKAORÐ", 30, "GREINAR")]
    assert es[0].authors == ["Jón Jónsson"]                               # clean_author title-cases a shouted name


def test_a_label_line_with_a_leader_is_a_row_whose_number_is_missing():
    """A heading has no leader: "Á víð og dreif ......" is a row (its number lost by the OCR), not a heading."""
    text = "Jón Jónsson: Fyrsta grein ..... 3\nÁ víð og dreif ..........\nNorrænt lögfræðingaþing — Ný rit\n12\n"
    got = _rows(text)
    assert got[-1][1] == 12 and got[-1][0].startswith("Á víð og dreif")


def test_a_label_column_in_an_annual_volume_is_a_heading_not_part_of_the_title():
    """TL 1995: "Á víð og dreif" heads the rows under it; it was glued onto the first one's title."""
    text = ("Ragnar Aðalsteinsson ..... 227\nÁ víð og dreif\nVígsla Mannréttindabyggingarinnar í Strasbourg ..... 229\n"
            "Aðalfundur Lögfræðingafélags íslands 1994 ..... 232\n")
    assert _rows(text)[1:] == [("Vígsla Mannréttindabyggingarinnar í Strasbourg", 229, None),
                               ("Aðalfundur Lögfræðingafélags íslands 1994", 232, None)]


def test_the_contents_title_starts_the_list_anew_and_a_column_head_does_not():
    """Úlfljótur 2005–2017 print a cover listing ("MEÐAL EFNIS:") before the contents page; its last column must not
    type the contents' first rows. A column head ("Bls.") under a heading leaves the heading in force."""
    text = "Fræðigreinar\nJón Jónsson: Fyrsta grein ..... 3\nEFNISYFIRLIT\nPáll Pálsson: Önnur grein ..... 20\n"
    assert [s for _, _, s in _rows(text)] == ["Fræðigreinar", None]
    text = "Fræðigreinar\nBls.\nJón Jónsson: Fyrsta grein ..... 3\nPáll Pálsson: Önnur grein ..... 20\n"
    assert [s for _, _, s in _rows(text)] == ["Fræðigreinar", "Fræðigreinar"]


def test_an_ulfljotur_column_heads_its_one_row():
    """Ulfljotur_2005_2_tbl.pdf: "Bókabálkur:" heads the review under it; the moot court reports and "Lögfræðingatal"
    after it have no heading of their own and are no reviews. A column ("-bálkur") is singular and heads one row; an
    unknown one ("Lagabálkur", 2007-4) is a heading that types nothing."""
    text = ("Bókabálkur:\nBjörn Þ. Guðmundsson, prófessor\nHæfisreglur stjórnsýslulaga eftir dr. Pál Hreinsson ..... 419\n"
            "Alþjóðlega málflutningskeppnin Jessup 2005 ..... 423\nLögfræðingatal ..... 433\n"
            "Fræðabálkur\nMálþing og fræðafundir ..... 440\nLagabálkur\nFréttir frá Alþingi ..... 450\n")
    assert [(t, s) for t, _, s in _rows(text)] == [
        ("Hæfisreglur stjórnsýslulaga eftir dr. Pál Hreinsson", "Ritdómur"), ("Alþjóðlega málflutningskeppnin Jessup 2005", None),
        ("Lögfræðingatal", None), ("Málþing og fræðafundir", "Fræðigrein"), ("Fréttir frá Alþingi", None)]


def test_a_row_whose_title_is_a_column_label_ends_the_heading_above():
    """Ulfljotur_2008_2_tbl.pdf: "Rekabálkur" / "363" is a row, the column itself; it is not under the heading above it,
    and neither is what follows it."""
    text = "Greinar\nJón Jónsson: Fyrsta grein ..... 3\nRekabálkur\n363\nSkýrsla stjórnar Orators ..... 370\n"
    assert _rows(text) == [("Fyrsta grein", 3, "Greinar"), ("Rekabálkur", 363, None), ("Skýrsla stjórnar Orators", 370, None)]


def test_a_name_and_a_role_after_a_comma_is_not_an_author_and_a_title():
    """Ulfljotur_2012_2_tbl.pdf: "GESTABÁLKUR" / "Ólafur Þór Hauksson, sérstakur saksóknari ..." / "265" (the guest
    column's interviewee): a title never starts in lower case, so the comma does not split it."""
    text = "GESTABÁLKUR\nÓlafur Þór Hauksson, sérstakur saksóknari . . . . . .\n265\n"
    (e,) = parse_toc(text, style="page_last")
    assert (e.title, e.authors, e.section) == ("Ólafur Þór Hauksson, sérstakur saksóknari", [], None)


def test_ulfljotur_column_labels():
    assert label_type("FRÉTTABÁLKUR") == "Frétt" and is_label("Rökstólar") and label_type("RÖKSTÓLAR") is None
    assert split_label("RÖKSTÓLAR Geta fréttamenn sér að refsilausu birt upplýsingar") == (
        "Rökstólar", "Geta fréttamenn sér að refsilausu birt upplýsingar")


def test_the_author_index_ends_the_contents_also_with_a_colon():
    """TL 1973–1975 and 1980 print "HÖFUNDASKRÁ:"; the index after it lists "Nafn: Titill 7" again."""
    text = ("Jón Jónsson: Fyrsta grein ..... 3\nPáll Pálsson: Önnur grein ..... 20\nHÖFUNDASKRÁ:\n"
            "Ajo, P.: Lögfræðingasamband Finnlands ..... 213\nJónsson, Jón: Fyrsta grein ..... 3\n")
    assert [t for t, _, _ in _rows(text)] == ["Fyrsta grein", "Önnur grein"]


def test_a_line_after_a_heading_is_an_author_only_when_it_reads_as_a_person():
    """Úlfljótur 2003-4's cover: "MEÐAL EFNIS:" / "International Criminal Law" / "Universal jurisdiction in absentia" /
    "537". With the heading gone, the subject line looked like an author line; after a heading only a list with a
    personal name BÍN knows or a patronymic counts. Úlfljótur 2003-3 "Dómabálkur" / "Dóra Sif Tynes og Gunnar Þór
    Pétursson" / title, and 2022-2 "Geir Gestsson & Peter Dalmay", keep their authors."""
    text = "MEÐAL EFNIS:\nInternational Criminal Law\nUniversal jurisdiction in absentia\n537\n"
    (e,) = parse_toc(text, style="page_last")
    assert e.authors == [] and e.title == "International Criminal Law Universal jurisdiction in absentia"
    text = ("Dómabálkur\nDóra Sif Tynes og Gunnar Þór Pétursson\nYfirlit yfir helstu dóma Evrópudómstólanna\n455\n"
            "MEÐAL EFNIS:\nGeir Gestsson & Peter Dalmay\nLeitað í vasa nakins manns\n549\n"
            "MEÐAL EFNIS:\nSigurður Líndal\nUm lagaskil og afturvirkni laga\n605\n")
    assert [e.authors for e in parse_toc(text, style="page_last")] == [
        ["Dóra Sif Tynes", "Gunnar Þór Pétursson"], ["Geir Gestsson", "Peter Dalmay"], ["Sigurður Líndal"]]
