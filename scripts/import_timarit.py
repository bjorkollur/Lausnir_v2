"""Journals: discover → analyze → (review) → import  (spec 2026-10-03 §7).

    uv run --env-file .env python scripts/import_timarit.py discover
    uv run --env-file .env python scripts/import_timarit.py analyze --source timarit_logfraedinga
    uv run --env-file .env python scripts/import_timarit.py import --dry-run
    uv run --env-file .env python scripts/import_timarit.py status
    uv run --env-file .env python scripts/import_timarit.py hefti --dry-run --source timarit_logfraedinga

The archive in TIMARIT_DROPFOLDER_DIR is read-only; manifests live in
data/timarit/manifests (git); state lives in journal_issues.

`discover --dry-run` writes nothing at all (no manifests, no _unmatched.yaml, no database access); it only
prints what a real discover would do. (Every run also logs to Lausnir_Data/logs/timarit/.)

`analyze` runs the per-issue analysis over the manifests on disk (sequentially: leitir.is is throttled anyway;
`--workers` belongs to `import`), merges it with the user's edits, saves the manifest and updates journal_issues,
committing after every manifest. It never touches an approved/imported issue. An issue that fails gets
status `failed` and the exception's first line in `error`; the run goes on. `analyze --dry-run` saves nothing and
opens no database session (it still reads leitir.is and Claude, through their caches under Lausnir_Data/cache).

Without ANTHROPIC_API_KEY the Claude fallback for an unreadable table of contents works offline (Task 22a): a cached
answer is used, otherwise the TOC text is queued in Lausnir_Data/cache/timarit/llm/pending/ (the issue is analysed
without it, as before) and the run says how many are waiting. The Claude Code session answers them with
scripts/timarit_llm_queue.py (list / show / answer); the next `analyze` of the issue then uses the answer (provenance
`llm`, confidence capped, always reviewed).

Discovery drops two kinds of duplicate: byte-identical files (`duplicate_of`) and files whose text lies inside
a larger volume file of the same year (`contained_in`, spec 5.6); both are listed in data/timarit/_unmatched.yaml.
A file that holds more than one volume (config.SPLIT_FILES, Task 23e) is one issue per volume, each a page window of
the file (`pdf_window`), kept under its year (`{year}_x_x.yaml`); analysis reads only the window, import cuts from the
whole file.

`import` takes the approved manifests (and imported ones whose file changed since their import): every article is
cut out of its issue PDF (or taken from its own file), its text extracted page by page in `--workers` processes,
turned into a Document, validated and upserted; markdown and the article PDF are written, the issue PDF is copied
to raw/{source}/hefti/, and the issue is marked imported, committing after every manifest. Articles that could not
be imported say why in their `analysis_notes`; an issue that fails, or of which no article could be imported, gets
status `failed`. Afterwards run backfill_passages.py and build_citations.py. `import --dry-run` reads the archive
and the database but writes nothing (no documents, files, manifests or journal_issues); it prints every article.

`import --prune` (opt-in) also deletes the issue's stored articles that its manifest no longer names, e.g. after a
start page was edited and the article got a new id: the rows (their passages, citations and links follow by ON DELETE
CASCADE), and their markdown and article PDF once the commit has gone through. An article that failed, had no text or
collided this time is still named by the manifest and stays. Nothing is pruned from an issue where nothing was
imported. `--dry-run --prune` lists what would go. The default is unchanged: such rows are only counted and listed.

`import --reimport` (Task 22f) also takes issues that are already `imported` and whose manifest is unchanged (a plain
import skips them), e.g. after a change to the importer itself: the same upsert by external_id updates their rows in
place and their files are overwritten; their passages are rebuilt by backfill_passages.py afterwards (the upsert sets
passage_hash to NULL). Issues in review/pending/failed are never touched; --source/--issue/--dry-run apply as usual.

A re-import that gives an article a new file name (its page_end was corrected) removes the old markdown and PDF after
the commit; `--dry-run` lists them. `status` also lists `imported` issues that carry an error.

`hefti` (Task 23f) gives each article of a bound volume (Tímarit lögfræðinga's `{year}_x_x.yaml`) that has no `issue`
the hefti its first page falls in, read from the hefti covers in the PDF (engine/timarit/hefti.py, the same function
analyze uses), with provenance `page`; an article whose hefti cannot be told gets a note instead. It is meant for
manifests analyze no longer touches (approved, imported): it changes nothing but those `issue` values, their provenance
and that note, never a status or any other field, and never an `issue` that is set. It writes no database row. The
article's external_id and file name carry the hefti, so the next import of an imported volume stores the article under
a new id; the rows under the old ids are then stale (`import --prune` deletes them). `hefti --dry-run` saves nothing
and prints, per volume, the hefti found and what each article would get.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import copy
import contextvars
import datetime
import logging
import os
import re
import sys
import unicodedata
import uuid
from collections import Counter, defaultdict
from multiprocessing import Pool
from pathlib import Path
from typing import Callable

import asyncpg
import fitz
import httpx
import yaml
from sqlalchemy import exc as sa_exc, func, null as sa_null, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

sys.path.insert(0, str(Path(__file__).parent.parent))

import engine.config.sources as sources_config
import engine.database.connection as _db
from engine.config.sources import TIMARIT_DROPFOLDER_DIR, _DATA_DIR, get_config
from engine.database.models import Document, Source
from engine.processors.extractor import Extractor
from engine.processors.pdf_parser import docling_ocr_pdf
from engine.processors.renderer import unique_verdict_filename, verdict_filename, write_markdown
from engine.processors.validator import validate
from engine.timarit import config as C
from engine.timarit.analyze import AnalyzeDeps, analyze_issue
from engine.timarit.extract import ExtractedText, extract_article
from engine.timarit.firstpage import PageReader
from engine.timarit.hefti import assign_hefti, bound_volume
from engine.timarit.leitir_articles import search_articles
from engine.timarit.llm_toc import PENDING_DIR, cache_key, is_pending, toc_via_llm
from engine.timarit.manifest import (
    ArticleEntry, IssueManifest, article_issue, assign_external_ids, date_precision, issue_label, load_manifest,
    manifest_path, manifest_sha256, merge_preserving_user, save_manifest,
)
from engine.timarit.naming import FileIdentity, identify, issue_key, nfc, reject_reason, split_parts
from engine.timarit.splitter import cut_pages, page_count, sha256_bytes
from engine.timarit.ulfljotur_xlsx import load_xlsx

log = logging.getLogger("import_timarit")
REPO = Path(__file__).resolve().parent.parent
DROP = Path(TIMARIT_DROPFOLDER_DIR)
LOG_DIR = Path(_DATA_DIR) / "logs" / "timarit"
_FROZEN = ("approved", "imported")                       # statuses only the user or import moves
_DISCOVERY_ERROR = "fleiri en eitt heftisskjal"           # the prefix of the one error discovery itself writes
_ERROR_SEP = " | "                                        # discovery's error, then analyze's, in one `error`
_CHANGED_AFTER_APPROVAL = "frumskjal breyttist eftir samþykkt — yfirfarðu og greindu aftur"


def _list_archive() -> list[str]:
    return sorted(nfc(str(p.relative_to(DROP))) for p in DROP.rglob("*")
                  if p.is_file() and p.name != ".DS_Store" and "_meta" not in p.relative_to(DROP).parts)


def _trigrams(text_: str) -> frozenset[str]:
    """Word trigrams (three consecutive words, case-folded) of a page. Character trigrams do not discriminate:
    two unrelated Icelandic pages share more than half of them (measured, TL 1975)."""
    words = re.findall(r"\w+", unicodedata.normalize("NFC", text_).casefold())
    return frozenset(" ".join(words[i:i + 3]) for i in range(len(words) - 2))


def _judged_trigrams(pages: list[str]) -> list[frozenset[str]]:
    """Trigram set of every page; a page with too little text to judge gets an empty set."""
    sets = (_trigrams(p) for p in pages)
    return [t if len(t) >= C.CONTAINED_MIN_TRIGRAMS else frozenset() for t in sets]


def _containment_counts(candidate: list[frozenset[str]], container: list[frozenset[str]]) -> tuple[int, int]:
    matched = judged = 0
    pool = [b for b in container if b]
    for a in candidate:
        if not a:
            continue
        judged += 1
        need = C.CONTAINED_PAGE_MIN * len(a)
        if any(len(a & b) >= need for b in pool):
            matched += 1
    return matched, judged


def page_containment(candidate_pages: list[str], container_pages: list[str]) -> tuple[int, int]:
    """(pages of the candidate that lie inside the container, pages of the candidate that were judged). A page
    is judged when it has at least CONTAINED_MIN_TRIGRAMS word trigrams; it lies inside the container when some
    container page holds at least CONTAINED_PAGE_MIN of its trigrams (a containment measure, so a page that the
    container prints with a noisier text layer still counts)."""
    return _containment_counts(_judged_trigrams(candidate_pages), _judged_trigrams(container_pages))


def find_contained_files(fids: list[FileIdentity], page_texts: Callable[[str], list[str]]) -> dict[str, str]:
    """{contained file: the volume file that holds it}. Within one (source, year, volume), a file is contained
    when at least CONTAINED_MIN_SHARE of its judged pages lie inside a strictly larger file of granularity
    'volume' that is itself kept. Only scopes that have such a volume file next to another file are opened.
    Files are tried largest first, so a chain A in B in C resolves to the surviving container C."""
    scopes: dict[tuple, list[FileIdentity]] = defaultdict(list)
    for f in fids:
        scopes[(f.source, f.year, f.volume)].append(f)
    found: dict[str, str] = {}
    for (_source, year, _volume), files in sorted(scopes.items(), key=lambda kv: str(kv[0])):
        if year is None or len(files) < 2 or not any(f.granularity == "volume" for f in files):
            continue
        texts = {f.rel_path: page_texts(f.rel_path) for f in files}
        sets: dict[str, list[frozenset[str]]] = {}
        containers: list[FileIdentity] = []
        for f in sorted(files, key=lambda f: (-len(texts[f.rel_path]), f.rel_path)):
            mine = sets[f.rel_path] = _judged_trigrams(texts[f.rel_path])
            holder = None
            for c in containers:
                if len(texts[c.rel_path]) <= len(texts[f.rel_path]):
                    continue
                matched, judged = _containment_counts(mine, sets[c.rel_path])
                if judged:
                    log.info("contained? %s in %s: %d/%d pages (%.0f%%)", f.rel_path, c.rel_path, matched, judged,
                             100 * matched / judged)
                if judged and matched / judged >= C.CONTAINED_MIN_SHARE:
                    holder = c
                    break
            if holder is not None:
                found[f.rel_path] = holder.rel_path
            elif f.granularity == "volume":
                containers.append(f)
    return found


def _split_discovery_error(error: str | None) -> tuple[str | None, str | None]:
    """(discovery's error, whatever analyze appended after it) of an `error` that starts with discovery's own
    message; (None, None) for any other error, which is not discovery's to touch."""
    if not error or not error.startswith(_DISCOVERY_ERROR):
        return None, None
    disc, _, rest = error.partition(_ERROR_SEP)
    return disc, rest or None


def _join_errors(*parts: str | None) -> str | None:
    return _ERROR_SEP.join(p for p in parts if p) or None


def plan_discovery(rel_paths: list[str], read_bytes: Callable[[str], bytes],
                   page_texts: Callable[[str], list[str]] | None = None
                   ) -> tuple[list[IssueManifest], list[dict]]:
    """Group the archive's files into issues. Returns (manifests, unmatched): one manifest per issue key with
    its `files` (and the sha256 of its issue/volume PDF), and a record {"path", "reason", "duplicate_of"?} for
    every file that is not imported (not a PDF, deferred MDE, working files, no filename pattern, byte
    duplicate, content duplicate). Paths are processed in sorted NFC order, so the first of two byte-identical
    files is the one that is kept. With `page_texts` (rel path → text of each page) a file whose text lies
    inside a larger volume file of the same year is recorded as `contained_in` and left out (spec 5.6). A file that
    holds more than one volume (config.SPLIT_FILES) is then one issue per volume, with its year, volume and page window
    (Task 23e); the parts share the file and its sha256, so they are split only after both duplicate checks."""
    unmatched: list[dict] = []
    seen_hash: dict[str, str] = {}
    hashes: dict[str, str] = {}
    kept: list[FileIdentity] = []
    for rel in sorted(nfc(r) for r in rel_paths):
        why = reject_reason(rel)
        if why:
            unmatched.append({"path": rel, "reason": why})
            continue
        h = hashes[rel] = sha256_bytes(read_bytes(rel))
        if h in seen_hash:
            unmatched.append({"path": rel, "reason": "duplicate_of", "duplicate_of": seen_hash[h]})
            continue
        seen_hash[h] = rel
        kept.append(identify(rel))
    if page_texts is not None:
        contained = find_contained_files(kept, page_texts)
        unmatched += [{"path": rel, "reason": "contained_in", "duplicate_of": holder} for rel, holder in contained.items()]
        kept = [f for f in kept if f.rel_path not in contained]
    kept = [part for f in kept for part in split_parts(f)]
    unmatched.sort(key=lambda u: u["path"])
    groups: dict[tuple, list] = defaultdict(list)
    for fid in kept:
        groups[issue_key(fid)].append(fid)
    manifests: list[IssueManifest] = []
    for (source, year, volume, issue), fids in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0, kv[0][2] or 0, kv[0][3] or "")):
        cfg = get_config(source)
        containers = [f for f in fids if f.granularity in ("issue", "volume")]
        year_end = next((f.year_end for f in fids if f.year_end), None)
        window = containers[0].pdf_window if containers else None
        m = IssueManifest(source=source, year=year, volume=volume, issue=issue,
                          label=issue_label(cfg, year, volume, issue), year_end=year_end,
                          file=containers[0].rel_path if containers else None,
                          sha256=hashes[containers[0].rel_path] if containers else None,
                          files=sorted(f.rel_path for f in fids), pdf_window=list(window) if window else None)
        if len(containers) > 1:
            m.error = _DISCOVERY_ERROR + " fyrir sama hefti: " + "; ".join(c.rel_path for c in containers)
        manifests.append(m)
    return manifests, unmatched


