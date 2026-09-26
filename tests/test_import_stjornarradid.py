"""Parsing of the Blazor-rendered stjornarradid.is pages (2026 site rewrite)."""
import re

import pytest

from engine.config.sources import get_config
from scripts.import_stjornarradid import (
    _blazor_list_entry,
    _case_number_from_title,
    _gegn_block,
    _parse_blazor_detail,
    _slug,
)

# Verbatim page.inner_text("main") of
# /stakur-urskurdur/2026-08-12-Mal-nr.-51-2026-Urskurdur-12.-agust-2026
_MAIN_TEXT = """12 ágúst 2026

/

Mál nr. 51/2026 Úrskurður 12. ágúst 2026
Mál nr. 51/2026                    Eiginnafn:     Herner (kk.)

Hinn 12. ágúst 2026 kveður mannanafnanefnd upp svohljóðandi úrskurð í máli 51/2026 en erindið barst nefndinni 3. júní 2026.

Til þess að heimilt sé að samþykkja nýtt eiginnafn þurfa öll skilyrði 5. gr. laga nr. 45/1996, um mannanöfn að vera uppfyllt. Skilyrðin eru:

Eiginnafn skal geta tekið íslenska eignarfallsendingu eða hafa unnið sér hefð í íslensku máli.

Eiginnafnið Herner (kk.) tekur íslenska eignarfallsendingu, Herners, og uppfyllir að öðru leyti ákvæði 5. gr. laga um mannanöfn.

Úrskurðarorð:

Beiðni um eiginnafnið Herner (kk.) er samþykkt og skal nafnið fært á mannanafnaskrá.

Mannanafnanefnd
Til baka
Hlusta"""

_H1 = "Mál nr. 51/2026 Úrskurður 12. ágúst 2026"
_SLUG = "2026-08-12-Mal-nr.-51-2026-Urskurdur-12.-agust-2026"


def test_parse_blazor_detail_extracts_fields_and_full_ruling():
    raw = _parse_blazor_detail(
        _SLUG,
        "https://www.stjornarradid.is/x/" + _SLUG,
        _H1,
        _MAIN_TEXT,
        get_config("mannanafnanefnd"),
        description="Beiðni um eiginnafnið Herner (kk.) er samþykkt.",
    )
    assert raw["case_number_str"] == "51/2026"
    assert raw["date_str"] == "12. ágúst 2026"
    assert raw["verdict_type_str"] == "Úrskurður"
    assert raw["newsid"] == _SLUG
    # Body runs from the Eiginnafn line through the úrskurðarorð, with the
    # date lead, the h1 and the trailing committee badge / chrome stripped.
    body = raw["body_text"]
    assert body.startswith("Mál nr. 51/2026")
    assert body.endswith("fært á mannanafnaskrá.")
    assert "Úrskurðarorð:" in body
    assert "12 ágúst 2026" not in body
    assert "Til baka" not in body and "Hlusta" not in body
    assert not body.endswith("Mannanafnanefnd")


def test_case_number_not_taken_from_law_citation_in_body():
    """"laga nr. 45/1996" must never become the case number — so a page whose
    h1 lacks a case number falls back only to the body's first line."""
    main_no_header = _MAIN_TEXT.replace("Mál nr. 51/2026                    ", "")
    raw = _parse_blazor_detail(
        _SLUG, "u", "Úrskurður 12. ágúst 2026", main_no_header,
        get_config("mannanafnanefnd"),
    )
    assert raw["case_number_str"] != "45/1996"


def test_blazor_list_entry_splits_case_number_from_abstract():
    case_number, description = _blazor_list_entry(
        "12 ágúst 2026\nMannanafnanefnd\nMál nr. 51/2026 Úrskurður 12. ágúst 2026\n\n"
        "Beiðni um eiginnafnið Herner (kk.) er samþykkt."
    )
    assert case_number == "51/2026"
    assert description == "Beiðni um eiginnafnið Herner (kk.) er samþykkt."
    # Pre-2005 entries carry no case number and are skipped by the collector
    assert _blazor_list_entry("10 ágúst 2001\nMannanafnanefnd\nÚrskurðir") == (None, "")


def test_slug_external_id_cannot_collide_with_old_newsid_uuids():
    slug = _slug("/gogn/urskurdir-og-alit-/stakur-urskurdur/" + _SLUG)
    assert slug == _SLUG
    assert not re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", slug)


# ─── Adversarial committees: "<plaintiff> gegn <defendant>" party block ───────
#
# Verbatim page.inner_text("main") heads from the live site, one per detail-page
# layout the nine parse_parties="gegn" committees actually use.

