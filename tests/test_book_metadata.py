from engine.processors.book_metadata import find_isbn


def test_finds_isbn13_with_hyphens():
    text = "Einhver texti\nISBN: 978-0-306-40615-7\nMeiri texti"
    assert find_isbn(text) == "9780306406157"


def test_finds_isbn13_without_hyphens():
    text = "9780306406157 kápusíða"
    assert find_isbn(text) == "9780306406157"


def test_finds_isbn10_with_x_check_digit():
    # 0-19-853453-1 is a real, valid ISBN-10 (Oxford UP)
    text = "ISBN 0-19-853453-1"
    assert find_isbn(text) == "0198534531"


def test_rejects_invalid_checksum():
    text = "ISBN 978-0-306-40615-8"  # wrong check digit (valid ISBN's last digit changed 7→8)
    assert find_isbn(text) is None


def test_returns_none_when_no_isbn_present():
    assert find_isbn("Bara venjulegur texti, engin tala hér.") is None


def test_returns_none_for_empty_text():
    assert find_isbn("") is None


import httpx


class _Replay(httpx.AsyncBaseTransport):
    def __init__(self, response: httpx.Response):
        self._response = response

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self._response._request = request
        return self._response


from engine.processors.book_metadata import lookup_openlibrary


async def test_lookup_openlibrary_returns_metadata():
    body = {
        "ISBN:9780306406157": {
            "title": "Kröfuréttur I",
            "authors": [{"name": "Páll Sigurðsson"}],
            "publish_date": "1985",
        }
    }
    transport = _Replay(httpx.Response(200, json=body))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await lookup_openlibrary(client, "9780306406157")
    assert result == {
        "title": "Kröfuréttur I",
        "author": "Páll Sigurðsson",
        "publish_date": "1985",
    }


async def test_lookup_openlibrary_returns_none_when_isbn_unknown():
    transport = _Replay(httpx.Response(200, json={}))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await lookup_openlibrary(client, "9780306406157")
    assert result is None


async def test_lookup_openlibrary_returns_none_on_http_error():
    transport = _Replay(httpx.Response(500))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await lookup_openlibrary(client, "9780306406157")
    assert result is None


async def test_lookup_openlibrary_handles_missing_authors():
    body = {"ISBN:9780306406157": {"title": "Ónefnt rit"}}
    transport = _Replay(httpx.Response(200, json=body))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await lookup_openlibrary(client, "9780306406157")
    assert result == {"title": "Ónefnt rit", "author": None, "publish_date": None}


from engine.processors.book_metadata import lookup_leitir


async def test_lookup_leitir_returns_metadata():
    body = {
        "info": {"total": 1},
        "docs": [{
            "pnx": {
                "display": {
                    "title": ["Afbrot og refsiábyrgð. 1 "],
                    "creator": ["Jónatan Þórmundsson 1937- höfundur$$QJónatan Þórmundsson"],
                    "identifier": ["$$CISBN$$V9789935233202"],
                    "creationdate": ["2023"],
                }
            }
        }],
    }
    transport = _Replay(httpx.Response(200, json=body))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await lookup_leitir(client, "9789935233202")
    assert result == {
        "title": "Afbrot og refsiábyrgð. 1",
        "author": "Jónatan Þórmundsson",
        "publish_date": "2023",
    }


async def test_lookup_leitir_returns_none_when_no_docs():
    transport = _Replay(httpx.Response(200, json={"info": {"total": 0}, "docs": []}))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await lookup_leitir(client, "9789935233202")
    assert result is None


async def test_lookup_leitir_returns_none_on_http_error():
    transport = _Replay(httpx.Response(500))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await lookup_leitir(client, "9789935233202")
    assert result is None


async def test_lookup_leitir_handles_missing_creator():
    body = {"docs": [{"pnx": {"display": {"title": ["Ónefnt rit"]}}}]}
    transport = _Replay(httpx.Response(200, json=body))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await lookup_leitir(client, "9789935233202")
    assert result == {"title": "Ónefnt rit", "author": None, "publish_date": None}


async def test_lookup_leitir_creator_without_qq_marker_uses_raw_value():
    body = {"docs": [{"pnx": {"display": {
        "title": ["Rit án Q-merkis"], "creator": ["Jón Jónsson"],
    }}}]}
    transport = _Replay(httpx.Response(200, json=body))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await lookup_leitir(client, "9789935233202")
    assert result["author"] == "Jón Jónsson"


from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

from engine.processors.book_metadata import (
    external_id_from_filename,
    find_author_llm,
    find_author_regex,
    parse_publish_year,
    resolve_book_metadata,
    slugify_filename,
)


def test_slugify_filename_replaces_separators():
    assert slugify_filename(Path("Skadabotarettur_a_Islandi.pdf")) == "Skadabotarettur a Islandi"


def test_slugify_filename_strips_extension_only():
    assert slugify_filename(Path("Bók-með-bandstriki.pdf")) == "Bók með bandstriki"


def test_find_author_regex_eftir_pattern():
    text = "Titill bókar\n\nEftir Pál Sigurðsson\n\nMeiri texti hér."
    assert find_author_regex(text) == "Pál Sigurðsson"


def test_find_author_regex_hofundur_pattern():
    text = "Höfundur: Sigríður Logadóttir\n\nInngangur."
    assert find_author_regex(text) == "Sigríður Logadóttir"


