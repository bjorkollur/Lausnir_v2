# tests/test_mcp_readonly_role_db.py
"""Layer 1 of the SELECT guard: the role itself cannot write, independent of sqlguard."""
import os

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

_URL = os.environ.get("DATABASE_URL_READONLY")
pytestmark = pytest.mark.skipif(not _URL, reason="needs DATABASE_URL_READONLY")


async def test_role_cannot_write_even_without_guard():
    eng = create_async_engine(_URL)
    try:
        async with eng.connect() as conn:
            who = (await conn.execute(text("select current_user"))).scalar()
            assert who == "lausnir_ro"
            with pytest.raises(DBAPIError) as ei:
                await conn.execute(text("UPDATE sources SET display_name = display_name WHERE false"))
                await conn.commit()
            msg = str(ei.value).lower()
            assert "read-only" in msg or "permission denied" in msg
    finally:
        await eng.dispose()


async def test_role_cannot_create_table():
    eng = create_async_engine(_URL)
    try:
        async with eng.connect() as conn:
            with pytest.raises(DBAPIError):
                await conn.execute(text("CREATE TABLE zz_should_fail (id int)"))
    finally:
        await eng.dispose()


async def test_role_can_select():
    eng = create_async_engine(_URL)
    try:
        async with eng.connect() as conn:
            assert (await conn.execute(text("select count(*) from documents"))).scalar() > 0
    finally:
        await eng.dispose()


async def test_role_has_no_temp_privilege():
    """TEMP is granted to PUBLIC by default, so revoking it from lausnir_ro
    alone is a no-op — deploy/sql/create_readonly_role.sql revokes it from
    PUBLIC. Without this the role could materialise a corpus-sized temp table
    on the production volume."""
    eng = create_async_engine(_URL)
    try:
        async with eng.connect() as conn:
            has_temp = (await conn.execute(text(
                "SELECT has_database_privilege(current_user, current_database(), 'TEMP')"))).scalar()
            assert has_temp is False
        async with eng.connect() as conn:
            with pytest.raises(DBAPIError):
                await conn.execute(text("CREATE TEMP TABLE zz_tmp (id int)"))
    finally:
        await eng.dispose()
