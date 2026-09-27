import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("golden_audit", Path("scripts/golden_audit.py"))
m = importlib.util.module_from_spec(spec)
sys.modules["golden_audit"] = m
spec.loader.exec_module(m)


def test_shared_phrase_detected():
    question = "bótaviðmið vegna varanlegrar örorku"
    summary = "Í málinu var deilt um bætur fyrir varanlega örorku vegna vinnuslyss."
    assert m.longest_shared_run(question, summary) >= 2


def test_three_or_more_content_words_copied_is_flagged():
    question = "skaðabótaskylda ríkisins vegna úthlutunar aflaheimilda"
    summary = "Deilt var um hvort skaðabótaskylda ríkisins vegna úthlutunar aflaheimilda væri fyrir hendi."
    run = m.longest_shared_run(question, summary)
    assert run >= 3


def test_stopwords_not_counted_toward_run():
    # "vegna", "um" and "og" are stopwords; without them there is no shared
    # content-word run of 2 or more.
    question = "nálgunarbann vegna og um sambýlismann"
    summary = "Dómurinn fjallaði um og vegna nálgunarbanns gagnvart óskyldum aðila."
    assert m.longest_shared_run(question, summary) < 2


def test_empty_question_gives_zero():
    assert m.longest_shared_run("", "einhver reifun texti hér") == 0


def test_empty_summary_gives_zero():
    assert m.longest_shared_run("einhver spurning hér", "") == 0


def test_extract_summary_from_note_field():
    note = "Reifun: Þetta er reifunartextinn í heild sinni. | Lykilorð: börn, forsjá | Málategund: Áfrýjað einkamál | Ár: 2021"
    assert m.extract_summary({"note": note}) == "Þetta er reifunartextinn í heild sinni."


def test_extract_summary_prefers_summary_field():
    entry = {"summary": "beinn reifunartexti", "note": "Reifun: annað | Lykilorð: x"}
    assert m.extract_summary(entry) == "beinn reifunartexti"


def test_audit_entries_flags_offenders_and_respects_max_run(tmp_path):
    entries = [
        {"id": "g1", "question": "skaðabótaskylda ríkisins vegna úthlutunar aflaheimilda",
         "summary": "Deilt var um hvort skaðabótaskylda ríkisins vegna úthlutunar aflaheimilda væri fyrir hendi."},
        {"id": "g2", "question": "bótaviðmið vegna varanlegrar örorku",
         "summary": "Í málinu var deilt um bætur fyrir varanlega örorku vegna vinnuslyss."},
    ]
    offenders = m.audit_entries(entries, max_run=2)
    ids = {o["id"] for o in offenders}
    assert "g1" in ids
    assert "g2" not in ids


def test_cli_exits_nonzero_when_offenders_found(tmp_path):
    yaml_path = tmp_path / "cand.yaml"
    yaml_path.write_text(
        "- id: g1\n"
        "  question: skaðabótaskylda ríkisins vegna úthlutunar aflaheimilda\n"
        "  summary: Deilt var um hvort skaðabótaskylda ríkisins vegna úthlutunar aflaheimilda væri fyrir hendi.\n",
        encoding="utf-8",
    )
    code = m.main([str(yaml_path)])
    assert code == 1


def test_cli_exits_zero_when_clean(tmp_path):
    yaml_path = tmp_path / "cand.yaml"
    yaml_path.write_text(
        "- id: g1\n"
        "  question: nálgunarbann staðfest þrátt fyrir rannsókn\n"
        "  summary: Dómurinn fjallaði um heimild til að setja nálgunarbann á sakborning.\n",
        encoding="utf-8",
    )
    code = m.main([str(yaml_path)])
    assert code == 0
