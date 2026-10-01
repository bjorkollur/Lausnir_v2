"""Locate the original PDF of a document on disk.

The importers did not agree on a filename, so a lookup by any single key misses
most of the corpus:

- court rulings (héraðsdómstólar, Landsréttur, Endurupptökudómur) are stored
  under ``verdict_filename``;
- books are stored under ``external_id`` (the ISBN);
- most theses are stored under the ``thesis_stem()`` name computed at import,
  which predates their current ``verdict_filename``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from engine.config.sources import SourceConfig


def _exists(p: Path) -> bool:
    """Path.exists() that tolerates unusable names.

    Some verdict_filenames are long enough that the filesystem rejects the path
    outright (ENAMETOOLONG), which raises rather than returning False.
    """
    try:
        return p.exists()
    except OSError:
        return False


def find_stored_pdf(
    cfg: SourceConfig,
    *,
    verdict_filename: str | None,
    external_id: str | None,
    case_number: str | None = None,
    raw: dict[str, Any] | None = None,
) -> Path | None:
    """Return the path of the document's stored PDF, or None if there is none."""
    for key in (verdict_filename, external_id):
        if key:
            p = cfg.pdf_path(key)
            if _exists(p):
                return p
    if cfg.short_name == "logfraediritgerdir":
        # Imported lazily: only theses need it, and it pulls in the extractor.
        from engine.processors.thesis_names import thesis_stem
        raw = raw or {}
        p = cfg.pdf_path(thesis_stem(case_number or "", raw.get("author"), raw.get("degree")))
        if _exists(p):
            return p
    return None
