"""The Claude Code session's side of the offline Claude fallback (Task 22a): the table-of-contents texts that
`import_timarit.py analyze` could not read and, with no ANTHROPIC_API_KEY, left in a queue.

    uv run --env-file .env python scripts/timarit_llm_queue.py list
    uv run --env-file .env python scripts/timarit_llm_queue.py show KEY
    uv run --env-file .env python scripts/timarit_llm_queue.py answer KEY svar.json

`list` shows what waits (key, issue, size); `show` prints the instructions (the same the API would be given) followed
by the text; `answer` checks the JSON array in a file (items {"title", "authors", "page_start", "article_type"}) and
stores it where analyze looks, taking the text off the queue. Run `analyze` on the issue again afterwards. KEY may be
shortened to any unique prefix of at least six characters. An answer that already exists can be replaced.

Reads and writes only under Lausnir_Data/cache/timarit/llm/; never the archive, never the database.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.timarit import config as C  # noqa: E402
from engine.timarit.llm_toc import KEY_PATTERN, PROMPT, pending_entries, write_answer  # noqa: E402

MIN_PREFIX = 6


def _llm_dir() -> Path:
    return C.CACHE_DIR / "llm"


def _error(message: str) -> int:
    print(f"Villa: {message}", file=sys.stderr)
    return 1


def _known_keys(cache_dir: Path) -> list[str]:
    """Keys that can be answered: those in the queue, and those already answered (to replace the answer)."""
    keys = {e["key"] for e in pending_entries(cache_dir)}
    keys |= {p.stem for p in cache_dir.glob("*.json") if KEY_PATTERN.fullmatch(p.stem)}
    return sorted(keys)


def _resolve(cache_dir: Path, given: str) -> str:
    """The full key `given` stands for (itself or a unique prefix); ValueError (Icelandic) when none or several."""
    given = given.strip().lower()
    if len(given) < MIN_PREFIX:
        raise ValueError(f"lykillinn er of stuttur: gefðu upp að minnsta kosti {MIN_PREFIX} stafi")
    hits = [k for k in _known_keys(cache_dir) if k.startswith(given)]
    if not hits:
        raise ValueError(f"lykillinn {given} finnst ekki í biðröðinni (sjá list)")
    if len(hits) > 1:
        raise ValueError(f"fleiri en einn lykill byrjar á {given}: gefðu upp fleiri stafi")
    return hits[0]


def cmd_list(cache_dir: Path) -> int:
    entries = pending_entries(cache_dir)
    if not entries:
        print("Biðröðin er tóm: ekkert efnisyfirlit bíður svars.")
        return 0
    print(f"{len(entries)} efnisyfirlit bíða svars Claude:")
    for e in entries:
        ctx = e.get("context") or {}
        label = ctx.get("label") or ctx.get("manifest") or "(óþekkt hefti)"
        print(f"  {e['key']}  {label}  {e.get('chars', '?')} stafir")
    return 0


def cmd_show(cache_dir: Path, given: str) -> int:
    try:
        key = _resolve(cache_dir, given)
    except ValueError as exc:
        return _error(str(exc))
    entry = next((e for e in pending_entries(cache_dir) if e["key"] == key), None)
    if entry is None:
        return _error(f"lykillinn {key} er þegar svarað; enginn texti er í biðröðinni")
    sys.stdout.write(PROMPT + entry["body_path"].read_text(encoding="utf-8"))
    return 0


def cmd_answer(cache_dir: Path, given: str, file: Path) -> int:
    try:
        key = _resolve(cache_dir, given)
        try:
            entries = json.loads(file.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ValueError(f"skráin {file} verður ekki lesin: {exc.strerror or exc}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"skráin {file} er ekki gilt JSON: {exc}") from exc
        path = write_answer(cache_dir, key, entries)
    except ValueError as exc:
        return _error(str(exc))
    print(f"Svar vistað ({len(entries)} greinar): {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Biðröð efnisyfirlita sem Claude Code-lotan svarar (án API-lykils).")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="sýna biðröðina")
    show = sub.add_parser("show", help="prenta fyrirmælin og texta efnisyfirlitsins")
    show.add_argument("key")
    answer = sub.add_parser("answer", help="vista svar (JSON-fylki) fyrir lykil")
    answer.add_argument("key")
    answer.add_argument("file", type=Path)
    args = ap.parse_args(argv)
    cache_dir = _llm_dir()
    if args.command == "list":
        return cmd_list(cache_dir)
    if args.command == "show":
        return cmd_show(cache_dir, args.key)
    return cmd_answer(cache_dir, args.key, args.file)


if __name__ == "__main__":
    sys.exit(main())
