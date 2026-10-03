"""Tunables for the journal pipeline (spec 2026-10-03 §5.4, §6). The defaults
here are starting points; Task 22 (pilot) replaces them with the user's choices."""
from pathlib import Path

from engine.config.sources import TIMARIT_DROPFOLDER_DIR, _DATA_DIR

JOURNALS_ROOT = "Tímarit og fræðigreinar/"
WEIGHTS = {"title_on_page": 0.35, "printed_page_match": 0.30, "author_on_page": 0.15, "external_record": 0.20}
APPROVE_THRESHOLD = 0.85
LLM_CONFIDENCE_CAP = 0.70
OCR_BIN_RATIO_THRESHOLD: float | None = None   # None → never re-OCR a PDF that has a text layer
PAGE_MAP_MIN_QUALITY = 0.5
PAGE_MAP_RESTART_RATIO = 0.25  # Tunable reviewed in the Task 22 pilot; marks map ambiguous when runner-up votes ≥ this × winner votes
# Largest run of PDF pages that may lie outside every article of an issue file (cover, TOC, adverts: before the
# first article, between articles, after the last) before issue_status sends the issue to review. Pilot-tunable, Task 22.
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
