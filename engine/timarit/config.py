"""Tunables for the journal pipeline (spec 2026-10-03 §5.4, §6). Four values were settled by the user in the Task 22
pilot on 2026-10-04, after the audits of the pilot's 41 issues: APPROVE_THRESHOLD, OCR_BIN_RATIO_THRESHOLD,
PAGE_MAP_MIN_QUALITY and COVERAGE_MAX_GAP_PAGES. The comment on each says what was chosen and, where there is one, the
evidence. PAGE_MAP_RESTART_RATIO, the fifth value the pilot report tabulates, was left as it was. The weights are the
plan's."""
from pathlib import Path

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
