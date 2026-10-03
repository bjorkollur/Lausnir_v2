"""Last-resort TOC reading with Claude (spec §5.3). Only called when the regex
parser finds nothing or disagrees with the PDF; the result is marked `llm`,
capped at LLM_CONFIDENCE_CAP, and always reviewed by the user.

Two ways to get the answer, one cache. With an API key `toc_via_llm` asks the API and caches the reply. Without
one (`offline=True`, Task 22a) it only reads the cache; a text with no cached answer is queued in
`cache_dir/pending/` (the text and a metadata file), and the Claude Code session answers it by `write_answer`
(scripts/timarit_llm_queue.py), which stores the answer under the very key the API path would have used. The next
`analyze` of the issue then finds it like any API answer."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from engine.timarit.toc import TocEntry

log = logging.getLogger(__name__)

ARTICLE_TYPES = frozenset(
    {"Fræðigrein", "Ritstjórnargrein", "Ritdómur", "Viðtal", "Frétt", "Dómareifun", "Annað"}
)

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
PENDING_DIR = "pending"            # under the cache directory: texts waiting for the session's answer
_BODY_CHARS = 12000                # how much of the text is sent (and keyed)
TEXT_LIMIT = _BODY_CHARS           # for callers that build the text to fit
KEY_PATTERN = re.compile(r"[0-9a-f]{40}")   # a cache key: sha1 hex
_ANSWER_KEYS = ("title", "authors", "page_start", "article_type")

# The instructions, public so that the session answers by the same rules the API would.
PROMPT = (
    "Hér fyrir neðan er efnisyfirlit úr íslensku lögfræðitímariti, lesið úr PDF með OCR. "
    "Skilaðu EINGÖNGU JSON-fylki, einu hluti á grein, á forminu "
    '{"title": "...", "authors": ["Fullt nafn", ...], "page_start": 7, "article_type": "Fræðigrein"} '
    "þar sem article_type er eitt af: Fræðigrein, Ritstjórnargrein, Ritdómur, Viðtal, Frétt, Dómareifun, Annað. "
    "Slepptu auglýsingum, ritstjórnarupplýsingum og starfsheitum höfunda. page_start er null ef blaðsíðutal vantar. "
    "Á eftir efnisyfirlitinu (eða í stað þess, ef ekkert fannst) kemur yfirlit yfir allar síður heftisins, ein lína á "
    "síðu: „[n] p.N: fyrsta lína | önnur lína“, þar sem n er númer síðunnar í PDF-skjalinu og N prentað blaðsíðutal "
    "hennar (? ef það er óþekkt). Grein byrjar á síðu sem hefst á titli hennar (oft með höfundi); notaðu þá N sem "
    "page_start og nefndu hverja grein heftisins, ekki aðeins þá fyrstu.\n\n"
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


def cache_key(text: str, model: str = DEFAULT_MODEL) -> str:
    """The key of an answer: sha1 of the model and the part of `text` that is sent. (The prompt is not in it.)"""
    return hashlib.sha1((model + text[:_BODY_CHARS]).encode("utf-8")).hexdigest()


def _read_answer(cache_dir: Path, key: str) -> list | None:
    path = cache_dir / f"{key}.json"
    if not path.exists():
        return None
    return _parse_list(path.read_text(encoding="utf-8"))  # a corrupt cache file is a miss, not an answer


def is_pending(cache_dir: Path, key: str) -> bool:
    return (cache_dir / PENDING_DIR / f"{key}.json").exists()


def _clear_pending(cache_dir: Path, key: str) -> None:
    for suffix in (".json", ".txt"):
        (cache_dir / PENDING_DIR / f"{key}{suffix}").unlink(missing_ok=True)


def _queue(cache_dir: Path, key: str, body: str, model: str, context: dict | None) -> None:
    """Put a text that has no answer yet in the queue; a second call for the same key changes nothing."""
    pdir = cache_dir / PENDING_DIR
    if is_pending(cache_dir, key) and (pdir / f"{key}.txt").exists():
        return
    pdir.mkdir(parents=True, exist_ok=True)
    meta = {"key": key, "model": model, "context": context, "chars": len(body),
            "queued_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    _write_atomic(pdir / f"{key}.txt", body)                    # text first: a metadata file always has its text
    _write_atomic(pdir / f"{key}.json", json.dumps(meta, ensure_ascii=False, indent=2, default=str))
    log.info("llm toc queued for the Claude Code session: %s", key)


def pending_entries(cache_dir: Path) -> list[dict]:
    """The queue, oldest first: each entry's metadata (key, model, context, chars, queued_at) plus `body_path`, the
    file holding the text. Entries that already have an answer in the cache (put there by hand) and broken pairs
    are left out."""
    pdir = cache_dir / PENDING_DIR
    out: list[dict] = []
    for meta_path in pdir.glob("*.json"):
        key = meta_path.stem
        body_path = pdir / f"{key}.txt"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = None
        if not isinstance(meta, dict) or not body_path.exists():
            log.warning("ignoring broken queue entry %s", key)
            continue
        if _read_answer(cache_dir, key) is not None:
            continue
        out.append({**meta, "key": key, "body_path": body_path})
    return sorted(out, key=lambda e: (str(e.get("queued_at", "")), e["key"]))


def _check_item(i: int, item) -> dict:
    """One answer item in its normalised form; ValueError (Icelandic) when its shape is wrong."""
    where = f"færsla {i}"
    if not isinstance(item, dict):
        raise ValueError(f"{where}: á að vera hlutur með titil, höfunda, blaðsíðu og tegund")
    unknown = sorted(set(item) - set(_ANSWER_KEYS))
    if unknown:
        raise ValueError(f"{where}: óþekktur lykill {', '.join(unknown)} (leyfð: {', '.join(_ANSWER_KEYS)})")
    title = item.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError(f"{where}: titill vantar eða er ekki texti")
    authors = item.get("authors", [])
    if not isinstance(authors, list) or not all(isinstance(a, str) and a.strip() for a in authors):
        raise ValueError(f"{where}: authors á að vera listi af nöfnum (textum, ekki tómum)")
    page = item.get("page_start")
    if page is not None and (isinstance(page, bool) or not isinstance(page, int) or page < 1):
        raise ValueError(f"{where}: page_start á að vera heiltala frá 1 eða null")
    kind = item.get("article_type")
    if kind is not None and (not isinstance(kind, str) or kind not in ARTICLE_TYPES):
        raise ValueError(f"{where}: article_type á að vera null eða eitt af: {', '.join(sorted(ARTICLE_TYPES))}")
    return {"title": title.strip(), "authors": [a.strip() for a in authors], "page_start": page, "article_type": kind}


def write_answer(cache_dir: Path, key: str, entries: list[dict]) -> Path:
    """Store the session's answer for the queued text `key` where `toc_via_llm` looks (the same file the API path
    writes, a JSON array), and take the text out of the queue. Each item is
    {"title": str, "authors": [str], "page_start": int | None, "article_type": one of ARTICLE_TYPES | None};
    authors, page_start and article_type may be left out. ValueError (Icelandic) when the shape is wrong, in which
    case nothing is written."""
    if not isinstance(key, str) or not KEY_PATTERN.fullmatch(key):
        raise ValueError("ógildur lykill: á að vera 40 sextándakerfisstafir (sha1)")
    if not isinstance(entries, list):
        raise ValueError("svarið á að vera fylki (listi) af færslum")
    clean = [_check_item(i, item) for i, item in enumerate(entries, 1)]
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{key}.json"
    _write_atomic(path, json.dumps(clean, ensure_ascii=False, indent=2))
    _clear_pending(cache_dir, key)
    return path


async def toc_via_llm(text: str, *, cache_dir: Path, client=None, model: str = DEFAULT_MODEL,
                      offline: bool = False, context: dict | None = None) -> list[TocEntry]:
    """The articles Claude reads out of a table of contents. `offline` never builds a client or reaches the network:
    a cached answer is used, otherwise the text is queued (with `context`, saying which issue it came from) and the
    result is empty."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    body = text[:_BODY_CHARS]
    key = cache_key(text, model)
    path = cache_dir / f"{key}.json"
    data = _read_answer(cache_dir, key)
    if data is None and offline:
        _queue(cache_dir, key, body, model, context)
        return []
    if data is None:
        if client is None:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        try:
            resp = await client.messages.create(model=model, max_tokens=4000,
                                                messages=[{"role": "user", "content": PROMPT + body}])
            raw = resp.content[0].text
        except Exception as exc:  # noqa: BLE001
            log.warning("llm toc failed: %s", exc)
            return []
        data = _parse_list(raw)
        if data is None:
            log.warning("llm toc answer is not a JSON list; not cached")
            return []
        _write_atomic(path, raw)  # only a usable answer is cached
    _clear_pending(cache_dir, key)                        # answered (by the API or by the session): off the queue
    out: list[TocEntry] = []
    for item in data:
        if not isinstance(item, dict) or not item.get("title"):
            continue
        section = item.get("article_type")
        out.append(TocEntry(str(item["title"]).strip(), _authors(item.get("authors")),
                            _page_start(item.get("page_start")),
                            section=section if isinstance(section, str) and section in ARTICLE_TYPES else None))
    return out
