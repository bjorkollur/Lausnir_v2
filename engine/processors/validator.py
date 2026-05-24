"""
Validates a Document before or after storage.
Returns a list of validation error dicts — never raises, never drops documents.
Errors are stored in documents.validation_errors for later triage.
"""
from __future__ import annotations

import re
from datetime import date
from typing import TYPE_CHECKING

_CASE_YEAR_RE = re.compile(r'/(\d{4})$')
_CASE_NUM_PREFIX_RE = re.compile(r'^(\d+)/')
_MAX_CASE_NUM_PREFIX = 1500

if TYPE_CHECKING:
    from engine.config.sources import SourceConfig
    from engine.database.models import Document

_MIN_BODY_CHARS = 200
_MAX_DATE = date(2030, 1, 1)
_MIN_DATE = date(1900, 1, 1)


def validate(doc: "Document", config: "SourceConfig") -> list[dict]:
    errors: list[dict] = []

    def err(field: str, message: str) -> None:
        errors.append({"field": field, "message": message})

    # case_number
    if not doc.case_number:
        err("case_number", "Missing")
    elif config.case_number_prefix and not doc.case_number.startswith(config.case_number_prefix):
        err("case_number", f"Expected prefix {config.case_number_prefix!r}, got {doc.case_number!r}")
    else:
        m_prefix = _CASE_NUM_PREFIX_RE.match(doc.case_number or "")
        if m_prefix:
            num = int(m_prefix.group(1))
            if num > _MAX_CASE_NUM_PREFIX:
                err("case_number", f"Fyrrihluti málsnúmers ({num}) er óvænt hár (> {_MAX_CASE_NUM_PREFIX}) — líklega villa í þáttun")

    # document_date
    if doc.document_date is None:
        err("document_date", "Missing")
    elif not (_MIN_DATE <= doc.document_date <= _MAX_DATE):
        err("document_date", f"Out of range: {doc.document_date}")
    elif doc.case_number:
        m = _CASE_YEAR_RE.search(doc.case_number)
        if m:
            case_year = int(m.group(1))
            doc_year = doc.document_date.year
            if doc_year < case_year:
                err("document_date", f"Dagsetning ({doc_year}) á undan stofnunarári máls ({case_year})")
            elif doc_year - case_year >= 4:
                err("document_date", f"Dagsetning ({doc_year}) er {doc_year - case_year} árum seinna en stofnunarár ({case_year})")

    # court
    if not doc.court:
        err("court", "Missing")
    elif not doc.court.startswith(config.abbreviation):
        err("court", f"Expected abbreviation starting with {config.abbreviation!r}, got {doc.court!r}")

    # verdict_type
    if not doc.verdict_type:
        err("verdict_type", "Missing")
    elif doc.verdict_type not in config.verdict_types_allowed:
        err("verdict_type", f"Unexpected type: {doc.verdict_type!r}")

    # body_text
    if not doc.body_text:
        err("body_text", "Missing")
    elif len(doc.body_text) < _MIN_BODY_CHARS:
        err("body_text", f"Suspiciously short: {len(doc.body_text)} chars")

    # parties (only for adversarial sources)
    if config.parse_parties != "none":
        if not doc.plaintiffs:
            err("plaintiffs", "Empty")
        if not doc.defendants:
            err("defendants", "Empty")

    # keywords
    if not doc.keywords:
        err("keywords", "Missing")

    return errors
