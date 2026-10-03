"""get_document for a journal article, the journal-aware _citation and the bibliographic
projections of search / passages (no DB)."""
import uuid
from datetime import date

from engine.search.passage_search import get_passages, search_by_passages
from engine.search.queries import _citation, get_document, search_documents
from tests.test_api_citations import DOC_ROW, KeyedSession, _Result

JOURNAL_ROW = {**DOC_ROW, "source": "timarit_logfraedinga", "source_display": "Tímarit lögfræðinga", "court": "TL",
               "case_number": "Hvenær er vanhæfi smitandi?", "verdict_type": "Fræðigrein", "document_date": date(2010, 1, 1),
               "plaintiffs": [{"name": "Kjartan Bjarni Björgvinsson", "lawyer": None}], "defendants": None,
               "volume": 60, "issue": "1", "page_start": 7, "page_end": 47, "lang": "is",
               "issue_label": "Tímarit lögfræðinga, 60. árg. 1. hefti (2010)"}

JOURNAL_URLAUSN = ("Kjartan Bjarni Björgvinsson: „Hvenær er vanhæfi smitandi?“ "
                   "Tímarit lögfræðinga, 60. árg. 1. hefti (2010), bls. 7–47")


def test_citation_renders_journal_form_when_given_bibliographic_fields():
    s = _citation("timarit_logfraedinga", "TL", "Hvenær er vanhæfi smitandi?", date(2010, 1, 1), "Fræðigrein",
                  plaintiffs=JOURNAL_ROW["plaintiffs"], volume=60, issue="1", page_start=7, page_end=47)
    assert s == JOURNAL_URLAUSN
    assert _citation("landsrettur", "Lrd.", "177/2024", date(2024, 3, 6), "Dómur") == "Lrd. 177/2024 6. mars 2024 – Dómur"


async def test_get_document_exposes_journal_block():
    s = KeyedSession(doc=_Result(first=JOURNAL_ROW), links=_Result(rows=[]), out=_Result(rows=[]), in_=_Result(rows=[]), unresolved=_Result(first=0))
    doc = await get_document(s, JOURNAL_ROW["id"])
    assert doc["journal"] == {"label": "Tímarit lögfræðinga, 60. árg. 1. hefti (2010)", "year": 2010, "volume": 60,
                              "issue": "1", "page_start": 7, "page_end": 47}
    assert (doc["volume"], doc["issue"], doc["page_start"], doc["page_end"], doc["lang"]) == (60, "1", 7, 47, "is")
    assert doc["urlausn"].startswith("Kjartan Bjarni Björgvinsson: „Hvenær")
    assert doc["urlausn"] == JOURNAL_URLAUSN
    assert doc["case_number_is_title"] is True


async def test_get_document_journal_is_null_for_rulings():
    s = KeyedSession(doc=_Result(first={**DOC_ROW, "volume": None, "issue": None, "page_start": None, "page_end": None, "lang": None, "issue_label": None}),
                     links=_Result(rows=[]), out=_Result(rows=[]), in_=_Result(rows=[]), unresolved=_Result(first=0))
    doc = await get_document(s, DOC_ROW["id"])
    assert doc["journal"] is None
    assert (doc["volume"], doc["issue"], doc["page_start"], doc["page_end"], doc["lang"]) == (None,) * 5


async def test_get_document_journal_label_falls_back_to_journal_name():
    """An article without an issue row (journal_issue_id NULL) still gets a journal block with a string label."""
    row = {**JOURNAL_ROW, "issue_label": None}
    s = KeyedSession(doc=_Result(first=row), links=_Result(rows=[]), out=_Result(rows=[]), in_=_Result(rows=[]), unresolved=_Result(first=0))
    doc = await get_document(s, row["id"])
    assert doc["journal"]["label"] == "Tímarit lögfræðinga" and doc["journal"]["year"] == 2010


