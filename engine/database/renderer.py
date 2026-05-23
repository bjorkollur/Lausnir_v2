"""
Derives Layer 3 outputs (markdown, urlausn) purely from DB columns.
No database access — only takes a Document and SourceConfig.
These outputs are always re-generatable; never treat them as source of truth.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.config.sources import SourceConfig
    from engine.database.models import Document

_MONTHS_IS = [
    "janúar", "febrúar", "mars", "apríl", "maí", "júní",
    "júlí", "ágúst", "september", "október", "nóvember", "desember",
]


def _date_str(dt) -> str:
    if not dt:
        return ""
    return f"{dt.day}. {_MONTHS_IS[dt.month - 1]} {dt.year}"


def _court_eignarfall(abbr: str) -> str:
    """Convert court abbreviation to eignarfall (genitive) for H1/H2 titles."""
    _MAP = {
        "Hrd.":            "Hæstaréttar",
        "Lrd.":            "Landsréttar",
        "Féld.":           "Félagsdóms",
        "Endurupptkd.":    "Endurupptökudóms",
        "Hrd. málsk.":     "Hæstaréttar",
        "Persónuvnd.":     "Persónuverndar",
        "Hérd. Rvk.":      "Héraðsdóms Reykjavíkur",
        "Hérd. Reykn.":    "Héraðsdóms Reykjaness",
        "Hérd. Vestl.":    "Héraðsdóms Vesturlands",
        "Hérd. Vestfj.":   "Héraðsdóms Vestfjarða",
        "Hérd. Norðvest.": "Héraðsdóms Norðurlands vestra",
        "Hérd. Norðeyst.": "Héraðsdóms Norðurlands eystra",
        "Hérd. Austl.":    "Héraðsdóms Austurlands",
        "Hérd. Suðl.":     "Héraðsdóms Suðurlands",
    }
    return _MAP.get(abbr, abbr)


# ─── Niðurstöðumerkingar ─────────────────────────────────────────────────────
# HTML-athugasemd sem er ósýnileg í rendered markdown en auðfundin með leit.
# Sett inn rétt áður en niðurstöðukaflinn byrjar í body_text / lower_body_text.

NIÐURSTÖÐUR_MARKER = "<!-- NIÐURSTÖÐUR -->"

# Mynstur sem bera kennsl á upphaf niðurstöðukafla.
# Raðaðar eftir forgangi — nákvæmari mynstur fyrst.
_VERDICT_SECTION_PATTERNS = re.compile(
    r"^(Dómsorð|Úrskurðarorð|Niðurstaða|Niðurstaðan er|Niðurstöður|"
    r"Álit nefndarinnar|Álit umboðsmanns|Ákvörðun|Forsendur og niðurstaða)"
    r"\b",
    re.MULTILINE | re.IGNORECASE,
)


def inject_verdict_marker(text: str) -> str:
    """
    Finnur niðurstöðukafla í texta og setur <!-- NIÐURSTÖÐUR --> rétt á undan.
    Ef ekkert mynstur finnst er textinn skilinn óbreyttur.

    Dæmi — input:
        ... meginmál dómsins ...

        Dómsorð

        Stefndi greiði ...

    Output:
        ... meginmál dómsins ...

        <!-- NIÐURSTÖÐUR -->
        Dómsorð

        Stefndi greiði ...
    """
    match = _VERDICT_SECTION_PATTERNS.search(text)
    if not match:
        return text

    pos = match.start()

    # Farðu aftur að upphafi línunnar (til að setja merkinguna á eigin línu)
    line_start = text.rfind("\n", 0, pos)
    if line_start == -1:
        line_start = 0
    else:
        line_start += 1  # eftir \n

    # Settu inn marker með tveimur línubilum umhverfis
    before = text[:line_start].rstrip("\n")
    after = text[line_start:]
    return f"{before}\n\n{NIÐURSTÖÐUR_MARKER}\n{after}"


def _format_parties(parties_json: list | None, config: "SourceConfig") -> str:
    if not parties_json:
        return ""
    parts = []
    for p in parties_json:
        name = re.sub(r"^og\b\s*", "", (p.get("name") or "").strip())
        if not name:
            continue
        lawyer = p.get("lawyer") or ""
        s = f"**{name}**"
        if lawyer and lawyer != name:
            s += f" ({lawyer})"
        parts.append(s)
    return "\n".join(parts)


def to_markdown(doc: "Document", config: "SourceConfig") -> str:
    """Build the full .md string from DB columns."""
    court_egnf = _court_eignarfall(doc.court or config.abbreviation)
    vt = doc.verdict_type or config.verdict_type_default
    case_nr = doc.case_number or ""

    # Header
    url_line = f"##### {doc.url}\n" if doc.url else ""
    title_line = (
        f"# {vt} {court_egnf} – {case_nr}\n" if case_nr
        else f"# {vt} {court_egnf}\n"
    )
    date_line = f"## {_date_str(doc.document_date)}\n" if doc.document_date else ""

    parties_block = ""
    if config.parse_parties != "none" and (doc.plaintiffs or doc.defendants):
        plf = _format_parties(doc.plaintiffs, config)
        dfd = _format_parties(doc.defendants, config)
        if plf or dfd:
            parties_block = "### Aðilar\n"
            if plf and dfd:
                parties_block += f"{plf}\n\ngegn\n\n{dfd}\n"
            else:
                parties_block += f"{plf or dfd}\n"

    keywords_block = ""
    if doc.keywords:
        keywords_block = f"### Lykilorð\n{'. '.join(doc.keywords)}\n"

    reifun_block = ""
    if doc.summary:
        reifun_block = f"### Reifun\n{doc.summary}\n"

    header = (
        f"{url_line}{title_line}{date_line}\n"
        f"{parties_block}\n"
        f"{keywords_block}\n"
        f"{reifun_block}\n"
    )

    # Body — inject niðurstöðumerking í báðum textalögum
    body_h2 = f"## {vt} {court_egnf}"
    if doc.body_text and doc.body_text.strip():
        marked_body = inject_verdict_marker(doc.body_text.strip())
        body = f"{body_h2}\n\n{marked_body}"
    else:
        body = body_h2

    # Lower court (appended if present)
    if doc.lower_body_text and doc.lower_body_text.strip():
        marked_lower = inject_verdict_marker(doc.lower_body_text.strip())
        body += f"\n\n<!-- lægra dómstig -->\n\n{marked_lower}"

    return header + body + "\n"


def to_urlausn(doc: "Document", config: "SourceConfig") -> str:
    """'Hrd. E-25/2020 5. maí 2020 – Dómur'"""
    abbr = doc.court or config.abbreviation
    parts = [abbr]
    if doc.case_number:
        parts.append(doc.case_number)
    if doc.document_date:
        parts.append(_date_str(doc.document_date))
    vt = doc.verdict_type or config.verdict_type_default
    return f"{' '.join(parts)} – {vt}"


def write_markdown(doc: "Document", config: "SourceConfig", data_dir: str) -> Path:
    """Write .md to disk and return the path."""
    if not doc.case_number:
        raise ValueError(f"Cannot write markdown for doc without case_number: {doc.id}")
    safe_case = doc.case_number.replace("/", "-").replace(" ", "_")
    out_dir = Path(data_dir) / "markdown" / config.short_name
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{safe_case}.md"
    path.write_text(to_markdown(doc, config), encoding="utf-8")
    return path
