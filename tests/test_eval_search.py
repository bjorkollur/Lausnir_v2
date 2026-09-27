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