async def test_get_document_selects_bibliographic_columns_and_joins_the_issue():
    # KeyedSession keeps the params only; the SQL text is checked via a recording subclass.
    seen: list[str] = []

    class Rec(KeyedSession):
        async def execute(self, stmt, params=None, *a, **k):
            seen.append(" ".join(str(stmt).split()))
            return await super().execute(stmt, params, *a, **k)

    r = Rec(doc=_Result(first=JOURNAL_ROW), links=_Result(rows=[]), out=_Result(rows=[]), in_=_Result(rows=[]), unresolved=_Result(first=0))
    await get_document(r, JOURNAL_ROW["id"])
    doc_sql = next(q for q in seen if "d.raw_api_data" in q)
    for frag in ("d.volume", "d.issue", "d.page_start", "d.page_end", "d.lang", "ji.label AS issue_label",
                 "LEFT JOIN journal_issues ji ON ji.id = d.journal_issue_id"):
        assert frag in doc_sql, frag


# ── search projections ────────────────────────────────────────────────────────

class _SqlSession:
    """Answers by SQL text: count statements → scalar total, everything else → the scripted rows."""

    def __init__(self, rows):
        self.rows, self.sql = rows, []

    async def execute(self, stmt, params=None, *a, **k):
        sql = " ".join(str(stmt).split())
        self.sql.append(sql)
        if sql.startswith("SET LOCAL"):
            return _Result()
        if "count(*)" in sql and "ORDER BY" not in sql:
            return _Result(first=len(self.rows))
        return _Result(rows=self.rows)


SEARCH_ROW = {
    "id": uuid.UUID("44444444-4444-4444-4444-444444444444"), "source": "timarit_logfraedinga",
    "source_display": "Tímarit lögfræðinga", "court": "TL", "case_number": "Hvenær er vanhæfi smitandi?",
    "document_date": date(2010, 1, 1), "verdict_type": "Fræðigrein", "summary": None, "keywords": [],
    "plaintiffs": JOURNAL_ROW["plaintiffs"], "defendants": None,
    "volume": 60, "issue": "1", "page_start": 7, "page_end": 47,
    "snippet": None, "body_head": None, "cited_by_count": 0, "has_appeal_links": False,
}

PASSAGE_ROW = {**SEARCH_ROW, "best_rank": 0.5, "match_count": 1, "tier": 0, "passage_id": uuid.uuid4(), "layer": "body",
               "section_kind": "annad", "section_path": None, "para_from": None, "para_to": None,
               "page_from": 23, "page_to": 23, "ordinal": 4, "snippet": "..."}


async def test_search_documents_browse_cites_journal_articles_bibliographically():
    s = _SqlSession([SEARCH_ROW])
    res = await search_documents(s, q="")
    assert res.results[0]["urlausn"] == JOURNAL_URLAUSN
    page_sql = next(q for q in s.sql if "cited_by_count" in q)
    for frag in ("d.plaintiffs", "d.volume", "d.issue", "d.page_start", "d.page_end"):
        assert frag in page_sql, frag
    assert "journal_issues" not in page_sql, "no extra joins on the hot search path"


async def test_search_by_passages_cites_journal_articles_bibliographically():
    s = _SqlSession([PASSAGE_ROW])
    res = await search_by_passages(s, tsq_fn="to_tsquery", tsq_param="q", where=[], params={"q": "vanhæfi"},
                                   sort="relevance", page=1, page_size=20, section_kinds=None, strict_total=1)
    assert res.results[0]["urlausn"] == JOURNAL_URLAUSN
    assert res.results[0]["anchor"] == "bls. 23"
    page_sql = next(q for q in s.sql if "bp.id AS passage_id" in q)
    for frag in ("d.plaintiffs", "d.volume", "d.issue", "d.page_start", "d.page_end"):
        assert frag in page_sql, frag
    assert "journal_issues" not in page_sql


async def test_get_passages_cites_journal_articles_bibliographically():
    doc = {k: SEARCH_ROW[k] for k in ("id", "source", "court", "case_number", "document_date", "verdict_type",
                                       "plaintiffs", "volume", "issue", "page_start", "page_end")}
    s = _SqlSession([])
    answers = iter([_Result(first=doc), _Result(first=0), _Result(rows=[])])

    async def execute(stmt, params=None, *a, **k):
        s.sql.append(" ".join(str(stmt).split()))
        return next(answers)
    s.execute = execute
    res = await get_passages(s, doc["id"], from_ordinal=0, to_ordinal=0, section_kinds=None, layer=None)
    assert res["urlausn"] == JOURNAL_URLAUSN
    for frag in ("d.plaintiffs", "d.volume", "d.issue", "d.page_start", "d.page_end"):
        assert frag in s.sql[0], frag
