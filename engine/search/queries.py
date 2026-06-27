"""Search queries over the documents table.

Two text modes:
  - ``keyword``: BÍN-lemmatized Icelandic full-text search via the GIN-indexed
    ``fts_is`` column (fast, morphology-aware). The user's query is lemmatized
    the same way the column was built, then matched with ``plainto_tsquery``.
  - ``regex``: POSIX ``~*`` (case-insensitive) over chosen fields, accelerated by
    the pg_trgm GIN index on ``body_text`` (``ix_doc_body_trgm``). Guarded by a
    per-statement timeout so a pathological pattern cannot wedge the server.

Both modes share the same scope (source/group) and date-range filters, and both
run those filters first so the expensive text match sees a smaller candidate set.

This module is pure data access — no FastAPI / HTTP concerns.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from engine.config.source_groups import resolve_scope
from engine.config.sources import get_config
from engine.database.models import Document
from engine.processors.lemmatizer import lemmatize_query
from engine.processors.renderer import to_urlausn

# Regex-targetable fields → SQL expression (aliased table d).
# IMPORTANT: for the pg_trgm-indexed columns (body_text, summary, case_number)
# the expression must be the *bare* column so the planner can use the GIN index
# (ix_doc_body_trgm etc.). Wrapping in coalesce() defeats the index and forces a
# full seq-scan. A NULL value with `~*` yields NULL (excluded), which is exactly
# the behaviour we want — so coalesce is unnecessary anyway.
REGEX_COLUMNS: dict[str, str] = {
    "body_text": "d.body_text",
    "summary": "d.summary",
    "case_number": "d.case_number",
    "lower_body_text": "d.lower_body_text",
    "parties": "(coalesce(d.plaintiffs::text, '') || ' ' || coalesce(d.defendants::text, ''))",
    "keywords": "d.keywords::text",
}
DEFAULT_REGEX_FIELDS = ["body_text"]

REGEX_TIMEOUT_MS = 10_000
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
# Cap how much body text we pull back to build a regex snippet (per page row).
_REGEX_SNIPPET_SCAN = 100_000
_SNIPPET_RADIUS = 160


@dataclass
class SearchResults:
    total: int
    page: int
    page_size: int
    results: list[dict[str, Any]]


class SearchError(ValueError):
    """Raised for bad input (invalid regex, unknown field) — maps to HTTP 400."""


def _citation(short_name: str | None, court, case_number, document_date, verdict_type) -> str:
    """Build the urlausn citation, reusing renderer.to_urlausn where possible."""
    doc = Document(
        court=court, case_number=case_number,
        document_date=document_date, verdict_type=verdict_type,
    )
    try:
        return to_urlausn(doc, get_config(short_name)) if short_name else ""
    except Exception:
        parts = [p for p in (court, case_number,
                             document_date.isoformat() if document_date else None) if p]
        tail = f" – {verdict_type}" if verdict_type else ""
        return " ".join(parts) + tail


def _order_clause(mode: str, has_text: bool, sort: str, rank_expr: str) -> str:
    """Return the ORDER BY body (uses real SQL expressions, no output aliases).

    ``relevance`` only applies to keyword and proximity search (both have FTS rank).
    """
    if sort == "relevance" and not (mode in ("keyword", "proximity") and has_text):
        sort = "newest"  # relevance is meaningless without FTS rank
    if sort == "relevance":
        return f"{rank_expr} DESC, d.document_date DESC NULLS LAST, d.id"
    if sort == "oldest":
        return "d.document_date ASC NULLS LAST, d.id"
    return "d.document_date DESC NULLS LAST, d.id"


def _regex_snippet(body_head: str | None, summary: str | None, pattern: str) -> str:
    """Build a highlighted snippet around the first regex match (Python side)."""
    if body_head:
        try:
            m = re.search(pattern, body_head, re.IGNORECASE)
        except re.error:
            m = None
        if m:
            start = max(0, m.start() - _SNIPPET_RADIUS)
            end = min(len(body_head), m.end() + _SNIPPET_RADIUS)
            pre = "…" if start > 0 else ""
            post = "…" if end < len(body_head) else ""
            seg = body_head[start:m.start()] + "<mark>" + body_head[m.start():m.end()] \
                + "</mark>" + body_head[m.end():end]
            return pre + seg.replace("\n", " ") + post
    base = (summary or body_head or "")[:240].replace("\n", " ")
    return base + ("…" if len(summary or body_head or "") > 240 else "")


VALID_MODES = frozenset({
    "keyword", "exact", "prefix", "substring", "any", "proximity", "regex"
})


def _build_text_filter(
    mode: str, words: list[str], fields: list[str] | None, proximity_n: int
) -> tuple[list[str], dict[str, Any]]:
    """Return (where_fragments, params) for exact/prefix/substring/any/proximity modes.

    words = [w for w in q.split() if w] — pre-split, filtered empty.
    Not called for 'keyword' or 'regex' (handled inline in search_documents).
    """
    frags: list[str] = []
    params: dict[str, Any] = {}
    if not words:
        return frags, params

    effective_fields = [f for f in (fields or DEFAULT_REGEX_FIELDS) if f in REGEX_COLUMNS]

    if mode == "proximity":
        lemma_words = [lemmatize_query(w) for w in words]
        lemma_words = [lw for lw in lemma_words if lw]
        if not lemma_words:
            return frags, params
        if len(lemma_words) == 1:
            tsq = lemma_words[0]
        else:
            tsq = f" <{proximity_n}> ".join(lemma_words)
        params["prox_q"] = tsq
        frags.append("d.fts_is @@ to_tsquery('simple', :prox_q)")
        return frags, params

    if mode == "any":
        pattern = "(" + "|".join(re.escape(w) for w in words) + ")"
        params["pattern"] = pattern
        if effective_fields:
            ors = " OR ".join(f"{REGEX_COLUMNS[f]} ~* :pattern" for f in effective_fields)
            frags.append(f"({ors})")
        return frags, params

    # exact, prefix, substring — one SQL AND-fragment per word
    templates: dict[str, str] = {
        "exact": r"\m{w}\M",
        "prefix": r"\m{w}",
        "substring": "{w}",
    }
    tmpl = templates[mode]
    for i, w in enumerate(words):
        pat = tmpl.format(w=re.escape(w))
        params[f"pat_{i}"] = pat
        if effective_fields:
            ors = " OR ".join(
                f"{REGEX_COLUMNS[f]} ~* :pat_{i}" for f in effective_fields
            )
            frags.append(f"({ors})")
    return frags, params


async def search_documents(
    session: AsyncSession,
    *,
    q: str = "",
    mode: str = "keyword",
    scope: list[str] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    sort: str = "relevance",
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    regex_fields: list[str] | None = None,
    proximity_n: int = 5,
) -> SearchResults:
    """Run a search and return one page of results plus the total match count."""
    if mode not in VALID_MODES:
        raise SearchError(f"Unknown mode {mode!r}")
    page = max(1, page)
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))
    offset = (page - 1) * page_size
    q = (q or "").strip()

    # ── Scope → ScopeFilter. Empty filter = unknown tokens only → match nothing. ──
    scope_filter = await resolve_scope(session, scope)
    if scope_filter is not None and scope_filter.is_empty:
        return SearchResults(total=0, page=page, page_size=page_size, results=[])

    where: list[str] = []
    params: dict[str, Any] = {}

    if scope_filter is not None:
        frag, sparams = scope_filter.to_sql()
        where.append(frag)
        params.update(sparams)
    if date_from is not None:
        where.append("d.document_date >= :date_from")
        params["date_from"] = date_from
    if date_to is not None:
        where.append("d.document_date <= :date_to")
        params["date_to"] = date_to

    has_text = bool(q)
    rank_expr = "0::real"
    regex_pattern: str | None = None
    snip_pattern: str | None = None  # for Python-side _regex_snippet
    words = [w for w in q.split() if w] if q else []

    if has_text and mode == "keyword":
        lemmas = lemmatize_query(q)
        if lemmas:
            params["lemmas"] = lemmas
            where.append("d.fts_is @@ plainto_tsquery('simple', :lemmas)")
            rank_expr = "ts_rank(d.fts_is, plainto_tsquery('simple', :lemmas))"
        else:
            has_text = False  # nothing lemmatizable → filter-only browse
    elif has_text and mode == "regex":
        try:
            re.compile(q)
        except re.error as exc:
            raise SearchError(f"Invalid regex: {exc}") from exc
        fields = regex_fields or DEFAULT_REGEX_FIELDS
        unknown = [f for f in fields if f not in REGEX_COLUMNS]
        if unknown:
            raise SearchError(f"Unknown regex field(s): {unknown}")
        regex_pattern = q
        snip_pattern = q
        params["pattern"] = q
        ors = " OR ".join(f"{REGEX_COLUMNS[f]} ~* :pattern" for f in fields)
        where.append(f"({ors})")
    elif has_text:
        # exact, prefix, substring, any, proximity
        text_frags, text_params = _build_text_filter(mode, words, regex_fields, proximity_n)
        if text_frags:
            where.extend(text_frags)
            params.update(text_params)
            if mode == "proximity":
                rank_expr = "ts_rank(d.fts_is, to_tsquery('simple', :prox_q))"
            elif mode == "any":
                snip_pattern = text_params.get("pattern")
            else:
                snip_pattern = text_params.get("pat_0")  # first word for snippet
        else:
            has_text = False

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    order_sql = _order_clause(mode, has_text, sort, rank_expr)

    # Apply timeout to all regex-backed modes.
    if mode in ("regex", "exact", "prefix", "substring", "any") and has_text:
        await session.execute(text(f"SET LOCAL statement_timeout = {REGEX_TIMEOUT_MS}"))

    # ── Total count ────────────────────────────────────────────────────────────
    total = (await session.execute(
        text(f"SELECT count(*) FROM documents d{where_sql}"), params
    )).scalar() or 0
    if total == 0:
        return SearchResults(total=0, page=page, page_size=page_size, results=[])

    # ── Page of IDs (headline/snippet computed only for these rows) ────────────
    page_params = {**params, "limit": page_size, "offset": offset}
    hits_sql = f"""
        WITH hits AS (
            SELECT d.id, row_number() OVER (ORDER BY {order_sql}) AS rn
            FROM documents d{where_sql}
            ORDER BY {order_sql}
            LIMIT :limit OFFSET :offset
        )
    """

    if mode == "keyword" and has_text:
        snippet_select = (
            "ts_headline('simple', coalesce(d.body_text, ''), "
            "plainto_tsquery('simple', :hl), "
            "'StartSel=<mark>,StopSel=</mark>,MaxFragments=2,MaxWords=28,"
            "MinWords=8,ShortWord=2') AS snippet"
        )
        page_params["hl"] = q
        body_head_select = "NULL AS body_head"
    elif mode == "proximity" and has_text and "prox_q" in params:
        snippet_select = (
            "ts_headline('simple', coalesce(d.body_text, ''), "
            "to_tsquery('simple', :prox_q), "
            "'StartSel=<mark>,StopSel=</mark>,MaxFragments=2,MaxWords=28,"
            "MinWords=8,ShortWord=2') AS snippet"
        )
        body_head_select = "NULL AS body_head"
    else:
        snippet_select = "NULL AS snippet"
        body_head_select = (
            f"left(d.body_text, {_REGEX_SNIPPET_SCAN}) AS body_head"
            if snip_pattern else "NULL AS body_head"
        )

    rows = (await session.execute(text(f"""
        {hits_sql}
        SELECT d.id, s.short_name AS source, s.display_name AS source_display,
               d.court, d.case_number, d.document_date, d.verdict_type,
               d.summary, d.keywords, d.plaintiffs, d.defendants,
               {snippet_select},
               {body_head_select},
               EXISTS (SELECT 1 FROM document_links dl
                       WHERE dl.from_doc_id = d.id OR dl.to_doc_id = d.id) AS has_appeal_links
        FROM hits
        JOIN documents d ON d.id = hits.id
        JOIN sources s ON s.id = d.source_id
        ORDER BY hits.rn
    """), page_params)).mappings().all()

    results: list[dict[str, Any]] = []
    for r in rows:
        if snip_pattern:
            snippet = _regex_snippet(r.get("body_head"), r.get("summary"), snip_pattern)
        else:
            snippet = r.get("snippet") or ""
        results.append({
            "id": str(r["id"]),
            "urlausn": _citation(r["source"], r["court"], r["case_number"],
                                 r["document_date"], r["verdict_type"]),
            "source": r["source"],
            "source_display": r["source_display"],
            "court": r["court"],
            "case_number": r["case_number"],
            "document_date": r["document_date"].isoformat() if r["document_date"] else None,
            "verdict_type": r["verdict_type"],
            "keywords": r["keywords"] or [],
            "plaintiffs": r["plaintiffs"] or [],
            "defendants": r["defendants"] or [],
            "snippet": snippet,
            "has_appeal_links": r["has_appeal_links"],
        })

    return SearchResults(total=total, page=page, page_size=page_size, results=results)


async def facet_counts(
    session: AsyncSession,
    *,
    q: str = "",
    mode: str = "keyword",
    date_from: date | None = None,
    date_to: date | None = None,
    regex_fields: list[str] | None = None,
    proximity_n: int = 5,
) -> tuple[dict[str, int], dict[tuple[str, str], int]]:
    """Per-source (and per-source+verdict_type) counts for the active query.

    Applies the text and date filters but NOT the source scope — so the facet
    panel can show how many of the current results fall in every source/category,
    including ones the user has not selected (standard faceted-search behaviour).
    Returns (by_source, by_source_vt) which the caller rolls up onto the scope tree.
    """
    if mode not in VALID_MODES:
        raise SearchError(f"Unknown mode {mode!r}")
    q = (q or "").strip()
    where: list[str] = []
    params: dict[str, Any] = {}

    if date_from is not None:
        where.append("d.document_date >= :date_from")
        params["date_from"] = date_from
    if date_to is not None:
        where.append("d.document_date <= :date_to")
        params["date_to"] = date_to

    words = [w for w in q.split() if w] if q else []

    if q and mode == "keyword":
        lemmas = lemmatize_query(q)
        if lemmas:
            params["lemmas"] = lemmas
            where.append("d.fts_is @@ plainto_tsquery('simple', :lemmas)")
    elif q and mode == "regex":
        try:
            re.compile(q)
        except re.error as exc:
            raise SearchError(f"Invalid regex: {exc}") from exc
        fields = regex_fields or DEFAULT_REGEX_FIELDS
        unknown = [f for f in fields if f not in REGEX_COLUMNS]
        if unknown:
            raise SearchError(f"Unknown regex field(s): {unknown}")
        params["pattern"] = q
        ors = " OR ".join(f"{REGEX_COLUMNS[f]} ~* :pattern" for f in fields)
        where.append(f"({ors})")
        await session.execute(text(f"SET LOCAL statement_timeout = {REGEX_TIMEOUT_MS}"))
    elif q:
        # exact, prefix, substring, any, proximity
        text_frags, text_params = _build_text_filter(mode, words, regex_fields, proximity_n)
        where.extend(text_frags)
        params.update(text_params)
        if mode in ("exact", "prefix", "substring", "any"):
            await session.execute(text(f"SET LOCAL statement_timeout = {REGEX_TIMEOUT_MS}"))

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    rows = (await session.execute(text(f"""
        SELECT s.short_name, d.verdict_type, count(*) AS n
        FROM documents d JOIN sources s ON s.id = d.source_id{where_sql}
        GROUP BY s.short_name, d.verdict_type
    """), params)).mappings().all()

    by_source: dict[str, int] = {}
    by_source_vt: dict[tuple[str, str], int] = {}
    for r in rows:
        by_source[r["short_name"]] = by_source.get(r["short_name"], 0) + r["n"]
        if r["verdict_type"] is not None:
            key = (r["short_name"], r["verdict_type"])
            by_source_vt[key] = by_source_vt.get(key, 0) + r["n"]
    return by_source, by_source_vt


async def get_document(session: AsyncSession, doc_id: str | uuid.UUID) -> dict[str, Any] | None:
    """Fetch one document with its full text, parties, and appeal links."""
    try:
        did = uuid.UUID(str(doc_id))
    except (ValueError, AttributeError):
        raise SearchError(f"Invalid document id: {doc_id!r}")

    row = (await session.execute(text("""
        SELECT d.id, s.short_name AS source, s.display_name AS source_display,
               d.external_id, d.url, d.court, d.case_number, d.document_date,
               d.verdict_type, d.instance_tier, d.case_type,
               d.plaintiffs, d.defendants, d.keywords, d.summary,
               d.body_text, d.lower_body_text
        FROM documents d JOIN sources s ON s.id = d.source_id
        WHERE d.id = :id
    """), {"id": did})).mappings().first()
    if row is None:
        return None

    # Appeal links. The table stores each relationship as a bidirectional pair
    # (X→Y appealed_to AND Y→X appealed_from); selecting only the rows where this
    # doc is the *from* side yields exactly one entry per related document, and
    # the relation then reads naturally from this doc's perspective:
    #   'appealed_to'   → other is the lower instance this one reviewed
    #   'appealed_from' → other is the higher instance that reviewed this one
    links = (await session.execute(text("""
        SELECT dl.relation, dl.confidence, dl.method,
               other.id AS other_id, other.case_number AS other_case,
               other.court AS other_court, other.document_date AS other_date,
               other.verdict_type AS other_verdict, os.short_name AS other_source
        FROM document_links dl
        JOIN documents other ON other.id = dl.to_doc_id
        JOIN sources os ON os.id = other.source_id
        WHERE dl.from_doc_id = :id
        ORDER BY other.document_date
    """), {"id": did})).mappings().all()

    appeal_links = []
    for L in links:
        appeal_links.append({
            "relation": L["relation"],
            "confidence": L["confidence"],
            "method": L["method"],
            "document_id": str(L["other_id"]),
            "source": L["other_source"],
            "urlausn": _citation(L["other_source"], L["other_court"], L["other_case"],
                                 L["other_date"], L["other_verdict"]),
        })

    return {
        "id": str(row["id"]),
        "source": row["source"],
        "source_display": row["source_display"],
        "external_id": row["external_id"],
        "url": row["url"],
        "urlausn": _citation(row["source"], row["court"], row["case_number"],
                             row["document_date"], row["verdict_type"]),
        "court": row["court"],
        "case_number": row["case_number"],
        "document_date": row["document_date"].isoformat() if row["document_date"] else None,
        "verdict_type": row["verdict_type"],
        "instance_tier": row["instance_tier"],
        "case_type": row["case_type"],
        "plaintiffs": row["plaintiffs"] or [],
        "defendants": row["defendants"] or [],
        "keywords": row["keywords"] or [],
        "summary": row["summary"],
        "body_text": row["body_text"],
        "lower_body_text": row["lower_body_text"],
        "appeal_links": appeal_links,
    }
