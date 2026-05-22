import httpx
import pytest
from unittest.mock import AsyncMock, patch

from engine.processors.http_utils import make_client, post_with_retry, get_with_retry


class _Replay(httpx.AsyncBaseTransport):
    """Return a fixed sequence of responses, repeating the last one indefinitely."""

    def __init__(self, *responses: httpx.Response):
        self._responses = list(responses)
        self._index = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        resp = self._responses[self._index]
        self._index = min(self._index + 1, len(self._responses) - 1)
        resp._request = request
        return resp


def test_make_client_browser_headers():
    client = make_client()
    assert "Mozilla" in client.headers["user-agent"]
    assert client.headers["referer"] == "https://island.is/"
    assert client.timeout.read == 30.0


async def test_post_returns_json_on_200():
    transport = _Replay(httpx.Response(200, json={"data": "ok"}))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await post_with_retry(client, "https://x.com/api", {})
    assert result == {"data": "ok"}


async def test_post_retries_twice_then_succeeds():
    transport = _Replay(
        httpx.Response(429),
        httpx.Response(429),
        httpx.Response(200, json={"ok": True}),
    )
    with patch("engine.processors.http_utils.asyncio.sleep", new_callable=AsyncMock):
        async with httpx.AsyncClient(transport=transport) as client:
            result = await post_with_retry(client, "https://x.com/api", {}, max_retries=3)
    assert result == {"ok": True}


async def test_post_raises_after_exhausting_retries():
    transport = _Replay(httpx.Response(429))
    with patch("engine.processors.http_utils.asyncio.sleep", new_callable=AsyncMock):
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await post_with_retry(client, "https://x.com/api", {}, max_retries=3)


async def test_post_raises_immediately_on_non_retryable_error():
    transport = _Replay(httpx.Response(500))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await post_with_retry(client, "https://x.com/api", {})


async def test_get_returns_response_on_200():
    transport = _Replay(httpx.Response(200, text="body"))
    async with httpx.AsyncClient(transport=transport) as client:
        resp = await get_with_retry(client, "https://x.com/page")
    assert resp.status_code == 200
    assert resp.text == "body"


async def test_get_retries_on_405():
    transport = _Replay(
        httpx.Response(405),
        httpx.Response(200, text="ok"),
    )
    with patch("engine.processors.http_utils.asyncio.sleep", new_callable=AsyncMock):
        async with httpx.AsyncClient(transport=transport) as client:
            resp = await get_with_retry(client, "https://x.com/page", max_retries=2)
    assert resp.status_code == 200
