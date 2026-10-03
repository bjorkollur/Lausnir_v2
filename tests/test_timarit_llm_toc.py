import hashlib
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from engine.timarit.llm_toc import PROMPT, cache_key, pending_entries, toc_via_llm, write_answer


class _Client:
    def __init__(self, payload):
        self.payload, self.calls = payload, 0
        self.messages = self

    async def create(self, **kw):
        self.calls += 1
        return SimpleNamespace(content=[SimpleNamespace(text=self.payload)])


class _FailingClient:
    def __init__(self):
        self.calls = 0
        self.messages = self

    async def create(self, **kw):
        self.calls += 1
        raise RuntimeError("boom")


def _item(**kw):
    base = {"title": "T", "authors": [], "page_start": 1, "article_type": "Fræðigrein"}
    base.update(kw)
    return base


async def test_parses_json_array_and_caches(tmp_path):
    client = _Client(json.dumps([{"title": "Grein ein", "authors": ["A B"], "page_start": 5, "article_type": "Fræðigrein"}]))
    out = await toc_via_llm("EFNISYFIRLIT …", cache_dir=tmp_path, client=client)
    assert out[0].title == "Grein ein" and out[0].authors == ["A B"] and out[0].page_start == 5 and out[0].section == "Fræðigrein"
    again = await toc_via_llm("EFNISYFIRLIT …", cache_dir=tmp_path, client=client)
    assert again == out and client.calls == 1


async def test_tolerates_code_fences_and_garbage(tmp_path):
    fenced = _Client("```json\n[{\"title\": \"X\", \"authors\": [], \"page_start\": null, \"article_type\": null}]\n```")
    assert (await toc_via_llm("a", cache_dir=tmp_path, client=fenced))[0].title == "X"
    assert await toc_via_llm("b", cache_dir=tmp_path, client=_Client("ekki json")) == []


async def test_unparsable_answer_is_not_cached(tmp_path):
    bad = _Client("ekki json")
    assert await toc_via_llm("c", cache_dir=tmp_path, client=bad) == []
    assert list(tmp_path.glob("*.json")) == []
    # a later, good answer for the same text is fetched and cached
    good = _Client(json.dumps([_item(title="Eftir")]))
    out = await toc_via_llm("c", cache_dir=tmp_path, client=good)
    assert out[0].title == "Eftir" and good.calls == 1
    assert len(list(tmp_path.glob("*.json"))) == 1


async def test_non_list_json_is_not_cached(tmp_path):
    client = _Client(json.dumps({"title": "ekki fylki"}))
    assert await toc_via_llm("d", cache_dir=tmp_path, client=client) == []
    assert list(tmp_path.glob("*.json")) == []


async def test_api_failure_returns_empty_and_leaves_no_cache(tmp_path):
    client = _FailingClient()
    assert await toc_via_llm("e", cache_dir=tmp_path, client=client) == []
    assert client.calls == 1 and list(tmp_path.iterdir()) == []


async def test_cache_write_leaves_no_temp_files(tmp_path):
    client = _Client(json.dumps([_item()]))
    await toc_via_llm("f", cache_dir=tmp_path, client=client)
    names = [p.name for p in tmp_path.iterdir()]
    assert len(names) == 1 and names[0].endswith(".json")


async def test_unknown_article_type_becomes_none(tmp_path):
    client = _Client(json.dumps([_item(title="A", article_type="Bókadómur"), _item(title="B", article_type="Ritdómur"),
                                 _item(title="C", article_type=None), _item(title="D", article_type=5)]))
    out = await toc_via_llm("g", cache_dir=tmp_path, client=client)
    assert [e.section for e in out] == [None, "Ritdómur", None, None]


async def test_page_start_coercion(tmp_path):
    client = _Client(json.dumps([_item(title="A", page_start="7"), _item(title=" B ", page_start=" 12 "),
                                 _item(title="C", page_start="sjö"), _item(title="D", page_start=3.5),
                                 _item(title="E", page_start=None), _item(title="F", page_start=True),
                                 _item(title="G", page_start=[4])]))
    out = await toc_via_llm("h", cache_dir=tmp_path, client=client)
    assert [e.title for e in out] == ["A", "B", "C", "D", "E", "F", "G"]
    assert [e.page_start for e in out] == [7, 12, None, None, None, None, None]


