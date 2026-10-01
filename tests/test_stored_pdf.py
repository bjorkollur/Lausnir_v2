"""find_stored_pdf: one lookup for every naming convention the importers used.

Court importers write the PDF under the document's verdict_filename, books
under their external_id (the ISBN), and theses under the thesis_stem() name
computed at import time. The API looked only under external_id, so the reader
offered no PDF for any district-court or Landsréttur document.
"""
from pathlib import Path

import pytest

import engine.config.sources as sources_config
from engine.config.sources import get_config
from engine.processors.stored_pdf import find_stored_pdf
from engine.processors.thesis_names import thesis_stem


@pytest.fixture(autouse=True)
def _tmp_raw_dir(monkeypatch, tmp_path):
    """Never touch the real Lausnir_Data raw directory."""
    monkeypatch.setattr(sources_config, "RAW_DIR", str(tmp_path / "raw"))


def _put(source: str, name: str) -> Path:
    path = get_config(source).pdf_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.5")
    return path


def test_court_pdf_is_found_by_verdict_filename():
    path = _put("heradsdomstolar", "HerdRvk_E-4047-2018_D_27-11-2020")
    found = find_stored_pdf(get_config("heradsdomstolar"),
                            verdict_filename="HerdRvk_E-4047-2018_D_27-11-2020",
                            external_id="g-9b7fbb27")
    assert found == path


def test_book_pdf_is_found_by_external_id():
    path = _put("logfraedibaekur", "9789979123456")
    found = find_stored_pdf(get_config("logfraedibaekur"),
                            verdict_filename="Bok_Kaupalog_2020", external_id="9789979123456")
    assert found == path


def test_thesis_pdf_is_found_by_its_import_time_stem():
    raw = {"author": "Diljá Mist Einarsdóttir 1987-", "degree": "Master's"}
    stem = thesis_stem("Sértækt eftirlit í barnaverndarstarfi", raw["author"], raw["degree"])
    path = _put("logfraediritgerdir", stem)
    found = find_stored_pdf(get_config("logfraediritgerdir"),
                            verdict_filename="Ritg_Sertaekt_eftirlit_14-04-2011",
                            external_id="1946/7899",
                            case_number="Sértækt eftirlit í barnaverndarstarfi", raw=raw)
    assert found == path


def test_missing_pdf_gives_none():
    assert find_stored_pdf(get_config("landsrettur"),
                           verdict_filename="Lrd_1-2020_01-01-2020", external_id="g-1") is None


def test_null_verdict_filename_falls_back_to_external_id():
    path = _put("endurupptokudomur", "g-42")
    assert find_stored_pdf(get_config("endurupptokudomur"),
                           verdict_filename=None, external_id="g-42") == path


def test_overlong_name_is_a_miss_not_an_error():
    # The filesystem rejects some long verdict_filenames outright (ENAMETOOLONG).
    assert find_stored_pdf(get_config("landsrettur"),
                           verdict_filename="x" * 400, external_id="g-1") is None
