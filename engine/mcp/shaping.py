"""Compaction of core results into the token-frugal shapes the MCP tools return (spec §4)."""
from __future__ import annotations

import datetime as _dt
import json
import math
import re
import uuid
from decimal import Decimal
from typing import Any

MAX_KEYWORDS = 5
CELL_MAX_CHARS = 500

# Matches the last whitespace character in a string (any whitespace kind,
# not just a literal space) — i.e. the one followed by nothing but
# non-whitespace up to the end.
_LAST_WS = re.compile(r"\s(?=\S*\Z)", re.S)


def truncate_text(text: str | None, max_chars: int) -> tuple[str | None, bool]:
    if text is None or max_chars <= 0:
        return None, False
    if len(text) <= max_chars:
        return text, False
    head = text[:max_chars]
    m = _LAST_WS.search(head)
    if m:
        head = head[: m.start()]
    stripped = head.strip()
    if stripped:
        return stripped, True
    return text[:max_chars].strip(), True


def _scalar(v: Any) -> Any:
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, memoryview):
        v = v.tobytes()
    if isinstance(v, (bytes, bytearray)):
        return f"<bytes {len(v)}>"
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    # Anything else (set, arbitrary class instance, ipaddress, range, ...)
    # is not JSON-native — stringify rather than let it leak through.
    return str(v)


def _jsonable(v: Any) -> Any:
    """Recursively convert a value into something json.dumps can handle."""
    if isinstance(v, dict):
        return {(k if isinstance(k, str) else str(k)): _jsonable(val) for k, val in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(item) for item in v]
    if isinstance(v, set):
        try:
            items = sorted(v)
        except TypeError:
            items = list(v)
        return [_jsonable(item) for item in items]
    return _scalar(v)


# Public alias: tools.py returns whole DB rows and needs the same hardening
# (Decimal, bytes, NaN/Inf, unknown types) that the compactors get.
jsonable = _jsonable


def cell_value(v: Any, max_chars: int = CELL_MAX_CHARS) -> tuple[Any, bool]:
    if isinstance(v, (dict, list, tuple, set)):
        converted = _jsonable(v)
        dumped = json.dumps(converted, ensure_ascii=False)
        if len(dumped) > max_chars:
            return dumped[:max_chars] + "…", True
        return converted, False
    v = _scalar(v)
    if isinstance(v, str) and len(v) > max_chars:
        return v[:max_chars] + "…", True
    return v, False


def compact_search_result(r: dict) -> dict:
    kws = r.get("keywords")
    if not isinstance(kws, list):
        kws = []
    date = r.get("document_date")
    result = {
        "doc_id": str(r.get("id")) if r.get("id") is not None else None,
        "urlausn": r.get("urlausn"),
        "source": r.get("source"),
        "court": r.get("court"),
        "case_number": r.get("case_number"),
        "date": date.isoformat() if isinstance(date, (_dt.date, _dt.datetime)) else date,
        "verdict_type": r.get("verdict_type"),
        "snippet": r.get("snippet"),
        "passage_id": str(r["passage_id"]) if r.get("passage_id") else None,
        "anchor": r.get("anchor"),
        "section_kind": r.get("section_kind"),
        "match_tier": r.get("match_tier") or 0,
        "keywords": [str(k) for k in kws[:MAX_KEYWORDS]],
    }
    return _jsonable(result)


def compact_passage(p: dict) -> dict:
    result = {
        "passage_id": str(p["id"]),
        "ordinal": p["ordinal"],
        "layer": p["layer"],
        "section_kind": p["section_kind"],
        "anchor": p.get("anchor"),
        "text": p["text"],
    }
    return _jsonable(result)
