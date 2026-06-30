"""Tests for engine.processors.provision_extractor."""
import pytest
from engine.processors.provision_extractor import extract_provisions


# ── P1: mgr before gr ──────────────────────────────────────────────────────────
def test_mgr_before_gr_basic():
    result = extract_provisions("2. mgr. 12. gr. laga nr. 68/2023")
    assert {"law": "68/2023", "gr": 12, "mgr": 2} in result


def test_mgr_before_gr_no_nr_keyword():
    result = extract_provisions("1. mgr. 175. gr. laga 91/1991")
    assert {"law": "91/1991", "gr": 175, "mgr": 1} in result


# ── P2: gr only ────────────────────────────────────────────────────────────────
def test_gr_only():
    result = extract_provisions("12. gr. laga nr. 68/2023")
    assert {"law": "68/2023", "gr": 12, "mgr": None} in result


# ── P3: article suffix letter ──────────────────────────────────────────────────
def test_article_suffix():
    result = extract_provisions("218. gr. a. laga nr. 19/1940")
    assert {"law": "19/1940", "gr": 218, "mgr": None, "sfx": "a"} in result


def test_mgr_with_suffix():
    result = extract_provisions("1. mgr. 218. gr. b. laga nr. 19/1940")
    assert {"law": "19/1940", "gr": 218, "mgr": 1, "sfx": "b"} in result


# ── P4: compound law name ──────────────────────────────────────────────────────
def test_compound_law_name_hegningarlög():
    result = extract_provisions("77. gr. almennra hegningarlaga nr. 19/1940")
    assert {"law": "19/1940", "gr": 77, "mgr": None} in result


def test_compound_law_name_umferðarlög():
    result = extract_provisions("3. mgr. 71. gr. almennra hegningarlaga nr. 19/1940")
    assert {"law": "19/1940", "gr": 71, "mgr": 3} in result


def test_compound_law_name_einkamál():
    result = extract_provisions("1. mgr. 66. gr. laga um meðferð einkamála nr. 91/1991")
    assert {"law": "91/1991", "gr": 66, "mgr": 1} in result


# ── P5: multi-article chain ────────────────────────────────────────────────────
def test_multi_article_chain():
    text = "sbr. 2. mgr. 48. gr. og 1. mgr. 50. gr. umferðarlaga nr. 77/2019"
    result = extract_provisions(text)
    assert {"law": "77/2019", "gr": 48, "mgr": 2} in result
    assert {"law": "77/2019", "gr": 50, "mgr": 1} in result


# ── P6: tölulið prefix ─────────────────────────────────────────────────────────
def test_tolulid_prefix():
    result = extract_provisions("2. tölul. 1. mgr. 10. gr. laga nr. 116/2006")
    assert {"law": "116/2006", "gr": 10, "mgr": 1} in result


# ── P7: sömu laga (same law propagation) ──────────────────────────────────────
def test_somu_laga_propagation():
    text = "1. mgr. 66. gr. laga nr. 91/1991 og sbr. 3. mgr. 63. gr. sömu laga"
    result = extract_provisions(text)
    assert {"law": "91/1991", "gr": 66, "mgr": 1} in result
    assert {"law": "91/1991", "gr": 63, "mgr": 3} in result


# ── P8: reglugerð (domestic regulation) ───────────────────────────────────────
def test_reglugerð():
    result = extract_provisions("8. gr. reglugerðar nr. 698/2014")
    assert {"law": "698/2014", "gr": 8, "mgr": None} in result


# ── Edge cases ─────────────────────────────────────────────────────────────────
def test_empty_text():
    assert extract_provisions("") == []


def test_none_text():
    assert extract_provisions(None) == []


def test_no_law_number_returns_empty():
    assert extract_provisions("2. mgr. 12. gr. einhvers staðar") == []


def test_deduplication():
    text = "2. mgr. 12. gr. laga nr. 68/2023 og 2. mgr. 12. gr. laga nr. 68/2023"
    result = extract_provisions(text)
    matching = [p for p in result if p["law"] == "68/2023" and p["gr"] == 12 and p["mgr"] == 2]
    assert len(matching) == 1


def test_multiple_laws_in_sentence():
    text = "12. gr. laga nr. 68/2023 og 175. gr. laga nr. 91/1991"
    result = extract_provisions(text)
    laws = {p["law"] for p in result}
    assert "68/2023" in laws
    assert "91/1991" in laws
