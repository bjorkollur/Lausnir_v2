# engine/timarit/leitir_articles.py
"""Article records from leitir.is (Primo VE public API) — spec §5.1.

Same endpoint as engine.processors.book_metadata.lookup_leitir; this one searches
for articles and parses `display.ispartof`, which carries year, volume, issue and
page range: '2010; 60 (1): bls. 7-47 Tímarit lögfræðinga :$$Q…$$Z<record>'.
Every query is cached on disk so re-runs cost nothing and are reproducible, and
uncached requests are spaced ≥ 1 s apart.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from engine.processors.book_metadata import _clean_primo_name
from engine.timarit.ulfljotur_xlsx import _fold, title_similarity

log = logging.getLogger(__name__)

_URL = "https://leitir.is/primaws/rest/pub/pnxs"
_PARAMS = {"scope": "10000_MYLIB", "tab": "Everything", "vid": "354ILC_NETWORK:10000_UNION",
           "inst": "354ILC_NETWORK", "lang": "is", "offset": "0"}
# hyphen-minus, en dash, em dash and minus sign all turn up as range separators in Primo data
_DASH = r"[-\u2013\u2014\u2212]"
_ISPARTOF = re.compile(
    r"(?P<pre>.*?)(?P<year>\d{4});\s*(?P<vol>\d+)?\s*"
    rf"(?:\((?P<issue>\d+(?:\s*(?:{_DASH}|/)\s*\d+)?)\))?\s*:?\s*"
    rf"(?:bls\.\s*(?P<p1>\d+)(?:\s*{_DASH}\s*(?P<p2>\d+))?)?(?P<post>.*)")
_ISSUE_SEP = re.compile(rf"\s*({_DASH}|/)\s*")
_RECORD = re.compile(r"\$\$Z(\d+)")
_QNAME = re.compile(r"\$\$Q([^$]+)")
_MIN_INTERVAL = 1.0
_lock = asyncio.Lock()
_last_call = 0.0


@dataclass(frozen=True)
class LeitirArticle:
    title: str
    authors: list[str]
    journal: str | None
    year: int | None
    volume: int | None
    issue: str | None
    page_start: int | None
    page_end: int | None
    record_id: str | None


def _page_end(p1: str, p2: str | None) -> int | None:
    """End page of a range: `7-47` -> 47; abbreviated `107-9` -> 109, `107-19` -> 119; impossible ranges -> None."""
    if p2 is None:
        return int(p1)
    if int(p2) >= int(p1):
        return int(p2)
    if len(p2) < len(p1):
        expanded = int(p1[:len(p1) - len(p2)] + p2)
        if expanded > int(p1):
            return expanded
    return None


def parse_ispartof(s: str) -> dict:
    out = {"year": None, "volume": None, "issue": None, "page_start": None, "page_end": None, "journal": None, "record_id": None}
    m = _ISPARTOF.match(s or "")
    if not m or not m.group("year"):
        return out
    out["year"] = int(m.group("year"))
    out["volume"] = int(m.group("vol")) if m.group("vol") else None
    if m.group("issue"):
        out["issue"] = _ISSUE_SEP.sub(lambda sep: "/" if sep.group(1) == "/" else "-", m.group("issue"))
    if m.group("p1"):
        out["page_start"] = int(m.group("p1"))
        out["page_end"] = _page_end(m.group("p1"), m.group("p2"))
    q = _QNAME.search(s)
    journal = q.group(1) if q else (m.group("pre") or m.group("post"))
    out["journal"] = re.sub(r"\s*:\s*$", "", journal.split("$$")[0]).strip() or None
    r = _RECORD.search(s)
    out["record_id"] = r.group(1) if r else None
    return out


def _to_article(doc: object) -> LeitirArticle | None:
    pnx = doc.get("pnx") if isinstance(doc, dict) else None
    disp = pnx.get("display") if isinstance(pnx, dict) else None
    if not isinstance(disp, dict) or "article" not in (disp.get("type") or []):
        return None
    title = (disp.get("title") or [""])[0].strip().rstrip(" .")
    authors = [_clean_primo_name(c) for c in (disp.get("creator") or []) if c and c.strip()]
    part = parse_ispartof((disp.get("ispartof") or [""])[0])
    return LeitirArticle(title, authors, part["journal"], part["year"], part["volume"], part["issue"],
                         part["page_start"], part["page_end"], part["record_id"])


def _is_response(data: object) -> bool:
    """A real answer from leitir.is: a JSON object with a `docs` list (possibly empty). Anything else — a rate-limit
    message, an error object — is transient and must be neither cached nor trusted from the cache."""
    return isinstance(data, dict) and isinstance(data.get("docs"), list)


def _read_cache(path: Path) -> dict | None:
    """The cached response, or None when there is no usable cache file (missing, unreadable, not a response)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        log.warning("ignoring unreadable leitir.is cache file %s: %s", path.name, exc)
        return None
    if not _is_response(data):
        log.warning("ignoring leitir.is cache file %s: not a response with a docs list", path.name)
        return None
    return data


