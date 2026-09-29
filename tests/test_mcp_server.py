import subprocess
import sys

import pytest

from engine.mcp import server as srv

EXPECTED = ("search", "passage_context", "get_passages", "get_document",
            "list_sources", "facets", "describe_schema", "sql_query")


def test_import_writes_nothing_to_stdout():
    """Stdout is the MCP channel — any stray print at import time corrupts the
    protocol. Run in a fresh interpreter so module-level side effects count."""
    p = subprocess.run([sys.executable, "-c", "import engine.mcp.server"], capture_output=True, text=True,
                       cwd=str(srv.REPO_ROOT))
    assert p.returncode == 0, p.stderr
    assert p.stdout == ""


# This test is the drift guard that pins srv.TOOL_NAMES to what build_server()
# actually registers — TOOL_NAMES is the documented contract, nothing enforces it
# at runtime (the SDK's tool registry is private).
async def test_list_tools_names_and_annotations():
    s = srv.build_server()
    tools = await s.list_tools()
    assert tuple(t.name for t in tools) == EXPECTED == srv.TOOL_NAMES
    assert all(t.annotations is not None and t.annotations.read_only_hint is True for t in tools)
    assert all(t.description for t in tools)
    schema = {t.name: t.input_schema for t in tools}
    assert schema["search"]["required"] == ["q"]
    assert schema["sql_query"]["required"] == ["sql"]
    assert schema["list_sources"].get("required", []) == []
    assert srv.INSTRUCTIONS and "urlausn" in srv.INSTRUCTIONS
    # The enum-valued parameters must be discoverable from the schema alone —
    # an LLM should not have to guess `mode`/`sort`/`layer` from prose.
    assert "keyword" in schema["search"]["properties"]["mode"]["enum"]
    assert set(schema["search"]["properties"]["mode"]["enum"]) == {
        "keyword", "exact", "prefix", "substring", "any", "proximity", "regex"}
    assert schema["search"]["properties"]["sort"]["enum"] == ["relevance", "newest", "oldest"]
    assert "keyword" in schema["facets"]["properties"]["mode"]["enum"]
    # Optional params come out as anyOf[<enum>, null].
    layer_enum = next(b["enum"] for b in schema["get_passages"]["properties"]["layer"]["anyOf"]
                      if "enum" in b)
    assert layer_enum == ["summary", "body", "lower_body"]
    # …and the prose lists the sort values too.
    search_desc = next(t.description for t in tools if t.name == "search")
    assert "relevance" in search_desc and "newest" in search_desc and "oldest" in search_desc


def test_main_requires_readonly_url(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL_READONLY", raising=False)
    monkeypatch.setattr(srv, "_load_env", lambda: None)
    assert srv.main([]) == 2
    out, err = capsys.readouterr()
    assert out == "" and "DATABASE_URL_READONLY" in err


async def test_tool_error_wrapping(monkeypatch):
    """ToolInputError from tools must surface as an MCP ToolError (is_error), never as a crash."""
    from mcp.server.mcpserver.exceptions import ToolError
    async def fake_search(session, **kw):
        from engine.mcp.tools import ToolInputError
        raise ToolInputError("prófvilla")
    monkeypatch.setattr(srv.tools, "search", fake_search)
    class _S:
        async def __aenter__(self): return object()
        async def __aexit__(self, *a): return False
    monkeypatch.setattr(srv, "_session_factory", lambda: _S())
    s = srv.build_server()
    with pytest.raises(ToolError) as ei:
        await s.call_tool("search", {"q": "x"})
    assert "prófvilla" in str(ei.value)


async def test_lifespan_refuses_empty_readonly_url(monkeypatch):
    """An empty DATABASE_URL_READONLY must not fall through to DATABASE_URL:
    init_db(url="") would silently open the read-write connection."""
    calls = []

    async def recorder(*a, **kw):
        calls.append((a, kw))

    monkeypatch.setattr(srv._db, "init_db", recorder)
    monkeypatch.setenv("DATABASE_URL_READONLY", "")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user@localhost/lausnir")
    with pytest.raises(RuntimeError) as ei:
        async with srv._lifespan(srv.build_server()):
            pass
    assert "DATABASE_URL_READONLY" in str(ei.value)
    assert calls == []


async def _call_search_raising(monkeypatch, exc):
    async def boom(session, **kw):
        raise exc
    monkeypatch.setattr(srv.tools, "search", boom)

    class _S:
        async def __aenter__(self): return object()
        async def __aexit__(self, *a): return False

    monkeypatch.setattr(srv, "_session_factory", lambda: _S())
    from mcp.server.mcpserver.exceptions import ToolError
    s = srv.build_server()
    with pytest.raises(ToolError) as ei:
        await s.call_tool("search", {"q": "x"})
    return str(ei.value)


async def test_db_error_is_reported_as_a_connection_failure(monkeypatch):
    from sqlalchemy.exc import OperationalError
    msg = await _call_search_raising(
        monkeypatch, OperationalError("SELECT 1", {}, Exception("connection refused")))
    assert "Gagnagrunnstenging brást" in msg and "OperationalError" in msg


async def test_other_errors_are_not_blamed_on_the_database(monkeypatch):
    msg = await _call_search_raising(monkeypatch, KeyError("reifun"))
    assert "Gagnagrunnstenging" not in msg
    assert "Óvænt villa í verkfæri" in msg and "KeyError" in msg


async def test_bare_oserror_is_reported_as_a_connection_failure(monkeypatch):
    """asyncpg raises a bare OSError (ConnectionRefusedError/gaierror) when it
    cannot reach the server at all — that is a connection failure, not a bug."""
    msg = await _call_search_raising(
        monkeypatch, ConnectionRefusedError(61, "Connection refused"))
    assert "Gagnagrunnstenging brást" in msg and "ConnectionRefusedError" in msg


class _StubResult:
    def __init__(self, row): self._row = row
    def one(self): return self._row
    def first(self): return self._row


class _StubSession:
    def __init__(self, row): self._row = row
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def execute(self, *a, **kw): return _StubResult(self._row)


def _stub_db(monkeypatch, row):
    calls = []

    async def recorder(*a, **kw):
        calls.append((a, kw))

    monkeypatch.setattr(srv._db, "init_db", recorder)
    monkeypatch.setattr(srv._db, "AsyncSessionLocal", lambda: _StubSession(row))
    monkeypatch.setenv("DATABASE_URL_READONLY", "postgresql+asyncpg://ro@localhost/lausnir_v2")
    return calls


async def test_lifespan_probe_accepts_a_read_only_role(monkeypatch):
    calls = _stub_db(monkeypatch, ("lausnir_ro", "on"))
    async with srv._lifespan(srv.build_server()):
        pass
    assert len(calls) == 1 and calls[0][1]["create_tables"] is False


async def test_lifespan_probe_refuses_a_read_write_role(monkeypatch):
    """A URL pointing at the read-write role must be caught at startup, not on
    the first tool call."""
    _stub_db(monkeypatch, ("geiri", "off"))
    with pytest.raises(RuntimeError) as ei:
        async with srv._lifespan(srv.build_server()):
            pass
    assert str(ei.value) == srv.NOT_READ_ONLY_MSG
    assert "default_transaction_read_only" in str(ei.value)
