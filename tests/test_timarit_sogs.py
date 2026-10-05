# tests/test_timarit_sogs.py
"""Task 23b: Stjórnmál og stjórnsýsla first pages, title and authors as printed (sogs.read_first_page). Every page is a
real first page from the archive, read by firstpage.read_page, in tests/fixtures/timarit/pages/pagekind.json (made by
make_pagekind_fixture.py there; the top of the page kept whole). Each test names the audited article it mirrors."""
import json
from pathlib import Path

import pytest

from engine.timarit import sogs
from engine.timarit.firstpage import Line, Page

FIX = json.loads((Path(__file__).parent / "fixtures/timarit/pages/pagekind.json").read_text(encoding="utf-8"))


def page(key: str) -> Page:
    d = FIX[key]
    return Page([Line(*ln) for ln in d["lines"]], d["height"], d["width"])


@pytest.mark.parametrize("key, title, authors", [
    # early layout: title in large type, subtitle and byline in body type, "prófessor og" / "lektor" after the names
    ("ss01/0", "Hvenær verður minnihluti atkvæða að meirihluta fulltrúa? Tengslin milli atkvæðahlutfalls og "
               "stjórnarmeirihluta í skoðanakönnunum og í bæjarstjórnarkosningum 1930-2002",
     ["Ólafur Þ. Harðarson", "Indriði H. Indriðason"]),
    ("ss03/0", "Stjórnarmyndanir á Íslandi 1971-2007 Frá framsóknaráratugum til drottinsvalds Sjálfstæðisflokksins "
               "(með drauminn um samfylkingu jafnaðarmanna í bakgrunni)", ["Guðni Th. Jóhannesson"]),
    # Vol 5 idx 15 (audit 4): the middle author was missing; her surname is joined by "og"
    ("ss05b/0", "Mannréttindi, fötlun og fjölskyldulíf Í tilefni yfirfærslu málefna fatlaðs fólks til sveitarfélaga",
     ["Hanna Björg Sigurjónsdóttir", "Helga Baldvins- og Bjargardóttir", "Rannveig Traustadóttir"]),
    # Vol 5 idx 8: a foreign name in the subtitle's size after a wide gap ends the title
    ("ss05a/0", "Iceland’s Neighbours in the EU Entry Queue: Contrasts or Parallels in EU Enlargement to the North and "
                "the South-East", ["Alyson J. K. Bailes", "Jóhanna María Þórdísardóttir"]),
    # Vol 7 idx 16 (audit 4): the text layer holds one word per line; the printed Icelandic title, not the file's
    ("ss07b/0", "Frá mjúkum yfir í harðar samdráttaraðgerðir á vinnumarkaði Sveigjanleiki fyrirtækja og stofnana í "
                "kjölfar hruns", ["Arney Einarsdóttir", "Katrín Ólafsdóttir", "Auður Arna Arnardóttir"]),
    # Vol 7 idx 0: a byline not in bold, a gap above it
    ("ss07a/0", "The European Citizens’ Initiative. Transnational Democracy in the EU at last?", ["Maximilian Conrad"]),
    # Vol 8 idx 23 (audit 4): the page is typeset twice, a few points apart: one title, the second author found
    ("ss08b/0", "Er álið málið? Samfélagsbreytingar og atvinnumöguleikar kvenna á Austurlandi",
     ["Tinna Kristbjörg Halldórsdóttir", "Guðbjörg Linda Rafnsdóttir"]),
    ("ss08a/0", "Áhrif niðurskurðar á starfshvata og innbyrðis þekkingarmiðlun heilsugæsluhjúkrunarfræðinga",
     ["Emilía Jarþrúður Einarsdóttir", "Ingi Rúnar Eðvarðsson", "Sigríður Halldórsdóttir"]),
    # Vol 9 idx 0 and 25 (audit 4): three authors in one sentence with their affiliations; a name over a line's end
    ("ss09a/0", "Scotland as an Independent Small State: Where would it seek shelter?",
     ["Alyson JK Bailes", "Baldur Thorhallsson", "Rachael Lorna Johnstone"]),
    ("ss09b/0", "The Faroe Islands and the Arctic: Genesis of a Strategy", ["Alyson J.K. Bailes", "Beinta í Jákupsstovu"]),
    ("ss10/0", "Staða leikskólakennara í tveimur sveitarfélögum í kjölfar hruns",
     ["Laufey Axelsdóttir", "Gyða Margrét Pétursdóttir"]),
    # Vol 11 idx 3 (audit 4): the citation, contacts, DOI and licence header is no title
    ("ss11/0", "Sjálfsritskoðun íslenskra blaðamanna", ["Birgir Guðmundsson"]),
    # Vol 14 idx 1: four authors, each on a bold line, the roles on lines in plain type
    ("ss14/0", "Internal audit in the public sector – comparative study between the Nordic countries: The development "
               "of internal auditing within the public sector in the Nordic countries",
     ["Anna Margrét Jóhannesdóttir", "Stina Nilsson Kristiansson", "Niina Sipiläinen", "Riikka Koivunen"]),
    ("ss21/0", "Staðbundnir þingmenn í alþjóðlegum heimi: þingerindrekstur íslenskra þingmanna",
     ["Vilborg Ása Guðjónsdóttir", "Silja Bára Ómarsdóttir"]),
])
def test_title_and_authors_as_printed(key, title, authors):
    assert sogs.read_first_page(page(key)) == (title, authors)


