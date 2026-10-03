# engine/timarit/article_type.py
import re

# A TOC section heading that names scholarly material outright. "\bgreinar" is anchored so that
# "Ritstjórnargreinar" is left to the editorial rule below.
_SCHOLARLY_SECTION = re.compile(r"fræðigrein|\bgreinar|ritgerð|erindi", re.I)

# Section headings are short and deliberate, so stems are fine here.
_SECTION_RULES = [
    ("Ritdómur", re.compile(r"ritdóm|bókarfregn|ritfregn", re.I)),
    ("Ritstjórnargrein", re.compile(r"frá ritstjór|ritstjórnargrein|leiðari|ávarp ritstjór", re.I)),
    ("Viðtal", re.compile(r"\bviðtal\b|\bviðtöl\b|í spjalli", re.I)),
    ("Dómareifun", re.compile(r"dómareifan|reifun dóm|dómar hæstaréttar|úr dómasafni", re.I)),
    ("Frétt", re.compile(r"\bfrétt|af félagsstarf|félagsmenn|dagskrá|tilkynning", re.I)),
]

# Titles are free text ("Dagskrárvald forseta", "Tilkynningarskylda lögmanna", "Fréttaflutningur og
# friðhelgi einkalífs" are articles), so these match whole words or the start of the title only.
_TITLE_RULES = [
    ("Ritdómur", re.compile(r"\britdóm|\bbókarfregn|\britfregn", re.I)),
    ("Ritstjórnargrein", re.compile(r"^\s*frá ritstjór|^\s*ávarp ritstjór|\britstjórnargrein|\bleiðari\b", re.I)),
    ("Viðtal", re.compile(r"^\s*(?:viðtal|viðtöl)\b|\bviðtal við\b", re.I)),
    ("Dómareifun", re.compile(r"\bdómareifan", re.I)),
    ("Frétt", re.compile(r"\bfrétt(?:ir)?\b|\bdagskrá\b|\btilkynning\b|\baf félagsstarf", re.I)),
]


def infer_article_type(section: str | None, title: str, default: str) -> str:
    """TOC section heading decides when there is one; otherwise the title; else the source default.

    A section that is given but matches no rule yields `default` without looking at the title.
    """
    if section and section.strip():
        if _SCHOLARLY_SECTION.search(section):
            return "Fræðigrein"
        for kind, pat in _SECTION_RULES:
            if pat.search(section):
                return kind
        return default
    for kind, pat in _TITLE_RULES:
        if pat.search(title or ""):
            return kind
    return default