def write_unmatched(items: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(items, allow_unicode=True, sort_keys=False), encoding="utf-8")


def discovery_path(root: Path, m: IssueManifest) -> Path:
    """Where discover keeps an issue's manifest: {year}_{volume}_{issue}.yaml under its source. A part of a file that
    holds more than one volume (pdf_window, Task 23e) is kept under its year alone, as the file name gives no volume
    (the volume is the split table's): so the first part keeps the manifest, and the journal_issues row (R4), that the
    whole file had."""
    return manifest_path(root, m.source, m.year, None if m.pdf_window is not None else m.volume, m.issue)


def merge_with_existing(path: Path, new: IssueManifest) -> IssueManifest:
    """The manifest to save for a freshly discovered issue. Without a file on disk that is `new` itself.
    With one, the existing manifest stays as it is — articles, status, and whatever analyze filled in (year,
    label, volume, confidence) — and only what discovery owns is refreshed: the file list (`file`, `files`,
    `year_end`) and its own error. Re-running discover therefore never discards an analysis or a review.

    When the source file changed (another container file, other bytes under the same name, another set of
    article files, another page window) what was derived from it is stale: sha256 (replaced by the new one), pages
    and page_map are cleared, and an issue that is not approved/imported goes back to `pending` so analyze runs again.
    An approved/imported issue keeps its status (R4) but is flagged in `error`. Analyze clears that flag when
    it re-runs. A discovery error that discovery no longer reports is removed (what analyze appended after it
    stays); errors written by analyze or import are left alone.

    A part of a file that holds more than one volume (Task 23e) is the split table's, the user's decision: its page
    window, year, volume and label are discovery's too, and so is the window's absence when a manifest that was a
    part is the whole file again."""
    if not path.exists():
        return new
    old = load_manifest(path)
    changed = (old.file != new.file or set(old.files) != set(new.files)
               or (old.sha256 is not None and old.sha256 != new.sha256) or old.pdf_window != new.pdf_window)
    if new.pdf_window is not None or old.pdf_window is not None:
        old.pdf_window, old.year, old.volume, old.label = new.pdf_window, new.year, new.volume, new.label
    old.file, old.files, old.year_end = new.file, new.files, new.year_end
    if changed:
        old.sha256, old.pages, old.page_map = new.sha256, None, {}
        if old.status not in _FROZEN:
            old.status = "pending"
    elif old.sha256 is None:
        old.sha256 = new.sha256
    disc, rest = _split_discovery_error(old.error)       # analyze may have appended its own error to discovery's
    if new.error:
        old.error = _join_errors(new.error, rest)
    elif disc:
        old.error = rest
    if changed and old.status in _FROZEN and _CHANGED_AFTER_APPROVAL not in (old.error or ""):
        old.error = "; ".join(filter(None, [_CHANGED_AFTER_APPROVAL, old.error]))
    return old


def summarize(manifests: list[IssueManifest], unmatched: list[dict]) -> list[str]:
    """The counts the user reads after discover (Icelandic)."""
    per_source = Counter(m.source for m in manifests)
    per_reason = Counter(u["reason"] for u in unmatched)
    lines = ["Hefti eftir heimild:"]
    lines += [f"  {s:28s} {n:5d}" for s, n in sorted(per_source.items())]
    lines.append(f"  {'samtals':28s} {len(manifests):5d}")
    lines.append("Skrár sem ekki eru fluttar inn, eftir ástæðu:")
    lines += [f"  {r:28s} {n:5d}" for r, n in sorted(per_reason.items())]
    lines.append(f"  {'samtals':28s} {len(unmatched):5d}")
    lines.append(f"discover: {len(manifests)} hefti, {len(unmatched)} skrár óþekktar/tvíteknar")
    return lines


async def _source_id(session, short_name: str):
    sid = (await session.execute(select(Source.id).where(Source.short_name == short_name))).scalar_one_or_none()
    if sid is None:
        cfg = get_config(short_name)
        row = Source(short_name=short_name, display_name=cfg.display_name)
        session.add(row)
        await session.flush()
        sid = row.id
    return sid


async def upsert_issue_row(session, m: IssueManifest, manifest_rel: str) -> None:
    """Insert or update the journal_issues row of a manifest. The row is identified by `manifest_path` (R4):
    analyze may later change year, volume, issue or label, and that must update the row, not add a second one.
    Status never leaves approved/imported, and an imported row keeps `file_path` (import has pointed it at the
    RAW copy by then). A year that is still unknown is stored as 0 (NOT NULL column); the manifest keeps None."""
    sid = await _source_id(session, m.source)
    await session.execute(text("""
        INSERT INTO journal_issues (id, source_id, year, volume, issue, label, file_path, sha256, page_count,
                                    manifest_path, status, confidence, error)
        VALUES (gen_random_uuid(), :sid, :year, :volume, :issue, :label, :file_path, :sha256, :pages,
                :manifest, :status, :confidence, :error)
        ON CONFLICT (manifest_path)
        DO UPDATE SET year = EXCLUDED.year, volume = EXCLUDED.volume, issue = EXCLUDED.issue,
                      label = EXCLUDED.label, sha256 = EXCLUDED.sha256, page_count = EXCLUDED.page_count,
                      confidence = EXCLUDED.confidence, error = EXCLUDED.error, updated_at = now(),
                      file_path = CASE WHEN journal_issues.status = 'imported' THEN journal_issues.file_path
                                       ELSE EXCLUDED.file_path END,
                      status = CASE WHEN journal_issues.status IN ('approved', 'imported') THEN journal_issues.status
                                    ELSE EXCLUDED.status END
    """), {"sid": sid, "year": m.year or 0, "volume": m.volume, "issue": m.issue, "label": m.label,
           "file_path": m.file, "sha256": m.sha256, "pages": m.pages, "manifest": manifest_rel,
           "status": m.status, "confidence": m.confidence, "error": m.error})


