"""Make tests/fixtures/timarit/pages/pagekind.json: the pilot pages tests/test_timarit_pagekind.py reads (Task 22c).

    uv run python tests/fixtures/timarit/pages/make_pagekind_fixture.py

Reads the user's archive (read only) through firstpage.read_page and keeps, per page, its height, width, the share
pictures cover, its vector drawings and its visual lines [text, size, top, bold, left, right]. The journals' running
text is not kept verbatim: a line of running text in the middle of the page (30 characters or more, set in the page's
body size or smaller and not bold, no name, no advert mark or firm, not a part heading, neither of the page's first two nor last
two text lines) becomes filler of the same length that keeps its first and last character, so sizes, positions,
lengths, initial case, sentence ends and every line the classifier reads for its words stay as printed. A file may keep
every line above a share of the page's height (Task 23b: a first page's title and byline, which the reading of them
needs). One page per line in the JSON.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from engine.timarit import firstpage as FP  # noqa: E402
from engine.timarit import pagekind as PK  # noqa: E402
from engine.timarit.config import JOURNALS_ROOT  # noqa: E402
from engine.config.sources import TIMARIT_DROPFOLDER_DIR  # noqa: E402

ARCHIVE = Path(TIMARIT_DROPFOLDER_DIR) / JOURNALS_ROOT
OUT = Path(__file__).with_name("pagekind.json")
FILES = {
    "ulf2023": ("Úlfljótur/Tímarit/Ulfljotur_2023-3tbl.pdf", [114, 115, 116, 117, 120, 121, 122, 123, 124, 125]),
    "lmbl2017": ("Lögmannablaðið/Lögmannablaðið 2017 01 .pdf", [20, 31, 32, 33, 34, 35]),
    "lmbl2020": ("Lögmannablaðið/Lögmannablaðið 2020 01 .pdf", [5, 6, 37]),
    "lmbl2005": ("Lögmannablaðið/Lögmannablaðið 2005 02.pdf", [21, 22, 28, 29, 30]),
    "tl1985": ("Tímarit Lögfræðinga/TL 1985.pdf", [83, 84, 88, 141, 142, 143, 144, 215, 216, 217, 224, 281, 289, 290,
                                                   291, 292, 293, 294]),
    "tl1974": ("Tímarit Lögfræðinga/TL 1974.pdf", [181, 193, 194]),
    "tl2004": ("Tímarit Lögfræðinga/TL 2004.pdf", [184, 185, 194, 471, 480]),
    "tl2024": ("Tímarit Lögfræðinga/TL 2024_01.pdf", [9, 10, 143, 149]),
    "ulf1957": ("Úlfljótur/Tímarit/Ulfljotur_1957 11 arg/187.pdf", [0, 1, 2, 3, 7, 8, 9]),
    "ulf1957b": ("Úlfljótur/Tímarit/Ulfljotur_1957 11 arg/1071.pdf", [0, 1]),
    "ulf1947": ("Úlfljótur/Tímarit/Ulfljotur_1947 01 arg/211.pdf", [0, 1, 2, 3, 4]),
    "ulf1947b": ("Úlfljótur/Tímarit/Ulfljotur_1947 01 arg/767.pdf", [0, 1, 9]),
    "ulf2004": ("Úlfljótur/Tímarit/Ulfljotur_2004_2_tbl/3240.pdf", [0, 1, 2, 3, 4, 8]),
    "ulf2010": ("Úlfljótur/Tímarit/Ulfljotur_2010_1_tbl.pdf", [34, 35, 36, 37, 38, 39, 40, 41]),
    "logretta2014": ("Lögrétta/Lögrétta 2014 1.tbl.pdf", [12, 13, 14, 15, 36, 37, 38, 39, 40]),
    "logretta2010": ("Lögrétta/Lögrétta 2010 1.tbl.pdf", [76]),
    "lf2011": ("Lögfræðingur UNAK/Lögfræðingur 5 arg 2011.pdf", [8, 9, 10, 11]),
    "lf2010": ("Lögfræðingur UNAK/Lögfræðingur 4 arg 2010.pdf", [89]),
    "ranns2011": ("Rannsóknir í félagsvísingum Lagadeild/Rannsóknir í félagsvísingum Lagadeild 2011 - XII.pdf",
                  [26, 27, 28, 29]),
    "ranns2003": ("Rannsóknir í félagsvísingum Lagadeild/Rannsóknir í félagsvísingum Lagadeild 2003 - IV.pdf", [211]),
    "logbru2015": ("Lögbrú/2015-01-Lögbrú.pdf", [44]),
    # Task 23b: bulk audit 4. Stjórnmál og stjórnsýsla first pages (their top kept whole: title and byline) …
    "ss01": ("Stjórnmál og Stjórnsýsla/Vol01/5-26_When_is_a_minority_a_majority.pdf", [0], 0.75),
    "ss03": ("Stjórnmál og Stjórnsýsla/Vol03/5-42_Government_formation_negotiations_in_Ice.pdf", [0], 0.75),
    "ss05a": ("Stjórnmál og Stjórnsýsla/Vol05/225-248_Iceland’s_Neighbours_in_the_EU_Entry_Que.pdf", [0], 0.75),
    "ss05b": ("Stjórnmál og Stjórnsýsla/Vol05/363-382_Human_right,_disability_and_family_life.pdf", [0], 0.75),
    "ss07a": ("Stjórnmál og Stjórnsýsla/Vol07/5-22_The_European_Citizens’_Initiative.pdf", [0], 0.45),
    "ss07b": ("Stjórnmál og Stjórnsýsla/Vol07/333-352_From_soft_to_hard_downsizing_methods.pdf", [0], 0.45),
    "ss08a": ("Stjórnmál og Stjórnsýsla/Vol08/389-412_The_effects_of_cutbacks_on_motivation_an.pdf", [0], 0.45),
    "ss08b": ("Stjórnmál og Stjórnsýsla/Vol08/451-468_Smelter_Jobs__Societal_changes_and_emplo.pdf", [0], 0.45),
    "ss09a": ("Stjórnmál og Stjórnsýsla/Vol09/1-20_Scotland_as_an_Independent_Small_State.pdf", [0], 0.45),
    "ss09b": ("Stjórnmál og Stjórnsýsla/Vol09/531-548_The_Faroe_Islands_and_the_Arctic.pdf", [0], 0.45),
    "ss10": ("Stjórnmál og Stjórnsýsla/Vol10/77-98_The_status_of_preschool_teachers_in_two.pdf", [0], 0.45),
    "ss11": ("Stjórnmál og Stjórnsýsla/Vol11/55-70_Self_Censorship_among_Icelandic_Journali.pdf", [0], 0.45),
    "ss14": ("Stjórnmál og Stjórnsýsla/Vol14/19-44_Internal_audit_in_the_public_sector_–.pdf", [0], 0.6),
    "ss21": ("Stjórnmál og Stjórnsýsla/Vol21/35-58_Local_parliamentarians_in_a_global_world.pdf", [0], 0.45),
    # … signatures, swallowed items, interviews and bylines, page kinds at an article's end, contents on its pages
    "lmbl2021": ("Lögmannablaðið/Lögmannablaðið 2021 03.pdf", [27, 28]),
    "lmbl2012": ("Lögmannablaðið/Lögmannablaðið 2012 02 .pdf", [26]),
    "tl1996": ("Tímarit Lögfræðinga/TL 1996.pdf", [330, 335, 336, 342, 348]),
    "lmbl2017b": ("Lögmannablaðið/Lögmannablaðið 2017 02 .pdf", [17]),
    "tl1964": ("Tímarit Lögfræðinga/TL 1964_01.pdf", [63]),
    "lmbl2004c": ("Lögmannablaðið/Lögmannablaðið 2004 03.pdf", [27]),
    "logretta2014b": ("Lögrétta/Lögrétta 2014 2.tbl.pdf", [131]),
    "tl1968": ("Tímarit Lögfræðinga/TL 1968_02.pdf", [14, 15, 16, 17]),
    "tl2014": ("Tímarit Lögfræðinga/TL 2014_03.pdf", [4, 5, 37, 38, 39]),
    "tl1970": ("Tímarit Lögfræðinga/TL 1970_01.pdf", [38, 39, 40, 41]),
    "lmbl2004d": ("Lögmannablaðið/Lögmannablaðið 2004 04.pdf", [3, 4, 5, 8, 9, 10, 11]),
    "ulf1983": ("Úlfljótur/Tímarit/Ulfljotur_1983 36 arg/86.pdf", [0, 1, 7, 8], 0.5),
    "tl1991": ("Tímarit Lögfræðinga/TL 1991_02.pdf", [2, 7, 12, 53, 69]),
    "tl2017": ("Tímarit Lögfræðinga/TL 2017_02.pdf", [4, 5, 48, 94], 0.5),
    "tl1971": ("Tímarit Lögfræðinga/TL 1971_1972.pdf", [270, 284]),
    # … and lists that work although their titles are not printed as the registry has them (fix round 1)
    "lmbl2016c": ("Lögmannablaðið/Lögmannablaðið 2016 03 .pdf", [35]),
    "lmbl2017d": ("Lögmannablaðið/Lögmannablaðið 2017 04 .pdf", [7], 0.33),
    "lmbl2020b": ("Lögmannablaðið/Lögmannablaðið 2020 01 .pdf", [13]),
    "lmbl2023": ("Lögmannablaðið/Lögmannablaðið 2023 01.pdf", [22, 23]),
    "tl2022": ("Tímarit Lögfræðinga/TL 2022_04.pdf", [2], 0.3),
    "tl1951": ("Tímarit Lögfræðinga/TL 1951_01.pdf", [11]),
    "tl2012": ("Tímarit Lögfræðinga/TL 2012_04.pdf", [2]),
    "logbru2013": ("Lögbrú/2013-01-Lögbrú.pdf", [154]),
    "tl1979": ("Tímarit Lögfræðinga/TL 1979.pdf", [112]),
    "lmbl2013": ("Lögmannablaðið/Lögmannablaðið 2013 03.pdf", [13]),
    # fix round 2: a label over the wrong entry, interviews' leads and credits, TL's "Name:" heading
    "lmbl2016b": ("Lögmannablaðið/Lögmannablaðið 2016 02 .pdf", [3, 5]),
    "lmbl2005b": ("Lögmannablaðið/Lögmannablaðið 2005 04.pdf", [9]),
    "lmbl2006": ("Lögmannablaðið/Lögmannablaðið 2006 02.pdf", [9, 10]),
    "tl2024d": ("Tímarit Lögfræðinga/TL 2024_04.pdf", [4]),
    "tl1971b": ("Tímarit Lögfræðinga/TL 1971_1972.pdf", [50]),
    # Task 23c: front matter between the contents and the first article (TL's inner title and masthead; Úlfljótur's
    # contents, masthead and editor's note), and a page whose text layer is garbled (TL 2010-2: replacement characters)
    "tl2024e": ("Tímarit Lögfræðinga/TL 2024_04.pdf", [1]),
    "ulf2024": ("Úlfljótur/Tímarit/Ulfljotur_2024-1tbl.pdf", [2, 3, 4]),
    "tl2010": ("Tímarit Lögfræðinga/TL 2010_02.pdf", [14]),
    # Task 23d: an article's last lines at the top of the next item's start page (signed, run on mid-sentence, signed
    # with initials, a table), its last lines over an advert, a page with a download stamp and a folio only, and short
    # items placed by their neighbours in volumes whose page numbers restart
    "tl1980": ("Tímarit Lögfræðinga/TL 1980.pdf", [273, 274]),
    "lmbl2021b": ("Lögmannablaðið/Lögmannablaðið 2021 02.pdf", [17, 18]),
    "tl1982": ("Tímarit Lögfræðinga/TL 1982.pdf", [224, 225, 226]),
    "lmbl2009": ("Lögmannablaðið/Lögmannablaðið 2009 04.pdf", [11, 12, 13, 14, 15]),
    "tl1976": ("Tímarit Lögfræðinga/TL 1976.pdf", [39, 40, 41]),
    "lf2009": ("Lögfræðingur UNAK/Lögfræðingur 3 arg 2009.pdf", [55]),
    "ulf2001": ("Úlfljótur/Tímarit/Ulfljotur_2001 54 arg/95.pdf", [28]),
    "tl2016d": ("Tímarit Lögfræðinga/TL 2016_04.pdf", [164, 165, 166, 189, 190]),
    "tl1993": ("Tímarit Lögfræðinga/TL 1993.pdf", [43, 44, 45, 79, 80, 81]),
    "logretta2011": ("Lögrétta/Lögrétta 2011 1.tbl.pdf", [42, 43, 44]),
    "tl1973": ("Tímarit Lögfræðinga/TL 1973.pdf", [34, 128, 129, 130]),
    "logretta2013": ("Lögrétta/Lögrétta 2013 1.tbl.pdf", [33, 34]),
    "lmbl2023c": ("Lögmannablaðið/Lögmannablaðið 2023 03.pdf", [20, 21]),
    "tl2003": ("Tímarit Lögfræðinga/TL 2003.pdf", [209, 210]),
    "lmbl2016e": ("Lögmannablaðið/Lögmannablaðið 2016 02 .pdf", [16, 17]),
    "tl1953": ("Tímarit Lögfræðinga/TL 1953_04.pdf", [9]),
    "lmbl2021c": ("Lögmannablaðið/Lögmannablaðið 2021 03.pdf", [12]),
    "tl1971c": ("Tímarit Lögfræðinga/TL 1971_1972.pdf", [172]),
    # Task 23d fix round 1: a box set in another article's page (LMBL 1999-1), a heading in bold body type (TL 1996)
    "lmbl1999": ("Lögmannablaðið/Lögmannablaðið 1999 01.pdf", [9, 10]),
    "tl1996b": ("Tímarit Lögfræðinga/TL 1996.pdf", [68, 69]),
}
_WORDS = ("og það sem fram kemur í máli þessu var talið rétt að líta til þess hvort ákvæðin ættu við um þau atvik "
          "sem um ræðir enda verður ekki séð að annað hafi legið fyrir ").split()


def _filler(text: str) -> str:
    out = ""
    k = 0
    while len(out) < len(text):
        out = f"{out} {_WORDS[k % len(_WORDS)]}" if out else _WORDS[0]
        k += 1
    middle = out[1:len(text) - 1].rstrip().ljust(len(text) - 2, "a")
    return text[0] + middle + text[-1]


def _keep(page: FP.Page, k: int, ln: FP.Line, text_lines: list[FP.Line], keep_top: float = 0.0) -> bool:
    body = FP.body_size(page)
    if ln.y < keep_top * page.height:
        return True                                   # (a first page's title and byline, for the reading of them)
    if len(ln.text) < 30 or not ln.text[:1].isalpha() or ln in text_lines[:2] or ln in text_lines[-2:]:
        return True
    if ln.bold or ln.size > 1.07 * body:
        return True                                   # a heading, a lead: kept
    if not 0.12 * page.height < ln.y < 0.88 * page.height:
        return True
    return bool(FP.names_in(ln.text) or PK._marks(ln.text) or PK._FIRM.search(ln.text) or PK._article_part(ln.text)
                or re.search(r"[A-ZÁÉÍÓÚÝÞÆÖ][a-záéíóúýþæö]+ [A-ZÁÉÍÓÚÝÞÆÖ][a-záéíóúýþæö]+(?:son|dóttir)", ln.text))


def build() -> str:
    """The fixture's text, from the archive."""
    pages: dict[str, dict] = {}
    for key, (rel, numbers, *top) in FILES.items():
        with fitz.open(ARCHIVE / rel) as d:
            reader = FP.PageReader(d)
            for i in numbers:
                p = reader(i)
                text_lines = [ln for ln in p.lines if re.search(r"[^\W\d_]{2}", ln.text)]
                lines = [[ln.text if _keep(p, k, ln, text_lines, *top) else _filler(ln.text), ln.size, ln.y, ln.bold,
                          ln.x0, ln.x1] for k, ln in enumerate(p.lines)]
                pages[f"{key}/{i}"] = {"height": round(p.height, 1), "width": round(p.width, 1),
                                       "picture": round(reader.picture(i), 2), "drawings": reader.drawings(i),
                                       "lines": lines}
    body = ",\n".join(f"{json.dumps(k, ensure_ascii=False)}: {json.dumps(v, ensure_ascii=False, separators=(',', ':'))}"
                      for k, v in pages.items())
    return "{\n" + body + "\n}\n"


def main(argv: list[str]) -> int:
    """Write the fixture; with --check, only say whether the committed one is what the generator makes (exit 1 if
    not: the classifier's word lists, which decide what is filled, changed since it was made)."""
    text = build()
    if "--check" in argv:
        same = OUT.read_text(encoding="utf-8") == text
        print("pagekind.json is up to date" if same else "pagekind.json differs from what the generator makes")
        return 0 if same else 1
    OUT.write_text(text, encoding="utf-8")
    print(f"{text.count(chr(10)) - 1} pages, {OUT.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
