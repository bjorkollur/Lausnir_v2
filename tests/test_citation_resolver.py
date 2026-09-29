import datetime as dt
import uuid

from engine.processors.citation_resolver import CitationIndex, INDEX_SQL, COURT_SOURCES, resolve
from engine.processors.citations import RawCitation

U = [uuid.UUID(int=i) for i in range(1, 12)]
ROWS = [
    (U[1], "Hrd.", "055/2001", dt.date(2001, 5, 3), "Dómur"),
    (U[2], "Lrd.", "93/2018", dt.date(2018, 6, 1), "Úrskurður"),
    (U[3], "Lrd.", "93/2018", dt.date(2018, 11, 9), "Dómur"),
    (U[4], "Hérd. Rvk.", "S-953/2008", dt.date(2008, 9, 1), "Dómur"),
    (U[5], "Hérd. Reykn.", "S-953/2008", dt.date(2008, 10, 1), "Dómur"),
    (U[6], "Hrd.", "7/2022", dt.date(2022, 3, 1), "Dómur"),
    (U[7], "Hrd.", "7/2022", dt.date(2022, 9, 1), "Úrskurður"),
    (U[8], "Hrd. málsk.", "2023-65", dt.date(2023, 8, 1), "Ákvörðun"),
    (U[9], "Féld.", "9/1999", dt.date(1999, 2, 2), "Dómur"),
    (U[10], "Hrd.", "700/2017", dt.date(2017, 11, 8), "Dómur"),
]
IDX = CitationIndex(ROWS)
CITING = uuid.UUID(int=99)
D = dt.date(2023, 1, 1)


def rc(court, num, date=None, verdict=None, form="prose"):
    return RawCitation(0, 1, "x", court, num, date, verdict, form)


def test_norm_applied_to_index_and_query():
    assert [c.id for c in IDX.candidates("Hrd.", "55/2001")] == [U[1]]
    assert [c.id for c in IDX.candidates("Hrd.", "055/2001")] == [U[1]]


def test_resolve_leading_zero():
    r = resolve(rc("Hrd.", "55/2001"), index=IDX, from_doc_id=CITING, from_date=D)
    assert (r.status, r.to_doc_id, r.method, r.confidence) == ("resolved", U[1], "casenum_unique", 0.8)


def test_exact_court_match_hrd_does_not_match_malsk():
    assert IDX.candidates("Hrd.", "2023-65") == []
    assert [c.id for c in IDX.candidates("Hrd. málsk.", "2023-65")] == [U[8]]


def test_bare_herd_matches_all_districts_and_is_ambiguous():
    r = resolve(rc("Hérd.", "S-953/2008"), index=IDX, from_doc_id=CITING, from_date=D)
    assert r.status == "ambiguous" and r.to_doc_id is None
    r2 = resolve(rc("Hérd. Rvk.", "S-953/2008"), index=IDX, from_doc_id=CITING, from_date=D)
    assert (r2.status, r2.to_doc_id) == ("resolved", U[4])


def test_reused_lrd_number_resolved_by_verdict_then_by_date():
    r = resolve(rc("Lrd.", "93/2018", verdict="Dómur"), index=IDX, from_doc_id=CITING, from_date=D)
    assert (r.to_doc_id, r.method, r.confidence) == (U[3], "casenum_verdict", 0.9)
    r = resolve(rc("Lrd.", "93/2018", date=dt.date(2018, 6, 1)), index=IDX, from_doc_id=CITING, from_date=D)
    assert (r.to_doc_id, r.method, r.confidence) == (U[2], "casenum_date", 1.0)
    r = resolve(rc("Lrd.", "93/2018"), index=IDX, from_doc_id=CITING, from_date=D)
    assert r.status == "ambiguous"


def test_date_that_empties_set_is_unresolved_not_fallback():
    r = resolve(rc("Lrd.", "93/2018", date=dt.date(2018, 1, 1), verdict="Dómur"), index=IDX, from_doc_id=CITING, from_date=D)
    assert r.status == "unresolved" and r.to_doc_id is None


def test_later_documents_are_excluded():
    r = resolve(rc("Hrd.", "7/2022"), index=IDX, from_doc_id=CITING, from_date=dt.date(2022, 6, 1))
    assert (r.status, r.to_doc_id) == ("resolved", U[6])     # U[7] is after the citing date
    r2 = resolve(rc("Hrd.", "7/2022"), index=IDX, from_doc_id=CITING, from_date=dt.date(2022, 1, 1))
    assert r2.status == "unresolved"
    r3 = resolve(rc("Hrd.", "7/2022"), index=IDX, from_doc_id=CITING, from_date=None)
    assert r3.status == "ambiguous"                              # no date to exclude with


