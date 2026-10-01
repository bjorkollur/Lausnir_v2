"""update_all.py logs must outlive a reboot: /tmp is wiped on restart, and the
logs are the only record of what an import run did."""
from pathlib import Path

import engine.config.sources as sources_config


def test_logs_live_next_to_the_data_not_in_tmp():
    from scripts.update_all import _LOG_DIR
    assert _LOG_DIR == Path(sources_config.RAW_DIR).parent / "logs" / "update"
    assert not str(_LOG_DIR).startswith("/tmp")