def test_find_author_regex_returns_none_when_absent():
    assert find_author_regex("Ekkert höfundarmerki hér, bara texti.") is None


def test_parse_publish_year_extracts_four_digit_year():
    assert parse_publish_year("October 1, 1988") == date(1988, 1, 1)
    assert parse_publish_year("1985") == date(1985, 1, 1)


def test_parse_publish_year_returns_none_for_missing():
    assert parse_publish_year(None) is None
    assert parse_publish_year("") is None
    assert parse_publish_year("n.d.") is None


def test_external_id_from_filename_is_ascii_slug():
    assert external_id_from_filename(Path("Kröfuréttur I.pdf")) == "krofurettur_i"


async def test_find_author_llm_parses_json_response():
    mock_message = AsyncMock()
    mock_message.content = [type("Block", (), {"text": '{"author": "Páll Sigurðsson"}'})()]
    with patch("engine.processors.book_metadata.AsyncAnthropic") as MockClient:
        MockClient.return_value.messages.create = AsyncMock(return_value=mock_message)
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            result = await find_author_llm("einhver texti")
    assert result == "Páll Sigurðsson"


async def test_find_author_llm_returns_none_on_failure():
    with patch("engine.processors.book_metadata.AsyncAnthropic") as MockClient:
        MockClient.return_value.messages.create = AsyncMock(side_effect=RuntimeError("boom"))
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            result = await find_author_llm("einhver texti")
    assert result is None


async def test_find_author_llm_returns_none_when_api_key_missing(monkeypatch):
    """Verify that missing ANTHROPIC_API_KEY returns None instead of raising KeyError."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = await find_author_llm("einhver texti")
    assert result is None


async def test_resolve_book_metadata_uses_isbn_and_openlibrary():
    body = {
        "ISBN:9780306406157": {
            "title": "Kröfuréttur I",
            "authors": [{"name": "Páll Sigurðsson"}],
            "publish_date": "1985",
        }
    }
    transport = _Replay(httpx.Response(200, json=body))
    text = "ISBN 978-0-306-40615-7\n\nKröfuréttur I"
    async with httpx.AsyncClient(transport=transport) as client:
        meta = await resolve_book_metadata(client, text, Path("einhver_skra.pdf"))
    assert meta == {
        "title": "Kröfuréttur I",
        "author": "Páll Sigurðsson",
        "isbn": "9780306406157",
        "external_id": "9780306406157",
        "document_date": date(1985, 1, 1),
    }


class _HostRouter(httpx.AsyncBaseTransport):
    """Route responses by request host — for tests exercising two lookup tiers in one call."""

    def __init__(self, responses: dict[str, httpx.Response]):
        self._responses = responses

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        resp = self._responses[request.url.host]
        resp._request = request
        return resp


async def test_resolve_book_metadata_falls_back_to_leitir_when_openlibrary_has_no_title():
    leitir_body = {"docs": [{"pnx": {"display": {
        "title": ["Afbrot og refsiábyrgð. 1 "],
        "creator": ["Jónatan Þórmundsson 1937- höfundur$$QJónatan Þórmundsson"],
        "creationdate": ["2023"],
    }}}]}
    transport = _HostRouter({
        "openlibrary.org": httpx.Response(200, json={}),
        "leitir.is": httpx.Response(200, json=leitir_body),
    })
    text = "ISBN 978-9935-233-20-2\n\nAfbrot og refsiábyrgð"
    async with httpx.AsyncClient(transport=transport) as client:
        meta = await resolve_book_metadata(client, text, Path("Afbrot.pdf"))
    assert meta["title"] == "Afbrot og refsiábyrgð. 1"
    assert meta["author"] == "Jónatan Þórmundsson"
    assert meta["isbn"] == "9789935233202"
    assert meta["external_id"] == "9789935233202"
    assert meta["document_date"] == date(2023, 1, 1)


async def test_resolve_book_metadata_falls_back_to_filename_and_regex():
    transport = _Replay(httpx.Response(200, json={}))  # ISBN not found on OpenLibrary
    text = "9780306406157\n\nEftir Jón Jónsson\n\nInngangur."
    async with httpx.AsyncClient(transport=transport) as client:
        meta = await resolve_book_metadata(client, text, Path("Skadabotarettur.pdf"))
    assert meta["title"] == "Skadabotarettur"
    assert meta["author"] == "Jón Jónsson"
    assert meta["isbn"] == "9780306406157"
    assert meta["external_id"] == "9780306406157"


async def test_resolve_book_metadata_falls_back_to_llm_when_regex_finds_nothing():
    mock_message = AsyncMock()
    mock_message.content = [type("Block", (), {"text": '{"author": "Ásta Ólafsdóttir"}'})()]
    text = "Engin ISBN hér, ekkert höfundarmerki heldur, bara laus texti."
    with patch("engine.processors.book_metadata.AsyncAnthropic") as MockClient:
        MockClient.return_value.messages.create = AsyncMock(return_value=mock_message)
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            async with httpx.AsyncClient() as client:
                meta = await resolve_book_metadata(client, text, Path("Ohefdbaerabok.pdf"))
    assert meta["author"] == "Ásta Ólafsdóttir"
    assert meta["isbn"] is None
    assert meta["external_id"] == "ohefdbaerabok"
