"""
Per-source configuration. Add one SourceConfig entry per source instead of
writing if/elif branches in the pipeline.
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class PdfCrop:
    header_pt: float = 0.0
    footer_pt: float = 0.0
    skip_header_on_first: bool = False
    # Size-based heading detection (héraðsdómstólar): {pt: "## "}
    heading_sizes: dict[float, str] = field(default_factory=dict)
    # Font-based heading detection (landsréttur): substring match on font name
    # e.g. {"BoldMT": "## "} matches "TimesNewRomanPS-BoldMT"
    heading_fonts: dict[str, str] = field(default_factory=dict)


@dataclass
class SourceConfig:
    short_name: str
    display_name: str
    abbreviation: str          # used in `documents.court` column
    instance_tier: int         # 1=first, 2=appeals, 3=supreme
    has_lower_court: bool      # True if body may embed lower court text
    parse_parties: str         # 'gegn' | 'role_based' | 'none'
    verdict_type_default: str  # fallback when not detected from text
    verdict_types_allowed: list[str] = field(default_factory=lambda: [
        "Dómur", "Úrskurður", "Niðurstaða", "Álit", "Dómsúrskurður",
    ])
    case_number_prefix: str = ""   # e.g. 'E-', 'S-', '' — used for validation
    pdf_crop: PdfCrop | None = None


# ─── Registry ────────────────────────────────────────────────────────────────

_SOURCES: list[SourceConfig] = [
    SourceConfig(
        short_name="haestirettur",
        display_name="Hæstiréttur",
        abbreviation="Hrd.",
        instance_tier=3,
        has_lower_court=True,
        parse_parties="gegn",
        verdict_type_default="Dómur",
        case_number_prefix="",
    ),
    SourceConfig(
        short_name="landsrettur",
        display_name="Landsréttur",
        abbreviation="Lrd.",
        instance_tier=2,
        has_lower_court=True,
        parse_parties="gegn",
        verdict_type_default="Dómur",
        case_number_prefix="",
        pdf_crop=PdfCrop(
            header_pt=0.0,
            footer_pt=65.0,
            skip_header_on_first=False,
            # "Bold" matches both old PDFs (Times New Roman,Bold / Times New Roman Bold,Bol)
            # and new PDFs (TimesNewRomanPS-BoldMT). Italic variants are excluded by
            # _heading_marker() — see extractor.py.
            heading_fonts={"Bold": "## "},
        ),
    ),
    SourceConfig(
        short_name="heradsdomstolar",
        display_name="Héraðsdómstólar",
        abbreviation="Hérd.",  # prefix — full abbr includes location, e.g. 'Hérd. Rvk.'
        instance_tier=1,
        has_lower_court=False,
        parse_parties="role_based",
        verdict_type_default="Dómur",
        case_number_prefix="",
        pdf_crop=PdfCrop(
            header_pt=65.0,
            footer_pt=62.0,
            skip_header_on_first=True,
            # Section headings are 12pt bold — same size as body text, so font-based
            # detection is needed. "Bold" matches TimesNewRomanPS-BoldMT and similar.
            heading_fonts={"Bold": "## "},
        ),
    ),
    SourceConfig(
        short_name="felagsdomur",
        display_name="Félagsdómur",
        abbreviation="Féld.",
        instance_tier=1,
        has_lower_court=False,
        parse_parties="gegn",
        verdict_type_default="Dómur",
        case_number_prefix="F-",
    ),
    SourceConfig(
        short_name="malskotsbeidnir",
        display_name="Málskotsbeiðnir Hæstaréttar",
        abbreviation="Hrd. málsk.",
        instance_tier=3,
        has_lower_court=False,
        parse_parties="gegn",
        verdict_type_default="Úrskurður",
    ),
    SourceConfig(
        short_name="endurupptokudomur",
        display_name="Endurupptökudómur",
        abbreviation="Endurupptkd.",
        instance_tier=1,
        has_lower_court=False,
        parse_parties="role_based",
        verdict_type_default="Úrskurður",
    ),
    SourceConfig(
        short_name="personuvernd",
        display_name="Persónuvernd",
        abbreviation="Persónuvnd.",
        instance_tier=1,
        has_lower_court=False,
        parse_parties="none",
        verdict_type_default="Úrskurður",
        verdict_types_allowed=["Úrskurður", "Álit", "Niðurstaða"],
    ),
]

# Index by short_name for O(1) lookup
SOURCE_REGISTRY: dict[str, SourceConfig] = {s.short_name: s for s in _SOURCES}


def get_config(short_name: str) -> SourceConfig:
    try:
        return SOURCE_REGISTRY[short_name]
    except KeyError:
        raise ValueError(f"Unknown source: {short_name!r}. Add it to engine/config/sources.py")


def all_configs() -> list[SourceConfig]:
    return list(_SOURCES)
