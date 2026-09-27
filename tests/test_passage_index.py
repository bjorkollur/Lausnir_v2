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


def test_rows_lagasafn_is_skipped():
    assert build_passage_rows("Lög um x", "1. gr. Texti", None, is_lagasafn=True) == []


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