async def test_skips_items_without_title_and_non_dicts(tmp_path):
    client = _Client(json.dumps([_item(title=""), "strengur", 5, _item(title="Gott")]))
    out = await toc_via_llm("i", cache_dir=tmp_path, client=client)
    assert [e.title for e in out] == ["Gott"]


async def test_authors_as_non_list_does_not_crash(tmp_path):
    client = _Client(json.dumps([_item(title="A", authors="Jón Jónsson"), _item(title="B", authors=None)]))
    out = await toc_via_llm("j", cache_dir=tmp_path, client=client)
    assert [e.title for e in out] == ["A", "B"]
    assert out[0].authors == ["Jón Jónsson"] and out[1].authors == []


async def test_unhashable_article_type_becomes_none(tmp_path):
    client = _Client(json.dumps([_item(title="A", article_type=["Ritdómur"])]))
    out = await toc_via_llm("k", cache_dir=tmp_path, client=client)
    assert out[0].section is None


async def test_corrupt_cache_file_is_refetched(tmp_path):
    first = _Client(json.dumps([_item(title="Eitt")]))
    await toc_via_llm("l", cache_dir=tmp_path, client=first)
    (cache_file,) = tmp_path.glob("*.json")
    cache_file.write_text("hálf skrá [{", encoding="utf-8")
    second = _Client(json.dumps([_item(title="Tvö")]))
    out = await toc_via_llm("l", cache_dir=tmp_path, client=second)
    assert out[0].title == "Tvö" and second.calls == 1
    assert json.loads(cache_file.read_text(encoding="utf-8"))[0]["title"] == "Tvö"


# --- Task 22a: offline mode (no API key: cached answers only, unanswered texts queued for the session) ----------

CONTEXT = {"manifest": "data/timarit/manifests/timarit_logfraedinga/2010_60_1.yaml", "source": "timarit_logfraedinga",
           "year": 2010, "volume": 60, "issue": "1", "label": "Tímarit lögfræðinga, 60. árg. 1. hefti (2010)"}


def _answer(**kw):
    base = {"title": "Grein", "authors": ["A B"], "page_start": 3, "article_type": "Fræðigrein"}
    base.update(kw)
    return base


async def test_offline_miss_queues_the_text_and_its_metadata_and_returns_nothing(tmp_path):
    text = "EFNISYFIRLIT\n" + "x" * 13000
    out = await toc_via_llm(text, cache_dir=tmp_path, offline=True, context=CONTEXT)
    assert out == []
    body = text[:12000]
    key = hashlib.sha1(("claude-haiku-4-5-20251001" + body).encode("utf-8")).hexdigest()
    assert cache_key(text) == key
    assert (tmp_path / "pending" / f"{key}.txt").read_text(encoding="utf-8") == body
    meta = json.loads((tmp_path / "pending" / f"{key}.json").read_text(encoding="utf-8"))
    assert meta["key"] == key and meta["model"] == "claude-haiku-4-5-20251001" and meta["context"] == CONTEXT
    assert meta["chars"] == 12000
    datetime.fromisoformat(meta["queued_at"])                       # ISO timestamp
    assert not (tmp_path / f"{key}.json").exists()                  # nothing was invented as an answer


async def test_the_queue_directory_holds_only_the_pair_and_no_temp_files(tmp_path):
    await toc_via_llm("EFNISYFIRLIT", cache_dir=tmp_path, offline=True, context=CONTEXT)
    names = sorted(p.name for p in (tmp_path / "pending").iterdir())
    assert len(names) == 2 and names[0].endswith(".json") and names[1].endswith(".txt")
    assert [p.name for p in tmp_path.iterdir()] == ["pending"]


async def test_queuing_the_same_text_again_is_idempotent(tmp_path):
    await toc_via_llm("EFNISYFIRLIT", cache_dir=tmp_path, offline=True, context=CONTEXT)
    (first,) = pending_entries(tmp_path)
    again = await toc_via_llm("EFNISYFIRLIT", cache_dir=tmp_path, offline=True, context={**CONTEXT, "issue": "2"})
    assert again == []
    (second,) = pending_entries(tmp_path)
    assert second == first and second["context"]["issue"] == "1"     # the first entry stands, queued_at included
    assert len(list((tmp_path / "pending").iterdir())) == 2


