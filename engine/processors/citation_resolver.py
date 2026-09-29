"""Resolve extracted citations against the corpus (spec §6). Task 1 adds only
norm_case_number; CitationIndex/resolve follow in Task 3."""
from __future__ import annotations

import re

_LEADING_ZEROS = re.compile(r"^([A-ZÞÆÖ]{1,2}-)?0+(?=\d)")


def norm_case_number(s: str | None) -> str | None:
    """'055/2001' → '55/2001'; '243 /2002' → '243/2002'; 'e-0012/2020' → 'E-12/2020'.

    Applied both to documents.case_number when building the index and to the
    numbers read from text, so the two sides always compare like for like.
    Hæstiréttur rows are stored with leading zeros (055/2001) and four with a
    stray space; citations never write them that way.
    """
    if s is None:
        return None
    s = "".join(s.split()).upper()
    if not s:
        return None
    return _LEADING_ZEROS.sub(lambda m: m.group(1) or "", s)
