"""init_db must accept an explicit URL and be able to skip create_all — the
MCP server connects with a SELECT-only role that cannot run DDL."""
import pytest

import engine.database.connection as conn


class _FakeConn:
    def __init__(self, log): self.log = log
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def run_sync(self, fn): self.log.append(("run_sync", fn.__name__))


class _FakeEngine:
    def __init__(self, url, log): self.url = url; self.log = log
    def begin(self): return _FakeConn(self.log)


@pytest.fixture(autouse=True)
def _restore_module_globals(monkeypatch):
    """init_db rebinds the module-level `_engine`/`AsyncSessionLocal`; leaving a
    fake engine behind there leaks into every later test in the session.
    monkeypatch.setattr restores both when the test ends."""
    monkeypatch.setattr(conn, "_engine", conn._engine, raising=False)
    monkeypatch.setattr(conn, "AsyncSessionLocal", conn.AsyncSessionLocal, raising=False)


async def test_init_db_explicit_url_and_no_create_tables(monkeypatch):
    log: list = []
    created: dict = {}
    def fake_create(url, **kw):
        created["url"] = url; created["kw"] = kw
        return _FakeEngine(url, log)
    monkeypatch.setattr(conn, "create_async_engine", fake_create)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    await conn.init_db(url="postgresql+asyncpg://ro@localhost/x", create_tables=False)
    assert created["url"] == "postgresql+asyncpg://ro@localhost/x"
    assert created["kw"]["connect_args"] == {"server_settings": {"plan_cache_mode": "force_custom_plan"}}
    assert log == []                      # no create_all
    assert conn.AsyncSessionLocal is not None


async def test_init_db_default_still_reads_env_and_creates(monkeypatch):
    log: list = []
    monkeypatch.setattr(conn, "create_async_engine", lambda url, **kw: _FakeEngine(url, log))
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://rw@localhost/y")
    await conn.init_db()
    assert log == [("run_sync", "create_all")]


async def test_init_db_without_url_or_env_raises(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError):
        await conn.init_db(create_tables=False)
