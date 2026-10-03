"""Pure parts of passage_index: rows, hash, anchor."""
import hashlib
from engine.search.passage_index import (
    build_passage_rows, passage_anchor, passage_hash, PASSAGE_HASH_SQL, STALE_WHERE,
)


def test_hash_matches_manual_md5_with_unit_separator():
    s, b, l = "Reifun með ð", "Meginmál", None
    expected = hashlib.md5(f"{s}\x1f{b}\x1f".encode("utf-8")).hexdigest()
    assert passage_hash(s, b, l) == expected


def test_hash_sql_uses_unit_separator_and_alias_d():
    assert "E'\\x1f'" in PASSAGE_HASH_SQL and "d.summary" in PASSAGE_HASH_SQL
    assert STALE_WHERE.startswith("d.passage_hash IS DISTINCT FROM ")


def test_rows_three_layers_in_order_with_continuous_ordinals():
    rows = build_passage_rows("Reifun.", "## Niðurstaða\n\n1. Texti.", "## Dómsorð\n\nSýkn.")
    assert [r["layer"] for r in rows] == ["summary", "body", "lower_body"]
    assert [r["ordinal"] for r in rows] == [0, 1, 2]
    assert rows[0]["section_kind"] == "reifun" and rows[0]["section_path"] is None
    assert rows[0]["char_start"] == 0 and rows[0]["char_end"] == len("Reifun.")
    assert rows[1]["section_kind"] == "nidurstada" and rows[1]["para_from"] == 1
    assert rows[2]["section_kind"] == "domsord"


def test_rows_summary_only_document():
    rows = build_passage_rows("Bara reifun.", None, None)
    assert len(rows) == 1 and rows[0]["layer"] == "summary"


def test_rows_no_text_at_all():
    assert build_passage_rows(None, None, None) == []
    assert build_passage_rows("", "  ", None) == []


def test_rows_lagasafn_shaped_text_is_segmented_too():
    """F1: lagasafn documents are no longer excluded — their body_text has
    '## N. gr.' headings the segmenter already handles, and their summary
    (the law's name) becomes a 'reifun' passage like any other document."""
    rows = build_passage_rows(None, "## 1. gr.\n\nTexti.\n\n## 2. gr.\n\nMeira.", None)
    assert len(rows) == 2
    assert [r["section_path"] for r in rows] == ["1. gr", "2. gr"]
    assert [r["layer"] for r in rows] == ["body", "body"]


def test_long_summary_is_segmented_too():
    summary = ("Setning hér. " * 500).strip()      # ~1000 words
    rows = build_passage_rows(summary, None, None)
    assert len(rows) > 1 and all(r["layer"] == "summary" and r["word_count"] <= 400 for r in rows)


def test_anchor_labels():
    assert passage_anchor("summary", None, None, None, 0) == "Reifun"
    assert passage_anchor("body", 4, 4, "Niðurstaða", 3) == "4. mgr."
    assert passage_anchor("body", 4, 7, None, 3) == "4.–7. mgr."
    assert passage_anchor("body", None, None, "Niðurstaða", 3) == "Niðurstaða"
    assert passage_anchor("lower_body", None, None, None, 11) == "hluti 12"


# --- printed page range, "bls." anchor, language-aware lemmas (journal import) ---
from engine.search.passage_index import pages_for_span, lemmatize_rows

OFFSETS = [{"char": 0, "pdf_page": 0, "printed": 7}, {"char": 100, "pdf_page": 1, "printed": 8},
           {"char": 250, "pdf_page": 2, "printed": None}, {"char": 400, "pdf_page": 3, "printed": 10}]


def test_pages_for_span_single_and_range():
    assert pages_for_span(OFFSETS, 10, 50) == (7, 7)
    assert pages_for_span(OFFSETS, 90, 120) == (7, 8)
    assert pages_for_span(OFFSETS, 260, 300) == (None, None)   # page without a printed number
    assert pages_for_span(OFFSETS, 120, 450) == (8, 10)        # skips the unnumbered page
    assert pages_for_span(None, 0, 10) == (None, None)
    assert pages_for_span([], 0, 10) == (None, None)


def test_anchor_prefers_paragraph_then_page_then_section():
    assert passage_anchor("body", 4, 4, "Niðurstaða", 3, page_from=12, page_to=12) == "4. mgr."
    assert passage_anchor("body", None, None, "2.1 Söguleg þróun", 3, page_from=12, page_to=12) == "bls. 12"
    assert passage_anchor("body", None, None, None, 3, page_from=12, page_to=14) == "bls. 12–14"
    assert passage_anchor("body", None, None, "2.1 Söguleg þróun", 3, page_from=None, page_to=None) == "2.1 Söguleg þróun"
    assert passage_anchor("summary", None, None, None, 0, page_from=1, page_to=1) == "Reifun"


def test_rows_carry_page_range_from_offsets():
    body = ("Fyrsta síða. " * 8).strip() + "\n\n" + ("Önnur síða. " * 8).strip()
    offsets = [{"char": 0, "pdf_page": 0, "printed": 7},
               {"char": body.index("Önnur"), "pdf_page": 1, "printed": 8}]
    rows = build_passage_rows(None, body, None, page_offsets=offsets)
    assert rows[0]["page_from"] == 7 and rows[-1]["page_to"] == 8


def test_rows_without_offsets_have_null_pages():
    rows = build_passage_rows(None, "Texti hér.", None)
    assert rows[0]["page_from"] is None and rows[0]["page_to"] is None


def test_page_range_only_on_body_layer():
    offsets = [{"char": 0, "pdf_page": 0, "printed": 7}]
    rows = build_passage_rows("Reifun.", "Meginmál.", "Neðri dómur.", page_offsets=offsets)
    by_layer = {r["layer"]: r for r in rows}
    assert (by_layer["body"]["page_from"], by_layer["body"]["page_to"]) == (7, 7)
    for layer in ("summary", "lower_body"):
        assert by_layer[layer]["page_from"] is None and by_layer[layer]["page_to"] is None


def test_lemmatize_rows_skips_bin_for_english():
    rows = [{"text": "The courts decided the matter", "ordinal": 0}]
    assert lemmatize_rows(rows, lang="en")[0]["lemmas"] == "the courts decided the matter"
    is_rows = [{"text": "dómararnir ákváðu", "ordinal": 0}]
    assert lemmatize_rows(is_rows, lang="is")[0]["lemmas"] == "dómari ákveða"
