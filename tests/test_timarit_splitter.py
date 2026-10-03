import fitz
import pytest

from engine.timarit.splitter import cut_pages, page_count, sha256_bytes


def _pdf(n):
    d = fitz.open()
    for i in range(n):
        d.new_page().insert_text((72, 72), f"Síða {i}")
    return d.tobytes()


def test_cut_keeps_only_the_range_and_its_text():
    out = cut_pages(_pdf(6), 2, 4)
    assert page_count(out) == 3
    d = fitz.open(stream=out, filetype="pdf")
    assert "Síða 2" in d[0].get_text() and "Síða 4" in d[2].get_text()


def test_cut_clips_last_to_document_end():
    assert page_count(cut_pages(_pdf(6), 4, 99)) == 2


def test_cut_rejects_bad_ranges():
    with pytest.raises(ValueError):
        cut_pages(_pdf(3), 2, 1)
    with pytest.raises(ValueError):
        cut_pages(_pdf(3), 3, 5)


def test_sha256_is_stable():
    b = _pdf(1)
    assert sha256_bytes(b) == sha256_bytes(b) and len(sha256_bytes(b)) == 64


def test_cut_rejects_negative_first_and_accepts_single_page():
    with pytest.raises(ValueError):
        cut_pages(_pdf(3), -1, 1)
    assert page_count(cut_pages(_pdf(3), 1, 1)) == 1
