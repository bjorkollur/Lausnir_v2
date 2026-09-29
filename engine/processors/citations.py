"""Extract references to other rulings from Icelandic court text (spec §5).

Pure: no DB. Everything here is regex over one layer's text. The design
decisions (why backwards-only, why the sentence window, why laws are masked
first) are in docs/superpowers/specs/2026-09-29-citations-design.md §3/§5.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from engine.processors.citation_resolver import norm_case_number

MAX_RAW = 240
WINDOW = 120          # preferred look-back distance for a court word (§5.3)
DATE_AFTER = 30       # chars after the number in which 'frá …' may introduce a date

MONTHS = {m: i + 1 for i, m in enumerate(
    "janúar febrúar mars apríl maí júní júlí ágúst september október nóvember desember".split())}

# Court words → documents.court values (spec §5.3). Order matters: specific district names first.
COURT_WORDS: dict[str, str] = {
    r"[Hh]éraðsdóm\w*\s+Reykjavíkur": "Hérd. Rvk.",
    r"[Hh]éraðsdóm\w*\s+Reykjaness": "Hérd. Reykn.",
    r"[Hh]éraðsdóm\w*\s+Suðurlands": "Hérd. Suðl.",
    r"[Hh]éraðsdóm\w*\s+Norðurlands\s+eystra": "Hérd. Norðeyst.",
    r"[Hh]éraðsdóm\w*\s+Norðurlands\s+vestra": "Hérd. Norðvest.",
    r"[Hh]éraðsdóm\w*\s+Vesturlands": "Hérd. Vestl.",
    r"[Hh]éraðsdóm\w*\s+Austurlands": "Hérd. Austl.",
    r"[Hh]éraðsdóm\w*\s+Vestfjarða": "Hérd. Vestfj.",
    r"[Hh]éraðsdóm\w*": "Hérd.",
    r"Hæstaréttar|Hæstarétti|Hæstiréttur|Hæstarétt": "Hrd.",
    r"Landsréttar|Landsrétti|Landsréttur|Landsrétt": "Lrd.",
    r"Félagsdóm\w*": "Féld.",
    r"Landsdóm\w*": "Ld.",
    r"Endurupptökudóm\w*": "Eud.",
}
_COURT_RX = re.compile("|".join(f"(?P<c{i}>{pat})" for i, pat in enumerate(COURT_WORDS)))
_COURT_BY_GROUP = {f"c{i}": abbr for i, abbr in enumerate(COURT_WORDS.values())}
# Same alternation without capture groups, for embedding in other patterns.
_COURT_ALT = "|".join(f"(?:{pat})" for pat in COURT_WORDS)

# Words that carry the last court named earlier in the same sentence (§5.3).
_INHERIT_RX = re.compile(r"\b(réttarins|réttinum|dómstólsins|dómstóllinn|sama\s+dómstóls|sama\s+réttar)\b")
# A non-court adjudicator standing nearer than the court word cancels the citation (§5.3).
_OTHER_BODY_RX = re.compile(
    r"\b(nefnd\w*|sýslumann\w*|ráðuneyt\w*|stofn\w*|Persónuvernd\w*|stjórn\w*|dómstól\w*)\b")
_LAW_RX = re.compile(
    r"\b(?:l(?:ög|aga|ögum|ögunum)|reglugerð\w*|auglýsing\w*|samþykkt\w*)\s+nr\.\s*\d{1,4}/\d{4}", re.I)

_NUM = r"(?:[A-ZÞÆÖ]{1,2}-\d{1,5}/\d{4}|\d{1,4}/\d{4}|\d{4}-\d{1,3})"
# 'í máli nr. X' and 'í máli Landsréttar nr. X' — the court may sit between the
# noun and 'nr.', which is common in Icelandic ('í máli Hæstaréttar nr. 1/2020').
_TRIGGER_RX = re.compile(
    r"\bmál(?:i|inu|s|um|unum)?\s+(?:(?:" + _COURT_ALT + r")\s+)?nr\.\s*(?P<first>" + _NUM + r")"
    r"(?P<rest>(?:\s*,\s*" + _NUM + r")*(?:\s+og\s+" + _NUM + r")?)")
_NUM_RX = re.compile(_NUM)
# Málskotsbeiðni without the word 'mál': 'ákvörðun réttarins nr. 2023-68'.
_AKVORDUN_RX = re.compile(r"\bákvörðun\w*\s+(?:réttarins\s+)?nr\.\s*(?P<first>\d{4}-\d{1,3})")
_MALSK_RX = re.compile(r"\d{4}-\d{1,3}")

_ABBREV_RX = re.compile(
    r"\b(?:(?P<hrd>Hrd)\.\s*(?P<hn>\d{1,4}/\d{4})|(?P<lrd>Lrd)\.\s*(?P<ln>\d{1,4}/\d{4})"
    r"|(?P<herd>Hérd)\.\s*(?P<place>Rvk|Reykn|Suðl|Norðeyst|Norðvest|Vestl|Austl|Vestfj)\.\s*"
    r"(?P<dn>[A-ZÞÆÖ]{1,2}-\d{1,5}/\d{4}))")
_REPORTER_RX = re.compile(
    r"\bHrd\.?\s*(?P<y>\d{4})[,:/]\s*(?:bls\.\s*)?(?P<p>\d{1,5})\b"
    r"|\bH\s?(?P<y2>\d{4}):(?P<p2>\d{1,5})\b")
_DATE_RX = re.compile(
    r"(?P<d>\d{1,2})\.\s+(?P<m>janúar|febrúar|mars|apríl|maí|júní|júlí|ágúst|september|október|nóvember|desember)"
    r"\s+(?P<y>\d{4}|sama\s+ár|s\.á\.|þess\s+árs|sl\.|síðastliðin\w*)")
_YEAR_RX = re.compile(r"\b(1[89]\d\d|20\d\d)\b")
_VERB_RX = re.compile(r"\b(dóm\w*|úrskurð\w*|ákvörð\w*)\b")
# Sentence break: '. ' + capital, ';', or a blank line. 'nr. 700', '8. nóvember'
# and 'sbr. dóm' are followed by a digit or a lower-case letter, so they survive.
_SENT_BREAK_RX = re.compile(r"\.\s+(?=[A-ZÁÐÉÍÓÚÝÞÆÖ])|;|\n\s*\n")
_DATE_INTRO_RX = re.compile(r"\s*(?:frá|dags\.|uppkveðn\w*)\s+")


@dataclass(frozen=True)
class RawCitation:
    char_start: int
    char_end: int
    raw_text: str
    target_court: str
    target_case_number: str | None
    target_date: date | None
    target_verdict: str | None
    form: str


def _sentence_start(text: str, pos: int) -> int:
    """Offset of the sentence containing `pos` (bounded look-back, §5.2)."""
    lo = max(0, pos - 600)
    last = lo
    for m in _SENT_BREAK_RX.finditer(text, lo, pos):
        last = m.end()
    return last


def _verdict(text: str, court_pos: int) -> str | None:
    """Last dóm*/úrskurð*/ákvörð* within 40 chars before the court word (§5.5)."""
    seg = text[max(0, court_pos - 40):court_pos]
    last = None
    for m in _VERB_RX.finditer(seg):
        last = m.group(1).lower()
    if last is None:
        return None
    if last.startswith("dóm"):
        return "Dómur"
    if last.startswith("úrskurð"):
        return "Úrskurður"
    return "Ákvörðun"


def _resolve_year(token: str, *, sentence_before: str, month: int, doc_date: date | None) -> int | None:
    t = "".join(token.split())
    if t.isdigit():
        return int(t)
    if t in ("samaár", "s.á.", "þessárs"):
        # nearest preceding 4-digit year in the same sentence; none → unknown
        years = _YEAR_RX.findall(sentence_before)
        return int(years[-1]) if years else None
    # sl. / síðastliðinn — relative to the document's own date
    if doc_date is None:
        return None
    return doc_date.year if month <= doc_date.month else doc_date.year - 1


def _mk_date(m: re.Match[str], sentence_before: str, doc_date: date | None) -> date | None:
    month = MONTHS[m.group("m")]
    year = _resolve_year(m.group("y"), sentence_before=sentence_before, month=month, doc_date=doc_date)
    if year is None:
        return None
    try:
        return date(year, month, int(m.group("d")))
    except ValueError:
        return None


def _find_date(text: str, court_pos: int, num_start: int, num_end: int, *,
               doc_date: date | None, sent_start: int) -> date | None:
    """A date between the court word and the number, else one right after the
    number introduced by frá/dags./uppkveðn* within DATE_AFTER chars (§5.5)."""
    last = None
    for m in _DATE_RX.finditer(text, court_pos, num_start):
        last = m
    if last is not None:
        return _mk_date(last, text[sent_start:last.start()], doc_date)

    intro = _DATE_INTRO_RX.match(text, num_end)
    if intro is None or intro.end() - num_end > DATE_AFTER:
        return None
    after = _DATE_RX.match(text, intro.end())
    if after is None:
        return None
    return _mk_date(after, text[sent_start:after.start()], doc_date)


def _overlaps(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(start < b and a < end for a, b in spans)


def _court_for(text: str, num_pos: int, *, sent_start: int,
               last_court: tuple[int, str] | None) -> tuple[int, str] | None:
    """Find the court that owns the number at num_pos, looking back within the sentence.

    Returns (court_pos, abbr) or None. `last_court` is the previous court found in
    this sentence, inherited through 'réttarins' etc. We prefer a court word within
    WINDOW chars and widen to the whole sentence only if that yields nothing; the
    sentence itself is the hard boundary.
    """
    for lo in dict.fromkeys((max(sent_start, num_pos - WINDOW), sent_start)):
        found = _court_in(text, lo, num_pos, last_court)
        if found is not None:
            return found
    return None


def _court_in(text: str, lo: int, num_pos: int,
              last_court: tuple[int, str] | None) -> tuple[int, str] | None:
    seg = text[lo:num_pos]
    courts = list(_COURT_RX.finditer(seg))
    inherits = list(_INHERIT_RX.finditer(seg))

    court: tuple[int, str] | None = None
    if courts:
        cm = courts[-1]
        abbr = next(v for k, v in _COURT_BY_GROUP.items() if cm.group(k))
        court = (lo + cm.start(), abbr)
    if inherits and last_court is not None and (court is None or inherits[-1].start() > court[0] - lo):
        court = (lo + inherits[-1].start(), last_court[1])
    if court is None:
        return None

    # A non-court adjudicator between the court word and the number wins (§5.3).
    # 'dómstól*' is in _OTHER_BODY_RX, so ignore hits that are really a court
    # word ('Héraðsdómstóll') or an inheriting pronoun ('dómstólsins').
    covered = [(m.start(), m.end()) for m in courts] + [(m.start(), m.end()) for m in inherits]
    for om in reversed(list(_OTHER_BODY_RX.finditer(seg))):
        if _overlaps(om.start(), om.end(), covered):
            continue
        if om.start() > court[0] - lo:
            return None
        break
    return court


def _other_body_between(text: str, lo: int, num_pos: int) -> bool:
    seg = text[lo:num_pos]
    covered = [(m.start(), m.end()) for m in _COURT_RX.finditer(seg)]
    covered += [(m.start(), m.end()) for m in _INHERIT_RX.finditer(seg)]
    return any(not _overlaps(m.start(), m.end(), covered) for m in _OTHER_BODY_RX.finditer(seg))


def _apply_prefix_rule(abbr: str, number: str) -> str:
    """The number's prefix beats the court word when the two disagree (§5.3)."""
    if _MALSK_RX.fullmatch(number):
        return "Hrd. málsk."
    if number.startswith("F-"):
        return "Féld."
    if "-" in number and not abbr.startswith("Hérd."):
        return "Hérd."
    return abbr


