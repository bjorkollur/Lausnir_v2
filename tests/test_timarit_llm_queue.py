# tests/test_timarit_llm_queue.py
"""Task 22a: scripts/timarit_llm_queue.py — the session's view of the offline Claude fallback queue
(list / show / answer), over a cache directory in tmp_path. No network, no database, no archive."""
import json

import pytest

from engine.timarit.llm_toc import PROMPT, cache_key, pending_entries, toc_via_llm
from scripts import timarit_llm_queue as q

CONTEXT = {"manifest": "data/timarit/manifests/timarit_logfraedinga/2010_60_1.yaml", "source": "timarit_logfraedinga",
           "year": 2010, "volume": 60, "issue": "1", "label": "Tímarit lögfræðinga, 60. árg. 1. hefti (2010)"}
ANSWER = [{"title": "Fyrsta greinin", "authors": ["Jón Jónsson"], "page_start": 1, "article_type": "Fræðigrein"}]


@pytest.fixture
def llm_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(q.C, "CACHE_DIR", tmp_path / "cache")
    return tmp_path / "cache" / "llm"


async def _queue(llm_dir, text="EFNISYFIRLIT\n1 Fyrsta greinin", context=CONTEXT):
    await toc_via_llm(text, cache_dir=llm_dir, offline=True, context=context)
    return cache_key(text)


async def test_list_prints_key_label_and_size_in_icelandic(llm_dir, capsys):
    assert q.main(["list"]) == 0
    assert "tóm" in capsys.readouterr().out                                 # nothing queued: says so
    key = await _queue(llm_dir)
    assert q.main(["list"]) == 0
    out = capsys.readouterr().out
    assert key in out and CONTEXT["label"] in out and f"{len('EFNISYFIRLIT' + chr(10) + '1 Fyrsta greinin')} stafir" in out


async def test_list_without_context_still_prints_the_key(llm_dir, capsys):
    key = await _queue(llm_dir, context=None)
    assert q.main(["list"]) == 0
    assert key in capsys.readouterr().out


async def test_show_prints_the_prompt_followed_by_the_queued_text(llm_dir, capsys):
    key = await _queue(llm_dir)
    assert q.main(["show", key]) == 0
    assert capsys.readouterr().out == PROMPT + "EFNISYFIRLIT\n1 Fyrsta greinin"


async def test_a_unique_prefix_of_at_least_six_characters_names_the_key(llm_dir, capsys):
    key = await _queue(llm_dir)
    assert q.main(["show", key[:8]]) == 0
    assert capsys.readouterr().out.endswith("1 Fyrsta greinin")
    assert q.main(["show", key[:5]]) == 1
    assert "stutt" in capsys.readouterr().err


def _fake_pending(llm_dir, key):
    pdir = llm_dir / "pending"
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / f"{key}.txt").write_text("texti", encoding="utf-8")
    (pdir / f"{key}.json").write_text(json.dumps({"key": key, "context": None, "chars": 5, "queued_at": "2026-10-03T12:00:00+00:00"}),
                                      encoding="utf-8")


def test_an_unknown_or_ambiguous_key_is_an_error_and_prints_nothing_to_stdout(llm_dir, capsys):
    _fake_pending(llm_dir, "abcdef" + "0" * 34)
    _fake_pending(llm_dir, "abcdef" + "1" * 34)
    assert q.main(["show", "0" * 40]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "finnst ekki" in captured.err
    assert q.main(["show", "abcdef"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "fleiri en einn" in captured.err
    assert q.main(["show", "abcdef0"]) == 0 and capsys.readouterr().out.endswith("texti")


async def test_show_of_an_answered_key_says_there_is_no_text_left(llm_dir, tmp_path, capsys):
    key = await _queue(llm_dir)
    f = tmp_path / "svar.json"
    f.write_text(json.dumps(ANSWER), encoding="utf-8")
    assert q.main(["answer", key, str(f)]) == 0
    capsys.readouterr()
    assert q.main(["show", key]) == 1
    assert "þegar svarað" in capsys.readouterr().err


async def test_answer_validates_and_writes_the_cache_file_and_clears_the_queue(llm_dir, tmp_path, capsys):
    key = await _queue(llm_dir)
    f = tmp_path / "svar.json"
    f.write_text(json.dumps(ANSWER), encoding="utf-8")
    assert q.main(["answer", key, str(f)]) == 0
    assert json.loads((llm_dir / f"{key}.json").read_text(encoding="utf-8"))[0]["title"] == "Fyrsta greinin"
    assert pending_entries(llm_dir) == [] and key in capsys.readouterr().out
    # the answer is found by the lookup analyze uses
    out = await toc_via_llm("EFNISYFIRLIT\n1 Fyrsta greinin", cache_dir=llm_dir, offline=True)
    assert [e.title for e in out] == ["Fyrsta greinin"]


async def test_answer_with_a_wrong_shape_or_broken_file_writes_nothing(llm_dir, tmp_path, capsys):
    key = await _queue(llm_dir)
    bad = tmp_path / "rangt.json"
    bad.write_text(json.dumps([{"title": "X", "authors": [], "page_start": "7", "article_type": None}]), encoding="utf-8")
    assert q.main(["answer", key, str(bad)]) == 1
    assert "page_start" in capsys.readouterr().err
    broken = tmp_path / "brotið.json"
    broken.write_text("[{", encoding="utf-8")
    assert q.main(["answer", key, str(broken)]) == 1
    assert "JSON" in capsys.readouterr().err
    assert q.main(["answer", key, str(tmp_path / "ekki-til.json")]) == 1
    capsys.readouterr()
    assert not (llm_dir / f"{key}.json").exists() and len(pending_entries(llm_dir)) == 1


async def test_answer_for_a_key_that_is_neither_queued_nor_answered_is_refused(llm_dir, tmp_path, capsys):
    await _queue(llm_dir)
    f = tmp_path / "svar.json"
    f.write_text(json.dumps(ANSWER), encoding="utf-8")
    assert q.main(["answer", "a" * 40, str(f)]) == 1
    assert "finnst ekki" in capsys.readouterr().err
    assert not (llm_dir / ("a" * 40 + ".json")).exists()


async def test_an_existing_answer_can_be_replaced(llm_dir, tmp_path, capsys):
    key = await _queue(llm_dir)
    f = tmp_path / "svar.json"
    f.write_text(json.dumps(ANSWER), encoding="utf-8")
    assert q.main(["answer", key, str(f)]) == 0
    f.write_text(json.dumps([{**ANSWER[0], "title": "Leiðrétt"}]), encoding="utf-8")
    assert q.main(["answer", key, str(f)]) == 0
    assert json.loads((llm_dir / f"{key}.json").read_text(encoding="utf-8"))[0]["title"] == "Leiðrétt"


def test_the_cli_works_only_under_the_llm_cache_directory(llm_dir, tmp_path, capsys):
    assert q.main(["list"]) == 0
    assert not llm_dir.exists()                       # reading the queue creates nothing