def _write_cache(path: Path, data: dict) -> None:
    """Atomic write (temp file in the same directory + os.replace); a failure is logged, never raised."""
    tmp_name = None
    try:
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.stem + ".", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp_name, path)
        tmp_name = None
    except OSError as exc:
        log.warning("could not write leitir.is cache file %s: %s", path.name, exc)
    finally:
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


async def search_articles(client: httpx.AsyncClient, query: str, *, cache_dir: Path, limit: int = 10,
                          journal_filter: str | None = None) -> list[LeitirArticle]:
    global _last_call
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(f"{query}|{limit}".encode("utf-8")).hexdigest()
    path = cache_dir / f"{key}.json"
    data = _read_cache(path)
    if data is None:
        async with _lock:
            wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                try:
                    resp = await client.get(_URL, params={**_PARAMS, "q": f"any,contains,{query}", "limit": str(limit)}, timeout=20.0)
                finally:
                    _last_call = time.monotonic()   # failed attempts count too: ≤ 1 request/s whatever happens
                resp.raise_for_status()
                data = resp.json()
            except (httpx.HTTPError, ValueError) as exc:    # ValueError: a 200 whose body is not JSON
                log.warning("leitir.is article search failed for %r: %s", query, exc)
                return []
        if not _is_response(data):
            log.warning("leitir.is article search for %r returned no docs list (not cached): %.200r", query, data)
            return []
        _write_cache(path, data)
    arts = [a for a in map(_to_article, data["docs"]) if a]
    if journal_filter:
        arts = [a for a in arts if a.journal and title_similarity(a.journal, journal_filter) > 0.8]
    return arts


_DISTINCTIVE_WORDS = 3
_DISTINCTIVE_CHARS = 20
_TIE = 1e-9


def _distinctive(head: str) -> bool:
    """A subtitle-less head that names one article: ≥ 3 words or ≥ 20 folded characters.
    Bare rubrics ('Ritdómur', 'Frá ritstjóra', 'Formáli', 'Dómar') are not distinctive."""
    folded = _fold(head)
    return len(folded.split()) >= _DISTINCTIVE_WORDS or len(folded) >= _DISTINCTIVE_CHARS


def _title_score(title: str, record_title: str, *, page_hit: bool, page_conflict: bool) -> float:
    """Similarity of a TOC title to a Primo title ('Head : subtitle'). The part before the subtitle may stand in for
    the full title only when it is distinctive or the page numbers agree, and never when they disagree: otherwise
    any 'Ritdómur : <anything>' of the right year would match the bare TOC rubric 'Ritdómur'."""
    score = title_similarity(title, record_title)
    head, sep, _ = record_title.partition(" : ")
    if sep and not page_conflict and (page_hit or _distinctive(head)):
        score = max(score, title_similarity(title, head))
    return score


def best_match(records: list[LeitirArticle], *, title: str | None, page_start: int | None,
               year: int | None, volume: int | None = None) -> LeitirArticle | None:
    """The record that best fits a TOC entry, or None. `year` and `volume` are gates: a record whose (known) year or
    volume differs from the given one is a different issue and is skipped, whatever its title says. A tie for the
    top score is no match: the choice would be an accident of list order."""
    best, best_s, tie = None, 0.0, False
    for r in records:
        if year is not None and r.year is not None and r.year != year:
            continue
        if volume is not None and r.volume is not None and r.volume != volume:
            continue
        page_hit = page_start is not None and r.page_start == page_start
        page_conflict = page_start is not None and r.page_start is not None and r.page_start != page_start
        s = 0.0
        if title:
            s += 0.7 * _title_score(title, r.title, page_hit=page_hit, page_conflict=page_conflict)
        if page_hit:
            s += 0.25
        if year is not None and r.year == year:
            s += 0.05
        if s <= 0:
            continue
        if s > best_s + _TIE:
            best, best_s, tie = r, s, False
        elif abs(s - best_s) <= _TIE:
            tie = True
    # With a title the score must clear 0.55; without one only a page hit confirmed by the year (0.25 + 0.05) counts.
    threshold = 0.55 if title else 0.30
    return best if best is not None and not tie and best_s >= threshold - _TIE else None
