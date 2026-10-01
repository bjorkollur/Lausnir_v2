"""Filename stems for theses (logfraediritgerdir).

The importer stored each thesis PDF under thesis_stem() before the
verdict_filename migration, and most files on disk still carry that name — so
anything that has to find a thesis PDF must be able to recompute it. Kept here,
outside scripts/, so the API can do that without importing an importer.
"""
from __future__ import annotations

import re
import unicodedata

from engine.processors.extractor import clean_person_name, degree_to_namsstig

_TRANSLIT = str.maketrans({
    "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ý": "y", "ð": "d",
    "þ": "th", "æ": "ae", "ö": "o", "Á": "A", "É": "E", "Í": "I", "Ó": "O",
    "Ú": "U", "Ý": "Y", "Ð": "D", "Þ": "Th", "Æ": "Ae", "Ö": "O",
})


def transliterate(s: str) -> str:
    s = s.translate(_TRANSLIT)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return s


def author_initials(author: str | None) -> str:
    name = (clean_person_name(author) or "").replace(",", " ")
    parts = [p for p in transliterate(name).split() if p]
    return "".join(p[0].upper() for p in parts)


def thesis_stem(title: str, author: str | None, degree: str | None, max_len: int = 40) -> str:
    """Return the filename stem (no extension): {M|B|D}_{initials}_{title}."""
    _, abbr = degree_to_namsstig(degree)
    abbr = abbr or "X"
    prefix = f"{abbr}_{author_initials(author)}_"
    t = re.sub(r"\([^)]*\)", "", title)        # drop parenthetical content
    t = transliterate(t)
    t = re.sub(r"[^A-Za-z0-9]+", "_", t).strip("_")
    stem = (prefix + t)[:max_len].rstrip("_")
    return stem or (prefix.rstrip("_") or "thesis")
