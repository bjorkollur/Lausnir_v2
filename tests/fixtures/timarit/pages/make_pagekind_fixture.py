"""Make tests/fixtures/timarit/pages/pagekind.json: the pilot pages tests/test_timarit_pagekind.py reads (Task 22c).

    uv run python tests/fixtures/timarit/pages/make_pagekind_fixture.py

Reads the user's archive (read only) through firstpage.read_page and keeps, per page, its height, width, the share
pictures cover, its vector drawings and its visual lines [text, size, top, bold, left, right]. The journals' running
text is not kept verbatim: a line of running text in the middle of the page (30 characters or more, set in the page's
body size or smaller and not bold, no name, no advert mark or firm, not a part heading, neither of the page's first two nor last
two text lines) becomes filler of the same length that keeps its first and last character, so sizes, positions,
lengths, initial case, sentence ends and every line the classifier reads for its words stay as printed. One page per
line in the JSON.
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


def _keep(page: FP.Page, k: int, ln: FP.Line, text_lines: list[FP.Line]) -> bool:
    body = FP.body_size(page)
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
    for key, (rel, numbers) in FILES.items():
        with fitz.open(ARCHIVE / rel) as d:
            reader = FP.PageReader(d)
            for i in numbers:
                p = reader(i)
                text_lines = [ln for ln in p.lines if re.search(r"[^\W\d_]{2}", ln.text)]
                lines = [[ln.text if _keep(p, k, ln, text_lines) else _filler(ln.text), ln.size, ln.y, ln.bold, ln.x0,
                          ln.x1] for k, ln in enumerate(p.lines)]
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
