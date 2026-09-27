"""DB-backed checks for rebuild_passages. Skipped without DATABASE_URL.
Everything runs inside a transaction that is rolled back."""
import os
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from engine.search.passage_index import (
    PASSAGE_HASH_SQL, STALE_WHERE, passage_hash, rebuild_passages,
)

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs DATABASE_URL")


async def _conn():
    eng = create_async_engine(os.environ["DATABASE_URL"])
    conn = await eng.connect()
    await conn.begin()
    return eng, conn


async def test_sql_md5_equals_python_md5_on_icelandic_text():
    eng, conn = await _conn()
    try:
        s, b, l = "Reifun með ð, þ og æ", "Meginmál – „gæsluvarðhald“", None
        row = (await conn.execute(text(
            "SELECT " + PASSAGE_HASH_SQL.replace("d.summary", ":s").replace("d.body_text", ":b")
            .replace("d.lower_body_text", ":l")), {"s": s, "b": b, "l": l})).scalar()
        assert row == passage_hash(s, b, l)
    finally:
        await conn.rollback(); await eng.dispose()


async def test_rebuild_is_idempotent_sets_hash_and_clears_staleness():
    eng, conn = await _conn()
    try:
        doc_id = (await conn.execute(text("""
            SELECT d.id FROM documents d JOIN sources s ON s.id=d.source_id
            WHERE s.short_name='landsrettur' AND d.body_text IS NOT NULL AND d.summary IS NOT NULL
            LIMIT 1"""))).scalar()
        n1 = await rebuild_passages(conn, doc_id)
        n2 = await rebuild_passages(conn, doc_id)
        assert n1 == n2 > 1
        cnt = (await conn.execute(text("SELECT count(*) FROM passages WHERE document_id=:id"), {"id": doc_id})).scalar()
        assert cnt == n1
        stale = (await conn.execute(text(f"SELECT count(*) FROM documents d WHERE d.id=:id AND {STALE_WHERE}"), {"id": doc_id})).scalar()
        assert stale == 0
        layers = (await conn.execute(text(
            "SELECT layer FROM passages WHERE document_id=:id ORDER BY ordinal"), {"id": doc_id})).scalars().all()
        assert layers[0] == "summary" and "body" in layers
        assert layers == sorted(layers, key=("summary", "body", "lower_body").index)
    finally:
        await conn.rollback(); await eng.dispose()


async def test_summary_only_document_gets_one_reifun_passage():
    eng, conn = await _conn()
    try:
        doc_id = (await conn.execute(text("""
            SELECT id FROM documents WHERE body_text IS NULL AND lower_body_text IS NULL
              AND summary IS NOT NULL AND length(summary) BETWEEN 50 AND 1500 LIMIT 1"""))).scalar()
        if doc_id is None:
            pytest.skip("no summary-only document in this DB")
        assert await rebuild_passages(conn, doc_id) == 1
        kind = (await conn.execute(text("SELECT section_kind FROM passages WHERE document_id=:id"), {"id": doc_id})).scalar()
        assert kind == "reifun"
    finally:
        await conn.rollback(); await eng.dispose()


async def test_lagasafn_gets_passages():
    """F1: lagasafn is segmented like any other source — its summary (law name)
    becomes a 'reifun' passage and its body_text (## N. gr. headings) is segmented."""
    eng, conn = await _conn()
    try:
        doc_id = (await conn.execute(text("""
            SELECT d.id FROM documents d JOIN sources s ON s.id=d.source_id
            WHERE s.short_name LIKE 'lagasafn_%'
              AND (d.body_text IS NOT NULL OR d.summary IS NOT NULL) LIMIT 1"""))).scalar()
        if doc_id is None:
            pytest.skip("no lagasafn document with text in this DB")
        n = await rebuild_passages(conn, doc_id)
        assert n >= 1
        h = (await conn.execute(text("SELECT passage_hash FROM documents WHERE id=:id"), {"id": doc_id})).scalar()
        assert h and len(h) == 32
    finally:
        await conn.rollback(); await eng.dispose()