def _reporter_ok(m: re.Match[str]) -> bool:
    """Tell 'Hrd.1983/1538' (reporter: year/page) from 'Hrd. 700/2017' (case number).

    A reporter reference starts with a plausible year; a case number ends with one.
    """
    if m.group("y2"):
        return True
    year, page = int(m.group("y")), m.group("p")
    if not 1800 <= year <= 2100:
        return False
    return not (len(page) == 4 and 1800 <= int(page) <= 2100)


def extract_citations(text: str, *, doc_date: date | None) -> list[RawCitation]:
    if not text:
        return []

    # §5.1 — law/regulation numbers are masked before anything else.
    law_spans = [(m.start(), m.end()) for m in _LAW_RX.finditer(text)]

    def in_law(pos: int) -> bool:
        return any(a <= pos < b for a, b in law_spans)

    out: list[RawCitation] = []
    seen: set[int] = set()

    # --- prose: 'í máli nr. X' and 'ákvörðun … nr. YYYY-N' -------------------
    triggers = [(m, "mal") for m in _TRIGGER_RX.finditer(text)]
    triggers += [(m, "malsk") for m in _AKVORDUN_RX.finditer(text)]
    triggers.sort(key=lambda t: t[0].start("first"))

    last_court: tuple[int, str] | None = None
    last_sent = -1

    def emit(num_start: int, num_text: str, court_pos: int, abbr: str,
             verdict: str | None, tdate: date | None) -> None:
        if num_start in seen or in_law(num_start):
            return
        seen.add(num_start)
        num_end = num_start + len(num_text)
        raw = text[court_pos:num_end][-MAX_RAW:]
        out.append(RawCitation(num_start, num_end, raw, _apply_prefix_rule(abbr, num_text),
                               norm_case_number(num_text), tdate, verdict, "prose"))

    for trig, kind in triggers:
        first_start = trig.start("first")
        first_text = trig.group("first")
        sent_start = _sentence_start(text, first_start)
        if sent_start != last_sent:
            last_court, last_sent = None, sent_start

        found = _court_for(text, first_start, sent_start=sent_start, last_court=last_court)
        if found is None:
            # A YYYY-N number names its own court (§5.3: always Hrd. málsk.), so it
            # does not need a court word — unless another body is named instead.
            if not _MALSK_RX.fullmatch(first_text) or _other_body_between(
                    text, max(sent_start, first_start - WINDOW), first_start):
                continue
            found = (trig.start(), "Hrd. málsk.")
        court_pos, abbr = found
        last_court = found

        verdict = _verdict(text, court_pos) or ("Ákvörðun" if kind == "malsk" else None)
        tdate = _find_date(text, court_pos, first_start, first_start + len(first_text),
                           doc_date=doc_date, sent_start=sent_start)
        emit(first_start, first_text, court_pos, abbr, verdict, tdate)

        rest = trig.groupdict().get("rest") or ""
        if rest:
            base = trig.start("rest")
            for nm in _NUM_RX.finditer(rest):
                emit(base + nm.start(), nm.group(0), court_pos, abbr, verdict, None)

    # --- reporter form first, so 'Hrd.1983/1538' is not read as a case number --
    reporter_spans: list[tuple[int, int]] = []
    for m in _REPORTER_RX.finditer(text):
        if not _reporter_ok(m) or in_law(m.start()):
            continue
        reporter_spans.append((m.start(), m.end()))
        if m.start() in seen:
            continue
        seen.add(m.start())
        out.append(RawCitation(m.start(), m.end(), m.group(0), "Hrd.", None, None, None, "reporter"))

    # --- abbreviations: 'Hrd. 700/2017', 'Hérd. Reykn. E-12/2020' -------------
    for m in _ABBREV_RX.finditer(text):
        if m.group("hrd"):
            abbr, num, ns = "Hrd.", m.group("hn"), m.start("hn")
        elif m.group("lrd"):
            abbr, num, ns = "Lrd.", m.group("ln"), m.start("ln")
        else:
            abbr, num, ns = f"Hérd. {m.group('place')}.", m.group("dn"), m.start("dn")
        if ns in seen or in_law(ns) or _overlaps(m.start(), m.end(), reporter_spans):
            continue
        seen.add(ns)
        out.append(RawCitation(ns, ns + len(num), text[m.start():ns + len(num)],
                               _apply_prefix_rule(abbr, num), norm_case_number(num), None, None, "abbrev"))

    out.sort(key=lambda c: c.char_start)
    return out