_GEGN_PAGES = {
    # knhus — heading stack ("KÆRUNEFND HÚSAMÁLA" / "ÚRSKURÐUR" / date / case)
    "knhus": (
        "Mál nr. 167/2025 - Úrskurður",
        "KÆRUNEFND HÚSAMÁLA\n\nÚRSKURÐUR\n\nuppkveðinn 14. júlí 2026\n\n"
        "í máli nr. 167/2025\n\n\xa0\n\nA ehf.\n\ngegn\n\nB ehf.\n\n\xa0\n\n"
        "Kærunefndina skipa í þessu máli Auður Björg Jónsdóttir lögmaður, "
        "Þorvaldur Hauksson lögmaður og Eyþór Rafn Þórhallsson verkfræðingur.\xa0\n",
        "167/2025", ["A ehf."], ["B ehf."],
    ),
    # urvel_atv — committee signs with a prefix of its display_name, plus a
    # weekday date line that must not become a plaintiff
    "urvel_atv": (
        "Mál nr. 761/2025-Úrskurður",
        "Úrskurðarnefnd velferðarmála\n\nMál nr. 761/2025\n\n"
        "Fimmtudaginn 6. febrúar 2026\n\nA\n\ngegn\n\nVinnumálastofnun\n\n"
        "Ú R S K U R Ð U R\n\nMál þetta úrskurða Hólmfríður Birna Guðmundsdóttir "
        "lögfræðingur, Agnar Bragi Bragason lögfræðingur og Arnar Kristinsson lögfræðingur.\n",
        "761/2025", ["A"], ["Vinnumálastofnun"],
    ),
    # matsnefnd_eignarnam — case number sits in a prose lead-in, defendants run
    # over four wrapped lines, block closes on "og kveðinn upp svohljóðandi"
    "matsnefnd_eignarnam": (
        "Matsmál nr. 2/2025, úrskurður 25. júní 2026",
        "25. júní 2026 var í matsnefnd eignarnámsbóta tekið fyrir matsmálið nr. 2/2025.\n\n"
        "\xa0\n\nLandsnet hf.\n\ngegn\n\nFinnbirni Bjarnasyni, Finni Boga Hannessyni,\n\n"
        "Helgu Bjarnadóttur, Hreini Bjarnasyni, Jóhönnu\n\n"
        "Bjarnadóttur, Jóni Sigurði Bjarnasyni, Sigríði Bjarnadóttur,\n\n"
        "Svanhvíti Bjarnadóttur og Theodóri Agnari Bjarnasyni.\n\n\xa0\n\n"
        "og kveðinn upp svohljóðandi\n\n\xa0\n\nú r s k u r ð u r :\n",
        "2/2025",
        ["Landsnet hf."],
        [
            "Finnbirni Bjarnasyni, Finni Boga Hannessyni",
            "Helgu Bjarnadóttur, Hreini Bjarnasyni, Jóhönnu",
            "Bjarnadóttur, Jóni Sigurði Bjarnasyni, Sigríði Bjarnadóttur",
            "Svanhvíti Bjarnadóttur og Theodóri Agnari Bjarnasyni.",
        ],
    ),
    # kaeruna_utbod — block closes on the "Lykilorð" metadata section, and a
    # defendant line ends in the list connector "og"
    "kaeruna_utbod": (
        "Mál nr. 36/2026. Ákvörðun kærunefndar útboðsmála.",
        "Ákvörðun kærunefndar útboðsmála 14. júlí 2026\ní máli nr. 36/2026:\n"
        "Dráttarbílar Vélaleiga ehf.\ngegn\nGarðabæ og\nGröfu og grjóti – Invit ehf.\n\n"
        "Lykilorð\nSjálfkrafa stöðvun samningsgerðar aflétt.\n\nÚtdráttur\n"
        "Kærunefnd útboðsmála féllst á kröfu um að sjálfkrafa stöðvun samningsgerðar "
        "yrði aflétt, sbr. 2. mgr. 107. gr. og 1. mgr. 110. gr. laga nr. 120/2016.\n",
        "36/2026",
        ["Dráttarbílar Vélaleiga ehf."],
        ["Garðabæ", "Gröfu og grjóti – Invit ehf."],
    ),
    # afryjunarnefnd_haskoli — lead-in is a single >120-char sentence, so the
    # block has to be bounded outward from "gegn" rather than scanned from the top
    "afryjunarnefnd_haskoli": (
        "2/2024 A gegn Háskóla Íslands",
        "Ár 2025, 23. nóvember, lauk áfrýjunarnefnd í kærumálum háskólanema, þau Elvar "
        "Jónsson lögmaður og formaður nefndarinnar, Eva Halldórsdóttir lögmaður og Pétur "
        "Marteinn Urbancic Tómasson lögfræðingur málinu\xa0\n\nnr. 2/2024\n\nA\n\ngegn\n\n"
        "Háskóla Íslands\n\n\xa0\n\nmeð\xa0svohljóðandi\n\nÚ R S K U R Ð I\n\nI.\n\nMálsmeðferð\n",
        "2/2024", ["A"], ["Háskóla Íslands"],
    ),
}


