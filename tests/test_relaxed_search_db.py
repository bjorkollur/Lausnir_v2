"""DB-backed check for relaxed keyword search (M8, final review of
feat/relaxed-search). Skipped without DATABASE_URL. Runs inside a transaction
that is rolled back — same pattern as tests/test_passage_index_db.py."""
import os
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from engine.search import relaxation
from engine.search.queries import search_documents

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs DATABASE_URL")


async def _conn():
    eng = create_async_engine(os.environ["DATABASE_URL"])
    conn = await eng.connect()
    await conn.begin()
    return eng, conn


async def test_relaxed_keyword_search_on_real_data():
    """A rare four-lemma legal-jargon query, chosen to have a strict (all-
    lemmas) match count below RELAX_BELOW on the real corpus, scoped to
    domstolar. Proves end to end: relaxation actually fires against live data,
    the returned page is ordered tier-first (non-decreasing match_tier), and
    both documented bounds hold — total never exceeds what the candidate cap
    can reach, and strict_total is (by definition of why relaxation fired)
    below RELAX_BELOW."""
    eng, conn = await _conn()
    try:
        res = await search_documents(
            conn, q="tómlæti verklaun byggingarframkvæmdir gjaldþrot",
            scope=["domstolar"], page_size=20)
        assert res.relaxed is True
        tiers = [r["match_tier"] for r in res.results]
        assert tiers == sorted(tiers), f"match_tier must be non-decreasing across the page: {tiers}"
        assert res.total <= res.strict_total + relaxation.RELAX_CAND_LIMIT
        assert res.strict_total <= relaxation.RELAX_BELOW
    finally:
        await conn.rollback()
        await eng.dispose()
