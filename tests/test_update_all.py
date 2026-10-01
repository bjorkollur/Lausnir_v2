"""update_all.py logs must outlive a reboot: /tmp is wiped on restart, and the
logs are the only record of what an import run did."""
from pathlib import Path

import engine.config.sources as sources_config


def test_logs_live_next_to_the_data_not_in_tmp():
    from scripts.update_all import _LOG_DIR
    assert _LOG_DIR == Path(sources_config.RAW_DIR).parent / "logs" / "update"
    assert not str(_LOG_DIR).startswith("/tmp")


def test_refuses_to_run_without_database_url(monkeypatch, tmp_path):
    """The database listens on 5433. A fallback URL without a port sent every
    import to 5432 — another Postgres, or none — instead of failing."""
    import pytest
    import scripts.update_all as ua

    calls = []
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(ua, "_LOG_DIR", tmp_path)
    monkeypatch.setattr(ua, "_run_script", lambda *a, **k: calls.append(a) or (True, 0.0))
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        ua.run_all()
    assert calls == []
