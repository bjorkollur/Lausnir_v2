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

_RETRY_STATUSES = {429, 405, 500, 503}
_RETRY_PAUSE = 5.0
_MAX_RETRIES = 3


def make_client(**kwargs) -> httpx.AsyncClient:
    """AsyncClient with browser headers and 30 s timeout."""
    return httpx.AsyncClient(headers=_HEADERS, timeout=30.0, **kwargs)


async def post_with_retry(
    client: httpx.AsyncClient,
    url: str,
    payload: dict,
    *,
    max_retries: int = _MAX_RETRIES,
) -> dict:
    """POST JSON; retry on 429/405/500/503 or timeout with a flat 5 s pause, max 3 attempts."""
    for attempt in range(max_retries):
        try:
            resp = await client.post(url, json=payload)
        except httpx.TimeoutException:
            if attempt == max_retries - 1:
                raise
            log.warning("POST timeout — sleeping %.1f s (attempt %d/%d)", _RETRY_PAUSE, attempt + 1, max_retries)
            await asyncio.sleep(_RETRY_PAUSE)
            continue
        if resp.status_code in _RETRY_STATUSES:
            if attempt == max_retries - 1:
                resp.raise_for_status()
            log.warning("HTTP %d — sleeping %.1f s (attempt %d/%d)", resp.status_code, _RETRY_PAUSE, attempt + 1, max_retries)
            await asyncio.sleep(_RETRY_PAUSE)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError("unreachable")  # pragma: no cover


async def get_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_retries: int = _MAX_RETRIES,
) -> httpx.Response:
    """GET; retry on 429/405/500/503 or timeout with a flat 5 s pause, max 3 attempts."""
    for attempt in range(max_retries):
        try:
            resp = await client.get(url)
        except httpx.TimeoutException:
            if attempt == max_retries - 1:
                raise
            log.warning("GET timeout — sleeping %.1f s (attempt %d/%d)", _RETRY_PAUSE, attempt + 1, max_retries)
            await asyncio.sleep(_RETRY_PAUSE)
            continue
        if resp.status_code in _RETRY_STATUSES:
            if attempt == max_retries - 1:
                resp.raise_for_status()
            log.warning("HTTP %d — sleeping %.1f s (attempt %d/%d)", resp.status_code, _RETRY_PAUSE, attempt + 1, max_retries)
            await asyncio.sleep(_RETRY_PAUSE)
            continue
        resp.raise_for_status()
        return resp
    raise RuntimeError("unreachable")  # pragma: no cover