def test_self_and_pre_coverage():
    r = resolve(rc("Hrd.", "700/2017"), index=IDX, from_doc_id=U[10], from_date=dt.date(2017, 11, 8))
    assert r.status == "self" and r.to_doc_id is None
    r = resolve(rc("Hrd.", "190/1996"), index=IDX, from_doc_id=CITING, from_date=D)
    assert r.status == "pre_coverage"
    r = resolve(rc("Hrd.", None, form="reporter"), index=IDX, from_doc_id=CITING, from_date=D)
    assert r.status == "pre_coverage"
    r = resolve(rc("Hrd.", "5/2005"), index=IDX, from_doc_id=CITING, from_date=D)
    assert r.status == "unresolved"
    r = resolve(rc("Lrd.", "1/1996"), index=IDX, from_doc_id=CITING, from_date=D)
    assert r.status == "unresolved"                              # pre-1999 rule is Hrd.-only


# --- additional tests -------------------------------------------------------

def test_candidates_with_none_case_number_returns_empty():
    assert IDX.candidates("Hrd.", None) == []


def test_rows_with_null_court_are_skipped():
    rows = ROWS + [(uuid.UUID(int=42), None, "1/2020", dt.date(2020, 1, 1), "Dómur")]
    idx = CitationIndex(rows)
    # A None court must not crash indexing and must not be reachable via candidates().
    assert idx.candidates(None, "1/2020") == []
    assert idx.candidates("Hrd.", "1/2020") == []


def test_load_sql_uses_any_sources_param():
    captured = {}

    class FakeResult:
        def all(self):
            return []

    class FakeConn:
        async def execute(self, stmt, params):
            captured["sql"] = str(stmt)
            captured["params"] = params
            return FakeResult()

    import asyncio

    idx = asyncio.run(CitationIndex.load(FakeConn()))
    assert isinstance(idx, CitationIndex)
    assert "ANY(:sources)" in captured["sql"]
    assert captured["params"] == {"sources": list(COURT_SOURCES)}


# --- Félagsdómur bare-number fallback ---------------------------------------
# Félagsdómur stores pre-2010 cases bare ('13/2001') and 2010-onward prefixed
# ('F-9/2019'), but its judgments cite both eras bare ('í máli nr. 5/2012').

FELD_F = uuid.UUID(int=20)
FELD_ROWS = ROWS + [
    (FELD_F, "Féld.", "F-5/2012", dt.date(2012, 6, 1), "Dómur"),
    (uuid.UUID(int=21), "Hrd.", "F-5/2012", dt.date(2012, 6, 1), "Dómur"),
]
FELD_IDX = CitationIndex(FELD_ROWS)


def test_bare_feld_number_finds_the_f_prefixed_case():
    assert [c.id for c in FELD_IDX.candidates("Féld.", "5/2012")] == [FELD_F]
    r = resolve(rc("Féld.", "5/2012"), index=FELD_IDX, from_doc_id=CITING, from_date=D)
    assert (r.status, r.to_doc_id, r.method, r.confidence) == ("resolved", FELD_F, "casenum_unique", 0.8)


def test_f_prefixed_citation_still_matches_directly():
    assert [c.id for c in FELD_IDX.candidates("Féld.", "F-5/2012")] == [FELD_F]


def test_both_forms_stored_is_ambiguous_until_date_or_verdict_narrows():
    rows = FELD_ROWS + [(uuid.UUID(int=22), "Féld.", "5/2012", dt.date(2012, 9, 1), "Úrskurður")]
    idx = CitationIndex(rows)
    assert {c.id for c in idx.candidates("Féld.", "5/2012")} == {FELD_F, uuid.UUID(int=22)}
    assert resolve(rc("Féld.", "5/2012"), index=idx, from_doc_id=CITING, from_date=D).status == "ambiguous"
    by_date = resolve(rc("Féld.", "5/2012", date=dt.date(2012, 6, 1)), index=idx,
                      from_doc_id=CITING, from_date=D)
    assert (by_date.status, by_date.to_doc_id, by_date.method) == ("resolved", FELD_F, "casenum_date")
    by_verdict = resolve(rc("Féld.", "5/2012", verdict="Úrskurður"), index=idx,
                         from_doc_id=CITING, from_date=D)
    assert (by_verdict.status, by_verdict.to_doc_id, by_verdict.method) == (
        "resolved", uuid.UUID(int=22), "casenum_verdict")


def test_the_fallback_is_feld_only_and_does_not_widen_other_courts():
    # 'Hrd. F-5/2012' exists in the index, but a bare Hrd. number must not reach it.
    assert FELD_IDX.candidates("Hrd.", "5/2012") == []
    assert resolve(rc("Hrd.", "5/2012"), index=FELD_IDX, from_doc_id=CITING,
                   from_date=D).status == "unresolved"


def test_the_fallback_only_applies_to_bare_numbers():
    # An already-prefixed number must not gain a second 'F-'.
    assert FELD_IDX.candidates("Féld.", "E-5/2012") == []
    assert [c.id for c in FELD_IDX.candidates("Féld.", "9/1999")] == [U[9]]
