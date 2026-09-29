"""Compaction of core results into the token-frugal shapes the MCP tools return (spec §4)."""
from __future__ import annotations

import datetime as _dt
import json
import uuid
from decimal import Decimal
from typing import Any

MAX_KEYWORDS = 5
CELL_MAX_CHARS = 500


def truncate_text(text: str | None, max_chars: int) -> tuple[str | None, bool]:
    if text is None or max_chars <= 0:
        return None, False
    if len(text) <= max_chars:
        return text, False
    head = text[:max_chars]
    cut = head.rfind(" ")
    if cut > 0:
        head = head[:cut]
    return head.rstrip(), True


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
    return v


def cell_value(v: Any, max_chars: int = CELL_MAX_CHARS) -> tuple[Any, bool]:
    v = _scalar(v)
    if isinstance(v, (dict, list)):
        dumped = json.dumps(v, ensure_ascii=False, default=_scalar)
        if len(dumped) > max_chars:
            return dumped[:max_chars] + "…", True
        return v, False
    if isinstance(v, str) and len(v) > max_chars:
        return v[:max_chars] + "…", True
    return v, False


def compact_search_result(r: dict) -> dict:
    kws = r.get("keywords")
    if not isinstance(kws, list):
        kws = []
    date = r.get("document_date")
    return {
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


def compact_passage(p: dict) -> dict:
    return {
        "passage_id": str(p["id"]),
        "ordinal": p["ordinal"],
        "layer": p["layer"],
        "section_kind": p["section_kind"],
        "anchor": p.get("anchor"),
        "text": p["text"],
    }
