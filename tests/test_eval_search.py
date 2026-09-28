import importlib.util, sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("eval_search", Path("scripts/eval_search.py"))
m = importlib.util.module_from_spec(spec); sys.modules["eval_search"] = m; spec.loader.exec_module(m)

EXP = [{"source": "haestirettur", "case_number": "36/2022", "document_date": "2023-02-08"}]


def _r(src, cn, d):
    return {"source": src, "case_number": cn, "document_date": d}


def test_hit_and_reciprocal_rank():
    res = [_r("landsrettur", "1/2020", "2020-01-01"), _r("haestirettur", "36/2022", "2023-02-08")]
    assert m.score(EXP, res, k=10) == (True, 0.5)


def test_miss_gives_zero():
    assert m.score(EXP, [_r("x", "y", "2000-01-01")], k=10) == (False, 0.0)


def test_only_top_k_counts():
    res = [_r("x", str(i), "2000-01-01") for i in range(10)] + [_r("haestirettur", "36/2022", "2023-02-08")]
    assert m.score(EXP, res, k=10) == (False, 0.0)


def test_filter_set_all_returns_everything():
    golden = [{"id": "a", "set": "core"}, {"id": "b", "set": "auto"}]
    assert m.filter_set(golden, "all") == golden


def test_filter_set_core_and_auto():
    golden = [{"id": "a", "set": "core"}, {"id": "b", "set": "auto"}]
    assert m.filter_set(golden, "core") == [golden[0]]
    assert m.filter_set(golden, "auto") == [golden[1]]


def test_filter_set_defaults_missing_set_field_to_core():
    golden = [{"id": "a"}, {"id": "b", "set": "auto"}]
    assert m.filter_set(golden, "core") == [golden[0]]


def test_by_style_groups_recall_and_mrr_per_style():
    golden = [{"id": "a", "style": "short"}, {"id": "b", "style": "medium"}, {"id": "c", "style": "short"}]
    per_query = {
        "a": {"id": "a", "hit": True, "rr": 1.0, "total": 1},
        "b": {"id": "b", "hit": False, "rr": 0.0, "total": 0},
        "c": {"id": "c", "hit": True, "rr": 0.5, "total": 2},
    }
    grouped = m.by_style(per_query, golden)
    assert grouped["short"]["n"] == 2
    assert grouped["short"]["recall"] == 1.0
    assert grouped["short"]["mrr"] == 0.75
    assert grouped["medium"]["n"] == 1
    assert grouped["medium"]["recall"] == 0.0


def test_by_style_defaults_missing_style_to_medium():
    golden = [{"id": "a"}]
    per_query = {"a": {"id": "a", "hit": True, "rr": 1.0, "total": 1}}
    grouped = m.by_style(per_query, golden)
    assert set(grouped.keys()) == {"medium"}


def test_relax_sweep_values():
    assert m.RELAX_SWEEP_VALUES == (0, 5, 10, 20, 50)


def test_evaluate_per_query_record_includes_relaxed(monkeypatch):
    """evaluate()'s per-query record must carry `relaxed` from res.relaxed so
    --relax-sweep can count how many queries relaxed at each threshold."""
    import asyncio
    from types import SimpleNamespace

    golden = [{"id": "a", "question": "q", "expected": []}]

    class FakeResults:
        def __init__(self, relaxed):
            self.results = []
            self.total = 0
            self.relaxed = relaxed

    async def fake_search_documents(session, q, mode, scope=None, page_size=10):
        return FakeResults(relaxed=True)

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(
        "engine.search.queries.search_documents", fake_search_documents
    )
    monkeypatch.setattr(
        "engine.database.connection.init_db", lambda: asyncio.sleep(0)
    )
    monkeypatch.setattr(
        "engine.database.connection.AsyncSessionLocal", lambda: FakeSession()
    )

    result = asyncio.run(m.evaluate(golden, k=10))
    assert result["per_query"]["a"]["relaxed"] is True
