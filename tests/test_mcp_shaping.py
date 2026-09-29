import datetime as dt
import json
import uuid
from decimal import Decimal

from engine.mcp.shaping import cell_value, compact_passage, compact_search_result, truncate_text


def test_truncate_text_cuts_at_word_boundary():
    text, cut = truncate_text("alpha beta gamma delta", 12)
    assert text == "alpha beta" and cut is True


def test_truncate_text_no_cut_when_short():
    assert truncate_text("stutt", 100) == ("stutt", False)


def test_truncate_text_zero_means_none():
    assert truncate_text("x", 0) == (None, False)
    assert truncate_text(None, 10) == (None, False)


def test_truncate_text_without_whitespace_hard_cuts():
    text, cut = truncate_text("a" * 50, 10)
    assert text == "a" * 10 and cut


def test_cell_value_types():
    u = uuid.uuid4()
    assert cell_value(u) == (str(u), False)
    assert cell_value(dt.date(2020, 5, 5)) == ("2020-05-05", False)
    assert cell_value(dt.datetime(2020, 5, 5, 12, 0)) == ("2020-05-05T12:00:00", False)
    assert cell_value(Decimal("1.50")) == ("1.50", False)
    assert cell_value(b"\x00\x01\x02") == ("<bytes 3>", False)
    assert cell_value(memoryview(b"abcd")) == ("<bytes 4>", False)
    assert cell_value(None) == (None, False)
    assert cell_value(3) == (3, False)
    assert cell_value(True) == (True, False)


def test_cell_value_truncates_long_str_and_json():
    s, cut = cell_value("x" * 600)
    assert len(s) == 501 and s.endswith("…") and cut
    big = {"k": "y" * 600}
    v, cut = cell_value(big)
    assert isinstance(v, str) and cut and v.endswith("…")
    small = {"k": 1}
    assert cell_value(small) == (small, False)


def test_compact_search_result_shape_and_keyword_cap():
    u = uuid.uuid4(); p = uuid.uuid4()
    r = {"id": u, "urlausn": "Hrd. 1/2020", "source": "haestirettur", "source_display": "Hæstiréttur",
         "court": "Hæstiréttur", "case_number": "1/2020", "document_date": dt.date(2020, 1, 2),
         "verdict_type": "Dómur", "keywords": list("abcdefg"), "plaintiffs": [{"n": 1}], "defendants": [],
         "snippet": "<b>x</b>", "has_appeal_links": True, "passage_id": p, "anchor": "body/II/mgr. 3",
         "section_kind": "nidurstada", "layer": "body", "match_count": 2, "match_tier": 1}
    out = compact_search_result(r)
    assert set(out) == {"doc_id", "urlausn", "source", "court", "case_number", "date", "verdict_type",
                        "snippet", "passage_id", "anchor", "section_kind", "match_tier", "keywords"}
    assert out["doc_id"] == str(u) and out["passage_id"] == str(p) and out["date"] == "2020-01-02"
    assert out["keywords"] == ["a", "b", "c", "d", "e"]
    assert out["match_tier"] == 1


def test_compact_search_result_tolerates_missing_passage_fields():
    out = compact_search_result({"id": "x", "keywords": None})
    assert out["passage_id"] is None and out["anchor"] is None and out["keywords"] == []
    assert out["date"] is None and out["match_tier"] == 0


def test_compact_passage():
    p = {"id": uuid.UUID(int=1), "ordinal": 4, "layer": "body", "anchor": "a", "section_path": "II",
         "section_kind": "malsatvik", "para_from": 3, "para_to": 3, "char_start": 0, "char_end": 9,
         "word_count": 2, "text": "hello there"}
    assert compact_passage(p) == {"passage_id": str(uuid.UUID(int=1)), "ordinal": 4, "layer": "body",
                                  "section_kind": "malsatvik", "anchor": "a", "text": "hello there"}


# --- fix round 1: JSON-safety of cell_value / truncate_text whitespace ---


def test_truncate_text_cuts_at_any_whitespace_kind():
    text, cut = truncate_text("alpha\tbeta gamma", 11)
    assert text == "alpha\tbeta" and cut is True


def test_truncate_text_leading_whitespace_stripped_on_hard_cut():
    assert truncate_text(" aaaaaaaaaa", 5) == ("aaaa", True)


def test_cell_value_converts_nested_dict_leaves_before_measuring():
    u = uuid.uuid4()
    d = {"id": u, "when": dt.date(2020, 1, 1), "n": Decimal("2.5"), "b": b"\x00"}
    v, cut = cell_value(d)
    assert cut is False
    assert v == {"id": str(u), "when": "2020-01-01", "n": "2.5", "b": "<bytes 1>"}
    json.dumps(v)  # must not raise


def test_cell_value_converts_list_of_uuids():
    us = [uuid.uuid4(), uuid.uuid4()]
    v, cut = cell_value(us)
    assert cut is False
    assert v == [str(x) for x in us]
    json.dumps(v)


def test_cell_value_converts_nested_dict_in_list_in_dict():
    inner = uuid.uuid4()
    nested = {"outer": [{"inner": inner}]}
    v, cut = cell_value(nested)
    assert cut is False
    assert v == {"outer": [{"inner": str(inner)}]}
    json.dumps(v)


def test_cell_value_set_becomes_sorted_list():
    assert cell_value({1, 2}) == ([1, 2], False)


def test_cell_value_unknown_object_becomes_string():
    v, cut = cell_value(object())
    assert isinstance(v, str) and cut is False


def test_cell_value_nan_and_inf_become_none():
    assert cell_value(float("nan")) == (None, False)
    assert cell_value(float("inf")) == (None, False)
    assert cell_value(float("-inf")) == (None, False)


def test_compact_search_result_output_is_json_serialisable_with_odd_keyword_types():
    r = {"id": uuid.uuid4(), "keywords": [uuid.uuid4(), "ok"]}
    out = compact_search_result(r)
    json.dumps(out)
    assert all(isinstance(k, str) for k in out["keywords"])


def test_compact_passage_output_is_json_serialisable():
    p = {"id": uuid.uuid4(), "ordinal": 1, "layer": "body", "section_kind": "x",
         "anchor": None, "text": "t"}
    out = compact_passage(p)
    json.dumps(out)