def _read_archive(rel: str) -> bytes:
    try:
        return (DROP / rel).read_bytes()
    except OSError as e:
        raise SystemExit(f"discover: ekki tókst að lesa {rel}: {e}") from e


def _pdf_page_texts(rel: str) -> list[str]:
    """Text of every page of an archive PDF; [] when the file cannot be parsed (it is then never judged)."""
    try:
        with fitz.open(stream=_read_archive(rel), filetype="pdf") as doc:
            return [page.get_text() for page in doc]
    except (fitz.FileDataError, RuntimeError, ValueError) as e:
        log.warning("cannot read the text of %s: %s", rel, e)
        return []


class _Progress:
    """Wraps the archive reader to log progress: hashing ~1,700 PDFs takes minutes."""
    def __init__(self, every: int = 200):
        self.n, self.every = 0, every

    def __call__(self, rel: str) -> bytes:
        self.n += 1
        if self.n % self.every == 0:
            log.info("discover: read %d files", self.n)
        return _read_archive(rel)


async def cmd_discover(args) -> None:
    manifests, unmatched = plan_discovery(_list_archive(), _Progress(), _pdf_page_texts)
    todo = [m for m in manifests if not args.source or m.source == args.source]
    # Merge everything before writing anything: a hand-edited manifest that does not load stops the run first.
    planned: list[tuple[Path, IssueManifest]] = []
    seen_paths: dict[Path, IssueManifest] = {}
    for m in todo:
        path = discovery_path(REPO / C.MANIFEST_ROOT, m)
        if path in seen_paths:
            raise SystemExit(f"discover: tvö hefti deila sömu yfirlitsskrá {path}: "
                             f"{seen_paths[path].label!r} og {m.label!r}")
        seen_paths[path] = m
        try:
            planned.append((path, merge_with_existing(path, m)))
        except ValueError as e:
            raise SystemExit(f"discover: yfirlitsskrá er ógild: {e}") from e

    for line in summarize(todo, unmatched):
        print(line)
    if args.dry_run:
        print("Þurrkeyrsla: ekkert var skrifað (engar yfirlitsskrár, enginn _unmatched.yaml, enginn gagnagrunnur).")
        return

    write_unmatched(unmatched, REPO / C.UNMATCHED_PATH)
    await _db.init_db(create_tables=False)
    try:
        async with _db.AsyncSessionLocal() as session:
            for path, m in planned:
                save_manifest(m, path)
                await upsert_issue_row(session, m, str(path.relative_to(REPO)))
            await session.commit()
    finally:
        await _db.dispose_db()
    log.info("discover: wrote %d manifests and journal_issues rows; %d unmatched files → %s",
             len(planned), len(unmatched), C.UNMATCHED_PATH)


async def _status_lines(session) -> list[str]:
    """What `status` prints: issues per source and status; the error of every `failed` issue; and, under a heading of
    their own, the issues that are `imported` but carry an `error` (Task 23a): a partial import (articles without
    text, collisions, stale rows) or a re-import that failed and left the earlier rows standing. Those would otherwise
    be listed nowhere."""
    rows = (await session.execute(text("""
        SELECT s.short_name, ji.status, count(*) FROM journal_issues ji JOIN sources s ON s.id = ji.source_id
        GROUP BY 1, 2 ORDER BY 1, 2"""))).all()
    lines = ["Engin hefti í gagnagrunni enn (keyrðu discover)."] if not rows else []
    lines += [f"{short:28s} {status:9s} {n:5d}" for short, status, n in rows]
    failed = (await session.execute(text(
        "SELECT label, error FROM journal_issues WHERE status = 'failed' ORDER BY label"))).all()
    lines += [f"  ✗ {label}: {err}" for label, err in failed]
    flawed = (await session.execute(text(
        "SELECT label, error FROM journal_issues WHERE status = 'imported' AND coalesce(error, '') <> '' "
        "ORDER BY label"))).all()
    if flawed:
        lines.append("Innflutt hefti með villu (heftið stendur, villan segir hvað vantar eða mistókst):")
        lines += [f"  ! {label}: {err}" for label, err in flawed]
    return lines


async def cmd_status(args) -> None:
    await _db.init_db(create_tables=False)
    try:
        async with _db.AsyncSessionLocal() as session:
            for line in await _status_lines(session):
                print(line)
    finally:
        await _db.dispose_db()


# --- analyze -------------------------------------------------------------------------------------------

def _manifest_rel(path: Path) -> str:
    """manifest_path as journal_issues stores it: relative to the repo root (always so outside tests)."""
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def _is_frozen(path: Path) -> bool:
    """True when the manifest on disk is approved/imported. A manifest that does not load is not frozen:
    it stays selected so that analyze reports it."""
    try:
        return load_manifest(path).status in _FROZEN
    except (ValueError, OSError):
        return False


def _select_manifests(args) -> list[Path]:
    """Manifests chosen by --source / --issue (a stem, or a prefix up to an underscore: "2010" → 2010_*) and
    --limit. The limit counts manifests the command will actually work on: for analyze those that are not
    approved/imported, for import exactly those."""
    paths = sorted((REPO / C.MANIFEST_ROOT).glob("*/*.yaml"))
    if args.source:
        paths = [p for p in paths if p.parent.name == args.source]
    if args.issue:
        paths = [p for p in paths if p.stem == args.issue or p.stem.startswith(args.issue + "_")]
    if args.limit:
        for_import = getattr(args, "command", None) == "import"
        chosen: list[Path] = []
        for p in paths:
            if len(chosen) == args.limit:
                break
            if _is_frozen(p) == for_import:
                chosen.append(p)
        paths = chosen
    return paths


# The issue being analysed, for the offline Claude queue (Task 22a): `_analyse` sets it around analyze_issue, so that
# the llm_toc dependency, which only receives the TOC text, can say which manifest the text came from.
_ISSUE_CONTEXT: contextvars.ContextVar[dict | None] = contextvars.ContextVar("timarit_issue_context", default=None)


def _llm_cache_dir() -> Path:
    return C.CACHE_DIR / "llm"


def _issue_context(m: IssueManifest, path: Path) -> dict:
    """Which issue a queued text came from; a part of a split file (Task 23e) also says which PDF pages are its own."""
    ctx = {"manifest": _manifest_rel(path), "source": m.source, "year": m.year, "volume": m.volume,
           "issue": m.issue, "label": m.label}
    return ctx | ({"pdf_window": list(m.pdf_window)} if m.pdf_window is not None else {})


def _real_deps_factory(client: httpx.AsyncClient, llm_client=None):
    """AnalyzeDeps over the real archive, leitir.is (one shared client) and Claude: through `llm_client` when it is
    given, otherwise (no API key) offline, from the cache only, queuing the TOC texts it cannot answer. Caches live
    in CACHE_DIR, never in the archive (R3). The returned factory has `queued`, the keys of the TOC texts that this
    run left in the offline queue."""
    xlsx_rows = load_xlsx(C.XLSX_PATH) if C.XLSX_PATH.exists() else []
    queued: set[str] = set()

    async def leitir(q, journal):
        return await search_articles(client, q, cache_dir=C.CACHE_DIR / "leitir", journal_filter=journal)

    async def llm(text_):
        return await toc_via_llm(text_, cache_dir=_llm_cache_dir(), client=llm_client)

    async def llm_offline(text_):
        entries = await toc_via_llm(text_, cache_dir=_llm_cache_dir(), offline=True, context=_ISSUE_CONTEXT.get())
        if not entries and is_pending(_llm_cache_dir(), cache_key(text_)):
            queued.add(cache_key(text_))
        return entries

    def factory() -> AnalyzeDeps:
        return AnalyzeDeps(read_bytes=lambda rel: (DROP / rel).read_bytes(), leitir=leitir,
                           llm_toc=llm if llm_client is not None else llm_offline, xlsx_rows=xlsx_rows,
                           llm_pending=None if llm_client is not None else
                           (lambda text_: is_pending(_llm_cache_dir(), cache_key(text_))))
    factory.queued = queued
    return factory


def _error_text(exc: BaseException, prefix: str = "") -> str:
    """"ExceptionName: first line of its message" (just the name when there is no message)."""
    lines = str(exc).splitlines()
    first = lines[0].strip() if lines else ""
    return f"{prefix}{type(exc).__name__}: {first}" if first else f"{prefix}{type(exc).__name__}"


async def _analyse(old: IssueManifest, path: Path, deps_factory) -> IssueManifest:
    """The manifest to save for `old`: analysed and merged with the user's edits, or `old` itself marked
    `failed` (with its articles, which are the user's work) when the analysis raised.

    The error discovery wrote (several container files for one issue; only the first is analysed, the other's
    articles would be dropped silently) outlives the analysis: analyze_issue starts from a clean `error`, so it is
    put back in front of analyze's own, and an issue that would be approved goes to review instead. Only the user
    resolves it (by editing the files or approving), and discover removes it once it stops reporting it."""
    carried, _ = _split_discovery_error(old.error)
    frozen = old.status in _FROZEN
    token = _ISSUE_CONTEXT.set(_issue_context(old, path))
    try:
        new = merge_preserving_user(old, await analyze_issue(old, get_config(old.source), deps_factory()))
    except Exception as exc:  # noqa: BLE001 — one bad issue must not stop the run
        log.exception("analyze failed for %s", path.name)
        old.status, old.error = "failed", _error_text(exc)
        new = old
    finally:
        _ISSUE_CONTEXT.reset(token)
    if carried:
        new.error = _join_errors(carried, new.error)
        if new.status != "failed" and not frozen:
            new.status = "review"
    return new