async def test_offline_hit_returns_the_entries_and_clears_the_queue(tmp_path):
    await toc_via_llm("EFNISYFIRLIT", cache_dir=tmp_path, offline=True, context=CONTEXT)
    (entry,) = pending_entries(tmp_path)
    (tmp_path / f"{entry['key']}.json").write_text(json.dumps([_answer(title="Svar")]), encoding="utf-8")
    out = await toc_via_llm("EFNISYFIRLIT", cache_dir=tmp_path, offline=True, context=CONTEXT)
    assert [e.title for e in out] == ["Svar"] and out[0].page_start == 3 and out[0].section == "Fræðigrein"
    assert pending_entries(tmp_path) == [] and list((tmp_path / "pending").iterdir()) == []


async def test_offline_corrupt_cache_file_counts_as_a_miss_and_is_queued(tmp_path):
    key = cache_key("EFNISYFIRLIT")
    (tmp_path / f"{key}.json").write_text("hálf skrá [{", encoding="utf-8")
    assert await toc_via_llm("EFNISYFIRLIT", cache_dir=tmp_path, offline=True) == []
    assert [e["key"] for e in pending_entries(tmp_path)] == [key]


async def test_a_cache_hit_in_online_mode_also_clears_a_pending_pair(tmp_path):
    await toc_via_llm("EFNISYFIRLIT", cache_dir=tmp_path, offline=True)
    write_answer(tmp_path, cache_key("EFNISYFIRLIT"), [_answer()])
    (tmp_path / "pending").mkdir(exist_ok=True)
    (tmp_path / "pending" / f"{cache_key('EFNISYFIRLIT')}.txt").write_text("x", encoding="utf-8")   # a stray leftover
    client = _Client("[]")
    out = await toc_via_llm("EFNISYFIRLIT", cache_dir=tmp_path, client=client)
    assert len(out) == 1 and client.calls == 0 and list((tmp_path / "pending").iterdir()) == []


async def test_offline_mode_never_constructs_a_client_nor_uses_the_one_given(tmp_path, monkeypatch):
    import anthropic

    def boom(*a, **k):
        raise AssertionError("offline mode must not construct a Claude client")
    monkeypatch.setattr(anthropic, "AsyncAnthropic", boom)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert await toc_via_llm("EFNISYFIRLIT", cache_dir=tmp_path, offline=True, context=CONTEXT) == []
    client = _Client(json.dumps([_item()]))
    assert await toc_via_llm("ANNAÐ", cache_dir=tmp_path, client=client, offline=True) == []
    assert client.calls == 0 and len(pending_entries(tmp_path)) == 2


async def test_the_api_path_sends_the_public_prompt_and_the_text(tmp_path):
    sent = []

    class _Spy(_Client):
        async def create(self, **kw):
            sent.append(kw)
            return await super().create(**kw)
    await toc_via_llm("EFNISYFIRLIT", cache_dir=tmp_path, client=_Spy(json.dumps([_item()])))
    assert sent[0]["messages"] == [{"role": "user", "content": PROMPT + "EFNISYFIRLIT"}]
    assert "JSON-fylki" in PROMPT and "Ritstjórnargrein" in PROMPT


async def test_an_answer_from_write_answer_is_read_by_the_same_lookup_the_api_path_uses(tmp_path):
    text = "EFNISYFIRLIT\n1 Fyrsta greinin"
    await toc_via_llm(text, cache_dir=tmp_path, offline=True, context=CONTEXT)
    (entry,) = pending_entries(tmp_path)
    path = write_answer(tmp_path, entry["key"], [_answer(title="Fyrsta greinin", authors=["Jón Jónsson"], page_start=1),
                                                  _answer(title="Ritdómur", authors=[], page_start=None, article_type=None)])
    assert path == tmp_path / f"{cache_key(text)}.json"
    assert isinstance(json.loads(path.read_text(encoding="utf-8")), list)              # the raw form the API path stores
    assert pending_entries(tmp_path) == [] and list((tmp_path / "pending").iterdir()) == []
    online = _Client("should not be asked")
    out = await toc_via_llm(text, cache_dir=tmp_path, client=online)                   # the API path's lookup: a hit
    assert online.calls == 0
    assert [(e.title, e.authors, e.page_start, e.section) for e in out] == [
        ("Fyrsta greinin", ["Jón Jónsson"], 1, "Fræðigrein"), ("Ritdómur", [], None, None)]
    assert out == await toc_via_llm(text, cache_dir=tmp_path, offline=True)


