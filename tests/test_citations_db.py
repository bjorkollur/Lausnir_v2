"""rebuild_citations against the live DB (read-only role for reads; writes happen
inside a transaction that is rolled back). Skipped until alembic 0004 exists."""
import datetime as dt
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from engine.processors.citation_build import STALE_WHERE, rebuild_citations
from engine.processors.citation_resolver import CitationIndex

_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not _URL, reason="needs DATABASE_URL (writes are rolled back)")


async def _conn():
    eng = create_async_engine(_URL)
    conn = await eng.connect()
    await conn.begin()
    # Both halves of 0004 are checked, not just the table: a stray `citations`
    # table already exists in the live DB (Base.metadata.create_all from an
    # earlier run made it, empty, while alembic is still at 0003), so the table
    # alone does not mean the migration ran. documents.citation_hash does.
    ready = (await conn.execute(text("""
        SELECT to_regclass('public.citations') IS NOT NULL
           AND EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name = 'documents' AND column_name = 'citation_hash')"""))).scalar()
    if not ready:
        await conn.rollback(); await conn.close(); await eng.dispose()
        pytest.skip("alembic 0004 not applied yet")
    return eng, conn


async def _pick_doc(conn):
    row = (await conn.execute(text("""
        SELECT d.id, d.summary, d.body_text, d.lower_body_text, d.document_date
        FROM documents d JOIN sources s ON s.id = d.source_id
        WHERE s.short_name = 'haestirettur' AND d.body_text ILIKE '%í máli nr.%'
          AND d.document_date >= '2019-01-01' ORDER BY d.id LIMIT 1"""))).first()
    if row is None:
        pytest.skip("no haestirettur document containing 'í máli nr.'")
    return row


async def test_rebuild_writes_rows_and_edges_and_is_idempotent():
    eng, conn = await _conn()
    try:
        idx = await CitationIndex.load(conn)
        d = await _pick_doc(conn)
        n1, e1 = await rebuild_citations(conn, d.id, summary=d.summary, body=d.body_text, lower=d.lower_body_text,
                                         doc_date=d.document_date, index=idx)
        assert n1 > 0
        n2, e2 = await rebuild_citations(conn, d.id, summary=d.summary, body=d.body_text, lower=d.lower_body_text,
                                         doc_date=d.document_date, index=idx)
        assert (n1, e1) == (n2, e2)
        rows = (await conn.execute(text("SELECT layer, char_start, char_end, status, to_doc_id FROM citations WHERE from_doc_id=:id"), {"id": d.id})).all()
        assert len(rows) == n1
        edges = (await conn.execute(text("SELECT count(*) FROM document_links WHERE from_doc_id=:id AND relation='cites'"), {"id": d.id})).scalar()
        resolved_targets = {r.to_doc_id for r in rows if r.status == "resolved" and r.layer in ("summary", "body")}
        assert edges == e1 == len(resolved_targets)
        # passage lookup at read time works for every row
        for layer, cs, ce, *_ in rows:
            pid = (await conn.execute(text("""SELECT id FROM passages WHERE document_id=:id AND layer=:layer AND char_start <= :pos
                                             ORDER BY char_start DESC LIMIT 1"""), {"id": d.id, "layer": layer, "pos": cs})).scalar()
            assert pid is not None
        # citation_hash written and document no longer stale
        stale = (await conn.execute(text(f"SELECT count(*) FROM documents d WHERE d.id=:id AND {STALE_WHERE}"), {"id": d.id})).scalar()
        assert stale == 0
    finally:
        await conn.rollback(); await conn.close(); await eng.dispose()


async def test_rebuild_is_scoped():
    eng, conn = await _conn()
    try:
        idx = await CitationIndex.load(conn)
        d = await _pick_doc(conn)
        before_links = (await conn.execute(text("SELECT count(*) FROM document_links WHERE relation <> 'cites'"))).scalar()
        other = (await conn.execute(text("SELECT count(*) FROM citations WHERE from_doc_id <> :id"), {"id": d.id})).scalar()
        # A sentinel row owned by a DIFFERENT document: rebuild_citations must not
        # see it, let alone delete it. Rolled back with the rest of the transaction.
        sentinel_doc = (await conn.execute(text("SELECT id FROM documents WHERE id <> :id LIMIT 1"), {"id": d.id})).scalar()
        sentinel_id = uuid.uuid4()
        await conn.execute(text("""
            INSERT INTO citations (id, from_doc_id, layer, char_start, char_end, raw_text, target_court, status)
            VALUES (:cid, :did, 'body', 0, 1, 'x', 'Hrd.', 'unresolved')"""),
            {"cid": sentinel_id, "did": sentinel_doc})
        await rebuild_citations(conn, d.id, summary=d.summary, body=d.body_text, lower=d.lower_body_text, doc_date=d.document_date, index=idx)
        assert (await conn.execute(text("SELECT count(*) FROM document_links WHERE relation <> 'cites'"))).scalar() == before_links
        assert (await conn.execute(text("SELECT count(*) FROM citations WHERE from_doc_id <> :id"), {"id": d.id})).scalar() == other + 1
        assert (await conn.execute(text("SELECT count(*) FROM citations WHERE id = :cid"), {"cid": sentinel_id})).scalar() == 1
    finally:
        await conn.rollback(); await conn.close(); await eng.dispose()


async def test_document_without_text():
    eng, conn = await _conn()
    try:
        idx = CitationIndex([])
        did = (await conn.execute(text("SELECT id FROM documents WHERE body_text IS NULL AND summary IS NULL AND lower_body_text IS NULL LIMIT 1"))).scalar()
        if did is None:
            pytest.skip("no textless document")
        n, e = await rebuild_citations(conn, did, summary=None, body=None, lower=None, doc_date=None, index=idx)
        assert (n, e) == (0, 0)
        assert (await conn.execute(text("SELECT citation_hash FROM documents WHERE id=:id"), {"id": did})).scalar()
    finally:
        await conn.rollback(); await conn.close(); await eng.dispose()


async def test_no_edge_points_to_a_later_document():
    eng, conn = await _conn()
    try:
        idx = await CitationIndex.load(conn)
        d = await _pick_doc(conn)
        await rebuild_citations(conn, d.id, summary=d.summary, body=d.body_text, lower=d.lower_body_text, doc_date=d.document_date, index=idx)
        bad = (await conn.execute(text("""SELECT count(*) FROM document_links dl JOIN documents a ON a.id=dl.from_doc_id
                                         JOIN documents b ON b.id=dl.to_doc_id WHERE dl.relation='cites' AND dl.from_doc_id=:id
                                         AND b.document_date > a.document_date"""), {"id": d.id})).scalar()
        assert bad == 0
    finally:
        await conn.rollback(); await conn.close(); await eng.dispose()
