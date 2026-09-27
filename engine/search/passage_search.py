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

__all__ = ["SECTION_KINDS", "validate_section_kinds", "build_hits_sql", "order_sql", "search_by_passages"]

_HEADLINE_OPTS = ("'StartSel=<mark>,StopSel=</mark>,MaxFragments=1,MaxWords=35,"
                  "MinWords=12,ShortWord=2'")


def validate_section_kinds(kinds: list[str] | None) -> list[str] | None:
    from engine.search.queries import SearchError  # local import: queries imports this module
    if not kinds:
        return None
    bad = [k for k in kinds if k not in SECTION_KINDS]
    if bad:
        raise SearchError(f"Unknown section_kind(s): {bad}; allowed: {sorted(SECTION_KINDS)}")
    return list(dict.fromkeys(kinds))


def build_hits_sql(*, tsq: str, doc_where: list[str], section_filter: bool) -> str:
    rank = f"ts_rank(p.fts_is, {tsq})"
    where = [f"d.fts_is @@ {tsq}", f"p.fts_is @@ {tsq}", *doc_where]
    if section_filter:
        where.append("p.section_kind = ANY(:section_kinds)")
    return f"""
        SELECT p.document_id,
               max({rank}) AS best_rank,
               count(*) AS match_count,
               (array_agg(p.id ORDER BY {rank} DESC, p.ordinal))[1] AS best_passage_id
        FROM passages p
        JOIN documents d ON d.id = p.document_id
        WHERE {" AND ".join(where)}
        GROUP BY p.document_id
    """


def order_sql(sort: str) -> str:
    if sort == "oldest":
        return "d.document_date ASC NULLS LAST, h.best_rank DESC, d.id"
    if sort == "newest":
        return "d.document_date DESC NULLS LAST, h.best_rank DESC, d.id"
    return "h.best_rank DESC, h.match_count DESC, d.document_date DESC NULLS LAST, d.id"


async def search_by_passages(
    session: AsyncSession, *, tsq_fn: str, tsq_param: str, where: list[str], params: dict[str, Any],
    sort: str, page: int, page_size: int, section_kinds: list[str] | None,
):
    from engine.search.queries import SearchResults, _citation  # local import (cycle)

    tsq = f"{tsq_fn}('simple', :{tsq_param})"
    hits = build_hits_sql(tsq=tsq, doc_where=where, section_filter=bool(section_kinds))
    p = dict(params)
    if section_kinds:
        p["section_kinds"] = section_kinds

    total = (await session.execute(text(f"SELECT count(*) FROM ({hits}) h"), p)).scalar() or 0
    if total == 0:
        return SearchResults(total=0, page=page, page_size=page_size, results=[])

    rows = (await session.execute(text(f"""
        WITH h AS ({hits})
        SELECT d.id, s.short_name AS source, s.display_name AS source_display,
               d.court, d.case_number, d.document_date, d.verdict_type,
               d.summary, d.keywords, d.plaintiffs, d.defendants,
               h.best_rank, h.match_count,
               bp.id AS passage_id, bp.layer, bp.section_kind, bp.section_path,
               bp.para_from, bp.para_to, bp.ordinal,
               ts_headline('simple', bp.text, {tsq}, {_HEADLINE_OPTS}) AS snippet,
               EXISTS (SELECT 1 FROM document_links dl
                       WHERE dl.from_doc_id = d.id
                          OR (dl.to_doc_id = d.id AND dl.relation <> 'leyfisbeidni_um')) AS has_appeal_links
        FROM h
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
