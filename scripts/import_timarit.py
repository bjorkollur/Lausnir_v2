"""Journals: discover → analyze → (review) → import  (spec 2026-10-03 §7).

    uv run --env-file .env python scripts/import_timarit.py discover
    uv run --env-file .env python scripts/import_timarit.py analyze --source timarit_logfraedinga
    uv run --env-file .env python scripts/import_timarit.py import --dry-run
    uv run --env-file .env python scripts/import_timarit.py status

The archive in TIMARIT_DROPFOLDER_DIR is read-only; manifests live in
data/timarit/manifests (git); state lives in journal_issues.

`discover --dry-run` writes nothing at all (no manifests, no _unmatched.yaml, no database access); it only
prints what a real discover would do. (Every run also logs to Lausnir_Data/logs/timarit/.)

`analyze` runs the per-issue analysis over the manifests on disk (sequentially: leitir.is is throttled anyway;
`--workers` belongs to `import`), merges it with the user's edits, saves the manifest and updates journal_issues,
committing after every manifest. It never touches an approved/imported issue. An issue that fails gets
status `failed` and the exception's first line in `error`; the run goes on. `analyze --dry-run` saves nothing and
opens no database session (it still reads leitir.is and Claude, through their caches under Lausnir_Data/cache).

Discovery drops two kinds of duplicate: byte-identical files (`duplicate_of`) and files whose text lies inside
a larger volume file of the same year (`contained_in`, spec 5.6); both are listed in data/timarit/_unmatched.yaml.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime
import logging
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable

import fitz
import httpx
import yaml
from sqlalchemy import select, text

sys.path.insert(0, str(Path(__file__).parent.parent))

import engine.database.connection as _db
from engine.config.sources import TIMARIT_DROPFOLDER_DIR, _DATA_DIR, get_config
from engine.database.models import Source
from engine.timarit import config as C
from engine.timarit.analyze import AnalyzeDeps, analyze_issue
from engine.timarit.leitir_articles import search_articles
from engine.timarit.llm_toc import toc_via_llm
from engine.timarit.manifest import (
    IssueManifest, issue_label, load_manifest, manifest_path, merge_preserving_user, save_manifest,
)
from engine.timarit.naming import FileIdentity, identify, issue_key, nfc, reject_reason
from engine.timarit.splitter import sha256_bytes
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
    inside a larger volume file of the same year is recorded as `contained_in` and left out (spec 5.6)."""
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
    unmatched.sort(key=lambda u: u["path"])
    groups: dict[tuple, list] = defaultdict(list)
    for fid in kept:
        groups[issue_key(fid)].append(fid)
    manifests: list[IssueManifest] = []
    for (source, year, volume, issue), fids in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0, kv[0][2] or 0, kv[0][3] or "")):
        cfg = get_config(source)
        containers = [f for f in fids if f.granularity in ("issue", "volume")]
        year_end = next((f.year_end for f in fids if f.year_end), None)
        m = IssueManifest(source=source, year=year, volume=volume, issue=issue,
                          label=issue_label(cfg, year, volume, issue), year_end=year_end,
                          file=containers[0].rel_path if containers else None,
                          sha256=hashes[containers[0].rel_path] if containers else None,
                          files=sorted(f.rel_path for f in fids))
        if len(containers) > 1:
            m.error = _DISCOVERY_ERROR + " fyrir sama hefti: " + "; ".join(c.rel_path for c in containers)
        manifests.append(m)
    return manifests, unmatched


def write_unmatched(items: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(items, allow_unicode=True, sort_keys=False), encoding="utf-8")


def merge_with_existing(path: Path, new: IssueManifest) -> IssueManifest:
    """The manifest to save for a freshly discovered issue. Without a file on disk that is `new` itself.
    With one, the existing manifest stays as it is — articles, status, and whatever analyze filled in (year,
    label, volume, confidence) — and only what discovery owns is refreshed: the file list (`file`, `files`,
    `year_end`) and its own error. Re-running discover therefore never discards an analysis or a review.

    When the source file changed (another container file, other bytes under the same name, another set of
    article files) what was derived from it is stale: sha256 (replaced by the new one), pages and page_map are
    cleared, and an issue that is not approved/imported goes back to `pending` so analyze runs again. An
    approved/imported issue keeps its status (R4) but is flagged in `error`. Analyze clears that flag when
    it re-runs. A discovery error that discovery no longer reports is removed (what analyze appended after it
    stays); errors written by analyze or import are left alone."""
    if not path.exists():
        return new
    old = load_manifest(path)
    changed = (old.file != new.file or set(old.files) != set(new.files)
               or (old.sha256 is not None and old.sha256 != new.sha256))
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
        path = manifest_path(REPO / C.MANIFEST_ROOT, m.source, m.year, m.volume, m.issue)
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


