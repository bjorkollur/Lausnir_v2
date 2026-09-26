import sys
import uuid
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from link_malskotsbeidnir import (
    extract_outcome,
    extract_target,
    pick_judgment,
    pick_lrd,
)


# ── extract_target ────────────────────────────────────────────────────────────

def test_landsrettur_reference_with_verdict_word():
    body = (
        "2. Með beiðni 1. júlí 2026 leitar Livio AB leyfis Hæstaréttar, á grundvelli "
        "1. mgr. 176. gr. laga nr. 91/1991 um meðferð einkamála, til að áfrýja dómi "
        "Landsréttar 4. júní sama ár í máli nr. 455/2025: Livio AB gegn Ingunni Jónsdóttur."
    )
    assert extract_target(body) == ("lrd", "455/2025", "dómi")


def test_landsrettur_reference_definite_form_malinu():
    """Older petitions write "í málinu nr. 168/2020", not "í máli nr."."""
    body = (
        "leitar Björn Ingi Hrafnsson leyfis Hæstaréttar til að áfrýja dómi Landsréttar "
        "14. maí sama ár í málinu nr. 168/2020: Björn Ingi Hrafnsson gegn þrotabúi."
    )
    assert extract_target(body) == ("lrd", "168/2020", "dómi")


def test_kaera_urskurdar_captures_urskurdur_as_verdict_word():
    body = (
        "leitar A leyfis Hæstaréttar, á grundvelli 2. mgr. 167. gr. laga nr. 91/1991, "
        "til að kæra úrskurð Landsréttar 23. júní sama ár í máli nr. 308/2026: A gegn B."
    )
    kind, case, hint = extract_target(body)
    assert (kind, case) == ("lrd", "308/2026")
    assert hint.startswith("úrskurð")


def test_leapfrog_appeal_targets_heradsdomur_with_court_name():
    """1. mgr. 175. gr. appeals skip Landsréttur entirely — the target is the
    district court, and its case number needs the court name to be unique."""
    body = (
        "leitar íslenska ríkið leyfis Hæstaréttar, á grundvelli 1. mgr. 175. gr. laga "
        "nr. 91/1991, til að áfrýja beint til Hæstaréttar dómi Héraðsdóms Reykjavíkur "
        "14. apríl sama ár í máli nr. E-590/2025: Dista ehf. gegn ÁTVR."
    )
    assert extract_target(body) == ("herd", "E-590/2025", "Reykjavíkur")


def test_no_reference_returns_none():
    assert extract_target("# Ákvörðun Hæstaréttar\n\nEkkert málsnúmer hér.") is None
    assert extract_target(None) is None


# ── extract_outcome ───────────────────────────────────────────────────────────

def test_outcome_denied():
    assert extract_outcome("Beiðni um áfrýjunarleyfi er því hafnað.") == "hafnað"


def test_outcome_granted_variants():
    assert extract_outcome("Er beiðni um áfrýjunarleyfi því tekin til greina.") == "veitt"
    assert extract_outcome("Beiðni um áfrýjunarleyfi er því samþykkt.") == "veitt"
    assert extract_outcome("Beiðnir um áfrýjunarleyfi eru því samþykktar.") == "veitt"


def test_outcome_ignores_trailing_landsrettur_url():
    """Petitions often close with a raw landsrettur.is link long enough to push
    the ruling sentence out of a fixed-size tail window."""
    body = (
        "Umsókn leyfisbeiðanda um áfrýjunarleyfi er því tekin til greina.\n\n"
        "https://landsrettur.is/domar-og-urskurdir/domur-urskurdur/"
        "?id=aed30725-5e13-4124-a133-b04984c39266&verdictid=73228649-7696-4889-b6d6-2075191249ad"
    )
    assert extract_outcome(body) == "veitt"


def test_outcome_none_when_unstated_or_contradictory():
    assert extract_outcome("Ekkert um niðurstöðu hér.") is None
    assert extract_outcome(None) is None


# ── pick_lrd ──────────────────────────────────────────────────────────────────

def _c(vt, d):
    return (uuid.uuid4(), vt, d)


def test_pick_lrd_single_candidate_needs_no_disambiguation():
    c = _c("Dómur", date(2025, 6, 4))
    assert pick_lrd([c], None, None) == c[0]


def test_pick_lrd_verdict_word_separates_urskurdur_from_domur():
    """The same Landsréttur case number carries both an interlocutory úrskurður
    and the final dómur — the petition says which one it contests."""
    urskurdur = _c("Úrskurður", date(2025, 6, 27))
    domur = _c("Dómur", date(2025, 10, 23))
    assert pick_lrd([urskurdur, domur], "dómi", None) == domur[0]
    assert pick_lrd([urskurdur, domur], "úrskurði", None) == urskurdur[0]


def test_pick_lrd_falls_back_to_latest_decision_before_the_petition():
    early = _c("Úrskurður", date(2019, 5, 24))
    late = _c("Úrskurður", date(2019, 7, 3))
    assert pick_lrd([early, late], "úrskurði", date(2019, 8, 1)) == late[0]
    # a decision after the petition cannot be the one it contests
    assert pick_lrd([early, late], "úrskurði", date(2019, 6, 1)) == early[0]


def test_pick_lrd_rejects_a_decision_handed_down_after_the_petition():
    """A petition cannot contest a decision that did not exist yet. When the
    case number only matches a later stage of the same case (whose earlier
    decision we do not hold), leave it unlinked rather than link wrongly —
    10 such impossible links existed before this rule was added."""
    later = _c("Dómur", date(2024, 3, 19))
    assert pick_lrd([later], "dómi", date(2018, 4, 17)) is None
    # …but it is fine when a valid earlier candidate exists alongside it
    earlier = _c("Dómur", date(2018, 2, 1))
    assert pick_lrd([later, earlier], "dómi", date(2018, 4, 17)) == earlier[0]


def test_pick_lrd_keeps_undated_candidate_since_it_cannot_be_ruled_out():
    undated = _c("Dómur", None)
    assert pick_lrd([undated], "dómi", date(2020, 1, 1)) == undated[0]


def test_pick_lrd_returns_none_rather_than_guessing():
    same_day = [_c("Úrskurður", date(2026, 7, 9)), _c("Úrskurður", date(2026, 7, 9))]
    assert pick_lrd(same_day, "úrskurði", None) is None
    assert pick_lrd([], "dómi", None) is None


# ── pick_judgment ─────────────────────────────────────────────────────────────

def test_pick_judgment_takes_the_first_hrd_judgment_after_the_petition():
    """A case can return to Hæstiréttur again later on a procedural kæra; the
    judgment this leave produced is the earliest of the candidates (which the
    query has already restricted to judgments post-dating the petition)."""
    first = (uuid.uuid4(), date(2026, 3, 27))
    later = (uuid.uuid4(), date(2026, 9, 1))
    assert pick_judgment([later, first]) == first[0]


def test_pick_judgment_none_when_no_usable_candidate():
    assert pick_judgment([]) is None
    assert pick_judgment([(uuid.uuid4(), None)]) is None