async def analyze_manifests(paths: list[Path], *, deps_factory: Callable[[], AnalyzeDeps], save: bool,
                            session=None) -> dict[str, int]:
    """Analyse the manifests at `paths` one after the other; returns {resulting status: count}. "skipped" counts
    approved/imported ones, which are never touched; "failed" an issue whose manifest file does not load (the
    file is left as it is: it is the user's hand edit), whose analysis raised, or whose journal_issues row could
    not be written. With `save` each manifest is written to its file and, given a `session`, upserted into
    journal_issues and committed before the next one starts, so a crash keeps the earlier work."""
    counts: dict[str, int] = defaultdict(int)
    for path in paths:
        try:
            old = load_manifest(path)
        except (ValueError, OSError) as exc:
            log.error("cannot load %s: %s", path.name, exc)
            counts["failed"] += 1
            continue
        if old.status in _FROZEN:
            counts["skipped"] += 1
            continue
        new = await _analyse(old, path, deps_factory)
        if save:
            save_manifest(new, path)
            if session is not None:
                try:
                    await upsert_issue_row(session, new, _manifest_rel(path))
                    await session.commit()
                except Exception as exc:  # noqa: BLE001 — e.g. a year/volume/issue another manifest already holds
                    await session.rollback()
                    log.exception("journal_issues update failed for %s", path.name)
                    new.status, new.error = "failed", _error_text(exc, "gagnagrunnur: ")
                    save_manifest(new, path)
        counts[new.status] += 1
        log.info("%-9s %s  (%d articles, confidence %.2f)%s", new.status, new.label, len(new.articles),
                 new.confidence, f"  {new.error}" if new.error else "")
    return dict(counts)


async def cmd_analyze(args) -> None:
    paths = _select_manifests(args)
    if not paths:
        print("Engar yfirlitsskrár fundust (keyrðu discover fyrst eða rýmkaðu --source/--issue).")
        return
    save = not args.dry_run
    if save:
        await _db.init_db(create_tables=False)
    try:
        async with contextlib.AsyncExitStack() as stack:
            client = await stack.enter_async_context(httpx.AsyncClient(headers={"User-Agent": "Lausnir/2.0"}))
            llm_client = None
            if os.environ.get("ANTHROPIC_API_KEY"):
                from anthropic import AsyncAnthropic
                llm_client = await stack.enter_async_context(AsyncAnthropic())
            session = await stack.enter_async_context(_db.AsyncSessionLocal()) if save else None
            deps_factory = _real_deps_factory(client, llm_client)
            counts = await analyze_manifests(paths, deps_factory=deps_factory, save=save, session=session)
    finally:
        if save:
            await _db.dispose_db()
    print(f"analyze: {len(paths)} hefti")
    for status, n in sorted(counts.items()):
        print(f"  {status:9s} {n:5d}")
    waiting = len(getattr(deps_factory, "queued", ()))
    if waiting:
        where = _llm_cache_dir() / PENDING_DIR
        log.info("%d TOC text(s) queued for the Claude Code session in %s", waiting, where)
        print(f"{waiting} efnisyfirlit {'bíður' if waiting == 1 else 'bíða'} svars Claude (enginn ANTHROPIC_API_KEY): {where}\n"
              "  Sjá biðröðina: uv run --env-file .env python scripts/timarit_llm_queue.py list; "
              "keyrðu analyze aftur þegar svör eru komin.")
    if args.dry_run:
        print("Þurrkeyrsla: ekkert var vistað (engar yfirlitsskrár, enginn gagnagrunnur).")


# --- import -------------------------------------------------------------------------------------------------

# What import writes into an article's analysis_notes (Icelandic: the reviewer reads them). They are dropped at the
# start of every import, so a problem that has since been fixed does not linger in the manifest.
_NOTE_NO_PAGES = "ekki flutt inn: engar PDF-síður (pdf_pages)"
_NOTE_NO_TEXT = "enginn texti — þarf OCR"
_NOTE_FAILED = "innflutningur mistókst: "
_NOTE_COLLISION = "auðkenni {} er þegar til í öðru hefti"
_IMPORT_NOTE = re.compile(r"ekki flutt inn: .*|enginn texti — þarf OCR|innflutningur mistókst: .*"
                          r"|auðkenni .+ er þegar til í öðru hefti")
# The issue-level error import writes (manifest and journal_issues): a failure, or what of the manifest did not reach
# the database. Every import replaces it (a clean one removes it); an error from elsewhere is kept beside it.
_IMPORT_ERROR = "innflutningur: "
_NOT_IMPORTED = (("no_text", "enginn texti"), ("failed", "villa"), ("collision", "árekstur"))
_MAX_LISTED_ORPHANS = 10
_MAX_FAILURES_IN_A_ROW = 5          # issues failing one after the other point at the environment, not at the issues
_COUNT_LABELS = {
    "imported": "greinar fluttar inn", "unchanged": "hefti óbreytt", "not_approved": "hefti ekki samþykkt",
    "skipped_no_pages": "greinar án PDF-síðna", "collision": "auðkenni þegar í öðru hefti",
    "no_text": "greinar án texta", "failed": "greinar mistókust", "failed_issue": "hefti mistókust",
    "source_changed": "frumskjal breytt", "discovery_error": "fleiri en eitt heftisskjal",
    "not_analysed": "hefti ekki greint", "orphaned": "eldri greinar ekki í skránni",
    "pruned": "úreltum greinum eytt", "renamed": "greinar með nýtt skráarnafn", "reimported": "hefti flutt inn aftur",
    "reimport_failed": "endurinnflutningur mistókst (fyrri innflutningur stendur)",
}


def article_pdf_bytes(m: IssueManifest, a: ArticleEntry, read_bytes) -> bytes:
    """The article's PDF: its own file, unchanged (spec §4.5), or its pdf_pages cut out of the issue PDF. An own
    file that holds more than one item (Task 22c: the next item starts inside it; Task 22e: the user added the
    item) is cut to the pages of this one, so that no text is stored twice; one that pdf_pages covers whole, or
    that has no pdf_pages, is returned as it is."""
    if a.source_file:
        data = read_bytes(a.source_file)
        if not a.pdf_pages:
            return data
        first, last = a.pdf_pages
        if first <= 0 and last >= page_count(data) - 1:        # the pages are the whole file (cut_pages clamps `last`)
            return data
        return cut_pages(data, first, last)
    if m.file is None:
        raise ValueError("greinin hefur hvorki eigið skjal (source_file) né hefti til að skera úr")
    if not a.pdf_pages:
        raise ValueError("pdf_pages vantar")
    first, last = a.pdf_pages
    return cut_pages(read_bytes(m.file), first, last)


def build_article_raw(m: IssueManifest, a: ArticleEntry, ex: ExtractedText, source_file: str) -> dict:
    """The raw dict _extract_timarit consumes (Task 6 contract): the manifest line as reviewed, the article's own
    issue (R31), the user's note and analyze's notes (R33), the issue it came from, and the extracted text with its
    page offsets. raw_api_data keeps all of it but the text and the offsets (they have columns of their own).
    `page_note`, only when the print skips a number after printed_start (Task 22f), is import's own note: it is kept
    in raw_api_data and never written to the manifest."""
    return {
        "title": a.title, "authors": list(a.authors), "author_titles": list(a.author_titles),
        "article_type": a.article_type, "page_start": a.page_start, "page_end": a.page_end, "lang": a.lang,
        "volume": m.volume, "issue": article_issue(m, a), "year": m.year, "date": a.date, "summary": a.summary,
        "keywords": list(a.keywords), "pdf_text": ex.body, "page_offsets": ex.page_offsets,
        "source_filename": source_file, "pdf_pages": a.pdf_pages, "provenance": dict(a.provenance),
        "checks": dict(a.checks), "confidence": a.confidence, "leitir_record_id": a.leitir_record_id,
        "xlsx_row": a.xlsx_row, "ocr_source": ex.ocr_source, "date_precision": date_precision(a),
        "bin_ratio": round(ex.bin_ratio, 3), "issue_label": m.label, "issue_file": m.file,
        "note": a.note, "analysis_notes": list(a.analysis_notes),
    } | ({"page_note": ex.page_note} if ex.page_note else {})


def _ocr(pdf: bytes) -> str | None:
    """docling + tesseract (isl) for an article without a usable text layer. Only scans reach it, and those stay in
    review (analyze notes "ekkert textalag"), so import runs it only for a scan the user approved."""
    return docling_ocr_pdf(pdf, timeout=900)


_PRINTED_SOURCES = frozenset({"page", "user"})


def printed_start_of(a: ArticleEntry) -> int | None:
    """The printed number of the article's first PDF page, for its page anchors (`bls. N`), or None (the
    article-local page map numbers the pages then).

    R17: only a start page that is known to be the printed one. R46 (Task 22f) says which those are: a page_start
    read from the printed page (provenance `page`, also `filename+page`; analyze labels a number from the issue's
    page map `page` as well) or set by the user (`user`, also `page+user`); and, as before, one that a registry gave
    and the printed page confirmed (`printed_page_match`, R41). A registry number the page did not confirm (`toc`,
    `xlsx`, `leitir`, `llm`, `filename` with the check False) anchors nothing. The provenance is read from the key
    `page_start` when the manifest has it (the narrower key: the user may set the start page alone), else `pages`.
    The later pages' numbers follow the print from there (pagemap.numbers_from_start)."""
    if a.page_start is None:
        return None
    label = a.provenance.get("page_start") or a.provenance.get("pages")     # the narrower key first (manifest aliases)
    sources = {s.strip().lower() for s in str(label or "").split("+")}
    if sources & _PRINTED_SOURCES or a.checks.get("printed_page_match"):
        return a.page_start
    return None


