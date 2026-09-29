"""_infer_hrd_lrd_case_type — the fallback for verdicts island.is classifies in
no category at all.

It runs only after the site has had its say (backfill_case_type.py
--missing-only), never at import time — see test_import_upsert_parity.py.

The rule has to be per court because the two courts do not classify the same
way: island.is files a kærumál brought by a lögreglustjóri as *einkamál* under
Hæstiréttur but as *sakamál* under Landsréttur.  Widening the prosecutor test
for both would lift Landsréttur from 70,0% to 99,4% and drop Hæstiréttur from
96,9% to 87,7%.
"""
from __future__ import annotations

import pytest

from engine.processors.extractor import _infer_hrd_lrd_case_type as infer


def _p(*names: str) -> list[dict]:
    return [{"name": n, "lawyer": None} for n in names]


# ── the route comes from the keywords ────────────────────────────────────────

@pytest.mark.parametrize("short_name", ["haestirettur", "landsrettur"])
def test_kaerumal_keyword_means_kaert(short_name: str):
    assert infer(["Kærumál", "Gæsluvarðhald"], _p("A"), short_name) == "Kært einkamál"


@pytest.mark.parametrize("short_name", ["haestirettur", "landsrettur"])
def test_no_kaerumal_keyword_means_afryjad(short_name: str):
    assert infer(["Börn", "Forsjársvipting"], _p("A"), short_name) == "Áfrýjað einkamál"


@pytest.mark.parametrize("short_name", ["haestirettur", "landsrettur"])
def test_missing_keywords_fall_back_to_afryjad(short_name: str):
    assert infer([], _p("A"), short_name) == "Áfrýjað einkamál"
    assert infer(None, None, short_name) == "Áfrýjað einkamál"


# ── the kind is court-specific ───────────────────────────────────────────────

@pytest.mark.parametrize("short_name", ["haestirettur", "landsrettur"])
def test_akaeruvaldid_is_a_sakamal_at_both_courts(short_name: str):
    plaintiffs = _p("Ákæruvaldið (Arnfríður Gígja Arngrímsdóttir settur saksóknari)")
    assert infer(["Kærumál"], plaintiffs, short_name) == "Kært sakamál"


def test_heradssaksoknari_is_a_sakamal_at_both_courts():
    """37 Hæstaréttarmál and 110 Landsréttarmál, none of them einkamál."""
    plaintiffs = _p("Héraðssaksóknari (Anna Barbara Andradóttir aðstoðarsaksóknari)")
    for short_name in ("haestirettur", "landsrettur"):
        assert infer(["Kærumál"], plaintiffs, short_name) == "Kært sakamál", short_name


def test_landsrettur_counts_every_prosecuting_authority():
    """Under Landsréttur each of these is sakamál in the site's own data:
    Lögreglustjórinn 1.671/3, Ríkissaksóknari 50/0, Skattrannsóknarstjóri 4/0."""
    for name in (
        "Lögreglustjórinn á Suðurnesjum (Daníel Reynisson aðstoðarsaksóknari)",
        "Lögreglustjórinn á höfuðborgarsvæðinu (Haukur Gunnarsson aðstoðarsaksóknari)",
        "Ríkissaksóknari (Helgi Magnús Gunnarsson vararíkissaksóknari)",
        "Skattrannsóknarstjóri (Bryndís Kristjánsdóttir skattrannsóknarstjóri)",
    ):
        assert infer(["Kærumál"], _p(name), "landsrettur") == "Kært sakamál", name


def test_haestirettur_keeps_the_narrow_test():
    """island.is files these as 'Kært einkamál' under Hæstiréttur.

    Hrd. 442/2013 (Lögreglustjórinn á höfuðborgarsvæðinu), 316/2010 (Sérstakur
    saksóknari) and 25/2000 (Ríkislögreglustjóri) are all einkamál there.
    Counting them as sakamál costs 1.508 documents.
    """
    for name in (
        "Lögreglustjórinn á höfuðborgarsvæðinu (Karl Ingi Vilbergsson saksóknari)",
        "Sérstakur saksóknari (Björn Þorvaldsson saksóknari)",
        "Ríkislögreglustjóri (Jón H. Snorrason saksóknari)",
    ):
        assert infer(["Kærumál"], _p(name), "haestirettur") == "Kært einkamál", name


# ── the three real gaps this fallback exists for ─────────────────────────────

def test_the_three_landsrettur_gaps_of_2026_09_29():
    assert infer(
        ["Kærumál", "Nauðungarvistun"],
        _p("A (Inga Lillý Brynjólfsdóttir lögmaður)"),
        "landsrettur",
    ) == "Kært einkamál"
    assert infer(
        ["2. mgr. 95. gr. laga nr. 88/2008", "Gæsluvarðhald", "Kærumál"],
        _p("Ákæruvaldið (Arnfríður Gígja Arngrímsdóttir settur saksóknari)"),
        "landsrettur",
    ) == "Kært sakamál"
    assert infer(
        ["Börn", "Forsjársvipting"],
        _p("Hannes Ólafur Hulduson (Oddgeir Einarsson lögmaður)"),
        "landsrettur",
    ) == "Áfrýjað einkamál"


# ── where the source itself is of two minds ──────────────────────────────────

def test_haestirettur_police_kaerumal_follow_the_source_majority():
    """'Lögreglustjórinn á …' is split 321 sakamál / 983 einkamál under
    Hæstiréttur in island.is's own data — gæsluvarðhald and other police
    kærumál are filed as einkamál there far more often than not.  The rule
    follows the majority, which is the whole of the remaining 2,8% error
    (337 documents, 'Kært sakamál' read as 'Kært einkamál').  Reproducing the
    source's taxonomy matters more here than tidying it: the column must stay
    consistent with the 12.208 values the site already gave us.
    """
    plaintiffs = _p("Lögreglustjórinn á höfuðborgarsvæðinu (Karl Ingi Vilbergsson saksóknari)")
    assert infer(["Kærumál", "Gæsluvarðhald"], plaintiffs, "haestirettur") == "Kært einkamál"
    assert infer(["Kærumál", "Gæsluvarðhald"], plaintiffs, "landsrettur") == "Kært sakamál"
