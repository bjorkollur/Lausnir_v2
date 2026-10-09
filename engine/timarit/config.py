"""Tunables for the journal pipeline (spec 2026-10-03 §5.4, §6). Four values were settled by the user in the Task 22
pilot on 2026-10-04, after the audits of the pilot's 41 issues: APPROVE_THRESHOLD, OCR_BIN_RATIO_THRESHOLD,
PAGE_MAP_MIN_QUALITY and COVERAGE_MAX_GAP_PAGES; three more in the bulk run (Task 23) on 2026-10-09, from the
distributions of the contents gate: TOC_MIN_TITLE_SHARE, TOC_MAX_UNCLAIMED_PAGES and TOC_MAX_UNCLAIMED_SHARE. The
comment on each says what was chosen and, where there is one, the evidence. PAGE_MAP_RESTART_RATIO, the fifth value
the pilot report tabulates, was left as it was. The weights are the plan's."""
from pathlib import Path
from typing import NamedTuple

from engine.config.sources import TIMARIT_DROPFOLDER_DIR, _DATA_DIR

JOURNALS_ROOT = "Tímarit og fræðigreinar/"
WEIGHTS = {"title_on_page": 0.35, "printed_page_match": 0.30, "author_on_page": 0.15, "external_record": 0.20}
# User's choice, Task 22 pilot, 2026-10-04: 0.9. Evidence, two audits of articles by confidence: >= 0.9 gave 31/31 correct
# citations, >= 0.85 gave 34/36, >= 0.8 gave 63/66. With these weights only an article whose four checks all hold reaches it.
APPROVE_THRESHOLD = 0.9
LLM_CONFIDENCE_CAP = 0.70
# User's choice, Task 22 pilot, 2026-10-04: None (never re-OCR a PDF that has a text layer). Evidence: a low BÍN ratio in
# the pilot meant an English-language article, not a bad text layer.
OCR_BIN_RATIO_THRESHOLD: float | None = None
# User's choice, Task 22 pilot, 2026-10-04: 0.5, unchanged.
PAGE_MAP_MIN_QUALITY = 0.5
# Unchanged by the Task 22 pilot (2026-10-04); marks a map ambiguous when runner-up votes >= this x winner votes.
PAGE_MAP_RESTART_RATIO = 0.25
# Largest run of PDF pages that may lie outside every article of an issue file (cover, TOC, adverts: before the
# first article, between articles, after the last) before issue_status sends the issue to review.
# User's choice, Task 22 pilot, 2026-10-04: 6, unchanged.
COVERAGE_MAX_GAP_PAGES = 6
# Task 23b (R47), contents quality gate (tocgate.py): a contents list the parser read is replaced by Claude's list when
# fewer than this share of its entries are found on their start pages (the title or a form of it, a person the entry
# names, the label of its kind). Column rubrics such as "Frá ritstjórn" are not counted; rows read without a title
# ("—") are, and a list with nothing to measure fails. Pilot-tunable: the user chooses from the distribution that
# scripts/timarit_pilot_report.py prints ("Efnisyfirlit á síðunum"). Default 0.8, measured again in fix rounds 1 and 2
# (2026-10-05): the lowest value that keeps every audited whole-issue failure of audit 4 gated (TL 1974 at 0.71, TL
# 2017-2 at 0.75, TL 1982 at 0.77, TL 1979 at 0.78). It gates 35 bulk lists (fix round 2 adds TL 1971, 8 of its 11 rows
# "—"), and the 22 working lists the review named (Lögmannablaðið 2016-2024, TL 1951-1, 2012-3, 2012-4, 2022-4) stand
# at 0.80 or above. The two groups still overlap between 0.75 and 0.80: working lists TL 1952-1 (0.75) and TL 1999
# (0.77) beside failing TL 1979, 1981 and 1983 (0.78-0.79).
# User's choice, Task 23 bulk run, 2026-10-09: 0.8, unchanged, chosen from that distribution.
TOC_MIN_TITLE_SHARE = 0.8
# Task 23c, the same gate: a parsed contents list also fails when the pages of article text that no entry claims exceed
# BOTH of these: TOC_MAX_UNCLAIMED_PAGES pages and TOC_MAX_UNCLAIMED_SHARE of the pages after the cover and the contents.
# The titles alone pass a list that covers only the end of a volume (TL 1989: 6 of 7 found, the first entry on pdf 224 of
# 304, 195 pages of articles before it unclaimed). The first COVERAGE_MAX_GAP_PAGES pages after the contents (masthead,
# editor's note, adverts) are front matter and not counted, nor are pages whose text layer is garbled (TL 2009-2012: their
# ranges are cut short there, which is no article missing from the list). Pilot-tunable: the user chooses from the
# distribution of `unclaimed` that scripts/timarit_pilot_report.py prints next to the share ("Efnisyfirlit á síðunum").
# Defaults 20 and 0.2, measured on the 170 parsed lists of the bulk run (2026-10-05): the 135 that pass on their titles leave
# at most 12 pages (0.13 of the volume) unclaimed, except two, TL 1989 (195 pages, 0.65; its first entry is on pdf 224) and
# Úlfljótur 2023-4 (43 pages, 0.24; three of the contents' Rökstólar items are not in the list), which are real partial
# lists. 20 pages and a fifth of the volume lie in the gap between those and the rest, and above the 22 working lists the
# review named (at most 3 pages, 0.10). Lists that already fail on their titles leave far more (Lögrétta 2018-2: 237
# pages, Lögrétta 2017-1: 204, TL 1971: 199, TL 1993: 185, Lögrétta 2018-1: 178), so this test would catch them too.
# User's choice, Task 23 bulk run, 2026-10-09: 20 and 0.2, unchanged, chosen from the distribution of `unclaimed`.
TOC_MAX_UNCLAIMED_PAGES = 20
TOC_MAX_UNCLAIMED_SHARE = 0.2


