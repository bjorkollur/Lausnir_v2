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
# Úlfljótur registry matcher (ulfljotur_xlsx.match_first_page); both are pilot-tunable in Task 22.
XLSX_MATCH_MIN = 0.6      # a first page "hits" a registry row when its windowed title score reaches this
XLSX_MATCH_MARGIN = 0.03  # a runner-up this close to the winner makes the match ambiguous (None)
