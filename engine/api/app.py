"""FastAPI app exposing the Lausnir v2 search API.

Endpoints (all JSON, local-only, no auth):
  GET /api/sources          → curated groups + flat source list with doc counts
  GET /api/search           → keyword/regex search with scope + date filters
  GET /api/document/{id}     → full document incl. body, parties, appeal links, markdown

Run:  uv run uvicorn engine.api.app:app --reload
      (requires DATABASE_URL in the environment)
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from typing import AsyncIterator
import re as _re_law

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import engine.database.connection as _db_conn
from engine.config.sources import SOURCE_REGISTRY, get_config
from engine.config.source_groups import catalog
from engine.database.connection import init_db
from engine.database.models import Document, Source
from engine.processors.renderer import to_markdown
from engine.search.queries import (
    DEFAULT_PAGE_SIZE,
    REGEX_COLUMNS,
    SearchError,
    facet_counts,
    get_document,
    search_documents,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await init_db()
    yield


app = FastAPI(title="Lausnir Leitar-API", version="1.0", lifespan=lifespan)

# Local-only frontend: allow any localhost origin to call the API.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+)(:\d+)?",
    allow_methods=["GET"],
    allow_headers=["*"],
)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with _db_conn.AsyncSessionLocal() as session:
        yield session


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


def _annotate(node: dict, by_source: dict, by_source_vt: dict) -> dict:
    """Copy a scope-tree node with a document count attached."""
    out = {"key": node["key"], "label": node["label"]}
    if node.get("verdict_types"):
        out["count"] = sum(
            by_source_vt.get((s, vt), 0)
            for s in node["sources"] for vt in node["verdict_types"]
        )
    elif "children" in node:
        out["children"] = [_annotate(c, by_source, by_source_vt) for c in node["children"]]
        out["count"] = sum(c["count"] for c in out["children"])
    else:  # plain single-source leaf
        out["count"] = sum(by_source.get(s, 0) for s in node["sources"])
    return out


@app.get("/api/sources")
async def sources(session: AsyncSession = Depends(get_session)) -> dict:
    """Hierarchical scope catalog (Fons Juris layout) with document counts."""
    rows = (await session.execute(text("""
        SELECT s.short_name, d.verdict_type, count(d.id) AS n
        FROM sources s
        LEFT JOIN documents d ON d.source_id = s.id
        GROUP BY s.short_name, d.verdict_type
    """))).mappings().all()
    by_source: dict[str, int] = {}
    by_source_vt: dict[tuple[str, str], int] = {}
    for r in rows:
        by_source[r["short_name"]] = by_source.get(r["short_name"], 0) + (r["n"] or 0)
        if r["verdict_type"] is not None:
            by_source_vt[(r["short_name"], r["verdict_type"])] = r["n"] or 0

    tree = [_annotate(cat, by_source, by_source_vt) for cat in catalog()]

    # Flat source list (for single-source autocomplete / 'leita í einstaka stofnun').
    flat = sorted(
        (
            {
                "short_name": sn,
                "display_name": (cfg := SOURCE_REGISTRY.get(sn)) and cfg.display_name or sn,
                "abbreviation": cfg.abbreviation if cfg else None,
                "count": n,
            }
            for sn, n in by_source.items()
        ),
        key=lambda s: -s["count"],
    )
    return {
        "catalog": tree,
        "sources": flat,
        "regex_fields": list(REGEX_COLUMNS.keys()),
        "total": sum(cat["count"] for cat in tree),
    }


@app.get("/api/search")
async def search(
    q: str = Query("", description="Search text or regex pattern"),
    mode: str = Query("keyword", pattern="^(keyword|exact|prefix|substring|any|proximity|regex)$"),
    scope: list[str] | None = Query(None, description="Group labels, source short_names, or 'all'"),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    sort: str = Query("relevance", pattern="^(relevance|newest|oldest)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=100),
    regex_fields: list[str] | None = Query(None, description="Fields for regex mode"),
    proximity_n: int = Query(5, ge=1, le=50),
    provision: str | None = Query(None, description="Provision reference, e.g. '218. gr. 19/1940'"),
    keyword: str | None = Query(None, description="Filter by keywords/tags column only, substring match"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        res = await search_documents(
            session, q=q, mode=mode, scope=scope,
            date_from=date_from, date_to=date_to, sort=sort,
            page=page, page_size=page_size, regex_fields=regex_fields,
            proximity_n=proximity_n, provision=provision, keyword=keyword,
        )
    except SearchError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"total": res.total, "page": res.page, "page_size": res.page_size, "results": res.results}


@app.get("/api/facets")
async def facets(
    q: str = Query(""),
    mode: str = Query("keyword", pattern="^(keyword|exact|prefix|substring|any|proximity|regex)$"),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    regex_fields: list[str] | None = Query(None),
    proximity_n: int = Query(5, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Document counts per scope-tree node for the active query (facet sidebar)."""
    try:
        by_source, by_source_vt = await facet_counts(
            session, q=q, mode=mode, date_from=date_from, date_to=date_to,
            regex_fields=regex_fields, proximity_n=proximity_n,
        )
    except SearchError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    tree = [_annotate(cat, by_source, by_source_vt) for cat in catalog()]
    return {"catalog": tree, "total": sum(cat["count"] for cat in tree)}


