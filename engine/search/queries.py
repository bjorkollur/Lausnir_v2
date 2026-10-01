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

import json
import re
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


# ── Provision query parser ────────────────────────────────────────────────────
# Handles patterns like:
#   "2. mgr. 218. gr. laga nr. 19/1940"   (mgr before gr — dominant Icelandic form)
#   "218. gr. 1. mgr. 19/1940"            (mgr after gr)
#   "3. gr. 33/1944"                       (gr only)
#   "218. gr. a. 1. mgr. 19/1940"         (suffix + mgr after gr)
#   "218. gr. a. 19/1940"                  (suffix only)
#   "19/1940 218. gr. a. 1. mgr."         (law before gr)
#   "33/1944, 3. gr."
#   "12. gr. laga nr. 91/1991"
#   "3. mgr. 70. gr. almennra hegningarlaga nr. 19/1940"  (compound law name)
_PROVISION_RE = re.compile(
    # Pattern A: [mgr] gr [suffix] [mgr] ... law   (gr anchors the match)
    r'(?:'
    r'(?:(?P<mgr_a1>\d+)\.\s*mgr\.\s*)?'            # optional: mgr before gr
    r'(?P<gr_a>\d+)\.\s*gr\.'                        # article number (required)
    r'(?:\s*(?P<sfx_a>[a-záðéíóúýþæö])\.)?'         # optional suffix letter
    r'(?:\s*(?P<mgr_a2>\d+)\.\s*mgr\.)?'            # optional: mgr after gr
    r'(?:\s+\w+){0,6}?'                              # 0-6 words (compound law name, non-greedy)
    r'\s*(?:laga?|lögum|reglugerðar?)?\s*'          # optional law keyword
    r'(?:nr\.\s*)?'                                  # optional "nr."
    r'(?P<law_a>\d+/\d{4})'                          # law number (required)
    r')'
    r'|'
    # Pattern B: law gr [suffix] [mgr]
    r'(?:(?:nr\.\s*)?(?P<law_b>\d+/\d{4})[,\s]+'
    r'(?P<gr_b>\d+)\.\s*gr\.'
    r'(?:\s*(?P<sfx_b>[a-záðéíóúýþæö])\.)?'
    r'(?:\s*(?P<mgr_b>\d+)\.\s*mgr\.)?)',
    re.IGNORECASE | re.UNICODE,
)


def parse_provision_query(q: str) -> tuple[str, int, str | None, int | None] | None:
    """Parse a provision reference. Returns (law, gr, suffix|None, mgr|None) or None.

    Handles mgr-before-gr (dominant Icelandic form) as well as gr-first and
    compound law names between the article reference and the law number.

    Examples:
      '2. mgr. 218. gr. laga nr. 19/1940'   → ('19/1940', 218, None, 2)
      '3. gr. 33/1944'                        → ('33/1944', 3, None, None)
      '218. gr. 1. mgr. 19/1940'             → ('19/1940', 218, None, 1)
      '218. gr. a. 19/1940'                   → ('19/1940', 218, 'a', None)
      '218. gr. a. 1. mgr. 19/1940'          → ('19/1940', 218, 'a', 1)
      '3. mgr. 70. gr. almennra hegningarlaga nr. 19/1940' → ('19/1940', 70, None, 3)
      '19/1940'                               → None (bare law, handled by caller)
    """
    if not q:
        return None
    m = _PROVISION_RE.search(q.strip())
    if not m:
        return None
    law = m.group('law_a') or m.group('law_b')
    gr = m.group('gr_a') or m.group('gr_b')
    sfx = m.group('sfx_a') or m.group('sfx_b')
    # mgr_a1 = before gr, mgr_a2 = after gr; prefer before-gr (dominant form)
    mgr = (m.group('mgr_a1') or m.group('mgr_a2')) if m.group('gr_a') else m.group('mgr_b')
    if law and gr:
        return (law, int(gr), sfx.lower() if sfx else None, int(mgr) if mgr else None)
    return None


