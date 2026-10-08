import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from sync_pending_hrd_cases import match_pending


def _p(id_, d, plf, dfd):
    return SimpleNamespace(id=id_, document_date=d, plaintiffs=[{"name": plf}], defendants=[{"name": dfd}])


def _case(num, issued, title):
    return {"caseNumber": num, "title": title, "appealPolicyDate": f"{issued}T00:00:00.000Z", "registrationDate": None}


WOW = "Skúla Mogensen (Reimar Pétursson lögmaður), Liv Bergþórsdóttur, Helgu Hlín Hákonardóttur og Davíð Mássyni (Hörður Felix Harðarson)"


def test_exact_party_set_beats_a_case_with_extra_parties():
    """Seven WOW air cases share the same defendants and counsel."""
    p = _p(1, date(2026, 6, 22), "Þrotabú WOW air hf. (Sveinn Andri Sveinsson lögmaður)", WOW)
    cases = [
        _case("31/2026", "2026-06-24", f"Þrotabú WOW air hf. (Sveinn Andri Sveinsson lögmaður) gegn Eurocontrol (Þórir Júlíusson lögmaður), {WOW}"),
        _case("37/2026", "2026-06-24", f"Þrotabú WOW air hf. (Sveinn Andri Sveinsson lögmaður) gegn {WOW}"),
    ]
    assert match_pending([p], cases) == {1: "37/2026"}


def test_each_case_goes_to_one_petition():
    a = _p(1, date(2026, 2, 12), "Isavia ANS ehf. (Stefán Geir Þórisson lögmaður)", "A (Daníel Isebarn Ágústsson lögmaður)")
    b = _p(2, date(2026, 2, 12), "Isavia Innanlandsflugvellir ehf. (Stefán Geir Þórisson lögmaður)", "A (Daníel Isebarn Ágústsson lögmaður)")
    cases = [
        _case("9/2026", "2026-02-12", "Isavia ANS ehf. (Stefán Geir Þórisson lögmaður) gegn A (Daníel Isebarn Ágústsson lögmaður)"),
        _case("10/2026", "2026-02-12", "Isavia Innanlandsflugvellir ehf. (Stefán Geir Þórisson lögmaður) gegn A (Daníel Isebarn Ágústsson lögmaður)"),
    ]
    assert match_pending([a, b], cases) == {1: "9/2026", 2: "10/2026"}


def test_case_issued_before_the_petition_is_not_a_match():
    p = _p(1, date(2026, 6, 22), "Rafver ehf. (Árni Ármann Árnason lögmaður)", "Heilsunni#1 ehf. (Arnar Þór Stefánsson lögmaður)")
    old = _case("1/2026", "2026-01-05", "Rafver ehf. (Árni Ármann Árnason lögmaður) gegn Heilsunni#1 ehf. (Arnar Þór Stefánsson lögmaður)")
    assert match_pending([p], [old]) == {}


def test_tie_between_identical_cases_stays_unmatched():
    p = _p(1, date(2026, 6, 1), "Acme ehf. (Anna Jónsdóttir lögmaður)", "Beta hf. (Bára Pálsdóttir lögmaður)")
    title = "Acme ehf. (Anna Jónsdóttir lögmaður) gegn Beta hf. (Bára Pálsdóttir lögmaður)"
    assert match_pending([p], [_case("5/2026", "2026-06-10", title), _case("6/2026", "2026-06-10", title)]) == {}