@app.get("/api/document/{doc_id}")
async def document(
    doc_id: str,
    markdown: bool = Query(True, description="Include rendered markdown"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        doc = await get_document(session, doc_id)
    except SearchError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    if markdown:
        orm = await session.get(Document, doc["id"])
        if orm is not None:
            try:
                doc["markdown"] = to_markdown(orm, get_config(doc["source"]))
            except Exception:
                doc["markdown"] = None
    return doc


_LAW_FOOTNOTE_RE = _re_law.compile(r'^\[|\]\d+\)$')


def _clean_law_name(name: str | None) -> str | None:
    """Strip Alþingi footnote brackets: '[Lög um ...]1)' → 'Lög um ...'"""
    if not name:
        return name
    return _LAW_FOOTNOTE_RE.sub("", name.strip()).strip()


@app.get("/api/law/{doc_id}")
async def get_law(
    doc_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return a lagasafn document with structured provisions for LawPanel."""
    orm = await session.get(Document, doc_id)
    if orm is None:
        raise HTTPException(status_code=404, detail="Law not found")

    src = await session.get(Source, orm.source_id)
    if src is None or not src.short_name.startswith("lagasafn_"):
        raise HTTPException(status_code=404, detail="Document is not a law")

    kafli_num = int(src.short_name.split("_")[1])  # "lagasafn_01" → 1

    return {
        "id": str(orm.id),
        "case_number": orm.case_number,
        "law_name": _clean_law_name(orm.summary),
        "verdict_type": orm.verdict_type,
        "document_date": orm.document_date.isoformat() if orm.document_date else None,
        "url": orm.url,
        "kafli": kafli_num,
        "kafli_label": src.display_name,
        "provisions": orm.provisions or [],
    }


@app.get("/api/provision")
async def get_provision(
    law: str = Query(..., description="Law number, e.g. '33/1944'"),
    gr: int = Query(..., description="Article number (grein)"),
    gr_suffix: str | None = Query(None, description="Letter suffix for e.g. '218. gr. a.' → gr_suffix=a"),
    mgr: int | None = Query(None, description="Sub-article number (málsgrein), optional"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return the text of a specific article or sub-article of a law.

    Examples:
      /api/provision?law=19/1940&gr=218              → 218. gr. (full)
      /api/provision?law=19/1940&gr=218&mgr=1        → 218. gr. 1. mgr.
      /api/provision?law=19/1940&gr=218&gr_suffix=a  → 218. gr. a.
      /api/provision?law=19/1940&gr=218&gr_suffix=a&mgr=1 → 218. gr. a. 1. mgr.
    """
    from sqlalchemy import select as sa_select

    result = (await session.execute(
        sa_select(Document)
        .join(Source, Source.id == Document.source_id)
        .where(
            Document.case_number == law,
            Source.short_name.like("lagasafn_%"),
        )
        .limit(1)
    )).scalar_one_or_none()

    if result is None:
        raise HTTPException(status_code=404, detail=f"Law {law!r} not found")

    provisions = result.provisions or []
    # Match on both num and suffix (None suffix matches provisions without suffix key)
    sfx = gr_suffix.lower() if gr_suffix else None
    prov = next(
        (p for p in provisions
         if p.get("num") == gr and p.get("suffix") == sfx),
        None,
    )
    if prov is None:
        label = f"{gr}. gr." + (f" {sfx}." if sfx else "")
        raise HTTPException(
            status_code=404,
            detail=f"{label} not found in law {law!r} (has {len(provisions)} articles)",
        )

    if mgr is not None:
        subs = prov.get("sub") or []
        sub = next((s for s in subs if s.get("num") == mgr), None)
        if sub is None:
            label = f"{gr}. gr." + (f" {sfx}." if sfx else "")
            raise HTTPException(
                status_code=404,
                detail=f"{mgr}. mgr. of {label} not found in law {law!r} (has {len(subs)} sub-articles)",
            )
        return {
            "law": law,
            "law_name": result.summary,
            "article": gr,
            "article_suffix": sfx,
            "sub_article": mgr,
            "text": sub["text"],
            "url": result.url,
        }

    return {
        "law": law,
        "law_name": result.summary,
        "article": gr,
        "article_suffix": sfx,
        "text": prov["text"],
        "sub_articles": prov.get("sub"),
        "url": result.url,
    }