def _build_provision_filter(
    law: str,
    gr: int | None,
    sfx: str | None,
    mgr: int | None,
) -> tuple[str, dict]:
    """Build a JSONB containment WHERE fragment for provision search.

    The containment object includes only the fields the user specified:
    - law always included
    - gr included if provided
    - mgr included if provided (only meaningful when gr is also provided)
    - sfx included if provided (only meaningful when gr is also provided)

    Returns (sql_fragment, params_dict) where sql_fragment uses :prov_filter.
    """
    obj: dict = {"law": law}
    if gr is not None:
        obj["gr"] = gr
        if sfx is not None:
            obj["sfx"] = sfx
        if mgr is not None:
            obj["mgr"] = mgr
    return (
        "d.cited_provisions @> CAST(:prov_filter AS jsonb)",
        {"prov_filter": json.dumps([obj])},
    )


def _resolve_provision_filter(provision: str) -> tuple[str, dict] | None:
    """Parse provision string and return (sql_frag, params) or None."""
    parsed = parse_provision_query(provision)
    if parsed:
        return _build_provision_filter(*parsed)
    bare = re.search(r'\b(\d+/\d{4})\b', provision)
    if bare:
        return _build_provision_filter(bare.group(1), None, None, None)
    return None


def _build_keyword_filter(keyword: str) -> tuple[str, dict]:
    """Build a WHERE fragment matching the keywords JSONB tag column only.

    Case-insensitive substring match — `keywords::text` casts the JSONB array
    to its text representation (e.g. '["forsjá", "skaðabætur"]') and ILIKE
    matches anywhere in it. Same mechanism the existing regex-mode "Lykilorð"
    field already uses (REGEX_COLUMNS["keywords"]), just exposed without
    requiring the user to switch into regex mode.

    Returns (sql_fragment, params_dict) where sql_fragment uses :keyword_pattern.
    """
    return (
        "d.keywords::text ILIKE :keyword_pattern",
        {"keyword_pattern": f"%{keyword}%"},
    )


from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from engine.config.source_groups import resolve_scope
from engine.config.sources import get_config
from engine.database.models import Document
from engine.processors.citation_resolver import norm_case_number
from engine.processors.lemmatizer import lemmatize_query
from engine.processors.renderer import to_urlausn
from engine.processors.stored_pdf import find_stored_pdf
from engine.search.passage_index import passage_anchor
from engine.search.passage_search import search_by_passages, validate_section_kinds
from engine.search.relaxation import build_keyword_queries, should_relax

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
DEFAULT_REGEX_FIELDS = ["body_text", "lower_body_text"]

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
    strict_total: int = 0
    relaxed: bool = False


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


def _order_clause(sort: str) -> str:
    """Return the ORDER BY body for the regex-family modes (exact/prefix/
    substring/any/regex) and filter-only browsing — the only cases that reach
    the page query below. None of them carry an FTS rank: keyword/proximity
    return early via search_by_passages, which has its own passage-level
    ORDER BY (see passage_search.order_sql). So ``relevance`` always falls
    back to ``newest`` here.
    """
    if sort == "relevance":
        sort = "newest"  # relevance is meaningless without FTS rank
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

# Tokens that remain after lemmatizing a provision reference like
# "2. mgr. 218. gr. laga nr. 19/1940" — numbers are stripped by BÍN so only
# these abbreviation stems survive. Matching all-noise means FTS is useless.
_PROVISION_NOISE = frozenset({"mgr", "gr", "lag", "lög", "nr", "sbr"})


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
        # Normalize: multi-token lemmas (e.g. "e mál" from "e-mál") need & between tokens
        safe_lemmas = [" & ".join(lw.split()) for lw in lemma_words]
        if len(safe_lemmas) == 1:
            tsq = safe_lemmas[0]
        else:
            # PostgreSQL <N> means EXACTLY N positions apart, not "within N".
            # Build bidirectional union: (A <1> B)|(B <1> A)|...|(A <N> B)|(B <N> A)
            # for each consecutive pair, then AND all pairs together.
            def _within_n(a: str, b: str, n: int) -> str:
                parts = [f"({a} <{k}> {b}) | ({b} <{k}> {a})" for k in range(1, n + 1)]
                return "(" + " | ".join(parts) + ")"

            pair_conds = [
                _within_n(safe_lemmas[i], safe_lemmas[i + 1], proximity_n)
                for i in range(len(safe_lemmas) - 1)
            ]
            tsq = " & ".join(pair_conds)
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