class SplitPart(NamedTuple):
    """One volume of an archive PDF that holds more than one: its year and volume, and the PDF pages it takes up in the
    file (0-based, inclusive)."""
    year: int
    volume: int
    pdf_window: tuple[int, int]


# Task 23e, the user's decision of 2026-10-09: a PDF that holds two volumes is two issues, each a page window of the
# file (discover makes one manifest per part; analysis reads only the part's pages; import cuts from the whole file).
# Keyed by the archive-relative path (NFC). TL 1971_1972.pdf (376 pages; its title page reads "XXI. og XXII. árg.
# 1971—1972") is 21. árg. 1971, bls. 1–150, and 22. árg. 1972, whose numbering restarts at bls. 1 (to bls. 119). The
# boundary was read on the pages: the 1971 part runs from the volume's title page and contents (pdf 0–3) through its
# last numbered page, bls. 150 on pdf 183 ("Skrá um lög árið 1970"), and the adverts bound after it (pdf 184–203); the
# 1972 part starts on its own cover, pdf 204 ("1. hefti 1972 ... EFNI"), then a blank page and bls. 1 on pdf 206 (no
# folio printed; pdf 207's reads "9" for 2); the first number read is bls. 3 on pdf 208, the last bls. 119 on pdf 352,
# then adverts to pdf 375.
SPLIT_FILES: dict[str, tuple[SplitPart, ...]] = {
    JOURNALS_ROOT + "Tímarit Lögfræðinga/TL 1971_1972.pdf": (
        SplitPart(1971, 21, (0, 203)),
        SplitPart(1972, 22, (204, 375)),
    ),
}
MANIFEST_ROOT = Path("data/timarit/manifests")
UNMATCHED_PATH = Path("data/timarit/_unmatched.yaml")
# Caches are written, so they live outside the (read-only) user archive.
CACHE_DIR = Path(_DATA_DIR) / "cache" / "timarit"
# The spreadsheet is only ever read.
XLSX_PATH = Path(TIMARIT_DROPFOLDER_DIR) / "_meta" / "Ulfljotur_efnisyfirlit.xlsx"
# analyze.verify_article: a title / an author name counts as printed on an article's first page when a window of
# the page's head is at least this similar (folded text; short names must occur as whole words). Pilot-tunable, Task 22.
TITLE_ON_PAGE_MIN = 0.75
AUTHOR_ON_PAGE_MIN = 0.8
# Úlfljótur registry matcher (ulfljotur_xlsx.match_first_page); both are pilot-tunable in Task 22.
XLSX_MATCH_MIN = 0.6      # a first page "hits" a registry row when its windowed title score reaches this
XLSX_MATCH_MARGIN = 0.03  # a runner-up this close to the winner makes the match ambiguous (None)
# discover: a file whose text is contained in a larger volume file of the same (source, year, volume) is a
# content duplicate (spec 5.6; the same article is never imported twice). A candidate page counts as contained when
# >= CONTAINED_PAGE_MIN of its word trigrams occur on one container page (pages with fewer than
# CONTAINED_MIN_TRIGRAMS trigrams are not judged); the file is contained when >= CONTAINED_MIN_SHARE of its judged
# pages are. Pilot-tunable, Task 22; measured 76-100% for true duplicates vs 0% for distinct files.
CONTAINED_MIN_SHARE = 0.7
CONTAINED_PAGE_MIN = 0.5
CONTAINED_MIN_TRIGRAMS = 20