@pytest.mark.parametrize("short_name", sorted(_GEGN_PAGES))
def test_gegn_parties_extracted_for_adversarial_committees(short_name):
    h1, main_text, case_number, plaintiffs, defendants = _GEGN_PAGES[short_name]
    raw = _parse_blazor_detail(
        "slug", "u", h1, f"{h1}\n{main_text}", get_config(short_name)
    )
    assert raw["case_number_str"] == case_number
    assert raw["plaintiffs_raw"] == plaintiffs
    assert raw["defendants_raw"] == defendants


def test_gegn_block_absent_leaves_parties_empty():
    """Non-adversarial pages have no "gegn" line — no parties, no crash."""
    assert _gegn_block("Mál nr. 51/2026\n\nEiginnafn: Herner (kk.)\n") == []


def test_no_parties_extracted_for_non_adversarial_committee():
    """parse_parties="none" committees must stay untouched even if a body
    happens to contain the word "gegn" on its own line."""
    raw = _parse_blazor_detail(
        "s", "u", "Mál nr. 51/2026 Úrskurður", "Mál nr. 51/2026 Úrskurður\nA\ngegn\nB\n",
        get_config("mannanafnanefnd"),
    )
    assert raw["plaintiffs_raw"] == [] and raw["defendants_raw"] == []


def test_bare_agency_case_number_recognised():
    """innanr_utl/kosninga_ursk list entries whose whole title is the case id;
    without this they were skipped as "no case number" and never deduped."""
    assert _case_number_from_title("IRR12030163") == "IRR12030163"
    assert _case_number_from_title("Úrskurður í máli nr. IRN26050091") == "IRN26050091"
    # Slash-format and law-citation behaviour must be unchanged
    assert _case_number_from_title("Mál nr. 51/2026 Úrskurður") == "51/2026"


def test_date_comes_from_the_publication_lead_not_the_first_date_in_prose():
    """The lead line above the h1 is the publication date the pre-2026 <time>
    path stored; the ruling prose often opens on an unrelated earlier date."""
    raw = _parse_blazor_detail(
        "s", "u", "Stjórnsýsluúrskurður vegna stjórnvaldssektar MVF23110238",
        "11 desember 2024\n\n/\n\n"
        "Stjórnsýsluúrskurður vegna stjórnvaldssektar MVF23110238\n"
        "Kærð er ákvörðun Ferðamálastofu frá 29. september 2021 um sekt.\n",
        get_config("ferdathjod"),
    )
    assert raw["date_str"] == "11. desember 2024"


def test_law_citation_in_opening_prose_is_not_read_as_the_case_number():
    """sveitarstj_alit álit open on "…sveitarstjórnarlaga nr. 138/2011…"; a bare
    N/YYYY there used to override the h1 and collapse every álit onto 138/2011."""
    raw = _parse_blazor_detail(
        "s", "u", "Álit innviðaráðuneytisins í máli nr. IRN25060099",
        "11 ágúst 2025\n\n/\n\nÁlit innviðaráðuneytisins í máli nr. IRN25060099\n"
        "Vísað er til erindis yðar þar sem óskað er álits á grundvelli "
        "sveitarstjórnarlaga nr. 138/2011.\n",
        get_config("sveitarstj_alit"),
    )
    assert raw["case_number_str"] == "IRN25060099"


def test_urskurdur_nr_recognised_when_maal_nr_is_absent():
    """kaeruna_utlend states its own number as "úrskurður nr. N/YYYY", not
    "mál nr.". Real incident: a page titled/slugged 888/2025 (a different,
    correctly-labeled ruling's number leaking from the site's own metadata)
    had a body opening "úrskurður nr. 825/2025" — importing the h1's 888/2025
    silently forked a duplicate onto the real 888/2025 row."""
    raw = _parse_blazor_detail(
        "s", "u", "Úrskurður nr. 888/2025",
        "24 október 2025\n\n/\n\nÚrskurður nr. 888/2025\n"
        "Hinn 24. október 2025 er kveðinn upp svohljóðandi\n\n"
        "úrskurður nr. 825/2025\n\ní stjórnsýslumáli nr. KNU24030130\n",
        get_config("kaeruna_utlend"),
    )
    assert raw["case_number_str"] == "KNU24030130"