def _text_is_noise_for_provision(q: str, provision: str | None) -> bool:
    """True when the lemmatised query is only provision noise ('mgr','gr','nr'…)
    AND a provision filter is present — then the text part is dropped."""
    if not provision:
        return False
    toks = set(lemmatize_query(q).split())
    return bool(toks) and toks.issubset(_PROVISION_NOISE)


async def _strict_doc_count(
    session: AsyncSession, tsq_sql: str, where: list[str], params: dict[str, Any]
) -> int:
    """Exact document count matching ``tsq_sql`` (a ready-made tsquery expression,
    e.g. ``to_tsquery('simple', :q_strict)``) under ``where`` — the same filters
    the caller's search/facets query applies. Used as the relaxation decision's
    input: how many documents match strictly (all lemmas), scoped exactly like
    the query that will use the result."""
    doc_where_sql = "".join(f" AND {frag}" for frag in where)
    return (await session.execute(
        text(f"SELECT count(*) FROM documents d WHERE d.fts_is @@ {tsq_sql}{doc_where_sql}"), params
    )).scalar() or 0


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
    provision: str | None = None,
    keyword: str | None = None,
    section_kind: list[str] | None = None,
) -> SearchResults:
    """Run a search and return one page of results plus the total match count."""
    if mode not in VALID_MODES:
        raise SearchError(f"Unknown mode {mode!r}")
    section_kinds = validate_section_kinds(section_kind)
    if section_kinds and mode not in ("keyword", "proximity"):
        raise SearchError("section_kind only applies to keyword and proximity modes")
    page = max(1, page)
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))
    offset = (page - 1) * page_size
    q = (q or "").strip()

    # ── Scope → ScopeFilter. Empty filter = unknown tokens only → match nothing. ──
    scope_filter = await resolve_scope(session, scope)
    if scope_filter is not None and scope_filter.is_empty:
        return SearchResults(total=0, page=page, page_size=page_size, results=[],
                             strict_total=0, relaxed=False)

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

    # Provision filter (independent of text mode)
    if provision:
        prov = _resolve_provision_filter(provision)
        if prov:
            where.append(prov[0])
            params.update(prov[1])

    # Keyword filter (independent of text mode) — matches the keywords JSONB
    # tag column only, never body text.
    if keyword and keyword.strip():
        kw_frag, kw_params = _build_keyword_filter(keyword.strip())
        where.append(kw_frag)
        params.update(kw_params)

    has_text = bool(q)
    regex_pattern: str | None = None
    # Regex-family modes only (exact/prefix/substring/any/regex) — for
    # Python-side _regex_snippet. keyword/proximity never set this; they
    # return early via search_by_passages before reaching the code below.
    snip_pattern: str | None = None
    words = [w for w in q.split() if w] if q else []

    if has_text and mode == "keyword":
        # Bug 1 fix: discard FTS when all lemma tokens are provision noise (e.g.
        # "2. mgr. 218. gr. laga nr. 19/1940" → {"mgr", "gr"}) and the user has
        # already supplied a provision filter. FTS rank would be ~1.0 everywhere,
        # making relevance sort meaningless; the provision filter is sufficient.
        lemmas = "" if _text_is_noise_for_provision(q, provision) else lemmatize_query(q)
        if lemmas:
            # Relaxed keyword search (spec 2026-09-28): decide up front, from an
            # exact doc-level strict count under the same filters, whether to
            # widen the passage prefilter to all-but-one/any-lemma tiers.
            try:
                kq = build_keyword_queries(lemmas)
            except ValueError:
                # M6: every token stripped down to nothing (all tsquery operator
                # chars) — nothing lemmatisable, same as an empty query.
                has_text = False
            else:
                params["q_strict"] = kq.strict
                strict_total = await _strict_doc_count(
                    session, "to_tsquery('simple', :q_strict)", where, params)
                relaxed = should_relax(strict_total, kq.n)
                or_param: str | None = None
                relax_params: tuple[str, str | None] | None = None
                if kq.n >= 2:
                    params["q_any"] = kq.any
                    or_param = "q_any"
                if relaxed:
                    if kq.nminus1:
                        params["q_nminus1"] = kq.nminus1
                    relax_params = ("q_any", "q_nminus1" if kq.nminus1 else None)
                return await search_by_passages(
                    session, tsq_fn="to_tsquery", tsq_param="q_strict", where=where, params=params,
                    sort=sort, page=page, page_size=page_size, section_kinds=section_kinds,
                    or_tsq_param=or_param, relax_params=relax_params, strict_total=strict_total)
        else:
            has_text = False  # nothing lemmatizable → filter-only browse
    elif has_text and mode == "regex":
        try:
            # PostgreSQL's POSIX engine supports \m/\M and POSIX classes that Python re
            # does not — strip them before Python-validation so they don't raise false errors.
            _py_test = re.sub(r'\\[mM]|\[\[:<:\]\]|\[\[:>:\]\]|'
                              r'\[\[:alpha:\]\]|\[\[:digit:\]\]|\[\[:space:\]\]', 'x', q)
            re.compile(_py_test)
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
            if mode == "proximity":
                params["prox_q"] = text_params["prox_q"]
                return await search_by_passages(
                    session, tsq_fn="to_tsquery", tsq_param="prox_q", where=where, params=params,
                    sort=sort, page=page, page_size=page_size, section_kinds=section_kinds)
            where.extend(text_frags)
            params.update(text_params)
            if mode == "any":
                snip_pattern = text_params.get("pattern")
            else:
                snip_pattern = text_params.get("pat_0")  # first word for snippet
        else:
            has_text = False

    # Reaching here with mode in (keyword, proximity) means the text degenerated
    # into a document-level browse (nothing lemmatisable, provision noise, or no
    # query at all) — search_by_passages was never called, so section_kind (a
    # passage-only filter) could never be honored. Non-passage modes were already
    # rejected above; this closes the remaining silent-drop case.
    if section_kinds and mode in ("keyword", "proximity"):
        raise SearchError("section_kind only applies to keyword and proximity modes")

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    order_sql = _order_clause(sort)

    # Apply timeout to all regex-backed modes.
    if mode in ("regex", "exact", "prefix", "substring", "any") and has_text:
        await session.execute(text(f"SET LOCAL statement_timeout = {REGEX_TIMEOUT_MS}"))

    # ── Total count ────────────────────────────────────────────────────────────
    total = (await session.execute(
        text(f"SELECT count(*) FROM documents d{where_sql}"), params
    )).scalar() or 0
    if total == 0:
        return SearchResults(total=0, page=page, page_size=page_size, results=[],
                             strict_total=0, relaxed=False)

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

    # keyword/proximity always return early via search_by_passages above when
    # has_text — only regex-backed modes (or a filter-only browse) reach here.
    snippet_select = "NULL AS snippet"
    # Include lower_body_text so _regex_snippet can find matches there too.
    body_head_select = (
        f"left(coalesce(d.body_text, '') || E'\\n\\n' || coalesce(d.lower_body_text, ''), "
        f"{_REGEX_SNIPPET_SCAN}) AS body_head"
        if snip_pattern else "NULL AS body_head"
    )

    rows = (await session.execute(text(f"""
        {hits_sql}
        SELECT d.id, s.short_name AS source, s.display_name AS source_display,
               d.court, d.case_number, d.document_date, d.verdict_type,
               d.summary, d.keywords, d.plaintiffs, d.defendants,
               {snippet_select},
               {body_head_select},
               -- 'cites' is one-way (citing → cited), so this is exactly the
               -- number of documents citing this one. One index scan per page
               -- row (ix_link_to today, ix_link_to_rel once 0004 is applied).
               (SELECT count(*) FROM document_links l
                WHERE l.to_doc_id = d.id AND l.relation = 'cites') AS cited_by_count,
               -- 'leyfisbeidni_um' is a one-way edge petition → judgment, so it
               -- counts only from the petition (from) side. Counting it from the
               -- judgment's side would flag the judgment as having an appeal
               -- chain it is not part of; the detail query likewise follows only
               -- from_doc_id, so the judgment never lists the petition either.
               -- 'cites' is excluded on both sides: a citation is not an appeal
               -- relation, and the reader lists it under its own heading.
               EXISTS (SELECT 1 FROM document_links dl
                       WHERE dl.relation <> 'cites'
                         AND (dl.from_doc_id = d.id
                           OR (dl.to_doc_id = d.id AND dl.relation <> 'leyfisbeidni_um'))) AS has_appeal_links
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
            snippet = r.get("snippet") or (r.get("summary") or "")[:240]
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
            "cited_by_count": r["cited_by_count"],
            "passage_id": None, "anchor": None, "section_kind": None, "layer": None, "match_count": None,
            "match_tier": 0,
        })

    # I1: every mode that reaches here (regex/exact/prefix/substring/any, and a
    # filter-only browse with no lemmatisable text) is non-relaxed — strict_total
    # always equals total.
    return SearchResults(total=total, page=page, page_size=page_size, results=results,
                         strict_total=total, relaxed=False)


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
            # Ruling 2026-09-28 (I2, spec decision 6 amended): facets never
            # relax — they always filter with the strict (all-lemmas) query,
            # exactly as before relaxed search existed. The sidebar shows the
            # distribution of the documents that contain every word — the same
            # number the results header reports as strict_total — while the
            # list additionally shows the reachable relaxed matches. Counting
            # the widest (any-lemma) query here produced tens of thousands next
            # to a list of <= RELAX_CAND_LIMIT. This also drops one corpus-wide
            # GIN count per keyword search (no more should_relax decision here).
            try:
                kq = build_keyword_queries(lemmas)
            except ValueError:
                pass  # M6: nothing left after stripping operator chars
            else:
                params["q_strict"] = kq.strict
                where.append("d.fts_is @@ to_tsquery('simple', :q_strict)")
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


# ── Citations (spec 2026-09-29 §8.1) ──────────────────────────────────────────
# A pair that also carries an appeal relation (either orientation) is flagged
# `also_appeal` so the frontend does not list it twice; 'cites' itself is
# one-way and never part of this set.
APPEAL_RELATIONS = ("appealed_to", "appealed_from", "leyfisbeidni_um", "leiddi_til_doms")

CITATION_LAYERS = ("summary", "body")
UNRESOLVED_STATUSES = ("unresolved", "ambiguous", "pre_coverage")
MAX_CITATION_PAGE_SIZE = 100

# One row per other document: the citation with the lowest char_start in `body`,
# falling back to `summary`. `passage_id` is never stored (passages are rebuilt
# by delete+insert, which would silently null it) — the passage is found on read
# with the LATERAL below: the last passage of that layer starting at or before
# the citation. count(*) OVER () runs before LIMIT, so it is the true total.
_CITATIONS_CTE = """
    WITH c AS (
        SELECT DISTINCT ON (c.{other_col})
               c.{other_col} AS other_doc_id, c.layer, c.char_start, c.raw_text, c.confidence
        FROM citations c
        WHERE c.{self_col} = :id AND c.status = 'resolved' AND c.layer = ANY(:layers)
        ORDER BY c.{other_col}, (c.layer = 'body') DESC, c.char_start
    )
"""

_CITATIONS_SQL = _CITATIONS_CTE + """
    SELECT c.layer, c.raw_text, c.confidence,
           o.id AS other_id, o.case_number AS other_case, o.court AS other_court,
           o.document_date AS other_date, o.verdict_type AS other_verdict,
           os.short_name AS other_source,
           p.id AS passage_id, p.layer AS p_layer, p.para_from, p.para_to,
           p.section_path, p.ordinal,
           EXISTS (SELECT 1 FROM document_links dl
                   WHERE dl.relation = ANY(:appeal_rels)
                     AND ((dl.from_doc_id = :id AND dl.to_doc_id = c.other_doc_id)
                       OR (dl.to_doc_id = :id AND dl.from_doc_id = c.other_doc_id))) AS also_appeal,
           count(*) OVER () AS total
    FROM c
    JOIN documents o ON o.id = c.other_doc_id
    JOIN sources os ON os.id = o.source_id
    LEFT JOIN LATERAL (
        SELECT p.id, p.layer, p.para_from, p.para_to, p.section_path, p.ordinal
        FROM passages p
        WHERE p.document_id = {passage_doc} AND p.layer = c.layer AND p.char_start <= c.char_start
        ORDER BY p.char_start DESC LIMIT 1
    ) p ON TRUE
    ORDER BY o.document_date {order} NULLS LAST, o.id
    LIMIT :limit OFFSET :offset
"""

# out: citations this document makes, oldest cited document first.
CITATIONS_OUT_SQL = _CITATIONS_SQL.format(
    self_col="from_doc_id", other_col="to_doc_id", passage_doc=":id", order="ASC")
# in: documents citing this one, newest first; the passage lives in the *other*
# document, so the LATERAL is scoped to it.
CITATIONS_IN_SQL = _CITATIONS_SQL.format(
    self_col="to_doc_id", other_col="from_doc_id", passage_doc="c.other_doc_id", order="DESC")

# Only needed when a page past the first comes back empty: count(*) OVER () has
# no row to ride on there, and the caller still needs the true total.
_CITATIONS_COUNT_SQL = _CITATIONS_CTE + "SELECT count(*) AS total FROM c"

CITATIONS_COUNT_OUT_SQL = _CITATIONS_COUNT_SQL.format(
    self_col="from_doc_id", other_col="to_doc_id")
CITATIONS_COUNT_IN_SQL = _CITATIONS_COUNT_SQL.format(
    self_col="to_doc_id", other_col="from_doc_id")

CITATIONS_UNRESOLVED_SQL = """
    SELECT count(*) FROM citations
    WHERE from_doc_id = :id AND layer = ANY(:layers) AND status = ANY(:statuses)
"""


def _citation_ref(r, my_court: str | None, my_norm_case: str | None) -> dict[str, Any]:
    """One CitationRef row. `same_case` compares the *other* document's court and
    normalised case number to mine — a judgment citing the ruling that carries the
    same number in the same court is the same case, not a precedent (spec §4.3)."""
    pid = r["passage_id"]
    return {
        "document_id": str(r["other_id"]),
        "urlausn": _citation(r["other_source"], r["other_court"], r["other_case"],
                             r["other_date"], r["other_verdict"]),
        "source": r["other_source"],
        "document_date": r["other_date"].isoformat() if r["other_date"] else None,
        "layer": r["layer"],
        "passage_id": str(pid) if pid is not None else None,
        "anchor": passage_anchor(r["p_layer"], r["para_from"], r["para_to"],
                                 r["section_path"], r["ordinal"]) if pid is not None else None,
        "raw_text": r["raw_text"],
        "confidence": r["confidence"],
        "also_appeal": bool(r["also_appeal"]),
        "same_case": bool(my_court and my_norm_case
                          and r["other_court"] == my_court
                          and norm_case_number(r["other_case"]) == my_norm_case),
    }


async def _citations_page(session: AsyncSession, did: uuid.UUID, *, direction: str,
                          page: int, page_size: int,
                          my_court: str | None, my_case: str | None) -> dict[str, Any]:
    """Shared body of get_citations, for a caller that already holds court/case_number."""
    page = max(1, page)
    page_size = max(1, min(page_size, MAX_CITATION_PAGE_SIZE))
    offset = (page - 1) * page_size
    sql = CITATIONS_OUT_SQL if direction == "out" else CITATIONS_IN_SQL
    rows = (await session.execute(text(sql), {
        "id": did, "layers": list(CITATION_LAYERS), "appeal_rels": list(APPEAL_RELATIONS),
        "limit": page_size, "offset": offset,
    })).mappings().all()
    if rows:
        total = rows[0]["total"]
    elif offset:
        # Past the last page: no row carries count(*) OVER (), so count separately
        # rather than reporting 0 and making the pager think the list ended early.
        total = (await session.execute(
            text(CITATIONS_COUNT_OUT_SQL if direction == "out" else CITATIONS_COUNT_IN_SQL),
            {"id": did, "layers": list(CITATION_LAYERS)})).scalar() or 0
    else:
        total = 0

    my_norm_case = norm_case_number(my_case)
    return {
        "direction": direction,
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_citation_ref(r, my_court, my_norm_case) for r in rows],
    }


async def get_citations(session: AsyncSession, doc_id: str | uuid.UUID, *,
                        direction: str = "out", page: int = 1,
                        page_size: int = 50) -> dict[str, Any] | None:
    """Resolved citations of one document. None when the document does not exist.

    direction='out' → documents this one cites; 'in' → documents citing this one.
    """
    if direction not in ("out", "in"):
        raise SearchError("direction must be 'out' or 'in'")
    try:
        did = uuid.UUID(str(doc_id))
    except (ValueError, AttributeError):
        raise SearchError(f"Invalid document id: {doc_id!r}")

    me = (await session.execute(text(
        "SELECT court, case_number FROM documents WHERE id = :id"), {"id": did})).mappings().first()
    if me is None:
        return None
    return await _citations_page(session, did, direction=direction, page=page,
                                 page_size=page_size, my_court=me["court"],
                                 my_case=me["case_number"])


def _stored_pdf(cfg, row, raw: dict) -> Path | None:
    return find_stored_pdf(cfg, verdict_filename=row["verdict_filename"],
                           external_id=row["external_id"], case_number=row["case_number"],
                           raw=raw)


async def get_stored_pdf(session: AsyncSession, doc_id: str | uuid.UUID) -> Path | None:
    """Path of the document's original PDF on disk, or None if none is stored.

    Raises SearchError for a malformed id and LookupError for an unknown one.
    """
    try:
        did = uuid.UUID(str(doc_id))
    except (ValueError, AttributeError):
        raise SearchError(f"Invalid document id: {doc_id!r}")

    row = (await session.execute(text("""
        SELECT s.short_name AS source, d.external_id, d.verdict_filename, d.case_number,
               d.raw_api_data
        FROM documents d JOIN sources s ON s.id = d.source_id
        WHERE d.id = :id
    """), {"id": did})).mappings().first()
    if row is None:
        raise LookupError(f"Document not found: {did}")
    try:
        cfg = get_config(row["source"])
    except ValueError:
        return None
    raw = row["raw_api_data"] if isinstance(row["raw_api_data"], dict) else {}
    return _stored_pdf(cfg, row, raw)


async def get_document(session: AsyncSession, doc_id: str | uuid.UUID) -> dict[str, Any] | None:
    """Fetch one document with its full text, parties, appeal links and citations."""
    try:
        did = uuid.UUID(str(doc_id))
    except (ValueError, AttributeError):
        raise SearchError(f"Invalid document id: {doc_id!r}")

    row = (await session.execute(text("""
        SELECT d.id, s.short_name AS source, s.display_name AS source_display,
               d.external_id, d.url, d.court, d.case_number, d.document_date,
               d.verdict_type, d.instance_tier, d.case_type, d.verdict_filename,
               d.plaintiffs, d.defendants, d.keywords, d.summary,
               d.body_text, d.lower_body_text, d.raw_api_data
        FROM documents d JOIN sources s ON s.id = d.source_id
        WHERE d.id = :id
    """), {"id": did})).mappings().first()
    if row is None:
        return None

    # Appeal links. The table stores each relationship as a bidirectional pair,
    # oriented by instance_tier: lower→higher carries 'appealed_to' and
    # higher→lower carries 'appealed_from'. Selecting only the rows where this
    # doc is the *from* side yields exactly one entry per related document, and
    # the relation then reads naturally from this doc's perspective:
    #   'appealed_to'   → other is the higher instance this one was appealed to
    #   'appealed_from' → other is the lower instance this one reviewed
    # (This comment described the opposite until 2026-09-16, because
    # backfill_hrd_lrd_links.py wrote the two relations the wrong way round and
    # the comment was written to match the broken data rather than the model.)
    # 'cites' edges live in the same table but are not appeal relations: they are
    # served by citations_out/cited_by below, and listing them here would put a
    # raw 'cites' label under "Tengd mál" in the reader.
    links = (await session.execute(text("""
        SELECT dl.relation, dl.confidence, dl.method,
               other.id AS other_id, other.case_number AS other_case,
               other.court AS other_court, other.document_date AS other_date,
               other.verdict_type AS other_verdict, os.short_name AS other_source
        FROM document_links dl
        JOIN documents other ON other.id = dl.to_doc_id
        JOIN sources os ON os.id = other.source_id
        WHERE dl.from_doc_id = :id AND dl.relation <> 'cites'
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

    # Citations. Both directions share the document row already fetched above,
    # so neither re-reads court/case_number for the same_case comparison.
    out = await _citations_page(session, did, direction="out", page=1, page_size=50,
                                my_court=row["court"], my_case=row["case_number"])
    incoming = await _citations_page(session, did, direction="in", page=1, page_size=50,
                                     my_court=row["court"], my_case=row["case_number"])
    unresolved_total = (await session.execute(text(CITATIONS_UNRESOLVED_SQL), {
        "id": did, "layers": list(CITATION_LAYERS), "statuses": list(UNRESOLVED_STATUSES),
    })).scalar() or 0

    # Access state — why body_text may be absent. Skemman embargoes a third of
    # all theses; without this the reader just shows a blank page and the user
    # can't tell a restriction from a bug. Sources that don't publish these keys
    # get None, which the frontend reads as "no restriction known".
    raw = row["raw_api_data"] if isinstance(row["raw_api_data"], dict) else {}
    locked = raw.get("locked")
    if isinstance(locked, str):  # some importers store "true"/"false" as text
        locked = locked.lower() == "true"

    cfg = None
    try:
        cfg = get_config(row["source"])
    except Exception:
        pass

    return {
        "id": str(row["id"]),
        "source": row["source"],
        "source_display": row["source_display"],
        "external_id": row["external_id"],
        "url": row["url"],
        # True for theses/books, where case_number holds a free-text title rather
        # than a case number — the reader must not label it "Mál nr.".
        "case_number_is_title": bool(cfg.case_number_is_title) if cfg else False,
        # True → the original PDF is on disk and /api/document/{id}/pdf will serve it.
        "has_pdf": bool(cfg and _stored_pdf(cfg, row, raw)),
        "locked": locked if isinstance(locked, bool) else None,
        "embargo_until": raw.get("embargo_until"),
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
        "citations_out": out["items"],
        "citations_out_total": out["total"],
        "cited_by": incoming["items"],
        "cited_by_total": incoming["total"],
        "citations_unresolved_total": unresolved_total,
    }
