import json
from pathlib import Path
from types import SimpleNamespace

from engine.timarit.llm_toc import toc_via_llm


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