def test_maal_nr_heading_a_few_lines_down_is_found_not_just_the_first_line():
    """Real incident: urvel's own body states "Mál nr. 126/2026" on line 2 (a
    nominative heading — "Mál nr.", not "máli nr." — which the case-number
    regex didn't recognise at all before this fix), but the first line is just
    the committee name. Both fell through to the h1/slug's "158/2025", which
    was actually a *different* ruling's number — two unrelated cases got
    silently merged onto one case_number."""
    raw = _parse_blazor_detail(
        "s", "u", "Mál nr. 158/2025 Úrskurður",
        "27 maí 2026\n\n/\n\nMál nr. 158/2025 Úrskurður\n"
        "Úrskurðarnefnd velferðarmála\n\nMál nr. 126/2026\n\n"
        "Miðvikudaginn 27. maí 2026\n\nA\n\ngegn\n\nSjúkratryggingum Íslands\n",
        get_config("urvel"),
    )
    assert raw["case_number_str"] == "126/2026"


def test_urskurdur_nr_recognised_when_the_ministry_names_itself_in_between():
    """velferdar_raduneyti opens on "Úrskurður velferðarráðuneytisins nr.
    006/2018" — the intervening ministry name defeated the "úrskurður nr."
    fallback, so four real rulings parsed with no case number at all and would
    have been re-imported as unlabeled duplicates of rows already in the DB."""
    raw = _parse_blazor_detail(
        "s", "u", "Stjórnsýslukæra vegna málsmeðferðar Embættis landlæknis",
        "19 janúar 2018\n\n/\n\nStjórnsýslukæra vegna málsmeðferðar Embættis landlæknis\n"
        "Úrskurður velferðarráðuneytisins nr. 006/2018\n\n"
        "Föstudaginn 19. janúar 2018 var í velferðarráðuneytinu kveðinn upp svohljóðandi\n\n"
        "Ú R S K U R Ð U R\n\nMeð kæru, dags. 10. apríl 2017, kærði A hrl. f.h. B\n",
        get_config("velferdar_raduneyti"),
    )
    assert raw["case_number_str"] == "006/2018"


def test_bare_mal_number_recognised_for_umhverfi_raduneyti():
    """umhverfi_raduneyti titles its own rulings "Mál 17050084 <description>" —
    an internal case id with no "nr.", no slash-year, unlike every other
    committee. Confirmed live: the site's own ministry filter returns exactly
    141 results, matching the DB's row count for this source exactly — nothing
    is missing, the historical importer just couldn't recognise this id shape
    and fell back to a law citation from the body instead."""
    raw = _parse_blazor_detail(
        "s", "u",
        "Mál 17050084 Skortur á eftirliti Umhverfisstofnunar",
        "15 ágúst 2018\n\n/\n\nMál 17050084 Skortur á eftirliti Umhverfisstofnunar\n"
        "Vísað er til erindis Landverndar, landgræðslu- og umhverfisverndarsamtaka "
        "Íslands, sem ráðuneytinu barst þann 23. maí 2017.\n",
        get_config("umhverfi_raduneyti"),
    )
    assert raw["case_number_str"] == "17050084"


def test_dot_year_case_number_recognised_for_urnefnd_raforka():
    """urnefnd_raforka switched to "í máli nr. 7. 2025" (period before the
    year, not a slash) for rulings from 2024 onward — older rulings from the
    same source still use "nr. 2/2023" and already worked. Confirmed live:
    9 of the source's 23 listing entries used this shape and were silently
    dropped by _collect_blazor_listing as "no case number in listing entry"
    before this fix, including every ruling from 2024-2026."""
    assert _case_number_from_title(
        "Úrskurður úrskurðarnefndar raforkumála í máli nr. 7. 2025"
    ) == "7/2025"
    assert _case_number_from_title(
        "Úrskurðir úrskurðarnefndar raforkumála í máli nr. 6. 2024"
    ) == "6/2024"
    assert _case_number_from_title(
        "Úrskurður úrskurðarnefndar raforkumála i máli nr. 2/2023"
    ) == "2/2023"


def test_wide_bare_mal_number_for_umhverfi_raduneyti_older_rulings():
    """umhverfi_raduneyti's oldest rulings (2000-2001) use a 9-digit
    "YYYYNNNNN" id, e.g. "Mál 199900452", and its 2004-era listing titles
    drop the "Mál" word entirely, leaving just the bare digit id as the
    whole line, e.g. "03120125". Both were previously unrecognised."""
    assert _case_number_from_title("Mál 199900452") == "199900452"
    assert _case_number_from_title("03120125") == "03120125"
    # a bare number inside ordinary prose must NOT be mistaken for a case id
    assert _case_number_from_title("Fundargerð nr. 12 frá 2019") is None


def test_titill_placeholder_recognised_for_umhverfi_raduneyti():
    """A few 2010-2011 listing titles have the unfilled template placeholder
    word "Titill" (Icelandic for "Title") where every other ruling has "Mál",
    but still carry the real case id right after it: "Titill 09120125"."""
    assert _case_number_from_title("Titill 09120125") == "09120125"
    assert _case_number_from_title("Titill 09090009") == "09090009"
