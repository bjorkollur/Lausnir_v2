"""Last-resort TOC reading with Claude (spec §5.3). Only called when the regex
parser finds nothing or disagrees with the PDF; the result is marked `llm`,
capped at LLM_CONFIDENCE_CAP, and always reviewed by the user."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from pathlib import Path

from engine.timarit.toc import TocEntry

log = logging.getLogger(__name__)

ARTICLE_TYPES = frozenset(
    {"Fræðigrein", "Ritstjórnargrein", "Ritdómur", "Viðtal", "Frétt", "Dómareifun", "Annað"}
)

_PROMPT = (
    "Hér fyrir neðan er efnisyfirlit úr íslensku lögfræðitímariti, lesið úr PDF með OCR. "
    "Skilaðu EINGÖNGU JSON-fylki, einu hluti á grein, á forminu "
    '{"title": "...", "authors": ["Fullt nafn", ...], "page_start": 7, "article_type": "Fræðigrein"} '
    "þar sem article_type er eitt af: Fræðigrein, Ritstjórnargrein, Ritdómur, Viðtal, Frétt, Dómareifun, Annað. "
    "Slepptu auglýsingum, ritstjórnarupplýsingum og starfsheitum höfunda. page_start er null ef blaðsíðutal vantar.\n\n"
)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)


def _parse_list(raw: str) -> list | None:
    """The answer as a JSON list, or None when it is not one (garbage, an object, a scalar)."""
    try:
        data = json.loads(_FENCE.sub("", raw.strip()))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None


def _write_atomic(path: Path, text: str) -> None:
    """Write via a temp file in the same directory and os.replace, so a reader never sees half a file."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _page_start(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip()
        if s.isascii() and s.isdigit():
            return int(s)
    return None


def _authors(value) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [s for s in (str(a).strip() for a in value if a) if s]


async def toc_via_llm(text: str, *, cache_dir: Path, client=None,
                      model: str = "claude-haiku-4-5-20251001") -> list[TocEntry]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    body = text[:12000]
    path = cache_dir / (hashlib.sha1((model + body).encode("utf-8")).hexdigest() + ".json")
    data = None
    if path.exists():
        data = _parse_list(path.read_text(encoding="utf-8"))  # a corrupt cache file is a miss, not an answer
    if data is None:
        if client is None:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        try:
            resp = await client.messages.create(model=model, max_tokens=4000,
                                                messages=[{"role": "user", "content": _PROMPT + body}])
            raw = resp.content[0].text
        except Exception as exc:  # noqa: BLE001
            log.warning("llm toc failed: %s", exc)
            return []
        data = _parse_list(raw)
        if data is None:
            log.warning("llm toc answer is not a JSON list; not cached")
            return []
        _write_atomic(path, raw)  # only a usable answer is cached
    out: list[TocEntry] = []
    for item in data:
        if not isinstance(item, dict) or not item.get("title"):
            continue
        section = item.get("article_type")
        out.append(TocEntry(str(item["title"]).strip(), _authors(item.get("authors")),
                            _page_start(item.get("page_start")),
                            section=section if isinstance(section, str) and section in ARTICLE_TYPES else None))
    return out
