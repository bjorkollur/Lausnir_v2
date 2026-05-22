"""
Extracts structured NORM-layer fields from raw_api_data.
Each source has its own extraction logic registered here.
The extractor never reads from DB — only transforms raw_api_data.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from bs4 import BeautifulSoup

from engine.config.sources import SourceConfig


class Extractor:
    """Extract normalised fields from raw_api_data for a given source."""

    def __init__(self, config: SourceConfig) -> None:
        self.config = config

    def extract(self, raw: dict[str, Any]) -> dict[str, Any]:
        """
        Returns a dict of Document column values extracted from raw.
        Caller is responsible for merging with external_id, url, source_id.
        """
        fn = _EXTRACTORS.get(self.config.short_name)
        if fn is None:
            raise NotImplementedError(
                f"No extractor registered for {self.config.short_name!r}. "
                "Add one to engine/processors/extractor.py"
            )
        return fn(raw, self.config)


# ─── Shared helpers ───────────────────────────────────────────────────────────

_MONTHS_IS = {
    "janúar": 1, "febrúar": 2, "mars": 3, "apríl": 4, "maí": 5, "júní": 6,
    "júlí": 7, "ágúst": 8, "september": 9, "október": 10, "nóvember": 11, "desember": 12,
}


def _parse_icelandic_date(s: str | None) -> date | None:
    if not s:
        return None
    # ISO format first
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    # "5. maí 2020" format
    m = re.match(r"(\d{1,2})\.\s*([a-záéíóúýðþæö]+)\s+(\d{4})", s, re.IGNORECASE)
    if m:
        month = _MONTHS_IS.get(m[2].lower())
        if month:
            try:
                return date(int(m[3]), month, int(m[1]))
            except ValueError:
                return None
    return None


def _keywords_from_content(content_list: list | None) -> list[str]:
    if not content_list:
        return []
    out = []
    for item in content_list:
        if isinstance(item, str):
            out.append(item.strip())
        elif isinstance(item, dict):
            title = item.get("title") or item.get("name") or ""
            if title:
                out.append(title.strip())
    return [k for k in out if k]


def _rich_text_to_plain(nodes: list | None) -> str:
    if not nodes:
        return ""
    parts = []
    for node in nodes:
        if isinstance(node, dict):
            if node.get("nodeType") == "text":
                parts.append(node.get("value", ""))
            elif "content" in node:
                parts.append(_rich_text_to_plain(node["content"]))
    return "".join(parts).strip()


def _html_to_plain(html: str | None) -> str | None:
    """Strip HTML tags to plain text. Returns None for blank/None/non-string input."""
    if not html or not isinstance(html, str):
        return None
    text = BeautifulSoup(html, "html.parser").get_text(separator="\n").strip()
    return text or None


def _detect_verdict_type(plain_text: str | None, keywords: list) -> str | None:
    """Return 'Úrskurður' if body text signals an úrskurður, else None."""
    if not plain_text:
        return None
    if re.search(r"Úrskurðarorð|úrskurðar\b", plain_text, re.IGNORECASE):
        return "Úrskurður"
    return None


def _parse_parties_gegn(title: str | None) -> tuple[list, list]:
    """'A ehf. gegn B hf.' → ([{name:A ehf.}], [{name:B hf.}])"""
    if not title:
        return [], []
    parts = re.split(r"\bgegn\b", title, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) == 2:
        return (
            [{"name": parts[0].strip(), "lawyer": None}],
            [{"name": parts[1].strip(), "lawyer": None}],
        )
    return [{"name": title.strip(), "lawyer": None}], []


# ─── Per-source extractors ────────────────────────────────────────────────────

def _richtext_body(value: str | dict | None) -> str | None:
    """Parse richText from island.is — either HTML string or Contentful rich text dict."""
    if not value:
        return None
    if isinstance(value, str):
        return _html_to_plain(value)
    if isinstance(value, dict):
        content = value.get("document", {}).get("content")
        text = _rich_text_to_plain(content)
        return text or None
    return None


def _extract_haestirettur(raw: dict, config: SourceConfig) -> dict:
    title = raw.get("title") or raw.get("caseTitle") or ""
    plf, dfd = _parse_parties_gegn(title)
    plain_body = _richtext_body(raw.get("richText"))
    return {
        "case_number": raw.get("caseNumber") or raw.get("id"),
        "document_date": _parse_icelandic_date(
            raw.get("verdictDate") or raw.get("date") or raw.get("dateOfRuling")
        ),
        "court": config.abbreviation,
        "verdict_type": (
            _detect_verdict_type(plain_body, raw.get("keywords") or [])
            or config.verdict_type_default
        ),
        "instance_tier": config.instance_tier,
        "plaintiffs": plf or None,
        "defendants": dfd or None,
        "keywords": _keywords_from_content(
            raw.get("keywords") or raw.get("categories")
        ),
        "summary": (
            raw.get("presentings") or raw.get("abstract") or raw.get("summary") or None
        ),
        "body_text": plain_body or raw.get("text") or raw.get("content") or None,
        "lower_body_text": raw.get("lowerCourtText") or None,
        "raw_api_data": raw,
    }


def _extract_landsrettur(raw: dict, config: SourceConfig) -> dict:
    return _extract_haestirettur(raw, config)


def _extract_heradsdomstolar(raw: dict, config: SourceConfig) -> dict:
    # Héraðsdómar come from island.is GraphQL — similar shape to haestirettur
    # court abbreviation includes location, e.g. 'Hérd. Rvk.'
    court_name = raw.get("court") or raw.get("courtName") or ""
    from engine.processors.court_names import graphql_to_abbreviation
    abbr = graphql_to_abbreviation(court_name) or config.abbreviation
    title = raw.get("title") or raw.get("caseTitle") or ""
    plf, dfd = _parse_parties_gegn(title)
    return {
        "case_number": raw.get("caseNumber"),
        "document_date": _parse_icelandic_date(raw.get("date")),
        "court": abbr,
        "verdict_type": raw.get("type") or config.verdict_type_default,
        "instance_tier": config.instance_tier,
        "plaintiffs": plf or None,
        "defendants": dfd or None,
        "keywords": _keywords_from_content(raw.get("keywords")),
        "summary": raw.get("abstract") or None,
        "body_text": raw.get("text") or None,
        "lower_body_text": None,
        "raw_api_data": raw,
    }


def _extract_felagsdomur(raw: dict, config: SourceConfig) -> dict:
    plf, dfd = _parse_parties_gegn(raw.get("title"))
    return {
        "case_number": raw.get("caseNumber"),
        "document_date": _parse_icelandic_date(raw.get("date")),
        "court": config.abbreviation,
        "verdict_type": raw.get("type") or config.verdict_type_default,
        "instance_tier": config.instance_tier,
        "plaintiffs": plf or None,
        "defendants": dfd or None,
        "keywords": _keywords_from_content(raw.get("keywords")),
        "summary": raw.get("abstract") or None,
        "body_text": raw.get("text") or None,
        "lower_body_text": None,
        "raw_api_data": raw,
    }


def _extract_malskotsbeidnir(raw: dict, config: SourceConfig) -> dict:
    plf, dfd = _parse_parties_gegn(raw.get("title"))
    # richText detail node
    content = raw.get("content") or {}
    body = _rich_text_to_plain(content.get("json", {}).get("content"))
    return {
        "case_number": raw.get("caseNumber") or raw.get("id"),
        "document_date": _parse_icelandic_date(raw.get("date")),
        "court": config.abbreviation,
        "verdict_type": config.verdict_type_default,
        "instance_tier": config.instance_tier,
        "plaintiffs": plf or None,
        "defendants": dfd or None,
        "keywords": _keywords_from_content(raw.get("keywords")),
        "summary": None,
        "body_text": body or raw.get("text") or None,
        "lower_body_text": None,
        "raw_api_data": raw,
    }


def _extract_personuvernd(raw: dict, config: SourceConfig) -> dict:
    return {
        "case_number": raw.get("caseNumber") or raw.get("id"),
        "document_date": _parse_icelandic_date(raw.get("date")),
        "court": config.abbreviation,
        "verdict_type": raw.get("type") or config.verdict_type_default,
        "instance_tier": config.instance_tier,
        "plaintiffs": None,
        "defendants": None,
        "keywords": _keywords_from_content(raw.get("keywords")),
        "summary": raw.get("abstract") or None,
        "body_text": raw.get("text") or None,
        "lower_body_text": None,
        "raw_api_data": raw,
    }


def _extract_endurupptokudomur(raw: dict, config: SourceConfig) -> dict:
    plf, dfd = _parse_parties_gegn(raw.get("title"))
    return {
        "case_number": raw.get("caseNumber"),
        "document_date": _parse_icelandic_date(raw.get("date")),
        "court": config.abbreviation,
        "verdict_type": raw.get("type") or config.verdict_type_default,
        "instance_tier": config.instance_tier,
        "plaintiffs": plf or None,
        "defendants": dfd or None,
        "keywords": _keywords_from_content(raw.get("keywords")),
        "summary": raw.get("abstract") or None,
        "body_text": raw.get("text") or None,
        "lower_body_text": None,
        "raw_api_data": raw,
    }


# ─── Registry ─────────────────────────────────────────────────────────────────

_EXTRACTORS: dict[str, Any] = {
    "haestirettur": _extract_haestirettur,
    "landsrettur": _extract_landsrettur,
    "heradsdomstolar": _extract_heradsdomstolar,
    "felagsdomur": _extract_felagsdomur,
    "malskotsbeidnir": _extract_malskotsbeidnir,
    "personuvernd": _extract_personuvernd,
    "endurupptokudomur": _extract_endurupptokudomur,
}
