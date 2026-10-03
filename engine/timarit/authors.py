# engine/timarit/authors.py
"""Author strings as journals print them → clean names + titles (spec §5.5)."""
from __future__ import annotations

import re
import unicodedata

# Titles that end in a literal "." need no trailing boundary; word titles must not run on into
# more letters, and none may start inside a word ("Hallmundur" is not "llm").
# Longest alternatives first: the regex takes the first alternative that matches.
_DOTTED_TITLES = (
    r"dr\.\s*(?:juris|jur|phil|med)(?:\.|(?!\w))|dr\.|hrl\.|hdl\.|fv\.|"
    r"cand\.\s*jur\.|cand\.\s*mag\.|mag\.\s*jur\.|stud\.\s*jur\.|LL\.M\.|ph\.d\.?"
)
_WORD_TITLES = (
    r"hæstaréttarlögmaður|héraðsdómslögmaður|hæstaréttardómari|héraðsdómari|fyrrverandi|"
    r"prófessor|professor|dósent|lektor|aðjúnkt|lögmaður|lögfræðingur|ritstjóri|emeritus|emerítus|LLM|phd"
)
_TITLE_RE = re.compile(rf"(?<!\w)(?:{_DOTTED_TITLES}|(?:{_WORD_TITLES})(?!\w))", re.I)
_EMPTY_BRACKETS = re.compile(r"\(\s*[,;.\s]*\)")
_TRAILING_ROLE = re.compile(r"\s+(?:og|við|hjá)\s.*$", re.I)
_SURNAME_END = re.compile(r"(?:son|s?sen|d[óo]ttir)$", re.I)
# lowercase name particles ("Otto von Gierke"), accepted between name tokens by the lenient check
_PARTICLES = frozenset({"von", "van", "de", "der", "den", "la", "le", "af", "di", "du", "zu", "el", "al", "bin", "ben"})
_SPLIT = re.compile(r"\s*,\s*(?:og\s+)?|\s+og\s+|\s*&\s*|\s*;\s*", re.I)


def _titlecase_if_shouting(s: str) -> str:
    """"ANNA-KRISTÍN O'BRIEN" -> "Anna-Kristín O'Brien": capitalise every run of letters."""
    if not s.isupper():
        return s
    return re.sub(r"[^\W\d_]+", lambda m: m.group(0).capitalize(), s)


def _cap_word(w: str) -> bool:
    """An uppercase letter followed by one or more lowercase letters, in any alphabet (Müller, Čech, Jørgen)."""
    return len(w) >= 2 and w[0].isupper() and all(c.isalpha() and c.islower() for c in w[1:])


def _name_part(p: str) -> bool:
    """One hyphen-free part of a name: Jón, Müller, McDonald, MacLeod, O'Brien, D'Arcy."""
    if _cap_word(p):
        return True
    for prefix in ("Mc", "Mac"):
        if p.startswith(prefix) and _cap_word(p[len(prefix):]):
            return True
    head, sep, tail = p.replace("\u2019", "'").partition("'")
    return bool(sep) and (_cap_word(head) or (len(head) == 1 and head.isupper())) and _cap_word(tail)


def _is_name_token(t: str) -> bool:
    t = unicodedata.normalize("NFC", t)
    t = t[:-1] if t.endswith(".") else t
    return bool(t) and all(_name_part(p) for p in t.split("-"))


def _is_initial(t: str) -> bool:
    """A single uppercase letter, with or without its period ("A.", "Þ.", "A")."""
    t = unicodedata.normalize("NFC", t)
    return len(t) in (1, 2) and t[0].isupper() and (len(t) == 1 or t[1] == ".")


def clean_author(raw: str) -> tuple[str, str | None]:
    s = " ".join((raw or "").split())
    titles = [m.group(0) for m in _TITLE_RE.finditer(s)]
    name = _TITLE_RE.sub("", s)
    name = _EMPTY_BRACKETS.sub("", name)
    name = " ".join(name.split())
    name = _TRAILING_ROLE.sub("", name)
    name = re.sub(r"[,;:]+", ",", name).strip(" ,.-")
    name = re.sub(r"\s+,", ",", name)
    name = name.split(",")[0].strip() if "," in name and len(name.split(",")[0].split()) >= 2 else name
    name = _titlecase_if_shouting(name)
    norm_titles = []
    for t in titles:
        t = t.strip()
        norm_titles.append(t.lower() if t.lower() in ("prófessor", "dósent", "lektor") else t)
    return name, (", ".join(norm_titles) or None)


def split_authors(s: str) -> list[str]:
    parts = [p.strip() for p in _SPLIT.split(s or "") if p and p.strip()]
    # A part that was only a title or a role ("dósent", "dómari við Mannréttindadómstól Evrópu",
    # "löglærður fulltrúi") is not an author: keep parts whose cleaned form has the shape of a name.
    names = [n for n in (clean_author(p)[0] for p in parts) if looks_like_person(n, strict=False)]
    if names:
        return names
    whole = clean_author(s)[0] if s and s.strip() else ""
    return [whole] if whole else []


def looks_like_person(s: str, *, strict: bool = True) -> bool:
    """Does `s` read like a personal name?

    Both modes need 2–4 tokens, each a capitalised name token or a single-letter initial
    ("Stefán A. Svensson").

    strict=True (default) also needs the last token to be a patronymic/matronymic surname
    (-son/-dóttir; foreign surnames mostly -sen). Capitalised words alone are not enough:
    titles such as "Efnisþáttur Tamílamálsins" or "Mannréttindadómstóll Evrópu" have the same
    shape. Use it for the "is this thing a person?" side of a decision.

    strict=False is shape only, so family names (Líndal, Snævarr, Thors, Briem, Hafstein, Schram)
    pass, and lowercase particles ("von", "van") between name tokens are tolerated. Use it for the
    "is this thing NOT a person?" side, where a false negative would cause a wrong action.
    """
    name, _ = clean_author(s or "")
    toks = name.split()
    if not 2 <= len(toks) <= 4:
        return False
    last = len(toks) - 1
    for i, t in enumerate(toks):
        if _is_name_token(t) or _is_initial(t):
            continue
        if not strict and 0 < i < last and t in _PARTICLES:
            continue
        return False
    if not strict:
        return True
    return bool(_SURNAME_END.search(toks[-1].rstrip(".")))
