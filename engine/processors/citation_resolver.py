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


import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Iterable

from sqlalchemy import text as _sql_text

COURT_SOURCES = ("haestirettur", "landsrettur", "heradsdomstolar", "felagsdomur",
                 "landsdomar", "endurupptokudomur", "malskotsbeidnir")
INDEX_SQL = """
    SELECT d.id, d.court, d.case_number, d.document_date, d.verdict_type
    FROM documents d JOIN sources s ON s.id = d.source_id
    WHERE s.short_name = ANY(:sources) AND d.case_number IS NOT NULL AND d.court IS NOT NULL
"""
HRD_COVERAGE_FROM_YEAR = 1999
# A case number with no letter prefix — the pre-2010 Félagsdómur form, and the
# form its judgments cite in prose whatever the era (see CitationIndex.candidates).
_BARE_NUM = re.compile(r"\d{1,4}/\d{4}")


@dataclass(frozen=True)
class Candidate:
    id: uuid.UUID
    court: str
    case_number: str
    document_date: date | None
    verdict_type: str | None


@dataclass(frozen=True)
class Resolution:
    status: str
    to_doc_id: uuid.UUID | None
    method: str | None
    confidence: float | None


class CitationIndex:
    """(court, normalised case number) -> candidates. Built once per run."""

    def __init__(self, rows: Iterable[tuple]) -> None:
        self._by_key: dict[tuple[str, str], list[Candidate]] = defaultdict(list)
        self._herd_by_num: dict[str, list[Candidate]] = defaultdict(list)
        for id_, court, case_number, document_date, verdict_type in rows:
            if court is None:
                continue
            num = norm_case_number(case_number)
            if num is None:
                continue
            c = Candidate(id_, court, num, document_date, verdict_type)
            self._by_key[(court, num)].append(c)
            if court.startswith("Hérd. "):
                self._herd_by_num[num].append(c)

    @classmethod
    async def load(cls, conn) -> "CitationIndex":
        rows = (await conn.execute(_sql_text(INDEX_SQL), {"sources": list(COURT_SOURCES)})).all()
        return cls(rows)

    def candidates(self, target_court: str | None, case_number: str | None) -> list[Candidate]:
        """Every stored ruling that the (court, number) pair could name.

        Exact on both, with two deliberate widenings — neither of them a guess:
        `Hérd.` without a place spans all districts, and a bare Félagsdómur
        number also tries the `F-` prefixed key. Félagsdómur changed its case
        number format in 2010 (`13/2001` before, `F-9/2019` after) but the
        prose in judgments keeps citing the bare form ('í máli nr. 5/2012'),
        so the bare key alone misses every post-2010 Félagsdómur case. Both
        keys are merged into one candidate list, so an ambiguity between them
        stays an ambiguity — resolve() still requires exactly one survivor.
        """
        if target_court is None:
            return []
        num = norm_case_number(case_number)
        if num is None:
            return []
        if target_court == "Hérd.":
            return list(self._herd_by_num.get(num, []))
        out = list(self._by_key.get((target_court, num), []))
        if target_court == "Féld." and _BARE_NUM.fullmatch(num):
            seen = {c.id for c in out}
            out += [c for c in self._by_key.get(("Féld.", "F-" + num), []) if c.id not in seen]
        return out


def _year_of(case_number: str | None) -> int | None:
    if not case_number:
        return None
    tail = case_number.rsplit("/", 1)[-1] if "/" in case_number else case_number.split("-", 1)[0]
    return int(tail) if tail.isdigit() and len(tail) == 4 else None


def resolve(raw, *, index: CitationIndex, from_doc_id: uuid.UUID, from_date: date | None) -> Resolution:
    if raw.form == "reporter" or raw.target_case_number is None:
        return Resolution("pre_coverage", None, None, None)
    cands = index.candidates(raw.target_court, raw.target_case_number)
    if from_date is not None:
        cands = [c for c in cands if c.document_date is None or c.document_date <= from_date]
    if not cands:
        yr = _year_of(raw.target_case_number)
        if raw.target_court == "Hrd." and yr is not None and yr < HRD_COVERAGE_FROM_YEAR:
            return Resolution("pre_coverage", None, None, None)
        return Resolution("unresolved", None, None, None)
    method, conf = "casenum_unique", 0.8
    if raw.target_date is not None:
        cands = [c for c in cands if c.document_date == raw.target_date]
        if not cands:
            return Resolution("unresolved", None, None, None)
        method, conf = "casenum_date", 1.0
    elif raw.target_verdict is not None and len(cands) > 1:
        narrowed = [c for c in cands if (c.verdict_type or "") == raw.target_verdict]
        if narrowed:
            cands, method, conf = narrowed, "casenum_verdict", 0.9
    if len(cands) > 1:
        return Resolution("ambiguous", None, None, None)
    c = cands[0]
    if c.id == from_doc_id:
        return Resolution("self", None, None, None)
    return Resolution("resolved", c.id, method, conf)
