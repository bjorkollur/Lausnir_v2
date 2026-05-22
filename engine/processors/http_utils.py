"""WAF-safe HTTP helpers shared by all import scripts."""
from __future__ import annotations

import asyncio
import logging

import httpx

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://island.is/",
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "is, en;q=0.9",
}

_RETRY_STATUSES = {429, 405}
_BASE_BACKOFF = 15.0
_BACKOFF_FACTOR = 2.5


def make_client(**kwargs) -> httpx.AsyncClient:
    """AsyncClient with browser headers and 30 s timeout."""
    return httpx.AsyncClient(headers=_HEADERS, timeout=30.0, **kwargs)


async def post_with_retry(
    client: httpx.AsyncClient,
    url: str,
    payload: dict,
    *,
    max_retries: int = 3,
) -> dict:
    """POST JSON; retry on 429/405 with exponential backoff (15 s × 2.5^n)."""
    for attempt in range(max_retries):
        resp = await client.post(url, json=payload)
        if resp.status_code in _RETRY_STATUSES:
            if attempt == max_retries - 1:
                resp.raise_for_status()
            delay = _BASE_BACKOFF * (_BACKOFF_FACTOR ** attempt)
            log.warning("WAF throttle %d — sleeping %.1f s", resp.status_code, delay)
            await asyncio.sleep(delay)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError("unreachable")  # pragma: no cover


async def get_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_retries: int = 3,
) -> httpx.Response:
    """GET; retry on 429/405 with same backoff. GET does not trigger CloudFront WAF."""
    for attempt in range(max_retries):
        resp = await client.get(url)
        if resp.status_code in _RETRY_STATUSES:
            if attempt == max_retries - 1:
                resp.raise_for_status()
            delay = _BASE_BACKOFF * (_BACKOFF_FACTOR ** attempt)
            log.warning("Throttled GET %d — sleeping %.1f s", resp.status_code, delay)
            await asyncio.sleep(delay)
            continue
        resp.raise_for_status()
        return resp
    raise RuntimeError("unreachable")  # pragma: no cover
