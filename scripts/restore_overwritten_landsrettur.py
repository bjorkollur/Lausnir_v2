"""Restore Landsréttur rulings that the importer overwrote.

island.is serves every ruling in a case under ONE id (the dómur, a later úrskurður,
the second dómur after Hæstiréttur sent the case back). The importer upserted by
`(source_id, external_id)`, so the last ruling imported replaced the earlier one in
the database — while the earlier one's `.md` and `.pdf` stayed on disk under their
own date-specific `verdict_filename`. island.is no longer serves the older content,
so the files on disk are the only copy.

This script rebuilds a `documents` row for every such ruling from its PDF and `.md`
and inserts it next to the row that overwrote it. Existing rows are never touched:
inserts use ON CONFLICT DO NOTHING and a stem already used as `verdict_filename`
is skipped.

Keys of a restored row (the row that holds the id as island.is serves it now keeps it):
    external_id  g-<uuid>~<YYYY-MM-DD>     (ruling date)
    url          https://island.is/domar/g-<uuid>#<YYYY-MM-DD>

`raw_api_data` is rebuilt, not what the API returned: it carries
`restored_from_disk` with the stem, so nobody mistakes it for RAW.

Run 08.10.2026: of 299 stems on disk and not in the DB only 4 were distinct rulings
(Lrd 241/2025 dómur 9.10.2025, 40/2024 dómur 20.2.2025, 541/2024 úrskurður 11.12.2025,
564/2019 dómur 27.9.2019). The other 295 were stale names of rulings the DB already
holds, which is what the case+date guard below filters out.

Usage:
    uv run python scripts/restore_overwritten_landsrettur.py --selftest 300
    uv run python scripts/restore_overwritten_landsrettur.py --dry-run
    uv run python scripts/restore_overwritten_landsrettur.py
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import logging
import os
import random
import re
import sys
import uuid
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text

import engine.database.connection as _db_conn
from engine.config.sources import get_config
from engine.processors.extractor import Extractor, _parse_icelandic_date
from engine.processors.validator import validate
from engine.database.models import Document

log = logging.getLogger(__name__)

DATA_DIR = Path(os.environ.get("DATA_DIR", "/Volumes/RuleOfLaw/Lausnir_Data"))
MD_DIR = DATA_DIR / "markdown" / "landsrettur"
PDF_DIR = DATA_DIR / "raw" / "landsrettur"

_ID_RE = re.compile(r"/domar/(g-[0-9a-f-]+)")
_SUFFIX_RE = re.compile(r"_\d+$")


def stem_base(stem: str) -> str:
    """`Lrd_510-2021_D_17-02-2023_2` → `Lrd_510-2021_D_17-02-2023` (byte-identical copies)."""
    return _SUFFIX_RE.sub("", stem)


def parse_md(path: Path) -> dict:
    """Read the NORM header back out of a rendered .md (see renderer.to_markdown)."""
    text_ = path.read_text(encoding="utf-8")
    lines = text_.split("\n")
    m = _ID_RE.search(lines[0])
    out: dict = {"island_id": m.group(1) if m else None}
    case = re.match(r"# .*? – (\S+)\s*$", lines[1]) if len(lines) > 1 else None
    out["case_number"] = case.group(1) if case else None
    out["date"] = _parse_icelandic_date(lines[2].lstrip("# ").strip()) if len(lines) > 2 else None

    def block(name: str) -> str | None:
        mm = re.search(rf"^### {name}\n(.*?)(?=\n### |\n## |\Z)", text_, re.S | re.M)
        return mm.group(1).strip("\n") if mm else None

    parties = block("Aðilar")
    if parties:
        parties = parties.replace("**", "")
        sides = re.split(r"\n\s*gegn\s*\n", parties, maxsplit=1)
        out["title"] = "\n gegn \n".join(s.strip() for s in sides)
    else:
        out["title"] = ""
    kw = block("Lykilorð")
    # keywords are joined with ". "; abbreviations inside one keyword ("1. mgr. 95. gr.")
    # are followed by a lower-case word or digit, a new keyword by a capital
    out["keywords"] = (
        [k.strip() for k in re.split(r"\.\s+(?=[A-ZÁÐÉÍÓÚÝÞÆÖ])", kw.strip().rstrip(".")) if k.strip()]
        if kw else []
    )
    out["presentings"] = block("Reifun")
    return out


def build_raw(stem: str, md: dict) -> tuple[dict, dict]:
    """Return (raw for the extractor incl. pdfString, raw to store)."""
    pdf_bytes = (PDF_DIR / f"{stem}.pdf").read_bytes()
    stored = {
        "id": md["island_id"],
        "court": "Landsréttur",
        "title": md["title"],
        "keywords": md["keywords"],
        "caseNumber": md["case_number"],
        "presentings": md["presentings"],
        "verdictDate": f"{md['date'].isoformat()}T00:00:00.000Z" if md["date"] else None,
        "richText": None,
        "resolutionLink": None,
        "pdf_path": f"raw/landsrettur/{stem}.pdf",
        "pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
    }
    return {**stored, "pdfString": base64.b64encode(pdf_bytes).decode()}, stored


def _ws(v: str | None) -> str:
    return re.sub(r"\s+", " ", v or "").strip()


_COMPARE = ("case_number", "document_date", "verdict_type", "plaintiffs", "defendants",
            "keywords", "summary", "body_text", "lower_body_text")


async def selftest(n: int) -> None:
    """Rebuild n rows that ARE in the DB from disk and compare with the stored row."""
    await _db_conn.init_db()
    config = get_config("landsrettur")
    ex = Extractor(config)
    async with _db_conn.AsyncSessionLocal() as s:
        rows = (await s.execute(text("""
            SELECT d.verdict_filename, d.case_number, d.document_date, d.verdict_type, d.plaintiffs,
                   d.defendants, d.keywords, d.summary, d.body_text, d.lower_body_text
            FROM documents d JOIN sources so ON so.id = d.source_id AND so.short_name = 'landsrettur'
            WHERE d.verdict_filename IS NOT NULL"""))).mappings().all()
    random.seed(7)
    sample = [r for r in rows if (MD_DIR / f"{r['verdict_filename']}.md").exists()
              and (PDF_DIR / f"{r['verdict_filename']}.pdf").exists()]
    sample = random.sample(sample, min(n, len(sample)))
    mism: dict[str, int] = defaultdict(int)
    shown = 0
    for r in sample:
        stem = r["verdict_filename"]
        raw, _ = build_raw(stem, parse_md(MD_DIR / f"{stem}.md"))
        got = ex.extract(raw)
        bad = []
        for f in _COMPARE:
            a, b = got.get(f), r[f]
            if f in ("plaintiffs", "defendants"):
                a = [_ws((p or {}).get("name")).replace(" ,", ",") for p in a or []]
                b = [_ws((p or {}).get("name")).replace(" ,", ",") for p in b or []]
            elif f in ("summary", "body_text", "lower_body_text"):
                a, b = _ws(a), _ws(b)      # layout (blank lines, indentation) is not content
            if (a or None) != (b or None):
                bad.append(f)
                mism[f] += 1
        if bad and shown < 5:
            shown += 1
            print("MISMATCH", stem, bad)
    print(f"selftest: {len(sample)} rows; field mismatches: {dict(mism) or 'none'}")


async def main(dry_run: bool) -> None:
    await _db_conn.init_db()
    config = get_config("landsrettur")
    ex = Extractor(config)

    async with _db_conn.AsyncSessionLocal() as s:
        src = (await s.execute(text("SELECT id FROM sources WHERE short_name = 'landsrettur'"))).scalar_one()
        db = (await s.execute(text(
            "SELECT external_id, verdict_filename, case_number, document_date FROM documents "
            "WHERE source_id = :s"), {"s": src})).all()
    db_ext = {r[0] for r in db}
    db_bases = {stem_base(r[1]) for r in db if r[1]}
    # A stem that is only on disk is NOT necessarily a lost ruling: `.md`/`.pdf` names
    # that predate a later verdict-type or date correction (backfill_verdict_type.py)
    # linger next to the renamed ones. The same case on the same date is that ruling.
    db_case_dates = {(r[2], r[3]) for r in db}

    # one canonical stem per base; the `_N` copies must be byte-identical
    groups: dict[str, list[Path]] = defaultdict(list)
    for p in MD_DIR.glob("*.md"):
        groups[stem_base(p.stem)].append(p)

    todo, skipped = [], defaultdict(int)
    for base, paths in sorted(groups.items()):
        if base in db_bases:
            continue
        paths.sort(key=lambda p: (len(p.stem), p.stem))
        canon = paths[0]
        if len({p.read_bytes() for p in paths}) > 1:
            skipped["md copies differ"] += 1
            continue
        if not (PDF_DIR / f"{canon.stem}.pdf").exists():
            skipped["no pdf"] += 1
            continue
        todo.append(canon)

    stats: dict[str, int] = defaultdict(int)
    async with _db_conn.AsyncSessionLocal() as s:
        for md_path in todo:
            stem = md_path.stem
            md = parse_md(md_path)
            if not md["island_id"] or not md["date"]:
                stats["unparsable"] += 1
                continue
            raw, stored = build_raw(stem, md)
            fields = ex.extract(raw)
            if (fields["case_number"], fields["document_date"]) in db_case_dates:
                stats["same case+date already in DB (stale name)"] += 1
                continue
            iso = md["date"].isoformat()
            ext_id = md["island_id"] if md["island_id"] not in db_ext else f"{md['island_id']}~{iso}"
            if ext_id in db_ext:
                stats["external_id taken"] += 1
                continue
            stored["restored_from_disk"] = {"verdict_filename": stem, "reason": "external_id reused by island.is; overwritten by a later ruling"}
            fields["raw_api_data"] = stored
            doc = Document(
                id=uuid.uuid4(), source_id=src, external_id=ext_id,
                url=f"https://island.is/domar/{md['island_id']}" + ("" if ext_id == md["island_id"] else f"#{iso}"),
                verdict_filename=stem, **fields)
            errs = validate(doc, config)
            doc.validation_errors = errs or None
            stats["would_insert" if dry_run else "inserted"] += 1
            if dry_run:
                continue
            await s.execute(text("""
                INSERT INTO documents (id, source_id, external_id, url, raw_api_data, case_number, document_date,
                    court, verdict_type, instance_tier, case_type, plaintiffs, defendants, keywords, summary,
                    body_text, lower_body_text, verdict_filename, validation_errors)
                VALUES (:id, :source_id, :external_id, :url, CAST(:raw AS jsonb), :case_number, :document_date,
                    :court, :verdict_type, :instance_tier, :case_type, CAST(:plaintiffs AS jsonb),
                    CAST(:defendants AS jsonb), CAST(:keywords AS jsonb), :summary, :body_text, :lower_body_text,
                    :verdict_filename, CAST(:errors AS jsonb))
                ON CONFLICT ON CONSTRAINT uq_doc_source_external DO NOTHING"""),
                {"id": doc.id, "source_id": src, "external_id": ext_id, "url": doc.url,
                 "raw": _json(fields["raw_api_data"]), "case_number": doc.case_number,
                 "document_date": doc.document_date, "court": doc.court, "verdict_type": doc.verdict_type,
                 "instance_tier": doc.instance_tier, "case_type": doc.case_type,
                 "plaintiffs": _json(doc.plaintiffs), "defendants": _json(doc.defendants),
                 "keywords": _json(doc.keywords), "summary": doc.summary, "body_text": doc.body_text,
                 "lower_body_text": doc.lower_body_text, "verdict_filename": stem,
                 "errors": _json(doc.validation_errors)})
        if not dry_run:
            await s.commit()

    print("DRY RUN — nothing written\n" if dry_run else "")
    print(f"candidates on disk but not in DB: {len(todo)}   skipped: {dict(skipped) or '-'}")
    for k in sorted(stats):
        print(f"{k:20s} {stats[k]:5d}")


def _json(v) -> str | None:
    import json
    return None if v is None else json.dumps(v, ensure_ascii=False, default=str)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", type=int, metavar="N", help="rebuild N existing rows from disk and compare")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        asyncio.run(selftest(a.selftest))
    else:
        asyncio.run(main(a.dry_run))
