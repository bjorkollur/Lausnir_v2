import datetime as dt
import hashlib
import uuid

from engine.processors.citation_build import build_rows, citation_hash
from engine.processors.citation_resolver import CitationIndex

T = uuid.UUID(int=5)
IDX = CitationIndex([(T, "Hrd.", "700/2017", dt.date(2017, 11, 8), "Dómur")])


def test_citation_hash_matches_sql_formula():
    h = citation_hash("a", None, "c")
    assert h == hashlib.sha256(("a" + chr(31) + "" + chr(31) + "c").encode("utf-8")).hexdigest()
    assert citation_hash(None, None, None) == hashlib.sha256((chr(31) + chr(31)).encode()).hexdigest()


def test_build_rows_per_layer_and_resolution():
    body = "Með dómi Hæstaréttar 8. nóvember 2017 í máli nr. 700/2017 var …"
    rows = build_rows(uuid.UUID(int=1), summary="sbr. Hrd. 700/2017.", body=body, lower="sbr. dóm Hæstaréttar í máli nr. 1/1990.",
                      doc_date=dt.date(2020, 1, 1), index=IDX)
    by_layer = {r["layer"]: r for r in rows}
    assert set(by_layer) == {"summary", "body", "lower_body"}
    assert by_layer["body"]["status"] == "resolved" and by_layer["body"]["to_doc_id"] == T
    assert by_layer["body"]["method"] == "casenum_date" and by_layer["body"]["confidence"] == 1.0
    assert by_layer["summary"]["status"] == "resolved"
    assert by_layer["lower_body"]["status"] == "pre_coverage"
    assert body[by_layer["body"]["char_start"]:by_layer["body"]["char_end"]] == "700/2017"
    assert all(set(r) >= {"from_doc_id", "layer", "char_start", "char_end", "raw_text", "target_court",
                          "target_case_number", "target_date", "target_verdict", "to_doc_id", "status",
                          "method", "confidence"} for r in rows)


def test_build_rows_empty_document():
    assert build_rows(uuid.UUID(int=1), summary=None, body=None, lower=None, doc_date=None, index=IDX) == []


# --- scripts/build_citations.py: the pure selection builder --------------------

from scripts.build_citations import selection  # noqa: E402
from engine.processors.citation_build import STALE_WHERE  # noqa: E402


def test_selection_since_is_bound_as_a_date():
    """asyncpg binds documents.document_date strictly — a str is a DataError."""
    sel = selection(force=False, source=None, doc=None, since="2020-01-01", has_col=True)
    assert sel.params["since"] == dt.date(2020, 1, 1)
    assert isinstance(sel.params["since"], dt.date) and not isinstance(sel.params["since"], str)
    sel = selection(force=False, source=None, doc=None, since=dt.date(2020, 1, 1), has_col=True)
    assert sel.params["since"] == dt.date(2020, 1, 1)


def test_selection_staleness_predicate_follows_has_col_and_force():
    stale = selection(force=False, source=None, doc=None, since=None, has_col=True)
    assert STALE_WHERE in stale.where_sql and stale.warn is False
    # Without documents.citation_hash (0004 not applied) there is no staleness to
    # read — every document counts as stale rather than the query erroring, and
    # the caller is told to warn.
    no_col = selection(force=False, source=None, doc=None, since=None, has_col=False)
    assert STALE_WHERE not in no_col.where_sql and no_col.warn is True
    forced = selection(force=True, source=None, doc=None, since=None, has_col=True)
    assert STALE_WHERE not in forced.where_sql and forced.warn is False


def test_selection_doc_wins_and_source_is_bound():
    sel = selection(force=False, source="haestirettur", doc="abc", since="2020-01-01", has_col=True)
    assert sel.where_sql == "d.id = :doc" and sel.params == {"doc": "abc"} and sel.warn is False
    sel = selection(force=True, source="felagsdomur", doc=None, since=None, has_col=True)
    assert "s.short_name = :sn" in sel.where_sql and sel.params["sn"] == "felagsdomur"
    assert "s.short_name = ANY(:sources)" in sel.where_sql
