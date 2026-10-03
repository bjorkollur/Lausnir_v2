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
MANIFEST_ROOT = Path("data/timarit/manifests")
UNMATCHED_PATH = Path("data/timarit/_unmatched.yaml")
# Caches are written, so they live outside the (read-only) user archive.
CACHE_DIR = Path(_DATA_DIR) / "cache" / "timarit"
# The spreadsheet is only ever read.
XLSX_PATH = Path(TIMARIT_DROPFOLDER_DIR) / "_meta" / "Ulfljotur_efnisyfirlit.xlsx"
