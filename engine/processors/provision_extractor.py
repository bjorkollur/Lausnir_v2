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

Pass 1 — backward scan from each law number: find all law numbers in the text,
          then scan backward to collect the chain of article anchors (gr. N)
          that directly precede it. The nearest anchor is always admitted (the
          gap between it and the law number can contain the law name, e.g.
          "umferðarlaga"). Each additional anchor further back is admitted only
          if the text between it and the next anchor in the chain consists
          solely of conjunctions (og, sbr., comma, whitespace). The scan stops
          as soon as a non-connector gap is encountered.

          This correctly handles multi-article chains of any length (I1) and
          prevents orphan anchors from stealing a neighbour's law number (I2).

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

# Combined: optional tölulið, optional mgr, gr, optional suffix, optional trailing mgr
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

# Connector-only text that may appear between consecutive article anchors in a chain.
# A gap that matches this (and only this) keeps the chain going.
_CHAIN_CONNECTOR_RE = re.compile(
    r'^\s*(?:og\s+(?:sbr\.\s*)?|sbr\.\s*|,\s*|og,\s*)*$',
    re.IGNORECASE | re.UNICODE,
)

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

    # Collect all article anchor matches once (shared by both passes)
    art_matches = list(_ART_RE.finditer(text))

    # ── Pass 1: backward scan from each law number ────────────────────────────
    law_matches = list(_LAW_NUM_RE.finditer(text))

    # Track which article anchors have already been claimed to prevent
    # double-assignment when multiple law numbers are close together.
    claimed: set[int] = set()  # indices into art_matches

    for law_m in law_matches:
        law_str = law_m.group("law")
        law_start = law_m.start()

        # Article anchors that end before this law number and are still unclaimed,
        # ordered nearest-first (reversed list of preceding anchors).
        preceding = [
            (i, am) for i, am in enumerate(art_matches)
            if am.end() <= law_start and i not in claimed
        ]
        # preceding is in document order; reverse so index 0 = nearest to law
        preceding_rev = list(reversed(preceding))

        chain: list[tuple[int, re.Match]] = []
        for idx, (art_idx, art_m) in enumerate(preceding_rev):
            if idx == 0:
                # Nearest anchor: always admit it — the gap between the last gr.
                # and the law number may contain the law name (e.g. "umferðarlaga")
                # which is already handled by _LAW_NUM_RE's own keyword prefix.
                chain.append((art_idx, art_m))
            else:
                # Further anchors: the gap between this anchor's end and the
                # previous (nearer) anchor's start must be connector-only.
                _, prev_art_m = preceding_rev[idx - 1]
                gap = text[art_m.end():prev_art_m.start()]
                if _CHAIN_CONNECTOR_RE.match(gap):
                    chain.append((art_idx, art_m))
                else:
                    # Non-connector gap: chain stops here
                    break

        for art_idx, art_m in chain:
            claimed.add(art_idx)
            results.append(_art_to_dict(art_m, law_str))

    # Collect all law positions for anaphora resolution
    all_law_positions: list[tuple[int, str]] = [
        (lm.start(), lm.group("law")) for lm in law_matches
    ]

    # ── Pass 2: anaphoric "sömu laga" references ─────────────────────────────
    for art_m in art_matches:
        art_end = art_m.end()
        # Look ahead up to 150 chars for an anaphoric phrase
        window_end = min(len(text), art_end + 150)
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
