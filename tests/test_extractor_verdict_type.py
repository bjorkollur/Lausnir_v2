"""_detect_verdict_type — the court's own wording decides, never a mention of
someone else's ruling.  Every sample here is trimmed from a real document that
the old keyword rule got wrong (see docs/wiki/09-gildrur.md)."""
import pytest

from engine.config.sources import get_config
from engine.processors.extractor import _detect_verdict_type

HAE = get_config("haestirettur")
LRD = get_config("landsrettur")
HERD = get_config("heradsdomstolar")


# ── the regression: a court discussing an úrskurður still issues a dómur ──────

def test_haestirettur_kaerumal_is_a_domur():
    # Hrd 201/2011: reifun first, then the court's own heading.  The body is
    # full of 'úrskurður héraðsdóms' — that is the ruling under appeal.
    body = (
        "Kærumál. Fjármálafyrirtæki. Slit.\n\n"
        "Kærður var úrskurður héraðsdóms, þar sem hafnað var að viðurkenna kröfu.\n\n"
        "Dómur Hæstaréttar.\n\nMál þetta dæma hæstaréttardómararnir A og B.\n\n"
        "Dómsorð:\nHinn kærði úrskurður er staðfestur.\n"
    )
    assert _detect_verdict_type(body, HAE) == "Dómur"


def test_landsrettur_domur_mentioning_another_bodys_urskurdur():
    # Lrd 197/2024 — 'ákvæði úrskurðar siðanefndar' is not this court's ruling.
    body = (
        "Dómur Landsréttar\n\nMál þetta dæma landsréttardómararnir A og B.\n\n"
        "## Dómsorð:\nÁkvæði úrskurðar siðanefndar gagnáfrýjanda eru felld úr gildi.\n"
    )
    assert _detect_verdict_type(body, LRD) == "Dómur"


def test_landsrettur_urskurdur_is_kept():
    body = (
        "Úrskurður Landsréttar\n\nLandsréttardómararnir A og B kveða upp úrskurð í máli þessu.\n\n"
        "## Úrskurðarorð:\nHinn kærði úrskurður er staðfestur.\n"
    )
    assert _detect_verdict_type(body, LRD) == "Úrskurður"


# ── composite pages: the first heading is the document's own ─────────────────

def test_first_self_heading_wins_over_an_appended_urskurdur():
    # Hrd 92/2013 publishes the dómur with the earlier EFTA-referral úrskurður
    # appended below it.  The page is the dómur.
    body = (
        "Tekjuskattur. Hjón. EFTA-dómstóllinn.\n\n"
        "# Dómur Hæstaréttar\n\nMál þetta dæma hæstaréttardómararnir A og B.\n\n"
        "Dómsorð:\nStefndi er sýkn.\n\n"
        "Úrskurður Hæstaréttar\n\nMál þetta úrskurða hæstaréttardómararnir C og D.\n\n"
        "Úrskurðarorð:\nLeitað er ráðgefandi álits EFTA-dómstólsins.\n"
    )
    assert _detect_verdict_type(body, HAE) == "Dómur"


def test_appended_lower_court_heading_is_ignored():
    # Hrd 412/2000: no heading of its own, then the héraðsdómur's úrskurður is
    # appended with its own letter-spaced operative clause.  'Dómsorð' is ours.
    body = (
        "Kærumál. Gæsluvarðhald.\n\nMál þetta dæma hæstaréttardómararnir A og B.\n\n"
        "Dómsorð:\nHinn kærði úrskurður er staðfestur.\n\n"
        "ÚRSKURÐUR\nHéraðsdóms Reykjavíkur 10. nóvember 2000.\n\n"
        "Ú r s k u r ð a r o r ð\nKærði sæti áfram gæsluvarðhaldi.\n"
    )
    assert _detect_verdict_type(body, HAE) == "Dómur"


# ── héraðsdómar: no embedded lower court, so the bare heading counts ─────────

def test_heradsdomur_bare_heading_beats_a_misnamed_operative_clause():
    # E-4202/2015 heads itself ÚRSKURÐUR but signs off under 'DÓMSORÐ'.
    body = (
        "## ÚRSKURÐUR\n\nMál þetta var höfðað með réttarstefnu og tekið til úrskurðar.\n\n"
        "Skúli Magnússon héraðsdómari kveður upp úrskurð þennan.\n\n"
        "## DÓMSORÐ\nMáli þessu er vísað frá dómi.\n"
    )
    assert _detect_verdict_type(body, HERD) == "Úrskurður"


def test_heradsdomur_closing_formula_decides():
    body = (
        "Mál þetta, sem dómtekið var 30. f.m., er höfðað 24. júní 2009.\n\n"
        "Þorgeir Ingi Njálsson dómstjóri kveður upp úrskurð þennan.\n\n"
        "Ú r s k u r ð a r o r ð :\nMáli þessu er vísað frá dómi.\n"
    )
    assert _detect_verdict_type(body, HERD) == "Úrskurður"


def test_another_bodys_ruling_does_not_decide():
    # E-395/2023: a siðanefnd issued an úrskurður; this court issued a dómur.
    body = (
        "Mál þetta, sem var dómtekið 22. janúar 2024, er höfðað með stefnu.\n\n"
        "Stefnendur telja að siðanefnd hafi kveðið upp úrskurð með refsikenndum viðurlögum.\n\n"
        "Ragnheiður Snorradóttir héraðsdómari kveður upp dóm þennan.\n\n"
        "## Dómsorð:\nStefndi er sýknaður af kröfum stefnenda.\n"
    )
    assert _detect_verdict_type(body, HERD) == "Dómur"


def test_reversed_word_order_in_the_closing_formula():
    body = (
        "Mál þetta barst dóminum 2. maí 2025.\n\n"
        "Sigurður G. Gíslason dómstjóri kveður upp þennan úrskurð.\n"
    )
    assert _detect_verdict_type(body, HERD) == "Úrskurður"


def test_submission_wording_is_the_last_resort():
    body = "Mál þetta, sem tekið var til úrskurðar 27. júní sl., barst dóminum 5. mars 2014.\n"
    assert _detect_verdict_type(body, HERD) == "Úrskurður"


# ── contradictions are reported as unknown, never guessed ────────────────────

def test_contradicting_statements_return_none():
    # E-8597/2007: 'kveður upp úrskurð þennan' under a 'DÓMSORÐ' heading, with
    # no heading of its own to break the tie.
    body = (
        "Mál þetta, sem var tekið til úrskurðar 10. mars sl., er höfðað fyrir Héraðsdómi.\n\n"
        "Sigrún Guðmundsdóttir héraðsdómari kveður upp úrskurð þennan.\n\n"
        "## DÓMSORÐ\nMálinu er vísað frá dómi án kröfu.\n"
    )
    assert _detect_verdict_type(body, HERD) is None


# ── the old keyword rule's false positives ───────────────────────────────────

def test_the_verb_urskurdar_alone_says_nothing():
    assert _detect_verdict_type("Dómurinn úrskurðar að kröfunni sé hafnað.", HAE) is None


@pytest.mark.parametrize("body", [None, "", "   "])
def test_empty_text_is_unknown(body):
    assert _detect_verdict_type(body, HAE) is None