async def cmd_status(args) -> None:
    await _db.init_db(create_tables=False)
    try:
        async with _db.AsyncSessionLocal() as session:
            rows = (await session.execute(text("""
                SELECT s.short_name, ji.status, count(*) FROM journal_issues ji JOIN sources s ON s.id = ji.source_id
                GROUP BY 1, 2 ORDER BY 1, 2"""))).all()
            if not rows:
                print("Engin hefti í gagnagrunni enn (keyrðu discover).")
            for short, status, n in rows:
                print(f"{short:28s} {status:9s} {n:5d}")
            failed = (await session.execute(text(
                "SELECT label, error FROM journal_issues WHERE status = 'failed' ORDER BY label"))).all()
            for label, err in failed:
                print(f"  ✗ {label}: {err}")
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
    --limit. The limit counts manifests analyze will actually run, so approved/imported ones do not use it up."""
    paths = sorted((REPO / C.MANIFEST_ROOT).glob("*/*.yaml"))
    if args.source:
        paths = [p for p in paths if p.parent.name == args.source]
    if args.issue:
        paths = [p for p in paths if p.stem == args.issue or p.stem.startswith(args.issue + "_")]
    if args.limit:
        chosen: list[Path] = []
        for p in paths:
            if len(chosen) == args.limit:
                break
            if not _is_frozen(p):
                chosen.append(p)
        paths = chosen
    return paths


def _real_deps_factory(client: httpx.AsyncClient, llm_client=None):
    """AnalyzeDeps over the real archive, leitir.is (one shared client) and, when `llm_client` is given, Claude.
    Caches live in CACHE_DIR, never in the archive (R3)."""
    xlsx_rows = load_xlsx(C.XLSX_PATH) if C.XLSX_PATH.exists() else []

    async def leitir(q, journal):
        return await search_articles(client, q, cache_dir=C.CACHE_DIR / "leitir", journal_filter=journal)

    async def llm(text_):
        return await toc_via_llm(text_, cache_dir=C.CACHE_DIR / "llm", client=llm_client)

    return lambda: AnalyzeDeps(read_bytes=lambda rel: (DROP / rel).read_bytes(), leitir=leitir,
                               llm_toc=llm if llm_client is not None else None, xlsx_rows=xlsx_rows)


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
    try:
        new = merge_preserving_user(old, await analyze_issue(old, get_config(old.source), deps_factory()))
    except Exception as exc:  # noqa: BLE001 — one bad issue must not stop the run
        log.exception("analyze failed for %s", path.name)
        old.status, old.error = "failed", _error_text(exc)
        new = old
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
            counts = await analyze_manifests(paths, deps_factory=_real_deps_factory(client, llm_client), save=save,
                                             session=session)
    finally:
        if save:
            await _db.dispose_db()
    print(f"analyze: {len(paths)} hefti")
    for status, n in sorted(counts.items()):
        print(f"  {status:9s} {n:5d}")
    if args.dry_run:
        print("Þurrkeyrsla: ekkert var vistað (engar yfirlitsskrár, enginn gagnagrunnur).")


async def cmd_import(args) -> None:
    raise SystemExit("import: sjá Task 20")


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
    ap.add_argument("command", choices=["discover", "analyze", "import", "status"])
    ap.add_argument("--source")
    ap.add_argument("--issue", help="ÁR[_ÁRG[_HEFTI]] (x = vantar)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int)
    args = ap.parse_args(argv)
    _setup_logging(args.command)
    cmd = {"discover": cmd_discover, "status": cmd_status, "analyze": cmd_analyze, "import": cmd_import}[args.command]
    asyncio.run(cmd(args))


if __name__ == "__main__":
    main()
