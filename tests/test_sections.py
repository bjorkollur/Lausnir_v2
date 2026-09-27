"""classify_section / normalize_heading — shared by renderer and segmenter."""
import pytest
from engine.processors.sections import SECTION_KINDS, classify_section, normalize_heading


def test_section_kinds_exact_set():
    assert SECTION_KINDS == frozenset({
        "reifun", "malsmedferd", "malsatvik", "malsastaedur",
        "nidurstada", "domsord", "annad",
    })


@pytest.mark.parametrize("raw,expected", [
    ("## Niðurstaða", "Niðurstaða"),
    ("## Dómsorð:", "Dómsorð"),
    ("**IV. Niðurstaða.**", "IV. Niðurstaða"),
    ("###   Málsatvik  ", "Málsatvik"),
    ("## Úrskurðarorð_:_", "Úrskurðarorð"),
])
def test_normalize_heading(raw, expected):
    assert normalize_heading(raw) == expected


@pytest.mark.parametrize("heading,kind", [
    ("Dómsorð", "domsord"),
    ("## Dómsorð:", "domsord"),
    ("Úrskurðarorð", "domsord"),
    ("Ú rskurðarorð", "domsord"),          # spaced letters from old PDFs
    ("Ályktunarorð", "domsord"),
    ("Niðurstaða", "nidurstada"),
    ("IV. Niðurstaða", "nidurstada"),
    ("## IV. Niðurstaða", "nidurstada"),
    ("Niðurstöður", "nidurstada"),
    ("Forsendur og niðurstaða", "nidurstada"),
    ("Álit umboðsmanns Alþingis", "nidurstada"),
    ("Málsatvik", "malsatvik"),
    ("I. Málavextir", "malsatvik"),
    ("Atvik máls", "malsatvik"),
    ("Málsástæður og lagarök stefnanda", "malsastaedur"),
    ("Lagarök", "malsastaedur"),
    ("Sjónarmið kæranda", "malsastaedur"),
    ("Málsmeðferð og dómkröfur aðila", "malsmedferd"),
    ("Dómkröfur", "malsmedferd"),
    ("Kæruefni", "malsmedferd"),
    ("Skýrslur fyrir dómi", "annad"),
    ("", "annad"),
    (None, "annad"),
])
def test_classify_section(heading, kind):
    assert classify_section(heading) == kind


def test_renderer_still_exports_verdict_patterns():
    from engine.processors import renderer
    assert renderer._VERDICT_SECTION_PATTERNS.search("## Dómsorð:")
    assert renderer._VERDICT_SECTION_PATTERNS_COMMITTEE.search("## IV. Niðurstaða")
