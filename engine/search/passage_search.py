"""Keyword/proximity search over the passages table (spec 2026-09-27).

Finds matching passages, groups them per document, ranks documents by their
best passage, and builds the snippet from that passage alone. Document-level
filters (scope, dates, provision, keyword) are passed in as SQL fragments over
alias ``d`` — the same fragments search_documents builds for the legacy path.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from engine.processors.sections import SECTION_KINDS
from engine.search.passage_index import passage_anchor

__all__ = ["SECTION_KINDS", "PASSAGE_CANDIDATE_DOCS", "validate_section_kinds",
           "build_hits_sql", "order_sql", "search_by_passages"]

_HEADLINE_OPTS = ("'StartSel=<mark>,StopSel=</mark>,MaxFragments=1,MaxWords=35,"
                  "MinWords=12,ShortWord=2'")

# Stage 1 caps candidate documents by document-level rank (ts_rank on d.fts_is)
# before Stage 2 aggregates passages, so the passage GROUP BY never runs over more
# than this many documents. Exact for queries matching <= PASSAGE_CANDIDATE_DOCS
# documents (the overwhelming majority); for very common terms that match more,
# only pagination beyond the cap is affected — see search_by_passages.
PASSAGE_CANDIDATE_DOCS = 2000


def validate_section_kinds(kinds: list[str] | None) -> list[str] | None:
    from engine.search.queries import SearchError  # local import: queries imports this module
    if not kinds:
        return None
    bad = [k for k in kinds if k not in SECTION_KINDS]
    if bad:
        raise SearchError(f"Unknown section_kind(s): {bad}; allowed: {sorted(SECTION_KINDS)}")
    return list(dict.fromkeys(kinds))


def build_hits_sql(*, tsq: str, or_tsq: str | None, doc_where: list[str], section_filter: bool) -> str:
    """Two- or three-stage CTE list (no leading ``WITH`` — callers prepend it).

    Stage 1 (``cand``) ranks documents by ``ts_rank(d.fts_is, tsq)`` (the doc-level
    AND match) and caps the candidate set at :cand_limit (PASSAGE_CANDIDATE_DOCS) so
    later stages never aggregate passages for more documents than that.

    Stage 2 (``hits_and``) finds, within those candidates, passages where all query
    terms co-occur in the *same* passage — the precise, well-ranked match.

    When ``or_tsq`` is given (keyword mode only; ``None`` for proximity), Stage 2b
    (``hits_or``) is a recall fallback: passages matching *any* query term, for
    candidate documents that had no co-located match in ``hits_and`` — this is what
    lets a document whose two query terms live in different passages still surface,
    since the doc-level AND match in ``cand`` already proved both terms are present
    somewhere in the document. ``hits`` unions the two (or is just ``hits_and`` when
    there's no fallback), tagging provenance via ``colocated`` so relevance ordering
    and snippet rendering can prefer the precise match.
    """
    doc_where_sql = "".join(f" AND {frag}" for frag in doc_where)
    and_rank = f"ts_rank(p.fts_is, {tsq})"
    and_where = [f"p.fts_is @@ {tsq}"]
    if section_filter:
        and_where.append("p.section_kind = ANY(:section_kinds)")

    ctes = f"""
        cand AS (
            SELECT d.id, ts_rank(d.fts_is, {tsq}) AS doc_rank
            FROM documents d
            WHERE d.fts_is @@ {tsq}{doc_where_sql}
            ORDER BY doc_rank DESC, d.id
            LIMIT :cand_limit
        ),
        hits_and AS (
            SELECT p.document_id,
                   max({and_rank}) AS best_rank,
                   count(*) AS match_count,
                   (array_agg(p.id ORDER BY {and_rank} DESC, p.ordinal))[1] AS best_passage_id
            FROM passages p
            JOIN cand c ON c.id = p.document_id
            WHERE {" AND ".join(and_where)}
            GROUP BY p.document_id
        )"""

    if or_tsq is not None:
        or_rank = f"ts_rank(p.fts_is, {or_tsq})"
        or_where = [
            f"p.fts_is @@ {or_tsq}",
            "NOT EXISTS (SELECT 1 FROM hits_and ha WHERE ha.document_id = p.document_id)",
        ]
        if section_filter:
            or_where.append("p.section_kind = ANY(:section_kinds)")
        ctes += f""",
        hits_or AS (
            SELECT p.document_id,
                   max({or_rank}) AS best_rank,
                   count(*) AS match_count,
                   (array_agg(p.id ORDER BY {or_rank} DESC, p.ordinal))[1] AS best_passage_id
            FROM passages p
            JOIN cand c ON c.id = p.document_id
            WHERE {" AND ".join(or_where)}
            GROUP BY p.document_id
        ),
        hits AS (
            SELECT ha.*, TRUE AS colocated FROM hits_and ha
            UNION ALL
            SELECT ho.*, FALSE AS colocated FROM hits_or ho
        )"""
    else:
        ctes += """,
        hits AS (
            SELECT ha.*, TRUE AS colocated FROM hits_and ha
        )"""

    return ctes


def order_sql(sort: str) -> str:
    if sort == "oldest":
        return "d.document_date ASC NULLS LAST, h.best_rank DESC, d.id"
    if sort == "newest":
        return "d.document_date DESC NULLS LAST, h.best_rank DESC, d.id"
    return "h.colocated DESC, h.best_rank DESC, h.match_count DESC, d.document_date DESC NULLS LAST, d.id"


async def search_by_passages(
    session: AsyncSession, *, tsq_fn: str, tsq_param: str, where: list[str], params: dict[str, Any],
    sort: str, page: int, page_size: int, section_kinds: list[str] | None,
    or_tsq_param: str | None = None,
):
    from engine.search.queries import SearchResults, _citation  # local import (cycle)

    tsq = f"{tsq_fn}('simple', :{tsq_param})"
    # OR-fallback recall stage, keyword mode only (proximity passes or_tsq_param=None).
    or_tsq = f"to_tsquery('simple', :{or_tsq_param})" if or_tsq_param else None
    ctes = build_hits_sql(tsq=tsq, or_tsq=or_tsq, doc_where=where, section_filter=bool(section_kinds))
    p = dict(params)
    p["cand_limit"] = PASSAGE_CANDIDATE_DOCS
    if section_kinds:
        p["section_kinds"] = section_kinds

    if section_kinds:
        # Bounded by the candidate cap (exact only up to PASSAGE_CANDIDATE_DOCS
        # matching documents) — a section filter can only be evaluated per-passage.
        total = (await session.execute(text(f"WITH {ctes} SELECT count(*) FROM hits"), p)).scalar() or 0
    else:
        # Exact document total straight off the GIN index — no rank, no cap.
        doc_where_sql = "".join(f" AND {frag}" for frag in where)
        total = (await session.execute(
            text(f"SELECT count(*) FROM documents d WHERE d.fts_is @@ {tsq}{doc_where_sql}"), p
        )).scalar() or 0
    if total == 0:
        return SearchResults(total=0, page=page, page_size=page_size, results=[])

    # A page beyond PASSAGE_CANDIDATE_DOCS documents simply comes back empty here
    # (the 'hits' CTE never contains more than the capped candidate set), while
    # 'total' above still reports the true (or cap-bounded, see above) count.
    # 'colocated' rows (exact same-passage match) highlight against tsq; OR-fallback
    # rows highlight against or_tsq (the only lexemes actually present in that
    # passage) — but 'colocated' itself is internal and never exposed in results.
    snippet_query = f"CASE WHEN h.colocated THEN {tsq} ELSE {or_tsq} END" if or_tsq is not None else tsq
    rows = (await session.execute(text(f"""
        WITH {ctes}
        SELECT d.id, s.short_name AS source, s.display_name AS source_display,
               d.court, d.case_number, d.document_date, d.verdict_type,
               d.summary, d.keywords, d.plaintiffs, d.defendants,
               h.best_rank, h.match_count,
               bp.id AS passage_id, bp.layer, bp.section_kind, bp.section_path,
               bp.para_from, bp.para_to, bp.ordinal,
               ts_headline('simple', bp.text, {snippet_query}, {_HEADLINE_OPTS}) AS snippet,
               EXISTS (SELECT 1 FROM document_links dl
                       WHERE dl.from_doc_id = d.id
                          OR (dl.to_doc_id = d.id AND dl.relation <> 'leyfisbeidni_um')) AS has_appeal_links
        FROM hits h
        JOIN documents d ON d.id = h.document_id
        JOIN sources s ON s.id = d.source_id
        JOIN passages bp ON bp.id = h.best_passage_id
        ORDER BY {order_sql(sort)}
        LIMIT :limit OFFSET :offset
    """), {**p, "limit": page_size, "offset": (page - 1) * page_size})).mappings().all()

    results = []
    for r in rows:
        results.append({
            "id": str(r["id"]),
            "urlausn": _citation(r["source"], r["court"], r["case_number"], r["document_date"], r["verdict_type"]),
            "source": r["source"], "source_display": r["source_display"],
            "court": r["court"], "case_number": r["case_number"],
            "document_date": r["document_date"].isoformat() if r["document_date"] else None,
            "verdict_type": r["verdict_type"],
            "keywords": r["keywords"] or [], "plaintiffs": r["plaintiffs"] or [], "defendants": r["defendants"] or [],
            "snippet": r["snippet"] or (r["summary"] or "")[:240],
            "has_appeal_links": r["has_appeal_links"],
            "passage_id": str(r["passage_id"]),
            "anchor": passage_anchor(r["layer"], r["para_from"], r["para_to"], r["section_path"], r["ordinal"]),
            "section_kind": r["section_kind"], "layer": r["layer"], "match_count": r["match_count"],
        })
    return SearchResults(total=total, page=page, page_size=page_size, results=results)