def _work(item: tuple) -> tuple[str, dict | None, str | None]:
    """Worker (a Pool process, or in-process with one worker): (key, pdf bytes, source, footnotes, printed_start)
    → (key, the extracted text as a plain dict, None), or (key, None, error) when extraction raised."""
    key, pdf, source, footnotes, printed_start = item
    try:
        ex = extract_article(pdf, source=source, footnotes=footnotes, ocr=_ocr,
                             ocr_bin_threshold=C.OCR_BIN_RATIO_THRESHOLD, printed_start=printed_start)
    except Exception as exc:  # noqa: BLE001 — one bad article must not stop its issue
        return key, None, _error_text(exc)
    return key, {"body": ex.body, "page_offsets": ex.page_offsets, "ocr_source": ex.ocr_source,
                 "bin_ratio": ex.bin_ratio, "page_note": ex.page_note}, None


@contextlib.contextmanager
def _extracting(jobs: list[tuple], workers: int, pool=None):
    """_work over `jobs`, results in job order: on the caller's pool, in-process for one worker or one job, else on
    a Pool of this call's own that is shut down when the caller is done with the results."""
    if pool is not None:
        yield pool.imap(_work, jobs)
    elif workers <= 1 or len(jobs) <= 1:
        yield map(_work, jobs)
    else:
        with Pool(processes=min(workers, len(jobs))) as own:
            yield own.imap(_work, jobs)


async def _upsert_doc(session, doc: Document) -> uuid.UUID:
    """INSERT or UPDATE by (source_id, external_id); returns the id of the row actually stored (the existing one
    on conflict). Writes every field _extract_timarit fills (tests/test_import_upsert_parity.py). passage_hash is
    reset: it hashes summary and body only, so changed page_offsets or lang would otherwise leave stale passages
    (NULL is stale to backfill_passages.py, see passage_index.STALE_WHERE)."""
    def _v(val):
        # asyncpg serializes Python None to JSON null for JSONB columns; use sa_null() for SQL NULL
        return sa_null() if val is None else val

    values = {
        "id": doc.id,
        "source_id": doc.source_id,
        "external_id": doc.external_id,
        "url": _v(doc.url),
        "raw_api_data": _v(doc.raw_api_data),
        "case_number": _v(doc.case_number),
        "document_date": _v(doc.document_date),
        "court": _v(doc.court),
        "verdict_type": _v(doc.verdict_type),
        "instance_tier": _v(doc.instance_tier),
        "case_type": _v(doc.case_type),
        "plaintiffs": _v(doc.plaintiffs),
        "defendants": _v(doc.defendants),
        "keywords": _v(doc.keywords),
        "summary": _v(doc.summary),
        "body_text": _v(doc.body_text),
        "lower_body_text": _v(doc.lower_body_text),
        "volume": _v(doc.volume),
        "issue": _v(doc.issue),
        "page_start": _v(doc.page_start),
        "page_end": _v(doc.page_end),
        "lang": _v(doc.lang),
        "page_offsets": _v(doc.page_offsets),
        "journal_issue_id": _v(doc.journal_issue_id),
        "verdict_filename": _v(doc.verdict_filename),
        "validation_errors": _v(doc.validation_errors),
    }
    stmt = pg_insert(Document).values(**values)
    update_cols = {k: v for k, v in values.items() if k not in ("id", "source_id", "external_id")}
    # The court importers' rule for a value another process fills (spec §7.1 step 4): fill, never overwrite.
    update_cols["case_type"] = func.coalesce(Document.case_type, stmt.excluded.case_type)
    update_cols["passage_hash"] = sa_null()
    update_cols["updated_at"] = func.now()
    stmt = stmt.on_conflict_do_update(constraint="uq_doc_source_external", set_=update_cols).returning(Document.id)
    return (await session.execute(stmt)).scalar_one()


async def _issue_row(session, manifest_rel: str):
    """(id, manifest_sha256) of the issue's journal_issues row, found by manifest_path (R4), or None."""
    return (await session.execute(text(
        "SELECT id, manifest_sha256 FROM journal_issues WHERE manifest_path = :p"), {"p": manifest_rel})).first()


async def _stored_docs(session, source_id) -> list[tuple]:
    """(external_id, journal_issue_id, verdict_filename) of every document the source already has."""
    if source_id is None:
        return []
    rows = (await session.execute(
        select(Document.external_id, Document.journal_issue_id, Document.verdict_filename)
        .where(Document.source_id == source_id))).all()
    return [tuple(r) for r in rows]


def _stale_ids(stored: list[tuple], issue_id, ids) -> list[str]:
    """External ids of this issue's stored documents that its manifest no longer has (sorted). Other issues' rows,
    and every id the manifest still names (whether or not it was imported this time), are never stale."""
    if issue_id is None:
        return []
    keep = set(ids)
    return sorted(eid for eid, jid, _ in stored if jid == issue_id and eid not in keep)


# --prune. Every FK to documents.id is ON DELETE CASCADE (passages, document_links both ways, citations.from_doc_id) or
# SET NULL (citations.to_doc_id), so this one DELETE takes the dependents with it; a citation of another document that
# pointed at the row is left dangling, which build_citations picks up and re-resolves (_DANGLING_PRED). Source and
# issue are in the WHERE clause so that another issue's or source's row can never go.
_PRUNE = text("""DELETE FROM documents WHERE source_id = :source AND journal_issue_id = :issue
                 AND external_id = ANY(:ids) RETURNING external_id, verdict_filename""")


async def _prune_rows(session, source_id, issue_id, ids: list[str]) -> list[tuple[str, str | None]]:
    """Delete the issue's rows with these external_ids; returns (external_id, verdict_filename) of those deleted."""
    rows = (await session.execute(_PRUNE, {"source": source_id, "issue": issue_id, "ids": list(ids)})).all()
    return [(r[0], r[1]) for r in rows]


async def _has_stale_rows(session, m: IssueManifest, issue_id) -> bool:
    """Whether the issue has stored articles its manifest no longer names (read only)."""
    source_id = (await session.execute(select(Source.id).where(Source.short_name == m.source))).scalar_one_or_none()
    return bool(_stale_ids(await _stored_docs(session, source_id), issue_id, assign_external_ids(m)))


async def _mark_issue_failed(session, manifest_rel: str, error: str) -> None:
    await session.execute(text(
        "UPDATE journal_issues SET status = 'failed', error = :error, updated_at = now() WHERE manifest_path = :p"),
        {"error": error, "p": manifest_rel})


async def _record_reimport_error(session, manifest_rel: str, error: str | None) -> None:
    """A failed import of an `imported` issue (Task 22f, 23a): the rows of the earlier import are still there, so the
    issue keeps its status; only `error` is recorded. The stored `manifest_sha256` stays what it was, on purpose: it is
    the version of the manifest the rows belong to. Storing the hash of the manifest as it is now would say that an
    edit the import could not carry out had been processed, and the next plain import would call the issue `unchanged`
    (the edit would land only with --reimport). Kept, it differs from the saved manifest (which now carries the error
    and the user's edit), so the next plain import tries again."""
    await session.execute(text(
        "UPDATE journal_issues SET error = :error, updated_at = now() WHERE manifest_path = :p"),
        {"error": error, "p": manifest_rel})


def _files_of(cfg, vf: str) -> tuple[Path, Path]:
    """The derived files of a stored row: its markdown and its article PDF."""
    return cfg.markdown_path(vf), cfg.pdf_path(vf)


def _blocked(m: IssueManifest) -> str | None:
    """Why an approved/imported manifest must not be imported as it stands (Task 18, R37): its source file changed
    after approval (sha256 alone cannot tell: pdf_pages may be stale), discovery found two container files for the
    issue, or its issue PDF was never analysed."""
    err = m.error or ""
    if _CHANGED_AFTER_APPROVAL in err:
        return "source_changed"
    if _DISCOVERY_ERROR in err:
        return "discovery_error"
    if m.file and m.pages is None:
        return "not_analysed"
    return None


def _issue_pdf_name(m: IssueManifest) -> str:
    parts = ("x" if v is None else str(v).replace("/", "-") for v in (m.year, m.volume, m.issue))
    return "_".join(parts) + ".pdf"


def _import_note(a: ArticleEntry, note: str) -> None:
    if note not in a.analysis_notes:
        a.analysis_notes.append(note)


def _dry(dry_run: bool, eid: str, what: str) -> None:
    if dry_run:
        print(f"  {eid:>26}  {what}")


def _pages_text(a: ArticleEntry, pdf: bytes, read_bytes) -> str:
    """For the dry run: how many pages the article's PDF has and, when it is cut out of an own file that holds
    more (a shared file), which pages of that file."""
    n = page_count(pdf)
    text = f"{n} {'síða' if n % 10 == 1 and n % 100 != 11 else 'síður'}"
    if a.source_file and a.pdf_pages:
        total = page_count(read_bytes(a.source_file))
        if n < total:
            first, last = a.pdf_pages[0] + 1, min(a.pdf_pages[1], total - 1) + 1
            text += f"  (úr {Path(a.source_file).name}, " + (f"pdf-síða {first}" if first == last else f"pdf-síður {first}–{last}") + f" af {total})"
    return text


def _articles(n: int) -> str:
    """'1 grein flutt' / '2 greinar fluttar' (21 takes the singular, 11 does not)."""
    return f"{n} grein flutt" if n % 10 == 1 and n % 100 != 11 else f"{n} greinar fluttar"


