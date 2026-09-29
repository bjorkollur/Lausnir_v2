"""MCP tool implementations as plain async functions (spec §4).

Every function takes an AsyncSession first, returns a JSON-safe dict, and
raises ToolInputError for anything the LLM should see as a tool error.
server.py wraps these; nothing here imports the mcp SDK, so the logic is
testable without a protocol layer.
"""
from __future__ import annotations

import datetime as _dt
import time as _time
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from engine.config.source_groups import annotate_counts, catalog, resolve_scope
from engine.config.sources import SOURCE_REGISTRY, get_config
from engine.database.models import Document
from engine.mcp.shaping import cell_value, compact_passage, compact_search_result, jsonable, truncate_text
from engine.mcp.sqlguard import SqlRejected, validate_sql
from engine.processors.renderer import to_markdown
from engine.search.passage_search import get_passages, validate_section_kinds
from engine.search.queries import SearchError, facet_counts, get_document, search_documents

PAGE_SIZE_MAX = 25
PAGE_SIZE_DEFAULT = 10
CONTEXT_MAX = 10
PASSAGES_COUNT_MAX = 50
DOC_TEXT_MAX_CHARS = 40_000
PASSAGE_LAYERS = ("summary", "body", "lower_body")

RELAXED_HINT = ("Færri en 10 skjöl innihalda öll orðin; niðurstöður með match_tier 1–2 "
                "innihalda aðeins hluta þeirra.")


class ToolInputError(ValueError):
    """User-facing error; server.py turns it into an MCP ToolError."""


