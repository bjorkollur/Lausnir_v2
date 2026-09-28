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

__all__ = ["SECTION_KINDS", "PASSAGE_CANDIDATE_DOCS", "MAX_PASSAGE_WINDOW", "PASSAGE_RANK_FN",
           "PASSAGE_RANK_STRATEGY", "RANK_STRATEGIES", "validate_section_kinds",
           "build_hits_sql", "order_sql", "search_by_passages", "get_passages"]

_HEADLINE_OPTS = ("'StartSel=<mark>,StopSel=</mark>,MaxFragments=1,MaxWords=35,"
                  "MinWords=12,ShortWord=2'")

# Stage 1 caps candidate documents by document-level rank (ts_rank on d.fts_is)
# before Stage 2 aggregates passages, so the passage GROUP BY never runs over more
# than this many documents. Exact for queries matching <= PASSAGE_CANDIDATE_DOCS
# documents (the overwhelming majority); for very common terms that match more,
# only pagination beyond the cap is affected — see search_by_passages.
PASSAGE_CANDIDATE_DOCS = 2000

# Ranking experiment knobs (Task 7 fix round 3). PASSAGE_RANK_FN selects the
# Postgres text-search rank function used for both doc_rank (in `cand`) and
# passage-level rank (in `hits_and`/`hits_or`); PASSAGE_RANK_STRATEGY selects the
# relevance ORDER BY. Read as module attributes at call time (not bound at import)
# so scripts/eval_search.py --rank-sweep can flip them between evaluations.
#
# Defaults set in fix round 5, measured on the 492-question golden set (50
# hand-curated core + 442 drafted, three query styles) on 2026-09-27
# (`scripts/eval_search.py --rank-sweep --set all`): ts_rank/breadth_coloc scored
# recall@10 .319, MRR .208, hit@1 .163, vs legacy `documents` impl .309/.186/.140
# and vs the fix-round-4 default ts_rank_cd/doc .297/.191/.144. On the smaller
# 50-query set ts_rank_cd/doc had looked best; the larger set showed that was
# noise — every `ts_rank` row beat every `ts_rank_cd` row here. `breadth_coloc`
# blends document-level rank with the best passage weighted by breadth
# (ln(1+match_count)) plus a small bonus for co-located terms.
PASSAGE_RANK_FN = "ts_rank"
PASSAGE_RANK_STRATEGY = "breadth_coloc"

# strategy -> relevance ORDER BY prefix; order_sql() appends
# ", d.document_date DESC NULLS LAST, d.id" to whichever is selected.
RANK_STRATEGIES: dict[str, str] = {
    "tiered": "h.colocated DESC, h.best_rank DESC, h.match_count DESC",
    "passage": "h.best_rank DESC, h.match_count DESC",
    "doc": "h.doc_rank DESC, h.best_rank DESC",
    "blend": "(h.doc_rank + h.best_rank) DESC, h.match_count DESC",
    "blend_coloc": "(h.doc_rank + h.best_rank + CASE WHEN h.colocated THEN 0.25 ELSE 0 END) DESC, h.match_count DESC",
    "breadth": "(h.doc_rank + h.best_rank * ln(1 + h.match_count)) DESC",
    "breadth_coloc": "(h.doc_rank + h.best_rank * ln(1 + h.match_count) + CASE WHEN h.colocated THEN 0.25 ELSE 0 END) DESC",
}


def validate_section_kinds(kinds: list[str] | None) -> list[str] | None:
    from engine.search.queries import SearchError  # local import: queries imports this module
    if not kinds:
        return None
    bad = [k for k in kinds if k not in SECTION_KINDS]
    if bad:
        raise SearchError(f"Unknown section_kind(s): {bad}; allowed: {sorted(SECTION_KINDS)}")
    return list(dict.fromkeys(kinds))


