"""island.is reuses one id for every ruling in a Landsréttur case; the importer must
keep the earlier ruling instead of overwriting it (see is_new_ruling)."""
import importlib.util
from datetime import date
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "import_landsrettur", Path(__file__).parent.parent / "scripts" / "import_landsrettur.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)
is_new_ruling = mod.is_new_ruling


def test_later_ruling_in_same_case_is_new():
    assert is_new_ruling(date(2025, 10, 9), "aaa", date(2026, 4, 24), "bbb")


def test_same_date_is_an_update_not_a_new_ruling():
    assert not is_new_ruling(date(2026, 4, 24), "aaa", date(2026, 4, 24), "bbb")


def test_corrected_date_on_the_same_pdf_is_not_new():
    assert not is_new_ruling(date(2022, 1, 12), "aaa", date(2022, 1, 14), "aaa")


def test_unknown_hash_is_treated_as_update():
    assert not is_new_ruling(date(2025, 10, 9), None, date(2026, 4, 24), "bbb")
    assert not is_new_ruling(date(2025, 10, 9), "aaa", date(2026, 4, 24), None)


def test_missing_date_is_not_new():
    assert not is_new_ruling(None, "aaa", date(2026, 4, 24), "bbb")
