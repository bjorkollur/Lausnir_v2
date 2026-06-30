"""Extract structured law provision citations from Icelandic legal text.

Handles the citation patterns found in Hæstiréttur, Landsréttur, Héraðsdómar
and regulatory body rulings (Yfirskattanefnd, Samgöngustofa, etc.).

Returns a deduplicated list of dicts, each representing one provision cite:
    {"law": "19/1940", "gr": 218, "mgr": 1}             # paragraph + article
    {"law": "19/1940", "gr": 218, "mgr": None}           # article only
    {"law": "19/1940", "gr": 218, "mgr": 1, "sfx": "a"} # with suffix

Law numbers cover both domestic statutes (19/1940) and EU/EEA regulations
(261/2004) since they share the same X/YYYY format.

Strategy
--------
Two-pass approach:

Pass 1 — for each article anchor (gr. N), scan ahead up to LOOKAHEAD_CHARS
          to find the nearest law number (nr. X/YYYY or bare X/YYYY after a
          law keyword). This naturally handles multi-article chains where
          several "X. gr." share one law number at the end.

Pass 2 — anaphoric "sömu laga" / "þeirra laga" / "laganna" references:
          propagate the last-seen law number to article anchors followed by
          these phrases.
"""
from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Sub-patterns
# ---------------------------------------------------------------------------

_LAW_NUM_PAT = r'\d+/\d{4}'

# Optional tölulið prefix (we capture nothing from it)
_TOLULID = r'(?:\d+\.\s*tölul\.\s*)?'

# Article anchor: optional mgr before gr, then gr number, optional suffix
# Named groups: mgr, gr, sfx, mgr2
_ART_ANCHOR_PAT = (
    r'(?P<mgr>\d+)\.\s*mgr\.\s*'   # optional mgr (we handle optionality outside)
    r'(?P<gr>\d+)\.\s*gr\.'
    r'(?:\s*(?P<sfx>[a-záðéíóúýþæö])\.)?'
    r'(?:\s*(?P<mgr2>\d+)\.\s*mgr\.)?'
)
_ART_ANCHOR_NO_MGR_PAT = (
    r'(?P<gr>\d+)\.\s*gr\.'
    r'(?:\s*(?P<sfx>[a-záðéíóúýþæö])\.)?'
    r'(?:\s*(?P<mgr2>\d+)\.\s*mgr\.)?'
)

# Combined: optional tölulið, optional mgr, gr, optional suffix
_ART_RE = re.compile(
    _TOLULID +
    r'(?:'
        r'(?P<mgr>\d+)\.\s*mgr\.\s*'
    r')?'
    r'(?P<gr>\d+)\.\s*gr\.'
    r'(?:\s*(?P<sfx>[a-záðéíóúýþæö])\.)?'
    r'(?:\s*(?P<mgr2>\d+)\.\s*mgr\.)?',
    re.IGNORECASE | re.UNICODE,
)

# Law number reference: "nr. X/YYYY" or bare "laga X/YYYY" etc.
# This matches a law number after a keyword window.
_LAW_KEYWORD_PAT = r'(?:laga?|lögum|reglugerðar?)'
_LAW_NUM_RE = re.compile(
    r'(?:'
        # "nr. X/YYYY" — preceded by an optional law keyword
        r'(?:' + _LAW_KEYWORD_PAT + r'(?:\s+\w+){0,5}?\s+)?'
        r'nr\.\s*'
    r'|'
        # bare "laga X/YYYY" / "reglugerðar X/YYYY" (no "nr.")
        r'(?:' + _LAW_KEYWORD_PAT + r'(?:\s+\w+){0,5}?\s+)'
    r')'
    r'(?P<law>' + _LAW_NUM_PAT + r')',
    re.IGNORECASE | re.UNICODE,
)

# Anaphoric reference patterns
_SOMU_LAGA_RE = re.compile(
    r'sömu\s+laga|þeirra\s+laga|laganna|sömu\s+lögum',
    re.IGNORECASE | re.UNICODE,
)

# How far ahead (in characters) from gr. to look for a law number
LOOKAHEAD_CHARS = 150

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _art_to_dict(m: re.Match, law: str) -> dict[str, Any]:
    gr = int(m.group("gr"))
    mgr_raw = m.group("mgr")
    mgr2_raw = m.group("mgr2")
    mgr = int(mgr_raw) if mgr_raw else (int(mgr2_raw) if mgr2_raw else None)
    sfx_raw = m.group("sfx")
    sfx = sfx_raw.lower() if sfx_raw else None
    d: dict[str, Any] = {"law": law, "gr": gr, "mgr": mgr}
    if sfx:
        d["sfx"] = sfx
    return d


def _dedup(provisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple] = set()
    out: list[dict[str, Any]] = []
    for p in provisions:
        key = (p["law"], p["gr"], p.get("mgr"), p.get("sfx"))
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _find_law_in_window(text: str, start: int, end: int) -> str | None:
    """Search for a law number in text[start:end]. Returns the law string or None."""
    window = text[start:end]
    m = _LAW_NUM_RE.search(window)
    return m.group("law") if m else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_provisions(text: str | None) -> list[dict[str, Any]]:
    """Extract all law provision citations from Icelandic legal text.

    Returns a deduplicated list of provision dicts, or [] for empty/None input.
    """
    if not text:
        return []

    results: list[dict[str, Any]] = []
    last_law: str | None = None
    last_law_pos: int = -1  # position where last_law was found

    # ── Pass 1: article anchors → look ahead for law number ──────────────────
    for art_m in _ART_RE.finditer(text):
        art_end = art_m.end()
        window_end = min(len(text), art_end + LOOKAHEAD_CHARS)

        law = _find_law_in_window(text, art_end, window_end)
        if law is None:
            continue

        # Record last seen law (use the position of the law number in original text)
        law_m = _LAW_NUM_RE.search(text[art_end:window_end])
        if law_m:
            last_law = law
            last_law_pos = art_end + law_m.start()

        results.append(_art_to_dict(art_m, law))

    # Track all law numbers for anaphora resolution (need global last-seen law)
    # Re-scan to find all law numbers in document order
    all_law_positions: list[tuple[int, str]] = []
    for lm in _LAW_NUM_RE.finditer(text):
        all_law_positions.append((lm.start(), lm.group("law")))

    # ── Pass 2: anaphoric "sömu laga" references ─────────────────────────────
    for art_m in _ART_RE.finditer(text):
        art_end = art_m.end()
        window_end = min(len(text), art_end + LOOKAHEAD_CHARS)
        window = text[art_end:window_end]

        if not _SOMU_LAGA_RE.search(window):
            continue

        # Find the law number that appeared most recently before this article
        art_start = art_m.start()
        preceding_law: str | None = None
        for pos, law_str in all_law_positions:
            if pos < art_start:
                preceding_law = law_str
            else:
                break

        if preceding_law is None:
            continue

        results.append(_art_to_dict(art_m, preceding_law))

    return _dedup(results)