def _relaxed_cand_sql(*, rank_fn: str, tsq: str, any_tsq: str, nminus1_tsq: str | None,
                      doc_where_sql: str, date_dir: str) -> str:
    """The ``cand`` CTE (plus its ``t0``/``t1``/``t2``/``tiers`` helpers) for relaxed search.

    Perf fix 2026-09-28. The first implementation selected candidates with a
    single scan over the widest (any-lemma) set, computing ``ts_rank(d.fts_is, …)``
    and rechecking two ``@@`` predicates in a ``CASE`` for *every* matching row
    before the top-N sort. That forces the heap scan to detoast each row's
    ``fts_is`` tsvector: 870 ms and ~190k buffers for an any-set of 7,927
    documents, 10–30 s for common lemmas (60–90k matches) — unusable.

    Instead each tier is selected on its own, straight off the GIN index, and
    capped by ``document_date`` (a cheap non-toasted column):

    * ``t0`` — strict matches. Uncapped: relaxation only happens when the strict
      count is below ``RELAX_BELOW``, so this is a handful of rows by definition.
    * ``t1`` — all-but-one-lemma matches not already in ``t0`` (omitted entirely
      when ``nminus1_tsq`` is None), capped at ``:cand_limit`` by date.
    * ``t2`` — any-lemma matches in neither, same cap.

    ``tiers`` unions them, the cut to ``:cand_limit`` runs on ``(tier, date, id)``
    only, and ``doc_rank`` is computed last, for the surviving rows alone — at
    most ``:cand_limit`` tsvector detoasts instead of the whole any-set.

    Trade-off: for very common lemmas the tier-1/2 candidates are the *newest*
    matches, not the highest-ranked ones (the rank isn't known before selection).
    Ordering *within* a tier is unchanged — ``order_sql`` still ranks the page.
    ``t0``/``t1`` are marked MATERIALIZED so the NOT EXISTS probes reuse one
    result rather than re-running the tsquery.
    """
    cut_order = f"tier ASC, document_date {date_dir} NULLS LAST, id"
    tier_order = f"ORDER BY d.document_date {date_dir} NULLS LAST, d.id\n            LIMIT :cand_limit"
    sql = f"""
        t0 AS MATERIALIZED (
            SELECT d.id, d.document_date
            FROM documents d
            WHERE d.fts_is @@ {tsq}{doc_where_sql}
        ),"""
    not_exists = ["NOT EXISTS (SELECT 1 FROM t0 WHERE t0.id = d.id)"]
    tier_unions = ["SELECT id, 0 AS tier, document_date FROM t0"]
    if nminus1_tsq:
        sql += f"""
        t1 AS MATERIALIZED (
            SELECT d.id, d.document_date
            FROM documents d
            WHERE d.fts_is @@ {nminus1_tsq}{doc_where_sql}
              AND {not_exists[0]}
            {tier_order}
        ),"""
        not_exists.append("NOT EXISTS (SELECT 1 FROM t1 WHERE t1.id = d.id)")
        tier_unions.append("SELECT id, 1, document_date FROM t1")
    tier_unions.append("SELECT id, 2, document_date FROM t2")
    not_exists_sql = "\n              AND ".join(not_exists)
    tiers_sql = "\n            UNION ALL ".join(tier_unions)
    sql += f"""
        t2 AS (
            SELECT d.id, d.document_date
            FROM documents d
            WHERE d.fts_is @@ {any_tsq}{doc_where_sql}
              AND {not_exists_sql}
            {tier_order}
        ),
        tiers AS (
            {tiers_sql}
        ),
        cand AS (
            SELECT c.id, {rank_fn}(d.fts_is, {tsq}) AS doc_rank, c.tier
            FROM (
                SELECT id, tier, document_date FROM tiers
                ORDER BY {cut_order}
                LIMIT :cand_limit
            ) c
            JOIN documents d ON d.id = c.id
        )"""
    return sql


def _lateral_hits_sql(*, rank_fn: str, hit_tsq: str, section_filter: bool,
                      extra_where: str | None) -> str:
    """Body of ``hits_and``/``hits_or`` for relaxed search: per-candidate LATERAL.

    Perf fix 2026-09-28 (round 2). The shared shape — ``FROM passages p JOIN cand c
    ON c.id = p.document_id WHERE p.fts_is @@ … GROUP BY p.document_id`` — is planned
    as a Bitmap Heap Scan on ``ix_passage_fts_is`` for the whole corpus, joined to
    ``cand`` only afterwards. For common lemmas the (OR) tsquery matches hundreds of
    thousands of passages, each detoasted to compute ``ts_rank``: ``hits`` alone took
    43.6 s for `krafa dómur skaðabót`. Relaxation is exactly the case where the OR
    query is wide, so the work has to be bounded by the candidates instead.

    Driving the aggregate from ``cand`` through ``CROSS JOIN LATERAL`` makes the
    planner walk ``ix_passage_doc (document_id, ordinal)`` once per candidate — at
    most ``RELAX_CAND_LIMIT`` short index scans — and the ``@@`` recheck then only
    touches that document's passages. The lateral aggregate always yields one row
    (``count(*) = 0``, the rest NULL, for a candidate with no matching passage), so
    ``WHERE x.match_count > 0`` restores the semantics of the GROUP BY form.

    Column names and their order (``document_id, best_rank, match_count,
    best_passage_id, doc_rank, tier``) match the unrelaxed CTEs exactly — ``hits``
    unions the two and downstream SQL reads them by name.
    """
    rank = f"{rank_fn}(p.fts_is, {hit_tsq})"
    inner_where = ["p.document_id = c.id", f"p.fts_is @@ {hit_tsq}"]
    if section_filter:
        inner_where.append("p.section_kind = ANY(:section_kinds)")
    outer_where = ["x.match_count > 0"]
    if extra_where:
        outer_where.append(extra_where)
    return f"""
            SELECT c.id AS document_id,
                   x.best_rank,
                   x.match_count,
                   x.best_passage_id,
                   c.doc_rank,
                   c.tier
            FROM cand c
            CROSS JOIN LATERAL (
                SELECT max({rank}) AS best_rank,
                       count(*) AS match_count,
                       (array_agg(p.id ORDER BY {rank} DESC, p.ordinal))[1] AS best_passage_id
                FROM passages p
                WHERE {" AND ".join(inner_where)}
            ) x
            WHERE {" AND ".join(outer_where)}"""


