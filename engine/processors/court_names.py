"""Maps API court name strings to abbreviations used in documents.court column."""

_GRAPHQL_TO_ABBR: dict[str, str] = {
    # Héraðsdómstólar
    "Héraðsdómur Reykjavíkur":          "Hérd. Rvk.",
    "Héraðsdómur Reykjaness":           "Hérd. Reykn.",
    "Héraðsdómur Vesturlands":          "Hérd. Vestl.",
    "Héraðsdómur Vestfjarða":           "Hérd. Vestfj.",
    "Héraðsdómur Norðurlands vestra":   "Hérd. Norðvest.",
    "Héraðsdómur Norðurlands eystra":   "Hérd. Norðeyst.",
    "Héraðsdómur Austurlands":          "Hérd. Austl.",
    "Héraðsdómur Suðurlands":           "Hérd. Suðl.",
    # Æðri dómstólar
    "Landsréttur":                      "Lrd.",
    "Hæstiréttur":                      "Hrd.",
    "Félagsdómur":                      "Féld.",
    # Nefndir
    "Endurupptökudómur":                "Endurupptkd.",
    "Persónuvernd":                     "Persónuvnd.",
}


def graphql_to_abbreviation(court_name: str) -> str | None:
    return _GRAPHQL_TO_ABBR.get(court_name.strip())


def abbreviation_to_nominative(abbr: str) -> str:
    _ABBR_TO_NOM = {v: k for k, v in _GRAPHQL_TO_ABBR.items()}
    return _ABBR_TO_NOM.get(abbr, abbr)


def abbreviation_to_eignarfall(abbr: str) -> str:
    _MAP = {
        "Hrd.":            "Hæstaréttar",
        "Lrd.":            "Landsréttar",
        "Féld.":           "Félagsdóms",
        "Endurupptkd.":    "Endurupptökudóms",
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
