"""MCP tool implementations as plain async functions (spec §4).

Every function takes an AsyncSession first, returns a JSON-safe dict, and
raises ToolInputError for anything the LLM should see as a tool error.
server.py wraps these; nothing here imports the mcp SDK, so the logic is
testable without a protocol layer.
"""
from __future__ import annotations

import datetime as _dt
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from engine.config.source_groups import annotate_counts, catalog, resolve_scope
from engine.config.sources import SOURCE_REGISTRY, get_config
from engine.database.models import Document
from engine.mcp.shaping import compact_passage, compact_search_result, truncate_text
from engine.processors.renderer import to_markdown
from engine.search.passage_search import get_passages
from engine.search.queries import SearchError, facet_counts, get_document, search_documents

PAGE_SIZE_MAX = 25
PAGE_SIZE_DEFAULT = 10
CONTEXT_MAX = 10
PASSAGES_COUNT_MAX = 50
DOC_TEXT_MAX_CHARS = 40_000

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
        raise ToolInputError(str(exc))
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
        raise ToolInputError(str(exc))
    if win is None:
        raise ToolInputError("Skjal fannst ekki.")
    return win


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
    did = _uuid(doc_id, "doc_id")
    from_ordinal = _clamp(from_ordinal, 0, 1_000_000, 0)
    count = _clamp(count, 1, PASSAGES_COUNT_MAX, 20)
    win = await _passage_window(session, did, from_ordinal=from_ordinal,
                                to_ordinal=from_ordinal + count - 1,
                                section_kinds=section_kind, layer=layer)
    passages = [compact_passage(p) for p in win["passages"]]
    last = passages[-1]["ordinal"] if passages else None
    next_from = last + 1 if last is not None and last + 1 < win["total"] else None
    return {"doc_id": str(did), "urlausn": win["urlausn"], "total_passages": win["total"],
            "passages": passages, "next_from_ordinal": next_from}


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
        raise ToolInputError(str(exc))
    if doc is None:
        raise ToolInputError("Skjal fannst ekki.")
    for k in ("raw_api_data", "body_text", "lower_body_text", "markdown"):
        doc.pop(k, None)
    rows = (await session.execute(_OUTLINE_SQL, {"id": did})).mappings().all()
    doc["outline"] = [dict(r) for r in rows]
    doc["total_passages"] = sum(r["passages"] for r in rows)
    doc["text"], doc["text_truncated"] = None, False
    if max_chars > 0:
        orm = await session.get(Document, did)
        md = None
        if orm is not None:
            try:
                md = to_markdown(orm, get_config(doc["source"]))
            except Exception:
                md = None
        doc["text"], doc["text_truncated"] = truncate_text(md, max_chars)
    return _jsonable(doc)


def _jsonable(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.isoformat()
    return v


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
        raise ToolInputError(str(exc))
    by_group: dict[str, int] = {}
    for cat in catalog():
        _flatten_groups(annotate_counts(cat, by_source, by_source_vt), by_group)
    return {"by_source": dict(by_source), "by_group": by_group}
