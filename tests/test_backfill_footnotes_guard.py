import asyncio
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "backfill_footnotes", Path(__file__).resolve().parents[1] / "scripts" / "backfill_footnotes.py")
backfill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backfill)


@pytest.mark.parametrize("source", ["timarit_logfraedinga", "ulfljotur"])
def test_refuses_journal_sources(source):
    with pytest.raises(SystemExit) as exc:
        asyncio.run(backfill.main(source, True, None, False))
    assert "tímarit" in str(exc.value)