def _uuid(value: Any, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise ToolInputError(f"Ógilt {what}: {value!r}")


def _date(value: str | None, name: str) -> _dt.date | None:
    if value in (None, ""):
        return None
    try:
        return _dt.date.fromisoformat(str(value))
    except ValueError:
        raise ToolInputError(f"{name} verður að vera ISO-dagsetning (ÁÁÁÁ-MM-DD), fékk {value!r}")


def _clamp(v: int | None, lo: int, hi: int, default: int) -> int:
    if v is None:
        return default
    try:
        v = int(v)
    except (TypeError, ValueError):
        raise ToolInputError(f"Væntanlegt heiltala á bilinu {lo}–{hi}, fékk {v!r}")
    return max(lo, min(v, hi))


# ── search ─────────────────────────────────────────────────────────────────────

async def search(session: AsyncSession, *, q: str, mode: str = "keyword",
                 scope: list[str] | None = None, date_from: str | None = None,
                 date_to: str | None = None, sort: str = "relevance",
                 section_kind: list[str] | None = None, page: int = 1,
                 page_size: int = PAGE_SIZE_DEFAULT) -> dict:
    page = _clamp(page, 1, 10_000, 1)
    page_size = _clamp(page_size, 1, PAGE_SIZE_MAX, PAGE_SIZE_DEFAULT)
    d_from, d_to = _date(date_from, "date_from"), _date(date_to, "date_to")

    hint: str | None = None
    if scope:
        sf = await resolve_scope(session, scope)
        if sf is not None and sf.is_empty:
            hint = f"Óþekkt scope: {', '.join(scope)}. Kallaðu á list_sources til að sjá gild heiti."
    try:
        res = await search_documents(session, q=q or "", mode=mode, scope=scope,
                                     date_from=d_from, date_to=d_to, sort=sort,
                                     page=page, page_size=page_size, section_kind=section_kind)
    except SearchError as exc:
        raise ToolInputError(f"Ógilt inntak: {exc}")
    if hint is None and res.relaxed:
        hint = RELAXED_HINT
    return {
        "total": res.total, "strict_total": res.strict_total, "relaxed": res.relaxed,
        "page": res.page, "page_size": res.page_size,
        "results": [compact_search_result(r) for r in res.results],
        "hint": hint,
    }


# ── passages ───────────────────────────────────────────────────────────────────

async def _passage_window(session: AsyncSession, doc_id: uuid.UUID, *, from_ordinal: int,
                          to_ordinal: int, section_kinds: list[str] | None, layer: str | None) -> dict:
    try:
        win = await get_passages(session, doc_id, from_ordinal=from_ordinal, to_ordinal=to_ordinal,
                                 section_kinds=section_kinds, layer=layer)
    except SearchError as exc:
        raise ToolInputError(f"Ógilt inntak: {exc}")
    if win is None:
        raise ToolInputError("Skjal fannst ekki.")
    return win


def _validate_passage_filters(section_kind: list[str] | None,
                              layer: str | None) -> tuple[list[str] | None, str | None]:
    """Same checks ``get_passages`` makes, run early so we can build SQL first."""
    try:
        kinds = validate_section_kinds(section_kind)
    except SearchError as exc:
        raise ToolInputError(f"Ógilt inntak: {exc}")
    if layer is not None and layer not in PASSAGE_LAYERS:
        raise ToolInputError(f"Ógilt inntak: Unknown layer {layer!r}")
    return kinds, layer


def _passage_filter_sql(section_kinds: list[str] | None,
                        layer: str | None) -> tuple[str, dict[str, Any]]:
    """Filter fragment (alias ``p``) shared by the ordinal-probe queries."""
    frag, params = "", {}
    if section_kinds:
        frag += " AND p.section_kind = ANY(:kinds)"
        params["kinds"] = section_kinds
    if layer:
        frag += " AND p.layer = :layer"
        params["layer"] = layer
    return frag, params


async def _min_ordinal_at_or_after(session: AsyncSession, doc_id: uuid.UUID, lo: int,
                                   frag: str, params: dict[str, Any]) -> int | None:
    """Lowest matching ordinal >= ``lo``, or None when nothing matches."""
    sql = text(f"SELECT min(p.ordinal) FROM passages p "
               f"WHERE p.document_id = :id AND p.ordinal >= :lo{frag}")
    return (await session.execute(sql, {"id": doc_id, "lo": lo, **params})).scalar()


async def _doc_passage_count(session: AsyncSession, doc_id: uuid.UUID) -> int:
    return (await session.execute(text(
        "SELECT count(*) FROM passages WHERE document_id = :id"), {"id": doc_id})).scalar() or 0


async def passage_context(session: AsyncSession, *, passage_id: str, before: int = 2,
                          after: int = 2) -> dict:
    pid = _uuid(passage_id, "passage_id")
    before = _clamp(before, 0, CONTEXT_MAX, 2)
    after = _clamp(after, 0, CONTEXT_MAX, 2)
    row = (await session.execute(text(
        "SELECT document_id, ordinal FROM passages WHERE id = :pid"), {"pid": pid})).first()
    if row is None:
        raise ToolInputError("Efnisgrein fannst ekki.")
    doc_id, ordinal = row[0], row[1]
    win = await _passage_window(session, doc_id, from_ordinal=max(0, ordinal - before),
                                to_ordinal=ordinal + after, section_kinds=None, layer=None)
    return {
        "doc_id": str(doc_id), "urlausn": win["urlausn"], "focus_ordinal": ordinal,
        "total_passages": win["total"],
        "passages": [compact_passage(p) for p in win["passages"]],
    }


async def get_passages_tool(session: AsyncSession, *, doc_id: str, from_ordinal: int = 0,
                            count: int = 20, section_kind: list[str] | None = None,
                            layer: str | None = None) -> dict:
    """One window of passages, pageable by following ``next_from_ordinal``.

    Ordinals are document-global, so with a ``layer``/``section_kind`` filter the
    requested ``from_ordinal`` usually points at a passage the filter excludes.
    We therefore skip forward to the first *matching* ordinal at or after it, and
    derive ``next_from_ordinal`` from the next matching ordinal past the window —
    never from the filtered count, which lives in a different space.
    """
    did = _uuid(doc_id, "doc_id")
    from_ordinal = _clamp(from_ordinal, 0, 1_000_000, 0)
    count = _clamp(count, 1, PASSAGES_COUNT_MAX, 20)
    section_kinds, layer = _validate_passage_filters(section_kind, layer)
    frag, fparams = _passage_filter_sql(section_kinds, layer)

    effective_from = await _min_ordinal_at_or_after(session, did, from_ordinal, frag, fparams)
    if effective_from is None:
        # Nothing matches at or after from_ordinal — or the document does not
        # exist at all, which the core call below reports as such.
        win = await _passage_window(session, did, from_ordinal=from_ordinal,
                                    to_ordinal=from_ordinal,
                                    section_kinds=section_kinds, layer=layer)
        return {"doc_id": str(did), "urlausn": win["urlausn"],
                "total_passages": await _doc_passage_count(session, did),
                "matching_passages": win["total"], "from_ordinal": from_ordinal,
                "passages": [], "next_from_ordinal": None}

    to_ordinal = effective_from + count - 1
    win = await _passage_window(session, did, from_ordinal=effective_from,
                                to_ordinal=to_ordinal,
                                section_kinds=section_kinds, layer=layer)
    return {"doc_id": str(did), "urlausn": win["urlausn"],
            "total_passages": await _doc_passage_count(session, did),
            "matching_passages": win["total"], "from_ordinal": effective_from,
            "passages": [compact_passage(p) for p in win["passages"]],
            "next_from_ordinal": await _min_ordinal_at_or_after(
                session, did, to_ordinal + 1, frag, fparams)}


# ── document ───────────────────────────────────────────────────────────────────

_OUTLINE_SQL = text("""
    SELECT layer, section_kind, min(ordinal) AS from_ordinal, max(ordinal) AS to_ordinal,
           count(*) AS passages
    FROM passages WHERE document_id = :id
    GROUP BY layer, section_kind ORDER BY min(ordinal)
""")


async def get_document_tool(session: AsyncSession, *, doc_id: str, max_chars: int = 0) -> dict:
    did = _uuid(doc_id, "doc_id")
    max_chars = _clamp(max_chars, 0, DOC_TEXT_MAX_CHARS, 0)
    try:
        doc = await get_document(session, did)
    except SearchError as exc:
        raise ToolInputError(f"Ógilt inntak: {exc}")
    if doc is None:
        raise ToolInputError("Skjal fannst ekki.")
    for k in ("raw_api_data", "body_text", "lower_body_text", "markdown"):
        doc.pop(k, None)
    rows = (await session.execute(_OUTLINE_SQL, {"id": did})).mappings().all()
    doc["outline"] = [dict(r) for r in rows]
    doc["total_passages"] = sum(r["passages"] for r in rows)
    doc["text"], doc["text_truncated"], doc["text_error"] = None, False, None
    if max_chars > 0:
        orm = await session.get(Document, did)
        md = None
        if orm is not None:
            try:
                md = to_markdown(orm, get_config(doc["source"]))
            except Exception as exc:  # rendering is derived data; report, don't fail
                md = None
                doc["text_error"] = f"{exc.__class__.__name__}: {str(exc)[:200]}"
        doc["text"], doc["text_truncated"] = truncate_text(md, max_chars)
    return jsonable(doc)


# ── sources / facets ───────────────────────────────────────────────────────────

_COUNTS_SQL = text("""
    SELECT s.short_name, d.verdict_type, count(d.id) AS n
    FROM sources s LEFT JOIN documents d ON d.source_id = s.id
    GROUP BY s.short_name, d.verdict_type
""")


async def list_sources(session: AsyncSession) -> dict:
    rows = (await session.execute(_COUNTS_SQL)).mappings().all()
    by_source: dict[str, int] = {}
    by_source_vt: dict[tuple[str, str], int] = {}
    for r in rows:
        by_source[r["short_name"]] = by_source.get(r["short_name"], 0) + (r["n"] or 0)
        if r["verdict_type"] is not None:
            by_source_vt[(r["short_name"], r["verdict_type"])] = r["n"] or 0
    tree = [annotate_counts(cat, by_source, by_source_vt) for cat in catalog()]
    flat = sorted(
        ({"short_name": sn,
          "display_name": (cfg := SOURCE_REGISTRY.get(sn)) and cfg.display_name or sn,
          "abbreviation": cfg.abbreviation if cfg else None,
          "count": n} for sn, n in by_source.items()),
        key=lambda s: -s["count"])
    return {"catalog": tree, "sources": flat, "total": sum(c["count"] for c in tree)}


def _flatten_groups(node: dict, out: dict[str, int]) -> None:
    out[node["key"]] = node["count"]
    for c in node.get("children", []):
        _flatten_groups(c, out)


async def facets(session: AsyncSession, *, q: str = "", mode: str = "keyword",
                 date_from: str | None = None, date_to: str | None = None) -> dict:
    try:
        by_source, by_source_vt = await facet_counts(
            session, q=q or "", mode=mode, date_from=_date(date_from, "date_from"),
            date_to=_date(date_to, "date_to"))
    except SearchError as exc:
        raise ToolInputError(f"Ógilt inntak: {exc}")
    by_group: dict[str, int] = {}
    for cat in catalog():
        _flatten_groups(annotate_counts(cat, by_source, by_source_vt), by_group)
    return {"by_source": dict(by_source), "by_group": by_group}


# ── schema / sql ───────────────────────────────────────────────────────────────

SQL_MAX_ROWS = 1000
SQL_DEFAULT_ROWS = 200
SQL_TIMEOUT = "15s"

_TABLE_DESCRIPTIONS = {
    "documents": "Eitt skjal á röð (dómur, úrskurður, ákvörðun, lagakafli). NORM-dálkar + raw_api_data (RAW, stórt JSONB).",
    "passages": "Efnisgreinar skjala, leitareining orðaleitar. (document_id, ordinal) einkvæmt; fts_is er GIN-vísað.",
    "sources": "Heimildir (short_name, display_name). documents.source_id → sources.id.",
    "document_links": "Tengingar milli skjala (t.d. málskotsbeiðni → Landsréttardómur), relation/confidence/method.",
    "alembic_version": "Skemaútgáfa (alembic).",
}
_TABLE_NOTES = {
    "documents": "raw_api_data er stórt JSONB (öll API-svörin); veldu einstaka lykla (raw_api_data->>'x') en aldrei dálkinn í heild. body_text/lower_body_text eru fullir textar; notaðu passages fyrir lesanleg brot.",
    "passages": "text getur verið langt; sía fyrst á document_id eða fts_is @@ to_tsquery('simple', …).",
}


async def describe_schema(session: AsyncSession, *, table: str | None = None) -> dict:
    if table is None:
        rows = (await session.execute(text("""
            SELECT c.relname AS name, c.reltuples::bigint AS approx_rows
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY c.relname"""))).mappings().all()
        return {"tables": [{"name": r["name"], "approx_rows": max(0, int(r["approx_rows"])),
                            "description": _TABLE_DESCRIPTIONS.get(r["name"], "")} for r in rows]}
    cols = (await session.execute(text("""
        SELECT column_name AS name, udt_name AS type, is_nullable = 'YES' AS nullable
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = :t ORDER BY ordinal_position"""), {"t": table})).mappings().all()
    if not cols:
        raise ToolInputError(f"Tafla fannst ekki: {table!r}. Kallaðu á describe_schema án table til að sjá lista.")
    idx = (await session.execute(text(
        "SELECT indexname AS name, indexdef AS definition FROM pg_indexes WHERE schemaname = 'public' AND tablename = :t ORDER BY indexname"),
        {"t": table})).mappings().all()
    return {"table": table, "description": _TABLE_DESCRIPTIONS.get(table, ""),
            "columns": [dict(c) for c in cols], "indexes": [dict(i) for i in idx],
            "notes": _TABLE_NOTES.get(table, "")}


async def sql_query(session: AsyncSession, *, sql: str, max_rows: int = SQL_DEFAULT_ROWS) -> dict:
    try:
        clean = validate_sql(sql)
    except SqlRejected as exc:
        raise ToolInputError(str(exc))
    max_rows = _clamp(max_rows, 1, SQL_MAX_ROWS, SQL_DEFAULT_ROWS)
    t0 = _time.perf_counter()
    try:
        async with session.begin():
            await session.execute(text("SET TRANSACTION READ ONLY"))
            await session.execute(text(f"SET LOCAL statement_timeout = '{SQL_TIMEOUT}'"))
            result = await session.execute(text(clean))
            columns = list(result.keys())
            raw = result.fetchmany(max_rows + 1)
    except Exception as exc:  # asyncpg errors arrive wrapped in sqlalchemy DBAPIError
        msg = str(getattr(exc, "orig", exc))
        low = msg.lower()
        if "canceling statement due to statement timeout" in low or "querycancelederror" in low:
            raise ToolInputError(f"Fyrirspurn féll á {SQL_TIMEOUT} tímamörkum.")
        if "permission denied" in low or "read-only transaction" in low or "insufficientprivilege" in low:
            raise ToolInputError("Lesaðgangshlutverkið má ekki gera þetta.")
        raise ToolInputError(f"Villa í fyrirspurn: {msg.splitlines()[0] if msg else exc.__class__.__name__}")
    elapsed = int((_time.perf_counter() - t0) * 1000)
    truncated_rows = len(raw) > max_rows
    raw = raw[:max_rows]
    rows, truncated_cells = [], 0
    for r in raw:
        out_row = []
        for v in r:
            val, cut = cell_value(v)
            truncated_cells += int(cut)
            out_row.append(val)
        rows.append(out_row)
    return {"columns": columns, "rows": rows, "row_count": len(rows),
            "truncated_rows": truncated_rows, "truncated_cells": truncated_cells, "elapsed_ms": elapsed}
