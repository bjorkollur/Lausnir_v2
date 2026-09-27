import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("golden_merge", Path("scripts/golden_merge.py"))
m = importlib.util.module_from_spec(spec)
sys.modules["golden_merge"] = m
spec.loader.exec_module(m)


def _cand(id_, question_summary="Óskyld reifun sem ekkert deilir með spurningunni um bíla.", style="medium"):
    return {
        "id": id_,
        "question": "",
        "style": style,
        "expected": [{"source": "haestirettur", "case_number": "1/2020", "document_date": "2020-01-01"}],
        "scope": ["domstolar"],
        "note": f"Reifun: {question_summary} | Lykilorð: x | Málategund: Áfrýjað einkamál | Ár: 2020",
    }


def test_validate_and_score_accepts_clean_entry():
    candidates = {"g1": _cand("g1")}
    drafted = [{"id": "g1", "question": "  nálgunarbann sambýlismaður  "}]
    accepted, dropped = m.validate_and_score(drafted, candidates)
    assert dropped == []
    assert len(accepted) == 1
    assert accepted[0]["question"] == "nálgunarbann sambýlismaður"
    assert accepted[0]["style"] == "medium"
    assert accepted[0]["expected"] == candidates["g1"]["expected"]


def test_validate_and_score_drops_empty_question():
    candidates = {"g1": _cand("g1")}
    drafted = [{"id": "g1", "question": "   "}]
    accepted, dropped = m.validate_and_score(drafted, candidates)
    assert accepted == []
    assert dropped[0]["reason"] == "empty question"


def test_validate_and_score_drops_unknown_id():
    candidates = {"g1": _cand("g1")}
    drafted = [{"id": "g999", "question": "eitthvað"}]
    accepted, dropped = m.validate_and_score(drafted, candidates)
    assert accepted == []
    assert "unknown id" in dropped[0]["reason"]


def test_validate_and_score_drops_duplicate_question_keeping_first():
    candidates = {"g1": _cand("g1"), "g2": _cand("g2")}
    drafted = [
        {"id": "g1", "question": "nálgunarbann sambýlismaður"},
        {"id": "g2", "question": "  Nálgunarbann   Sambýlismaður "},
    ]
    accepted, dropped = m.validate_and_score(drafted, candidates)
    assert len(accepted) == 1
    assert accepted[0]["id"] == "g1"
    assert dropped[0]["reason"] == "duplicate question"


def test_validate_and_score_drops_question_that_copies_summary():
    candidates = {"g1": _cand("g1", question_summary="Deilt var um hvort skaðabótaskylda ríkisins vegna úthlutunar aflaheimilda væri fyrir hendi.")}
    drafted = [{"id": "g1", "question": "skaðabótaskylda ríkisins vegna úthlutunar aflaheimilda"}]
    accepted, dropped = m.validate_and_score(drafted, candidates)
    assert accepted == []
    assert "copies summary" in dropped[0]["reason"]


def test_allocate_style_quota_matches_ratio_when_plenty_available():
    counts = {"short": 100, "medium": 100, "term": 100}
    quotas = m.allocate_style_quota(counts, 100)
    assert quotas == {"short": 40, "medium": 45, "term": 15}
    assert sum(quotas.values()) == 100


def test_allocate_style_quota_redistributes_shortfall():
    # term is scarce (only 5 available); the other styles should absorb the
    # shortfall so the total still hits target where possible.
    counts = {"short": 100, "medium": 100, "term": 5}
    quotas = m.allocate_style_quota(counts, 100)
    assert quotas["term"] == 5
    assert sum(quotas.values()) == 100


def test_allocate_style_quota_caps_at_total_available():
    counts = {"short": 2, "medium": 2, "term": 1}
    quotas = m.allocate_style_quota(counts, 100)
    assert sum(quotas.values()) == 5


def test_balance_styles_respects_quota_and_order():
    accepted = (
        [{"id": f"s{i}", "style": "short"} for i in range(5)]
        + [{"id": f"m{i}", "style": "medium"} for i in range(5)]
        + [{"id": f"t{i}", "style": "term"} for i in range(5)]
    )
    out = m.balance_styles(accepted, 10)
    styles = [e["style"] for e in out]
    assert styles.count("short") == 4
    assert styles.count("medium") == 5
    assert styles.count("term") == 1


def test_merge_tags_core_and_auto_and_drops_note():
    core = [{"id": "g001", "question": "kjarnaspurning", "expected": [{"source": "x", "case_number": "1", "document_date": "2020-01-01"}], "scope": ["domstolar"]}]
    candidates = {"g1001": _cand("g1001", style="short")}
    drafted = [{"id": "g1001", "question": "stutt spurning"}]
    merged, dropped = m.merge(core, drafted, candidates, target=10)
    assert dropped == []
    assert len(merged) == 2
    core_entry = next(e for e in merged if e["id"] == "g001")
    auto_entry = next(e for e in merged if e["id"] == "g1001")
    assert core_entry["set"] == "core"
    assert core_entry["style"] == "medium"
    assert "note" not in core_entry
    assert auto_entry["set"] == "auto"
    assert auto_entry["style"] == "short"
    assert "note" not in auto_entry


def test_cli_writes_merged_file_and_reports_drops(tmp_path):
    core_path = tmp_path / "core.yaml"
    cand_path = tmp_path / "candidates.yaml"
    slice_path = tmp_path / "slice_01.done.yaml"
    out_path = tmp_path / "out.yaml"

    import yaml
    yaml.safe_dump([{"id": "g001", "question": "kjarnaspurning",
                      "expected": [{"source": "x", "case_number": "1", "document_date": "2020-01-01"}],
                      "scope": ["domstolar"]}], core_path.open("w", encoding="utf-8"), allow_unicode=True)
    yaml.safe_dump([_cand("g1001"), _cand("g1002")], cand_path.open("w", encoding="utf-8"), allow_unicode=True)
    yaml.safe_dump([{"id": "g1001", "question": "gild spurning"}, {"id": "g1002", "question": "  "}],
                    slice_path.open("w", encoding="utf-8"), allow_unicode=True)

    code = m.main([
        "--core", str(core_path), "--slices", str(tmp_path / "slice_*.done.yaml"),
        "--candidates", str(cand_path), "--out", str(out_path), "--target", "10",
    ])
    assert code == 0
    written = yaml.safe_load(out_path.read_text(encoding="utf-8"))
    assert len(written) == 2
    ids = {e["id"] for e in written}
    assert ids == {"g001", "g1001"}
