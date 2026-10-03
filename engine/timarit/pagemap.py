"""Printed page numbers of a PDF (spec §5.2).

Reads the number from the first/last lines of each page with per-journal
patterns, fits the single offset printed = pdf_index + k that most pages
support, and infers the rest. Pages that disagree with the fit are treated as
misreads (adverts, covers, OCR noise). Nothing is stored for a document whose
numbers cannot be fitted — the anchor then falls back to the section.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from engine.timarit.config import PAGE_MAP_RESTART_RATIO

_BARE = re.compile(r"^\s*(\d{1,4})\s*$")
_LEADING = re.compile(r"^\s*(\d{1,4})\s+\S")          # "81 Samræmi laga …" (first line only)

PATTERNS: dict[str, list[re.Pattern]] = {
    "default": [_BARE],
    "ulfljotur": [_BARE, re.compile(r"^\s*Úlfljótur\s+(\d{1,4})\s*$", re.I), re.compile(r"^\s*(\d{1,4})\s+Úlfljótur\s*$", re.I)],
    "logretta": [_BARE, re.compile(r"^\s*[a-z]\s+(\d{1,4})\s*$")],
    "logbru": [_BARE, re.compile(r"^\s*(\d{1,4})\s*/\s*LÖGBRÚ\s*$", re.I)],
    "stjornmal_stjornsysla": [_BARE, _LEADING],
    "timarit_logfraedinga": [_BARE, _LEADING],
}
MIN_AGREEING = 3


def _candidates(page_text: str) -> list[str]:
    lines = [l for l in page_text.splitlines() if l.strip()]
    if not lines:
        return []
    return [lines[0], lines[-1]] if len(lines) > 1 else [lines[0]]


def read_page_numbers(page_texts: list[str], patterns: list[re.Pattern]) -> list[int | None]:
    """Extract page numbers using two-pass strategy: non-LEADING patterns first,
    then LEADING patterns only if no match found (to avoid weak headers preempting
    strong footers like bare numbers)."""
    out: list[int | None] = []
    for t in page_texts:
        found = None
        candidates = _candidates(t)

        # First pass: try all non-LEADING patterns on all candidate lines
        for i, line in enumerate(candidates):
            for pat in patterns:
                if pat is _LEADING:
                    continue  # Skip LEADING in first pass
                m = pat.match(line)
                if m:
                    found = int(m.group(1))
                    break
            if found is not None:
                break

        # Second pass: if still no match, try LEADING on first line only
        if found is None and candidates:
            for pat in patterns:
                if pat is _LEADING:
                    m = pat.match(candidates[0])
                    if m:
                        num = int(m.group(1))
                        # Optionally ignore year-like values (≥ 1800)
                        if num < 1800:
                            found = num
                            break

        out.append(found)
    return out


def fit_offset(read: list[int | None]) -> tuple[int | None, float]:
    """Fit the offset, returning (offset, runner_up_share).
    Ambiguous when: runner_up_votes >= max(MIN_AGREEING, PAGE_MAP_RESTART_RATIO * winner_votes)
    """
    votes = Counter(n - i for i, n in enumerate(read) if n is not None)
    if not votes:
        return None, 0.0

    most_common_list = votes.most_common(2)
    offset, winner_votes = most_common_list[0]

    # Check if winner has enough votes
    if winner_votes < MIN_AGREEING:
        return None, 0.0

    # Calculate runner-up share for ambiguity detection (only when there's a winner)
    if len(most_common_list) > 1:
        runner_up_votes = most_common_list[1][1]
        runner_up_share = runner_up_votes / winner_votes if winner_votes > 0 else 0.0
    else:
        runner_up_share = 0.0

    # Check if runner-up is strong enough to indicate ambiguity (restart)
    # Ambiguous when: runner_up_votes >= max(MIN_AGREEING, PAGE_MAP_RESTART_RATIO * winner_votes)
    if len(most_common_list) > 1:
        runner_up_votes = most_common_list[1][1]
        ambiguity_threshold = max(MIN_AGREEING, PAGE_MAP_RESTART_RATIO * winner_votes)
        if runner_up_votes >= ambiguity_threshold:
            return None, runner_up_share

    return offset, runner_up_share


@dataclass
class PageMap:
    offset: int | None
    printed: list[int | None]
    read: list[bool]
    quality: float
    runner_up_share: float = 0.0  # Runner-up offset votes / winner votes (for ambiguity tracking)

    def pdf_index_for(self, printed_page: int) -> int | None:
        if self.offset is None or printed_page < 1:
            return None
        idx = printed_page - self.offset
        # Also check that the target page number is actually in the printed list
        if not (0 <= idx < len(self.printed)):
            return None
        if self.printed[idx] is None:
            return None
        return idx

    def as_dict(self) -> dict:
        return {"offset": self.offset, "quality": round(self.quality, 3), "runner_up_share": round(self.runner_up_share, 3), "pages": len(self.printed)}


def build_page_map(page_texts: list[str], source: str) -> PageMap:
    patterns = PATTERNS.get(source, PATTERNS["default"])
    read = read_page_numbers(page_texts, patterns)
    offset, runner_up_share = fit_offset(read)
    n = len(page_texts)
    if offset is None:
        return PageMap(None, [None] * n, [False] * n, 0.0, runner_up_share)
    consistent = [r is not None and r - i == offset for i, r in enumerate(read)]
    printed = [i + offset if i + offset > 0 else None for i in range(n)]
    return PageMap(offset, printed, consistent, sum(consistent) / n if n else 0.0, runner_up_share)