def test_write_answer_leaves_no_temp_files_and_accepts_an_empty_answer(tmp_path):
    key = cache_key("t")
    write_answer(tmp_path, key, [])
    assert [p.name for p in tmp_path.iterdir()] == [f"{key}.json"]
    assert json.loads((tmp_path / f"{key}.json").read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("bad, message", [
    (_answer(article_type="Bókadómur"), "article_type"),
    (_answer(title=""), "titill"),
    (_answer(title="   "), "titill"),
    ({k: v for k, v in _answer().items() if k != "title"}, "titill"),
    (_answer(title=5), "titill"),
    (_answer(page_start="7"), "page_start"),
    (_answer(page_start=3.5), "page_start"),
    (_answer(page_start=True), "page_start"),
    (_answer(page_start=0), "page_start"),
    (_answer(authors="Jón Jónsson"), "authors"),
    (_answer(authors=["Jón", 5]), "authors"),
    (_answer(authors=["Jón", ""]), "authors"),
    (_answer(pagestart=3), "pagestart"),                       # a typo'd key is not silently ignored
    ("strengur", "hlutur"),
])
def test_write_answer_rejects_a_wrong_shape_in_icelandic_and_writes_nothing(tmp_path, bad, message):
    with pytest.raises(ValueError) as exc:
        write_answer(tmp_path, cache_key("t"), [_answer(), bad])
    assert "færsla 2" in str(exc.value) and message in str(exc.value)       # names the item and the field
    assert list(tmp_path.iterdir()) == []


def test_write_answer_rejects_a_non_list_and_a_bad_key(tmp_path):
    with pytest.raises(ValueError, match="fylki"):
        write_answer(tmp_path, cache_key("t"), {"title": "x"})
    for key in ("", "../evil", "abc", "A" * 40, cache_key("t") + "0"):
        with pytest.raises(ValueError, match="lykill"):
            write_answer(tmp_path, key, [])
    assert list(tmp_path.iterdir()) == []


def test_write_answer_missing_optional_keys_are_filled_in(tmp_path):
    path = write_answer(tmp_path, cache_key("t"), [{"title": " Aðeins titill "}])
    assert json.loads(path.read_text(encoding="utf-8")) == [
        {"title": "Aðeins titill", "authors": [], "page_start": None, "article_type": None}]


async def test_pending_entries_lists_metadata_and_body_path_oldest_first_and_skips_answered_and_broken(tmp_path):
    assert pending_entries(tmp_path) == [] and pending_entries(tmp_path / "ekki-til") == []
    for i, t in enumerate(["EITT", "TVÖ", "ÞRJÚ"]):
        await toc_via_llm(t, cache_dir=tmp_path, offline=True, context={**CONTEXT, "issue": str(i)})
    entries = pending_entries(tmp_path)
    assert len(entries) == 3 and all(e["body_path"].read_text(encoding="utf-8") in ("EITT", "TVÖ", "ÞRJÚ") for e in entries)
    assert {e["context"]["issue"] for e in entries} == {"0", "1", "2"}
    assert [e["queued_at"] for e in entries] == sorted(e["queued_at"] for e in entries)
    # an answer put in place by hand (not through write_answer) hides the entry; a broken pair is ignored
    (tmp_path / f"{cache_key('TVÖ')}.json").write_text(json.dumps([_answer()]), encoding="utf-8")
    (tmp_path / "pending" / f"{cache_key('EITT')}.txt").unlink()
    (tmp_path / "pending" / ("f" * 40 + ".json")).write_text("{", encoding="utf-8")
    assert [e["key"] for e in pending_entries(tmp_path)] == [cache_key("ÞRJÚ")]
