"""get_document / get_citations against the live DB. Read-only.

Skipped until the citations table actually holds resolved rows — the table
exists (empty) in the live DB long before build_citations.py has run, so the
table alone is not enough to tell the test there is anything to assert on.
"""
import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from engine.search.queries import get_citations, get_document

_URL = os.environ.get("DATABASE_URL_READONLY") or os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not _URL, reason="needs DATABASE_URL")


async def _conn():
    eng = create_async_engine(_URL)
    conn = await eng.connect()
    await conn.begin()
    if not (await conn.execute(text("SELECT to_regclass('public.citations')"))).scalar():
        await conn.rollback(); await conn.close(); await eng.dispose()
        pytest.skip("citations table does not exist yet")
    if not (await conn.execute(text(
            "SELECT 1 FROM citations WHERE status = 'resolved' LIMIT 1"))).scalar():
        await conn.rollback(); await conn.close(); await eng.dispose()
        pytest.skip("no resolved citations in the table yet")
    return eng, conn


async def test_get_document_exposes_resolved_out_citations():
    eng, conn = await _conn()
    try:
        doc_id = (await conn.execute(text(
            "SELECT from_doc_id FROM citations WHERE status='resolved' AND layer='body' LIMIT 1"
        ))).scalar()
        doc = await get_document(conn, doc_id)
        assert doc is not None
        assert doc["citations_out_total"] >= 1
        assert isinstance(doc["cited_by_total"], int)
        assert isinstance(doc["citations_unresolved_total"], int)
        for item in doc["citations_out"]:
            assert item["urlausn"] and item["raw_text"]
            # Every body/summary citation sits inside a passage of the citing
            # document, so the LATERAL lookup must find one.
            assert item["passage_id"] and item["anchor"]
            assert item["document_id"] != str(doc["id"])   # 'self' is never resolved
    finally:
        await conn.rollback(); await eng.dispose()


async def test_in_direction_finds_the_citing_document_back():
    eng, conn = await _conn()
    try:
        doc_id = (await conn.execute(text(
            "SELECT from_doc_id FROM citations WHERE status='resolved' AND layer='body' LIMIT 1"
        ))).scalar()
        doc = await get_document(conn, doc_id)
        cited = doc["citations_out"][0]["document_id"]

        page, found, seen = 1, False, 0
        while True:
            res = await get_citations(conn, cited, direction="in", page=page, page_size=50)
            assert res["direction"] == "in"
            if any(i["document_id"] == str(doc["id"]) for i in res["items"]):
                found = True
                break
            seen += len(res["items"])
            if seen >= res["total"] or not res["items"]:
                break
            page += 1
        assert found, f"{doc['id']} cites {cited} but is missing from its cited_by"
    finally:
        await conn.rollback(); await eng.dispose()
