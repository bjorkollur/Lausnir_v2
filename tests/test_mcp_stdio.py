"""End-to-end over stdio: spawn the server exactly as a client would.

Requires DATABASE_URL_READONLY. The child inherits this process's environment,
so it resolves the URL the same way a real client launch does — nothing is
injected here that would paper over a missing variable.
"""
import json
import os
import sys

import pytest

_URL = os.environ.get("DATABASE_URL_READONLY")
pytestmark = pytest.mark.skipif(not _URL, reason="needs DATABASE_URL_READONLY")


async def test_stdio_roundtrip():
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    params = StdioServerParameters(command=sys.executable, args=["-m", "engine.mcp"],
                                   env=dict(os.environ), cwd=os.getcwd())
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = [t.name for t in (await session.list_tools()).tools]
            assert len(names) == 9 and "search" in names and "sql_query" in names and "citations" in names
            res = await session.call_tool("list_sources", {})
            assert res.is_error is False
            text = res.content[0].text
            assert "domstolar" in text

            # Honesty guard: the server process itself must be on lausnir_ro.
            who = await session.call_tool("sql_query", {"sql": "SELECT current_user AS u"})
            assert who.is_error is False
            assert json.loads(who.content[0].text)["rows"] == [["lausnir_ro"]]

            bad = await session.call_tool("sql_query", {"sql": "DELETE FROM documents"})
            assert bad.is_error is True and "SELECT" in bad.content[0].text
