"""End-to-end over stdio: spawn the server exactly as a client would."""
import os
import sys

import pytest

_URL = os.environ.get("DATABASE_URL_READONLY") or os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not _URL, reason="needs DATABASE_URL_READONLY or DATABASE_URL")


async def test_stdio_roundtrip():
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    env = dict(os.environ)
    env["DATABASE_URL_READONLY"] = _URL      # tests may run before the RO role exists
    params = StdioServerParameters(command=sys.executable, args=["-m", "engine.mcp"], env=env,
                                   cwd=os.getcwd())
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = [t.name for t in (await session.list_tools()).tools]
            assert len(names) == 8 and "search" in names and "sql_query" in names
            res = await session.call_tool("list_sources", {})
            assert res.is_error is False
            text = res.content[0].text
            assert "domstolar" in text
            bad = await session.call_tool("sql_query", {"sql": "DELETE FROM documents"})
            assert bad.is_error is True and "SELECT" in bad.content[0].text
