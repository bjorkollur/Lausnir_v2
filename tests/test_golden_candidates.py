import importlib.util
import sys
from datetime import date
from pathlib import Path

spec = importlib.util.spec_from_file_location("golden_candidates", Path("scripts/golden_candidates.py"))
m = importlib.util.module_from_spec(spec)
sys.modules["golden_candidates"] = m
spec.loader.exec_module(m)


def _row(i, source="haestirettur", year=2020, keyword="börn", case_type="Áfrýjað einkamál"):
    return {
        "id": i, "short_name": source, "case_number": f"{i}/{year}",
        "document_date": date(year, 1, 1), "summary": "reifun",
        "keywords": [keyword], "case_type": case_type,
    }


def test_select_diverse_respects_keyword_cap_of_two():
    rows = [_row(i, keyword="börn") for i in range(10)]
    selected = m.select_diverse(rows, n=100, year_cap=100)
    assert len(selected) == 2


def test_select_diverse_respects_year_cap():
    rows = [_row(i, keyword=f"kw{i}") for i in range(10)]
    selected = m.select_diverse(rows, n=100, year_cap=3)
    assert len(selected) == 3


def test_select_diverse_year_cap_default_is_thirty():
    assert m.SOURCE_YEAR_CAP_DEFAULT == 30


def test_select_diverse_is_reproducible_with_fixed_seed():
    rows = [_row(i, keyword=f"kw{i % 7}") for i in range(30)]
    a = m.select_diverse(rows, n=10, year_cap=30)
    b = m.select_diverse(rows, n=10, year_cap=30)
    assert [r["id"] for r in a] == [r["id"] for r in b]


def test_diversity_table_reports_buckets_at_the_year_cap():
    selected = [_row(i, source="landsrettur", year=2021, keyword=f"kw{i}") for i in range(3)]
    entries = [{"style": "medium"}] * 3
    table = m.diversity_table(selected, selected, entries, year_cap=3)
    assert "('landsrettur', 2021)" in table
    assert "buckets at the cap (1)" in table


def test_diversity_table_no_buckets_at_cap_when_under():
    selected = [_row(i, source="landsrettur", year=2021, keyword=f"kw{i}") for i in range(2)]
    entries = [{"style": "medium"}] * 2
    table = m.diversity_table(selected, selected, entries, year_cap=30)
    assert "buckets at the cap (0)" in table