def _import_summary(counts: dict[str, int], orphans: list[str]) -> str | None:
    """One line for the issue's `error` saying what of the manifest did not reach the database: articles that
    failed, had no text or collided (an article without pdf_pages was approved as such and is not counted, unless
    nothing at all was imported), and stored articles the manifest no longer has. None when there is nothing to say."""
    missing = [(label, counts[k]) for k, label in _NOT_IMPORTED if counts.get(k)]
    parts: list[str] = []
    if not counts.get("imported"):
        missing += [("engar síður", counts["skipped_no_pages"])] if counts.get("skipped_no_pages") else []
        detail = ", ".join(f"{label} {n}" for label, n in missing) or "heftið hefur engar greinar"
        parts.append(f"engin grein var flutt inn ({detail})")
    elif missing:
        n = sum(k for _, k in missing)
        parts.append(f"{_articles(n)} ekki inn ({', '.join(f'{label} {k}' for label, k in missing)})")
    if orphans:
        more = f" o.fl. ({len(orphans)} alls)" if len(orphans) > _MAX_LISTED_ORPHANS else ""
        parts.append("úreltar færslur: " + ", ".join(orphans[:_MAX_LISTED_ORPHANS]) + more)
    if not parts:
        return None
    return _IMPORT_ERROR + "; ".join(parts) + (" — sjá analysis_notes" if missing else "")


def _with_import_error(error: str | None, mine: str | None) -> str | None:
    """`error` without an earlier import's part, plus `mine`; another stage's error stays where it was."""
    kept = [part for part in (error or "").split(_ERROR_SEP) if part and not part.startswith(_IMPORT_ERROR)]
    return _join_errors(*kept, mine)


