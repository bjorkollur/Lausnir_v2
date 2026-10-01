"""Run all import scripts to pick up new documents across every source.

Runs each source's import script sequentially. Stjornarradid sources are
handled via import_stjornarradid.py with --source.
Logs each source to a separate file under $DATA_DIR/logs/update/ (each run
overwrites the previous run's log for that source).

Usage:
    uv run python scripts/update_all.py                    # full re-import
    uv run python scripts/update_all.py --new-only         # only new docs (fast)
    uv run python scripts/update_all.py --only haestirettur landsrettur
    uv run python scripts/update_all.py --skip logfraediritgerdir
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.config.sources import RAW_DIR, SOURCE_REGISTRY
from engine.database.connection import _get_db_url

log = logging.getLogger(__name__)

_SCRIPTS_DIR = Path(__file__).parent
# Next to the data rather than in /tmp, which macOS wipes on restart — the logs
# are the only record of what an import run did.
_LOG_DIR = Path(RAW_DIR).parent / "logs" / "update"

# Sources whose import scripts support --new-only (stop when known docs found)
_NEW_ONLY_CAPABLE: set[str] = {
    "haestirettur",
    "landsrettur",
    "heradsdomstolar",
    "umbodsmadur",           # starts from max DB id+1, stops after 50 consecutive 404s
    "endurupptokudomur",         # island.is GraphQL — stops when page is fully known
    "malskotsbeidnir",           # island.is GraphQL — stops when page is fully known
    "personuvernd",              # island.is GraphQL — stops when page is fully known
    "fjolmidlanefnd",            # WP REST date DESC — stops at first known post
    "urskurdarnefnd_logmanna",   # HTML listing newest-first — stops at first known
    "yfirskattanefnd",           # years newest-first — stops after first fully-known year
    "uua",                       # HTML listing newest-first — stops at first known
    "landsdomar",                # HTML listing newest-first — stops at first known
    "hugverkastofa",             # Contentful API newest-first — stops at first known
    "felagsdomur",               # AJAX offset-based newest-first — stops at first known
    "enf",                       # HTML listing newest-first per page — stops at first known per page
    "kaeruna_voruthjonusta",     # sequential order — filters to last 2 years
    "samgongustofa",             # sequential order — filters to last 1 year by year section
    "fjarskiptastofa",           # Playwright scrape — restricts to current year only
    # stjornarradid sources already skip existing docs by default
}

# Sources that have a dedicated import script (just run it, no extra args)
_DEDICATED_SCRIPTS: dict[str, str] = {
    "haestirettur":           "import_haestirettur.py",
    "landsrettur":            "import_landsrettur.py",
    "heradsdomstolar":        "import_heradsdomstolar.py",
    "malskotsbeidnir":        "import_malskotsbeidnir.py",
    "landsdomar":             "import_landsdomar.py",
    "endurupptokudomur":      "import_endurupptokudomur.py",
    "felagsdomur":            "import_felagsdomur.py",
    "umbodsmadur":            "import_umbodsmadur.py",
    "yfirskattanefnd":        "import_yfirskattanefnd.py",
    "uua":                    "import_uua.py",
    "samkeppni":              "import_samkeppni.py",
    "personuvernd":           "import_personuvernd.py",
    "hugverkastofa":          "import_hugverkastofa.py",
    "fjarskiptastofa":        "import_fjarskiptastofa.py",
    "fjolmidlanefnd":         "import_fjolmidlanefnd.py",
    "samgongustofa":          "import_samgongustofa.py",
    "enf":                    "import_enf.py",
    "kaeruna_voruthjonusta":  "import_kaeruna_voruthjonusta.py",
    "urskurdarnefnd_logmanna":"import_urskurdarnefnd_logmanna.py",
    "logfraediritgerdir":     "import_logfraediritgerdir.py",
}

def _stjornarradid_sources() -> list[str]:
    """Return all short_names with stjornarradid_source=True in config."""
    return [
        name for name, cfg in SOURCE_REGISTRY.items()
        if cfg.stjornarradid_source
    ]


def _run_script(
    short_name: str,
    cmd: list[str],
    log_path: Path,
) -> tuple[bool, float]:
    """Run a subprocess, stream output to log file. Returns (success, elapsed)."""
    t0 = time.time()
    log.info("▶ %-30s  %s", short_name, " ".join(cmd))
    with log_path.open("w") as fout:
        result = subprocess.run(
            cmd,
            stdout=fout,
            stderr=subprocess.STDOUT,
            env=os.environ.copy(),
        )
    elapsed = time.time() - t0
    ok = result.returncode == 0
    status = "✓" if ok else "✗"
    log.info("%s %-30s  %.0fs  log: %s", status, short_name, elapsed, log_path)
    return ok, elapsed


def run_all(
    only: list[str] | None = None,
    skip: list[str] | None = None,
    new_only: bool = False,
) -> None:
    # Fail before the first import rather than letting every subprocess guess a
    # URL: the database listens on 5433, and a port-less default means 5432.
    _get_db_url()
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    results: list[tuple[str, bool, float]] = []

    def _should_run(name: str) -> bool:
        if only and name not in only:
            return False
        if skip and name in skip:
            return False
        return True

    # 1. Dedicated scripts
    for short_name, script in _DEDICATED_SCRIPTS.items():
        if not _should_run(short_name):
            continue
        script_path = _SCRIPTS_DIR / script
        if not script_path.exists():
            log.warning("Script not found: %s — skipping", script_path)
            continue
        cmd = ["uv", "run", "python", str(script_path)]
        if new_only and short_name in _NEW_ONLY_CAPABLE:
            cmd.append("--new-only")
        log_path = _LOG_DIR / f"{short_name}.log"
        ok, elapsed = _run_script(short_name, cmd, log_path)
        results.append((short_name, ok, elapsed))

    # 2. Stjornarradid sources via import_stjornarradid.py
    stjornarradid_script = str(_SCRIPTS_DIR / "import_stjornarradid.py")
    for short_name in _stjornarradid_sources():
        if not _should_run(short_name):
            continue
        # No --cid: the 2026 Blazor rewrite dropped the committee-UUID search
        # entirely, and all three formerly cid-filtered committees resolve from
        # ?Verkefni=<display_name> like every other one.
        cmd = ["uv", "run", "python", stjornarradid_script, "--source", short_name]
        if new_only:
            cmd.append("--new-only")
        log_path = _LOG_DIR / f"{short_name}.log"
        ok, elapsed = _run_script(short_name, cmd, log_path)
        results.append((short_name, ok, elapsed))

    # 3. Fill case_type for newly-imported court documents.  For Hæstiréttur and
    # Landsréttur this is the court's own classification, readable only through
    # the island.is caseTypes filter; for héraðsdómstólar it is the case-number
    # prefix.  --missing-only never overwrites a stored value: island.is
    # classifies Hæstaréttar verdicts from 2016 on only, so the older rows are
    # the last copy in existence (docs/snapshots/README.md).  Cheap when nothing
    # is new — the listing is newest-first and the walk stops at the oldest gap.
    if not (skip and "case_type" in skip):
        ct_script = _SCRIPTS_DIR / "backfill_case_type.py"
        if ct_script.exists():
            cmd = ["uv", "run", "python", str(ct_script), "--missing-only"]
            log_path = _LOG_DIR / "case_type_refresh.log"
            ok, elapsed = _run_script("case_type_refresh", cmd, log_path)
            results.append(("case_type_refresh", ok, elapsed))

    # 4. Refresh Icelandic FTS (fts_is) for any newly-imported docs.
    # Imports do NOT populate fts_is (lemmatization is Python/BÍN, not SQL), so
    # without this step new documents are unsearchable. backfill_fts_is.py only
    # touches rows where fts_is IS NULL, so this is cheap when nothing is new.
    if not (skip and "fts" in skip):
        fts_script = _SCRIPTS_DIR / "backfill_fts_is.py"
        if fts_script.exists():
            cmd = ["uv", "run", "python", str(fts_script)]
            log_path = _LOG_DIR / "fts_refresh.log"
            ok, elapsed = _run_script("fts_refresh", cmd, log_path)
            results.append(("fts_refresh", ok, elapsed))

    # 5. Rebuild passages for documents whose text changed (passage_hash stale).
    # Cheap when nothing is new; see engine/search/passage_index.py.
    if not (skip and "passages" in skip):
        pass_script = _SCRIPTS_DIR / "backfill_passages.py"
        if pass_script.exists():
            cmd = ["uv", "run", "python", str(pass_script)]
            log_path = _LOG_DIR / "passages_refresh.log"
            ok, elapsed = _run_script("passages_refresh", cmd, log_path)
            results.append(("passages_refresh", ok, elapsed))

    # 6. Rebuild citations for documents whose text changed (citation_hash stale),
    # then re-resolve rows that could not be linked before the new documents
    # existed. Must run after step 4 (docs/wiki/04-innflutningur.md § keyrsluröð).
    # Cheap when nothing is new; see engine/processors/citation_build.py.
    if not (skip and "citations" in skip):
        cit_script = _SCRIPTS_DIR / "build_citations.py"
        if cit_script.exists():
            for name, extra in (("citations_refresh", "--all"),
                                ("citations_relink", "--relink-unresolved")):
                cmd = ["uv", "run", "python", str(cit_script), extra]
                log_path = _LOG_DIR / f"{name}.log"
                ok, elapsed = _run_script(name, cmd, log_path)
                results.append((name, ok, elapsed))

    # Summary
    total = len(results)
    failed = [(n, e) for n, ok, e in results if not ok]
    total_time = sum(e for _, _, e in results)

    log.info("\n=== Niðurstaða ===")
    log.info("%d/%d sources updated  (%.0f min total)", total - len(failed), total, total_time / 60)
    if failed:
        log.warning("MISTÓKST (%d):", len(failed))
        for name, elapsed in failed:
            log.warning("  ✗ %-30s  log: %s", name, _LOG_DIR / f"{name}.log")
    else:
        log.info("Allt tókst.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="+", metavar="SOURCE",
                        help="Run only these sources")
    parser.add_argument("--skip", nargs="+", metavar="SOURCE",
                        help="Skip these sources (also accepts 'fts', 'passages' and "
                             "'citations' to skip the respective post-import refresh steps)")
    parser.add_argument("--new-only", action="store_true",
                        help="Only fetch documents not yet in DB (fast incremental update)")
    args = parser.parse_args()
    run_all(only=args.only, skip=args.skip, new_only=args.new_only)