def build_hits_sql(*, tsq: str, or_tsq: str | None, doc_where: list[str], section_filter: bool,
                   sort: str = "relevance", relax: tuple[str, str | None] | None = None) -> str:
    """Two- or three-stage CTE list (no leading ``WITH`` — callers prepend it).

    Stage 1 (``cand``) ranks documents by ``ts_rank(d.fts_is, tsq)`` (the doc-level
    AND match) and caps the candidate set at :cand_limit (PASSAGE_CANDIDATE_DOCS) so
    later stages never aggregate passages for more documents than that.

    F2: when ``sort`` is ``newest``/``oldest``, ``cand`` is ordered by
    ``d.document_date`` instead of ``doc_rank`` — otherwise the date sort only ever
    picks among the :cand_limit best-*ranked* documents, not the newest/oldest
    overall (a 2000-document rank cap silently overriding an explicit date sort).
    ``doc_rank`` is still computed and selected either way, since ``hits``/ordering
    downstream (``order_sql``) uses it as a tiebreaker.

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

    ``relax`` (``(any_tsq, nminus1_tsq_or_None)``) switches ``cand`` to a tiered
    prefilter for relaxed keyword search (spec 2026-09-28): the candidate set is
    widened to documents matching ``any_tsq`` (any query lemma), tagged with a
    ``tier`` (0 = all lemmas, 1 = all-but-one, 2 = any) so strict matches still
    sort first. Each tier is selected and capped separately — see
    ``_relaxed_cand_sql`` for why (detoasting ``fts_is`` across the whole
    any-set was the original implementation's 10–30 s bottleneck).
    ``relax=None`` (the default) keeps today's behaviour exactly: prefilter on
    ``tsq`` alone, constant ``tier = 0``.

    Relaxed mode also swaps ``hits_and``/``hits_or`` for the per-candidate LATERAL
    form (see ``_lateral_hits_sql``) — the corpus-wide passage bitmap the GROUP BY
    shape plans is the one thing a wide OR tsquery cannot afford. The unrelaxed
    branch is byte-identical to the pre-2026-09-28 SQL; existing tests pin it.
    """
    rank_fn = PASSAGE_RANK_FN
    if rank_fn not in ("ts_rank", "ts_rank_cd"):
        raise ValueError(f"Unknown PASSAGE_RANK_FN: {rank_fn!r}; allowed: ts_rank, ts_rank_cd")
    doc_where_sql = "".join(f" AND {frag}" for frag in doc_where)
    and_rank = f"{rank_fn}(p.fts_is, {tsq})"
    and_where = [f"p.fts_is @@ {tsq}"]
    if section_filter:
        and_where.append("p.section_kind = ANY(:section_kinds)")

    if sort == "newest":
        cand_order = "d.document_date DESC NULLS LAST, d.id"
    elif sort == "oldest":
        cand_order = "d.document_date ASC NULLS LAST, d.id"
    else:
        cand_order = "doc_rank DESC, d.id"

    if relax is None:
        cand_cte = f"""
        cand AS (
            SELECT d.id, {rank_fn}(d.fts_is, {tsq}) AS doc_rank, 0 AS tier
            FROM documents d
            WHERE d.fts_is @@ {tsq}{doc_where_sql}
            ORDER BY {cand_order}
            LIMIT :cand_limit
        )"""
    else:
        any_tsq, nminus1_tsq = relax
        cand_cte = _relaxed_cand_sql(
            rank_fn=rank_fn, tsq=tsq, any_tsq=any_tsq, nminus1_tsq=nminus1_tsq,
            doc_where_sql=doc_where_sql, date_dir="ASC" if sort == "oldest" else "DESC")

    if relax is None:
        ctes = f"""{cand_cte},
        hits_and AS (
            SELECT p.document_id,
                   max({and_rank}) AS best_rank,
                   count(*) AS match_count,
                   (array_agg(p.id ORDER BY {and_rank} DESC, p.ordinal))[1] AS best_passage_id,
                   max(c.doc_rank) AS doc_rank,
                   max(c.tier) AS tier
            FROM passages p
            JOIN cand c ON c.id = p.document_id
            WHERE {" AND ".join(and_where)}
            GROUP BY p.document_id
        )"""
    else:
        ctes = f"""{cand_cte},
        hits_and AS ({_lateral_hits_sql(rank_fn=rank_fn, hit_tsq=tsq,
                                        section_filter=section_filter, extra_where=None)}
        )"""

    if or_tsq is not None:
        or_rank = f"{rank_fn}(p.fts_is, {or_tsq})"
        or_where = [
            f"p.fts_is @@ {or_tsq}",
            "NOT EXISTS (SELECT 1 FROM hits_and ha WHERE ha.document_id = p.document_id)",
        ]
        if section_filter:
            or_where.append("p.section_kind = ANY(:section_kinds)")
        if relax is None:
            hits_or_body = f"""
            SELECT p.document_id,
                   max({or_rank}) AS best_rank,
                   count(*) AS match_count,
                   (array_agg(p.id ORDER BY {or_rank} DESC, p.ordinal))[1] AS best_passage_id,
                   max(c.doc_rank) AS doc_rank,
                   max(c.tier) AS tier
            FROM passages p
            JOIN cand c ON c.id = p.document_id
            WHERE {" AND ".join(or_where)}
            GROUP BY p.document_id"""
        else:
            hits_or_body = _lateral_hits_sql(
                rank_fn=rank_fn, hit_tsq=or_tsq, section_filter=section_filter,
                extra_where="NOT EXISTS (SELECT 1 FROM hits_and ha WHERE ha.document_id = c.id)")
        ctes += f""",
        hits_or AS ({hits_or_body}
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


def order_sql(sort: str, strategy: str | None = None) -> str:
    if sort == "oldest":
        return "h.tier ASC, d.document_date ASC NULLS LAST, h.best_rank DESC, d.id"
    if sort == "newest":
        return "h.tier ASC, d.document_date DESC NULLS LAST, h.best_rank DESC, d.id"
    key = strategy or PASSAGE_RANK_STRATEGY
    try:
        prefix = RANK_STRATEGIES[key]
    except KeyError:
        raise ValueError(f"Unknown passage rank strategy: {key!r}; allowed: {sorted(RANK_STRATEGIES)}")
    return f"h.tier ASC, {prefix}, d.document_date DESC NULLS LAST, d.id"


async def search_by_passages(
    session: AsyncSession, *, tsq_fn: str, tsq_param: str, where: list[str], params: dict[str, Any],
    sort: str, page: int, page_size: int, section_kinds: list[str] | None,
    or_tsq_param: str | None = None, relax_params: tuple[str, str | None] | None = None,
    strict_total: int | None = None,
):
    from engine.search.queries import SearchResults, _citation  # local import (cycle)

    tsq = f"{tsq_fn}('simple', :{tsq_param})"
    # OR-fallback recall stage, keyword mode only (proximity passes or_tsq_param=None).
    or_tsq = f"to_tsquery('simple', :{or_tsq_param})" if or_tsq_param else None
    # Relaxed keyword search (spec 2026-09-28): relax_params=(any_param, nminus1_param_or_None)
    # names the bind params for the widened prefilter; build the SQL fragments build_hits_sql
    # expects from them.
    relax: tuple[str, str | None] | None = None
    if relax_params is not None:
        any_param, nminus1_param = relax_params
        relax = (
            f"to_tsquery('simple', :{any_param})",
            f"to_tsquery('simple', :{nminus1_param})" if nminus1_param else None,
        )
    ctes = build_hits_sql(tsq=tsq, or_tsq=or_tsq, doc_where=where, section_filter=bool(section_kinds),
                          sort=sort, relax=relax)
    p = dict(params)
    # Relaxed search aggregates passages per candidate (LATERAL, see
    # _lateral_hits_sql), so its cost is linear in the candidate count and the
    # any-lemma tsquery it walks is wide. It gets its own, much smaller cap;
    # the unrelaxed path keeps PASSAGE_CANDIDATE_DOCS. Read at call time so
    # --relax-cand-limit can flip it.
    if relax_params is not None:
        import engine.search.relaxation as _rx
        p["cand_limit"] = _rx.RELAX_CAND_LIMIT
    else:
        p["cand_limit"] = PASSAGE_CANDIDATE_DOCS
    if section_kinds:
        p["section_kinds"] = section_kinds

    if section_kinds:
        # Bounded by the candidate cap (exact only up to PASSAGE_CANDIDATE_DOCS
        # matching documents) — a section filter can only be evaluated per-passage.
        total = (await session.execute(text(f"WITH {ctes} SELECT count(*) FROM hits"), p)).scalar() or 0
    elif strict_total is not None and relax_params is None:
        # Unrelaxed keyword search: the caller already ran this exact strict
        # count (to decide whether to relax) — reuse it instead of re-running
        # the identical query against documents.fts_is.
        total = strict_total
    else:
        # Exact document total straight off the GIN index — no rank, no cap. In
        # relaxed mode, 'total' counts the widest (any-lemma) query; unrelaxed it's
        # the strict query, same as before.
        doc_where_sql = "".join(f" AND {frag}" for frag in where)
        total_tsq = relax[0] if relax is not None else tsq
        total = (await session.execute(
            text(f"SELECT count(*) FROM documents d WHERE d.fts_is @@ {total_tsq}{doc_where_sql}"), p
        )).scalar() or 0
    if strict_total is None:
        strict_total = total
    if total == 0:
        return SearchResults(total=0, page=page, page_size=page_size, results=[],
                             strict_total=strict_total, relaxed=relax_params is not None)

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
               h.best_rank, h.match_count, h.tier,
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
            "match_tier": r["tier"],
        })
    return SearchResults(total=total, page=page, page_size=page_size, results=results,
                         strict_total=strict_total, relaxed=relax_params is not None)


MAX_PASSAGE_WINDOW = 200


async def get_passages(session: AsyncSession, doc_id, *, from_ordinal: int, to_ordinal: int,
                       section_kinds: list[str] | None, layer: str | None) -> dict[str, Any] | None:
    import uuid
    from engine.search.queries import SearchError, _citation
    try:
        did = uuid.UUID(str(doc_id))
    except (ValueError, AttributeError):
        raise SearchError(f"Invalid document id: {doc_id!r}")
    if to_ordinal < from_ordinal:
        raise SearchError("'to' must be >= 'from'")
    if to_ordinal - from_ordinal + 1 > MAX_PASSAGE_WINDOW:
        raise SearchError(f"at most {MAX_PASSAGE_WINDOW} passages per request")
    if layer is not None and layer not in ("summary", "body", "lower_body"):
        raise SearchError(f"Unknown layer {layer!r}")
    section_kinds = validate_section_kinds(section_kinds)

    doc = (await session.execute(text("""
        SELECT d.id, s.short_name AS source, d.court, d.case_number, d.document_date, d.verdict_type
        FROM documents d JOIN sources s ON s.id = d.source_id WHERE d.id = :id"""), {"id": did})).mappings().first()
    if doc is None:
        return None

    where = ["p.document_id = :id"]
    params: dict[str, Any] = {"id": did}
    if section_kinds:
        where.append("p.section_kind = ANY(:kinds)"); params["kinds"] = section_kinds
    if layer:
        where.append("p.layer = :layer"); params["layer"] = layer
    w = " AND ".join(where)
    total = (await session.execute(text(f"SELECT count(*) FROM passages p WHERE {w}"), params)).scalar() or 0
    rows = (await session.execute(text(f"""
        SELECT p.id, p.ordinal, p.layer, p.section_path, p.section_kind, p.para_from, p.para_to,
               p.char_start, p.char_end, p.word_count, p.text
        FROM passages p WHERE {w} AND p.ordinal BETWEEN :lo AND :hi ORDER BY p.ordinal"""),
        {**params, "lo": from_ordinal, "hi": to_ordinal})).mappings().all()
    return {
        "document_id": str(doc["id"]),
        "urlausn": _citation(doc["source"], doc["court"], doc["case_number"], doc["document_date"], doc["verdict_type"]),
        "total": total,
        "passages": [{
            "id": str(r["id"]), "ordinal": r["ordinal"], "layer": r["layer"],
            "anchor": passage_anchor(r["layer"], r["para_from"], r["para_to"], r["section_path"], r["ordinal"]),
            "section_path": r["section_path"], "section_kind": r["section_kind"],
            "para_from": r["para_from"], "para_to": r["para_to"],
            "char_start": r["char_start"], "char_end": r["char_end"],
            "word_count": r["word_count"], "text": r["text"],
        } for r in rows],
    }