@pytest.mark.parametrize("text, authors", [
    ("Gunnar Helgi Kristinsson, Professor of Political Science, University of Iceland", ["Gunnar Helgi Kristinsson"]),
    ("Kristín Loftsdóttir Professor, University of Iceland", ["Kristín Loftsdóttir"]),
    ("Hafsteinn Dan Kristjánsson. Magister juris", ["Hafsteinn Dan Kristjánsson"]),
    ("Walter L. Brent van der Hell, Research assistant", ["Walter L. Brent van der Hell"]),
    ("Adjunct Professor of Political Science and International Relations", []),
    ("Háskóla Íslands", []),
])
def test_byline_names_split_from_roles_and_affiliations(text, authors):
    assert sogs.byline_authors([Line(text, 12.0, 100.0, True)]) == authors


def test_a_name_runs_on_over_a_lines_end_only_while_it_is_not_whole():
    # Vol 8 idx 24: "… og Gyða Margrét" / "Pétursdóttir, lektor …"; Vol 6: "Háskóla Íslands" / "Friðrik Eysteinsson"
    assert sogs.byline_authors([Line("Steinunn Rögnvaldsdóttir, doktorsnemi og Gyða Margrét", 12.0, 100.0, True),
                                Line("Pétursdóttir, lektor við Háskóla Íslands", 12.0, 114.0, True)]) == \
        ["Steinunn Rögnvaldsdóttir", "Gyða Margrét Pétursdóttir"]
    assert sogs.byline_authors([Line("Þórhallur Guðlaugsson", 14.0, 100.0), Line("dósent við Viðskiptafræðideild", 14.0, 117.0),
                                Line("Háskóla Íslands", 14.0, 134.0), Line("Friðrik Eysteinsson", 14.0, 151.0)]) == \
        ["Þórhallur Guðlaugsson", "Friðrik Eysteinsson"]


def test_the_citation_on_the_first_page_gives_the_printed_range():
    # Vol 7 idx 16 (audit 4): no page number is read; the file name says 333–352, the journal's citation (327-346)
    assert sogs.printed_range(page("ss07b/0"), 20) == (327, 346)
    assert sogs.printed_range(page("ss11/0"), 16) == (55, 70)
    assert sogs.printed_range(page("ss07b/0"), 22) is None           # a citation that does not span the file
    assert sogs.printed_range(page("ss01/0"), 22) is None            # the early volumes print none


def test_a_number_of_an_acronym_in_the_title_is_no_footnote_mark():
    # Vol 11 idx 1 (review of 23b): the title ends "… using VSM94"; "… bankahruni1" ends in a footnote mark
    assert sogs._clean(Line("states using VSM94", 20.0, 149.2)).text == "states using VSM94"
    assert sogs._clean(Line("Lærdómar af bankahruni1", 20.0, 149.2)).text == "Lærdómar af bankahruni"
    assert sogs._clean(Line("fyrir íslensk félagasamtök?1", 20.0, 149.2)).text == "fyrir íslensk félagasamtök?"


def test_the_folio_of_the_second_page_checks_the_citation():
    # Vol 7 idx 10 (review of 23b): the citation says (204-226), the second page prints 206 on a line of its own
    assert sogs.folio("Texti greinarinnar\nSTJÓRNSÝSLA\n206\nFræðigreinar\n", 205) == 206
    assert sogs.folio("1. tbl. 7. árg.\n12\n", 205) is None                    # nothing near the cited page
