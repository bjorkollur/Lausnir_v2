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