class _FileLog:
    """The files one manifest's import writes, so that a failed commit can put the disk back as it was: the
    previous content of each path (None: it did not exist) is remembered before its first write."""
    def __init__(self) -> None:
        self._before: dict[Path, bytes | None] = {}
        self._removals: list[Path] = []

    def track(self, path: Path) -> Path:
        if path not in self._before:
            self._before[path] = path.read_bytes() if path.exists() else None
        return path

    def write(self, path: Path, data: bytes) -> None:
        self.track(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def defer_removal(self, path: Path) -> None:
        """Delete `path` once the caller's commit went through (apply_removals); undo() forgets it. This is for the
        files of pruned rows: while the commit is open the rows may come back, and so must their files."""
        if path not in self._removals:
            self._removals.append(path)

    def apply_removals(self) -> int:
        """Delete the deferred files that exist; returns how many went. Never inside the archive, and a file that
        cannot be deleted is logged, not raised (the commit is behind us)."""
        n = 0
        for path in self._removals:
            try:
                if path.resolve().is_relative_to(DROP.resolve()):
                    log.error("refusing to delete %s: inside the archive", path)
                elif path.exists():
                    path.unlink()
                    n += 1
            except Exception:  # noqa: BLE001 — the commit is behind us; a file that stays is harmless
                log.exception("could not delete %s", path)
        self._removals.clear()
        return n

    def undo(self) -> None:
        self._removals.clear()
        for path, old in self._before.items():
            try:
                if old is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(old)
            except OSError:
                log.exception("could not undo %s", path)
        self._before.clear()


class _StopRun(Exception):
    """A failure of the environment, not of one issue: the run stops instead of marking every remaining issue
    failed (which would rewrite hundreds of git-tracked manifests). The message is the user's (Icelandic)."""


# Lost connections: SQLAlchemy's own classes, an invalidated connection, asyncpg's connection errors (also when
# SQLAlchemy wraps them), sockets. Anything else is judged by asking the database whether it still answers.
_CONNECTION_ERRORS = (sa_exc.OperationalError, sa_exc.InterfaceError, sa_exc.DisconnectionError,
                      asyncpg.PostgresConnectionError, asyncpg.InterfaceError, ConnectionError, TimeoutError)


def _is_connection_error(exc: BaseException) -> bool:
    seen: set[int] = set()
    e: BaseException | None = exc
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        if isinstance(e, _CONNECTION_ERRORS) or (isinstance(e, sa_exc.DBAPIError) and e.connection_invalidated):
            return True
        e = e.__cause__ or getattr(e, "orig", None)
    return False


async def _session_alive(session) -> bool:
    """Roll the session back and ask the database for SELECT 1; False when either fails (the connection is gone)."""
    try:
        await session.rollback()
        await session.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        log.exception("the database does not answer")
        return False


def _check_raw_dir(raw_dir: Path) -> None:
    """RAW must never lie inside the read-only archive: refuse the whole run, not one issue after another."""
    if Path(raw_dir).resolve().is_relative_to(DROP.resolve()):
        raise SystemExit(f"import: RAW ({raw_dir}) er inni í safninu {DROP}, sem aldrei má skrifa í; lagaðu DATA_DIR.")


async def import_manifest(path: Path, *, session, read_bytes, raw_dir: Path, dry_run: bool, workers: int,
                          pool=None, files: _FileLog | None = None, prune: bool = False,
                          reimport: bool = False) -> dict[str, int]:
    """Import one issue (spec §7.1 step 4) and return its counts. Only an `approved` manifest, or an `imported` one
    whose file differs from the version journal_issues recorded, is imported; a manifest the guards block
    (_blocked), or whose issue PDF no longer has the sha256 it was analysed with, is not.

    Every article gets its id from assign_external_ids (R28). An article without pdf_pages, whose id another
    issue already holds (never overwritten), whose PDF cannot be cut or read, or that yields no text (R19) is not
    imported and says why in its analysis_notes. The others are extracted (`workers` processes, or `pool`), built
    by the journal extractor, validated and upserted under a verdict_filename that never reuses another issue's
    name. The manifest is then saved as `imported` and journal_issues updated, with `error` saying what did not
    reach the database (_import_summary); when no article could be imported the issue is `failed` instead.

    Files come last, after all database work of the manifest: markdown and the article PDF of every imported
    article, and the issue PDF at raw/{source}/hefti/{year}_{volume}_{issue}.pdf. A failure before that leaves the
    disk untouched; what is written is recorded in `files`, so the caller can undo it when its commit fails.

    With `prune`, and when at least one article was imported, the issue's stored rows whose external_id the manifest
    no longer names are deleted in the same transaction (after the upserts), counted `pruned`; their files are
    deferred in `files` (`apply_removals` after the commit), except those a remaining row also uses. An article that
    failed, had no text or collided this time is still named by the manifest, so its row stays. Without `prune`
    such rows are only counted `orphaned` and listed in the issue's `error`. An issue the manifest of which is
    unchanged since its import is skipped as before, unless `prune` is set and the issue has such rows.

    With `reimport` (Task 22f) an `imported` issue is imported again even when its manifest is unchanged, exactly as an
    approved one: the same upsert by external_id (a row whose id is unchanged is updated in place, its passage_hash
    set to NULL so that backfill_passages.py rebuilds its passages) and the same files overwritten. Any `imported`
    issue imported again is counted `reimported`. Issues in review/pending/failed are never touched. An `imported`
    issue of which nothing could be imported this time (or whose import raised, see _import_one) keeps its status,
    since the rows of its earlier import are still there: `error` says what went wrong (`reimport_failed`). It keeps its
    stored manifest_sha256 too (Task 23a): the manifest was edited, the edit did not land, so the next plain import
    must see a manifest that differs and try again.

    When an upsert gives a stored row a different verdict_filename (a corrected page_end, a new title), the row's old
    markdown and article PDF are deferred in `files` like a pruned row's (`apply_removals` after the commit; never
    inside the archive), unless another row has that name afterwards, and the articles are counted `renamed`; with
    `dry_run` the old files that exist are printed ("yrði eytt") and nothing is removed.

    With `dry_run` the database is only read and nothing is written anywhere; every article is printed."""
    m = load_manifest(path)
    if m.status not in _FROZEN:
        return {"not_approved": 1}
    blocked = _blocked(m)
    if blocked:
        log.warning("%s not imported (%s): %s", path.name, blocked, m.error or "issue PDF not analysed")
        return {blocked: 1}
    manifest_rel = _manifest_rel(path)
    row = await _issue_row(session, manifest_rel)
    if row is None and not dry_run:
        await upsert_issue_row(session, m, manifest_rel)
        row = await _issue_row(session, manifest_rel)
    issue_id = uuid.UUID(str(row[0])) if row else None
    if m.status == "imported" and row is not None and row[1] == manifest_sha256(path) and not reimport:
        if not (prune and await _has_stale_rows(session, m, issue_id)):
            return {"unchanged": 1}
    again = m.status == "imported"
    if not dry_run:
        _check_raw_dir(raw_dir)
    files = files if files is not None else _FileLog()

    cfg = get_config(m.source)
    cache: dict[str, bytes] = {}

    def read(rel: str) -> bytes:
        if rel not in cache:
            cache[rel] = read_bytes(rel)
        return cache[rel]

    issue_bytes = read(m.file) if m.file else None
    issue_sha = sha256_bytes(issue_bytes) if issue_bytes is not None else None
    if issue_sha is not None and m.sha256 and issue_sha != m.sha256:
        log.warning("%s not imported: %s changed since it was analysed (run discover and analyze)", path.name, m.file)
        return {"source_changed": 1}

    if dry_run:
        print(f"{m.label}  ({path.name})")
        source_id = (await session.execute(select(Source.id).where(Source.short_name == m.source))).scalar_one_or_none()
    else:
        source_id = await _source_id(session, m.source)
    stored = await _stored_docs(session, source_id)
    owner = {eid: jid for eid, jid, _ in stored}
    taken = {vf for _, jid, vf in stored if vf and (issue_id is None or jid != issue_id)}

    counts: dict[str, int] = defaultdict(int)
    ids = assign_external_ids(m)
    by_id = dict(zip(ids, m.articles))
    for a in m.articles:
        a.analysis_notes = [n for n in a.analysis_notes if not _IMPORT_NOTE.fullmatch(n)]
    stale = _stale_ids(stored, issue_id, by_id)

    pdfs: dict[str, bytes] = {}
    stored_docs: list[tuple[Document, str]] = []
    named: dict[str, str] = {}                    # external_id -> the verdict_filename of every article imported now
    jobs: list[tuple] = []
    for eid, a in by_id.items():
        if a.pdf_pages is None:
            counts["skipped_no_pages"] += 1
            _import_note(a, _NOTE_NO_PAGES)
            _dry(dry_run, eid, "sleppt: engar PDF-síður")
            continue
        if eid in owner and owner[eid] != issue_id:
            counts["collision"] += 1
            _import_note(a, _NOTE_COLLISION.format(eid))
            _dry(dry_run, eid, "sleppt: auðkennið er þegar til í öðru hefti")
            continue
        try:
            pdfs[eid] = article_pdf_bytes(m, a, read)
        except Exception as exc:  # noqa: BLE001 — a bad page range or an unreadable article file fails that article
            counts["failed"] += 1
            _import_note(a, _NOTE_FAILED + _error_text(exc))
            _dry(dry_run, eid, f"mistókst: {_error_text(exc)}")
            continue
        jobs.append((eid, pdfs[eid], m.source, cfg.has_footnotes, printed_start_of(a)))     # R17, R46

    with _extracting(jobs, workers, pool) as results:
        for eid, extracted, err in results:
            a = by_id[eid]
            if err:
                counts["failed"] += 1
                _import_note(a, _NOTE_FAILED + err)
                _dry(dry_run, eid, f"mistókst: {err}")
                continue
            ex = ExtractedText(extracted["body"], extracted["page_offsets"], extracted["ocr_source"],
                               extracted["bin_ratio"], page_note=extracted.get("page_note"))
            if ex.ocr_source == "none" or not ex.body.strip():                                     # R19
                counts["no_text"] += 1
                _import_note(a, _NOTE_NO_TEXT)
                _dry(dry_run, eid, "sleppt: enginn texti — þarf OCR")
                continue
            fields = Extractor(cfg).extract(build_article_raw(m, a, ex, a.source_file or m.file))
            doc = Document(id=uuid.uuid4(), source_id=source_id, external_id=eid, url=None,
                           journal_issue_id=issue_id, **fields)
            doc.validation_errors = validate(doc, cfg) or None
            vf = unique_verdict_filename(verdict_filename(doc, cfg), taken)
            taken.add(vf)
            doc.verdict_filename = vf
            named[eid] = vf
            counts["imported"] += 1
            if ex.page_note:
                log.info("%s %s: %s", path.name, eid, ex.page_note)
            if dry_run:
                errors = "; ".join(f"{e['field']}: {e['message']}" for e in doc.validation_errors or [])
                _dry(True, eid, f"{vf}  {len(doc.body_text or '')} stafir  {_pages_text(a, pdfs[eid], read)}"
                     + (f"  {ex.page_note}" if ex.page_note else "") + (f"  villur: {errors}" if errors else ""))
                continue
            await _upsert_doc(session, doc)
            stored_docs.append((doc, vf))

    pruned: list[tuple[str, str | None]] = []
    if prune and stale and counts["imported"]:
        if dry_run:
            vf_of = {eid: vf for eid, _, vf in stored}
            pruned = [(eid, vf_of[eid]) for eid in stale]
            for eid, vf in pruned:
                _dry(True, eid, f"yrði eytt: úrelt grein, ekki lengur í skránni ({vf})")
        else:
            pruned = await _prune_rows(session, source_id, issue_id, stale)
            for eid, vf in pruned:
                log.info("pruned %s (%s) of %s: the manifest no longer has it", eid, vf, path.name)
    gone = {eid for eid, _ in pruned}
    orphans = [eid for eid in stale if eid not in gone]
    if gone:
        counts["pruned"] = len(gone)
    if orphans:
        counts["orphaned"] = len(orphans)
        log.warning("%s: stored articles no longer in the manifest (left in place): %s", path.name, ", ".join(orphans))
        _dry(dry_run, "", f"eldri greinar heftisins sem eru ekki lengur í skránni: {', '.join(orphans)}")

    # Files no row has once this import commits: a pruned row's, and those of a row whose verdict_filename changed now
    # (a corrected page_end, a new title). A name that a row still has afterwards, under its new name or another's,
    # stays; so does a name that is no plain file name.
    old_name = {eid: vf for eid, _, vf in stored}
    renamed = {eid: old_name[eid] for eid, vf in named.items() if old_name.get(eid) and old_name[eid] != vf}
    in_use = {named.get(eid, vf) for eid, _, vf in stored if vf and eid not in gone} | set(named.values())
    leaving = {vf: eid for eid, vf in (*pruned, *renamed.items()) if vf and vf not in in_use and vf == Path(vf).name}
    if renamed:
        counts["renamed"] = len(renamed)
    if dry_run:
        for eid, vf in renamed.items():
            there = [q.name for q in _files_of(cfg, vf) if q.exists()] if vf in leaving else []
            if there:
                _dry(True, eid, f"gamla skráin yrði eytt: {', '.join(there)} (nýtt nafn: {named[eid]})")

    summary = _import_summary(counts, orphans)
    if again:
        counts["reimported" if counts["imported"] else "reimport_failed"] = 1
    if dry_run:
        if summary:
            outcome = "" if counts["imported"] else (
                "  (heftið héldi stöðunni imported)" if again else "  (heftið fengi stöðuna failed)")
            print(f"  → {summary}{outcome}")
        return dict(counts)

    if summary:
        log.warning("%s: %s", path.name, summary)
    m.error = _with_import_error(m.error, summary)
    if not counts["imported"] and again:         # the earlier import's rows stay: so does the status
        save_manifest(m, path)
        await _record_reimport_error(session, manifest_rel, m.error)
        return dict(counts)
    if not counts["imported"]:
        m.status = "failed"
        save_manifest(m, path)
        await _mark_issue_failed(session, manifest_rel, m.error)
        counts["failed_issue"] += 1
        return dict(counts)

    issue_dest = issue_rel = None
    if issue_bytes is not None:
        issue_dest = Path(raw_dir) / m.source / "hefti" / _issue_pdf_name(m)
        issue_rel = str(issue_dest.relative_to(Path(raw_dir).parent))  # relative to DATA_DIR (raw_dir = DATA_DIR/raw)
        m.sha256 = issue_sha
    m.status = "imported"
    save_manifest(m, path)
    await session.execute(text("""
        UPDATE journal_issues SET status = 'imported', manifest_sha256 = :sha, file_path = coalesce(:fp, file_path),
               sha256 = coalesce(:s, sha256), page_count = coalesce(:pc, page_count), error = :error,
               updated_at = now()
        WHERE id = :id"""), {"sha": manifest_sha256(path), "fp": issue_rel, "s": m.sha256, "pc": m.pages,
                             "error": m.error, "id": issue_id})

    # Files last (see the docstring): every row and the issue's UPDATE went through. The caller commits, and undoes
    # these writes through `files` if that fails.
    for doc, vf in stored_docs:
        files.track(cfg.markdown_path(vf))
        write_markdown(doc, cfg, vf=vf)
        files.write(cfg.pdf_path(vf), pdfs[doc.external_id])
    if issue_dest is not None and not (issue_dest.exists() and sha256_bytes(issue_dest.read_bytes()) == issue_sha):
        files.write(issue_dest, issue_bytes)
    # The files of the pruned and the renamed rows go once the commit has (see `leaving` above).
    for vf, eid in leaving.items():
        if eid in renamed:
            log.info("%s %s: file name %s → %s; the old files go after the commit", path.name, eid, vf, named[eid])
        for q in _files_of(cfg, vf):
            files.defer_removal(q)
    return dict(counts)


def _read_drop(rel: str) -> bytes:
    """A file of the archive (read only). An OSError fails the issue, not the run (unlike discover's reader)."""
    return (DROP / rel).read_bytes()


async def _import_one(path: Path, session, args, pool) -> tuple[dict[str, int], bool]:
    """import_manifest for one manifest, committed on its own; returns (counts, whether it raised).

    When it raises, the files it wrote are undone and the session is rolled back. A lost database connection (by
    the exception's type, or because the database no longer answers SELECT 1) raises _StopRun: the issue is not to
    blame and is left as it was. Any other error is the issue's: it gets status `failed` with the error (manifest
    and journal_issues, committed) and the run goes on, except an issue that was `imported` before this run: it keeps
    that status and only `error` is recorded (`reimport_failed`, Task 22f), with the stored manifest_sha256 left as it was
    (Task 23a: an edited manifest that failed to import is tried again by the next plain import). A manifest that does
    not load is left as it is (it is the user's hand edit).

    The one exception to the undo: a connection lost *during the commit* may have lost only the reply. The server
    may have committed, and then the files are the right ones for the rows (undoing them would leave rows without
    files, which the next run would call `unchanged`). If it did not commit, the next run imports the issue again
    over the same paths, because the stored manifest_sha256 still differs. So those files stay, and so do the files
    of pruned rows (they are deleted only after a commit that is known to have succeeded)."""
    files = _FileLog()
    committing = False
    try:
        imported_before = load_manifest(path).status == "imported"
    except (ValueError, OSError):
        imported_before = False
    try:
        counts = await import_manifest(path, session=session, read_bytes=_read_drop,
                                       raw_dir=Path(sources_config.RAW_DIR), dry_run=args.dry_run,
                                       workers=args.workers, pool=pool, files=files,
                                       prune=getattr(args, "prune", False),
                                       reimport=getattr(args, "reimport", False))
        if not args.dry_run:
            committing = True
            await session.commit()
            committing = False
            files.apply_removals()
        return counts, False
    except Exception as exc:  # noqa: BLE001 — one bad issue must not stop the run
        log.exception("import failed for %s", path.name)
        lost = _is_connection_error(exc) or not await _session_alive(session)
        if committing and lost:
            log.warning("%s: the outcome of the commit is unknown; its files are kept", path.name)
        else:
            files.undo()
        if lost:
            raise _StopRun(f"tengingin við gagnagrunninn brást við {path.name} ({_error_text(exc)}); "
                           "heftið var ekki merkt failed og ekkert hefti á eftir því var reynt") from exc
        if args.dry_run:
            print(f"  ✗ {path.name}: {_error_text(exc)}")
            return ({"reimport_failed": 1} if imported_before else {"failed_issue": 1}), True
        try:
            m = load_manifest(path)
        except (ValueError, OSError):
            return {"failed_issue": 1}, True
        # An issue imported before this run keeps its status: the earlier rows are still there (Task 22f). The status
        # on disk may be this run's own `imported`, written before the commit that failed, so the one read first counts.
        again = imported_before
        m.error = _with_import_error(m.error, _error_text(exc, _IMPORT_ERROR))
        m.status = "imported" if again else "failed"
        save_manifest(m, path)
        try:
            if again:
                await _record_reimport_error(session, _manifest_rel(path), m.error)
            else:
                await _mark_issue_failed(session, _manifest_rel(path), m.error)
            await session.commit()
        except Exception:  # noqa: BLE001
            await session.rollback()
            log.exception("journal_issues update failed for %s", path.name)
        return ({"reimport_failed": 1} if again else {"failed_issue": 1}), True


async def cmd_import(args) -> None:
    paths = _select_manifests(args)
    if not paths:
        print("Engar yfirlitsskrár fundust (keyrðu discover og analyze fyrst eða rýmkaðu --source/--issue).")
        return
    if not args.dry_run:
        _check_raw_dir(Path(sources_config.RAW_DIR))
    totals: dict[str, int] = defaultdict(int)
    done, in_a_row, stop = 0, 0, None
    with contextlib.ExitStack() as stack:
        # One pool for the whole run, started before the database connects (nothing to inherit when forking).
        pool = stack.enter_context(Pool(processes=args.workers)) if args.workers > 1 else None
        await _db.init_db(create_tables=False)
        try:
            async with _db.AsyncSessionLocal() as session:
                for path in paths:
                    try:
                        counts, raised = await _import_one(path, session, args, pool)
                    except _StopRun as e:
                        stop = str(e)
                        break
                    done += 1
                    log.info("%s → %s", path.stem, counts)
                    for k, n in counts.items():
                        totals[k] += n
                    in_a_row = in_a_row + 1 if raised else 0
                    if in_a_row >= _MAX_FAILURES_IN_A_ROW:
                        stop = (f"{in_a_row} hefti í röð mistókust — líklega villa í umhverfinu (diskur, RAW, "
                                "gagnagrunnur) frekar en í heftunum sjálfum; sjá loggskrána")
                        break
        finally:
            await _db.dispose_db()
    print(f"import: {done} hefti" if done == len(paths) else f"import: {done} af {len(paths)} hefti")
    for k, n in sorted(totals.items()):
        print(f"  {_COUNT_LABELS.get(k, k):30s} {n:5d}")
    if args.dry_run:
        print("Þurrkeyrsla: ekkert var skrifað (engin skjöl, engar skrár, ekkert í gagnagrunn).")
    elif totals.get("imported") or totals.get("pruned"):
        print("Næst: keyrðu scripts/backfill_passages.py og scripts/build_citations.py.")
    if totals.get("orphaned") and not getattr(args, "prune", False):
        print("Ábending: eldri færslur sem skráin nefnir ekki lengur eru enn í gagnagrunninum; "
              "import --prune eyðir þeim (og skrám þeirra).")
    if stop:
        raise SystemExit(f"import stöðvað: {stop}")


# --- hefti (Task 23f) -------------------------------------------------------------------------------------------------

_HEFTI_LABELS = {"assigned": "greinar fengu hefti", "unset": "greinar án heftis (athugasemd)",
                 "disagree": "skráð hefti stangast á við síðurnar (óbreytt)", "changed": "yfirlitsskrár vistaðar",
                 "doubt": "bindi þar sem heftaskil eru ótraust", "source_changed": "frumskjal breytt (sleppt)",
                 "failed": "bindi mistókust"}


def _printed_start(m: IssueManifest, first: int) -> int | None:
    """The first printed page number at or after PDF page `first`, from the page map's segments (None: unknown)."""
    for lo, hi, off in sorted(m.page_map.get("segments") or []):
        if hi >= first:
            return max(lo, first) + off
    return None


def _where(a: ArticleEntry) -> str:
    return f"bls. {a.page_start}" if a.page_start is not None else f"„{a.title or ''}“"


def hefti_manifest(path: Path, *, read_bytes: Callable[[str], bytes], dry_run: bool) -> dict[str, int]:
    """`hefti` for one bound volume: assign_hefti over its PDF (its window for a part of a split file) and, unless
    `dry_run`, save the manifest when an article changed. Only `issue`, its provenance and the hefti note can change.
    A volume whose PDF is no longer the one it was analysed with is left alone. Prints what it found and did."""
    m = load_manifest(path)
    pdf = read_bytes(m.file)
    print(f"{m.label}  ({path.name})")
    if m.sha256 and sha256_bytes(pdf) != m.sha256:
        log.warning("%s: %s changed since it was analysed; left alone", path.name, m.file)
        print("  sleppt: frumskjalið breyttist eftir greiningu (keyrðu discover og analyze)")
        return {"source_changed": 1}
    before = [copy.deepcopy(a) for a in m.articles]
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        first, last = m.pdf_window or (0, doc.page_count - 1)
        r = assign_hefti(m, PageReader(doc), first, last)
    for h in r.hefti:
        name = f"{h.issue}. hefti" + (f" {h.foreign} (bundið inn, ekki bindisins)" if h.foreign else "")
        printed = _printed_start(m, h.first)
        print(f"  {name}: pdf-síða {h.first + 1}" + (f" (bls. {printed})" if printed is not None else ""))
    if r.doubt:
        print(f"  heftaskil ótraust: {r.doubt} — engin grein fær hefti")
    for k in r.assigned:
        a = m.articles[k]
        print(f"  {_where(a)} → {a.issue}. hefti  {a.title or ''}")
    for k, why in r.unset.items():
        print(f"  ekki skráð: {_where(m.articles[k])}  {m.articles[k].title or ''} — {why}")
    for k, was, pages in r.disagree:
        a = m.articles[k]
        print(f"  ósamræmi: {_where(a)} skráð {was}. hefti ({a.provenance.get('issue') or 'án uppruna'}), "
              f"síðurnar segja {pages}. hefti — skráningin stendur  {a.title or ''}")
    counts = {"assigned": len(r.assigned), "unset": len(r.unset), "disagree": len(r.disagree), "doubt": int(bool(r.doubt))}
    if m.articles != before and not dry_run:
        save_manifest(m, path)
        counts["changed"] = 1
    return {k: n for k, n in counts.items() if n}


def _bound(path: Path) -> bool:
    try:
        return bound_volume(load_manifest(path))
    except (ValueError, OSError):
        return False


async def cmd_hefti(args) -> None:
    paths = [p for p in _select_manifests(argparse.Namespace(**{**vars(args), "limit": None})) if _bound(p)]
    paths = paths[:args.limit] if args.limit else paths
    if not paths:
        print("Engin bundin bindi fundust (rýmkaðu --source/--issue).")
        return
    totals: dict[str, int] = defaultdict(int)
    for path in paths:
        try:
            counts = hefti_manifest(path, read_bytes=_read_drop, dry_run=args.dry_run)
        except Exception as exc:  # noqa: BLE001 — one bad volume must not stop the run
            log.exception("hefti failed for %s", path.name)
            print(f"  ✗ {path.name}: {_error_text(exc)}")
            counts = {"failed": 1}
        for k, n in counts.items():
            totals[k] += n
    print(f"hefti: {len(paths)} bindi")
    for k, n in sorted(totals.items()):
        print(f"  {_HEFTI_LABELS.get(k, k):45s} {n:5d}")
    if args.dry_run:
        print("Þurrkeyrsla: engin yfirlitsskrá var vistuð (og ekkert skrifað í gagnagrunn).")
    elif totals.get("changed"):
        print("Auðkenni (external_id) og skráarnöfn greinanna sem fengu hefti breytast við næsta innflutning; "
              "eldri raðir þeirra verða úreltar (import --prune eyðir þeim).")


def _setup_logging(command: str) -> list[logging.Handler]:
    """stderr plus Lausnir_Data/logs/timarit/{command}_{date}.log (spec 7.4); the file is best effort.
    Returns the handlers it attached to the root logger."""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(LOG_DIR / f"{command}_{datetime.date.today():%Y-%m-%d}.log", encoding="utf-8"))
    except OSError as e:
        print(f"Aðvörun: ekki tókst að opna logg í {LOG_DIR}: {e}", file=sys.stderr)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in handlers:
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        root.addHandler(h)
    return handlers


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["discover", "analyze", "import", "status", "hefti"])
    ap.add_argument("--source")
    ap.add_argument("--issue", help="ÁR[_ÁRG[_HEFTI]] (x = vantar)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--prune", action="store_true",
                    help="import: eyða greinum heftisins sem yfirlitsskráin nefnir ekki lengur (og skrám þeirra)")
    ap.add_argument("--reimport", action="store_true",
                    help="import: flytja aftur inn hefti sem þegar eru innflutt, þótt yfirlitsskráin sé óbreytt")
    args = ap.parse_args(argv)
    for flag in ("prune", "reimport"):
        if getattr(args, flag) and args.command != "import":
            print(f"Aðvörun: --{flag} gildir aðeins um import; það er hunsað við {args.command}.", file=sys.stderr)
            setattr(args, flag, False)
    _setup_logging(args.command)
    cmd = {"discover": cmd_discover, "status": cmd_status, "analyze": cmd_analyze, "import": cmd_import,
           "hefti": cmd_hefti}[args.command]
    asyncio.run(cmd(args))


if __name__ == "__main__":
    main()
