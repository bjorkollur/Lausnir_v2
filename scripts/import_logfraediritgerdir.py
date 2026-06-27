"""Import legal theses (lögfræðiritgerðir) from skemman.is into lausnir_v2.

Source: DSpace (older) on skemman.is. Enumeration via a single slow browse page
(subject = Lögfræði); per-item metadata via OAI-PMH (dim) + the handle's HTML
file table. The full-text ("Heildartexti") PDF is downloaded only when access is
"Opinn"; locked / university-restricted / embargoed items are stored metadata-only
(body_text = NULL), per the locked-thesis decision.

Field mapping (no new DB columns):
    Höfundur     → plaintiffs[0].name      (birth year / parenthetical stripped)
    Leiðbeinandi → plaintiffs[0].lawyer
    Titill       → case_number
    Námsstig     → case_type ("Meistara/Bakkalár/Doktors ritgerð")
    Útdráttur+Athugasemdir → summary
    Efnisorð     → keywords  (all kept, incl. generic "Lögfræði")
    Samþykkt     → document_date  (fallback: browse-row date)
    URI/handle   → external_id + url
    court = "Ritg.",  verdict_type = "Ritgerð",  instance_tier = NULL

PDF / .md filename stem: {M|B|D}_{author-initials}_{title}, ASCII-safe, 40-char cap.

Usage:
    set -a; . ./.env; set +a
    uv run python scripts/import_logfraediritgerdir.py --year 2007 --dry-run
    uv run python scripts/import_logfraediritgerdir.py --year 2007
    uv run python scripts/import_logfraediritgerdir.py --year 2007 --limit 5
"""
from __future__ import annotations

import argparse
import asyncio
import html
import json
import logging
import re
import shutil
import subprocess
import unicodedata
import uuid
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, null as sa_null, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from engine.config.sources import SourceConfig, get_config
import engine.database.connection as _db_conn
from engine.database.connection import init_db
from engine.database.models import Document, Source
from engine.processors.extractor import Extractor, clean_person_name, degree_to_namsstig
from engine.processors.http_utils import get_with_retry, make_client
from engine.processors.renderer import unique_verdict_filename, write_markdown
from engine.processors.validator import validate

log = logging.getLogger(__name__)

BASE = "https://skemman.is"
BROWSE = (
    "https://skemman.is/browse?sort_by=2&order=desc&type=subject"
    "&rpp=5000&autoforward=true&value=L%C3%B6gfr%C3%A6%C3%B0i"
)
BROWSE_CACHE = Path("/tmp/skemman_browse.html")

_CHECKPOINT_DIR = Path("checkpoints")
_CHECKPOINT_FILE = _CHECKPOINT_DIR / "logfraediritgerdir.json"
_REQUEST_DELAY = 0.4  # seconds between items

# University-navigation handles in the browse table that are not theses.
_NAV_HANDLES = {"28", "1991", "1992", "2057", "3515", "6000", "6001", "10473"}

# Full-text file labels (Lýsing column). Everything else (efnisyfirlit, fylgiskjöl…)
# is not treated as the main body.
_FULLTEXT_LABELS = {"heildartexti", "meginmál", "meginmal"}


# ── Filename stem (M/B/D scheme) ───────────────────────────────────────────────

_TRANSLIT = str.maketrans({
    "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ý": "y", "ð": "d",
    "þ": "th", "æ": "ae", "ö": "o", "Á": "A", "É": "E", "Í": "I", "Ó": "O",
    "Ú": "U", "Ý": "Y", "Ð": "D", "Þ": "Th", "Æ": "Ae", "Ö": "O",
})


def transliterate(s: str) -> str:
    s = s.translate(_TRANSLIT)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return s


def author_initials(author: str | None) -> str:
    name = (clean_person_name(author) or "").replace(",", " ")
    parts = [p for p in transliterate(name).split() if p]
    return "".join(p[0].upper() for p in parts)


def thesis_stem(title: str, author: str | None, degree: str | None, max_len: int = 40) -> str:
    """Return the filename stem (no extension): {M|B|D}_{initials}_{title}."""
    _, abbr = degree_to_namsstig(degree)
    abbr = abbr or "X"
    prefix = f"{abbr}_{author_initials(author)}_"
    t = re.sub(r"\([^)]*\)", "", title)        # drop parenthetical content
    t = transliterate(t)
    t = re.sub(r"[^A-Za-z0-9]+", "_", t).strip("_")
    stem = (prefix + t)[:max_len].rstrip("_")
    return stem or (prefix.rstrip("_") or "thesis")


# ── Browse page ────────────────────────────────────────────────────────────────

_BROWSE_ROW_RE = re.compile(
    r'<td headers="t1"[^>]*>([^<]*)</td>'
    r'<td headers="t2"[^>]*><a href="/handle/1946/(\d+)">([^<]*)</a></td>'
    r'<td headers="t3"[^>]*>([^<]*)</td>'
)


def parse_browse(htmltext: str) -> list[tuple[str, str, str, str]]:
    """Return [(date, handle, title, author)] rows, nav handles removed."""
    out = []
    for date_s, hd, title, author in _BROWSE_ROW_RE.findall(htmltext):
        if hd in _NAV_HANDLES:
            continue
        out.append((
            date_s.strip(),
            hd,
            html.unescape(title).strip(),
            html.unescape(author).strip(),
        ))
    return out


def year_of(date_s: str) -> int | None:
    m = re.search(r"(\d{4})\s*$", date_s)
    return int(m.group(1)) if m else None


async def load_browse(client: httpx.AsyncClient) -> str:
    if BROWSE_CACHE.exists():
        return BROWSE_CACHE.read_text(encoding="utf-8", errors="replace")
    log.info("Fetching browse page (slow — up to ~200 s)…")
    resp = await client.get(BROWSE, timeout=200.0)
    resp.raise_for_status()
    BROWSE_CACHE.write_text(resp.text, encoding="utf-8")
    return resp.text


# ── OAI dim metadata ───────────────────────────────────────────────────────────

async def fetch_oai_dim(client: httpx.AsyncClient, handle: str) -> dict:
    url = f"{BASE}/oai/request?verb=GetRecord&identifier=oai:skemman.is:1946/{handle}&metadataPrefix=dim"
    resp = await get_with_retry(client, url)
    x = resp.text
    fields = re.findall(r'<dim:field ([^>]*)>([^<]*)</dim:field>', x)

    def attr(s: str, k: str) -> str:
        m = re.search(rf'{k}="([^"]*)"', s)
        return m.group(1) if m else ""

    d: dict = {"subjects": []}
    for a, val in fields:
        el, q = attr(a, "element"), attr(a, "qualifier")
        val = html.unescape(val).strip()
        key = f"{el}.{q}" if q else el
        if key == "title":
            d.setdefault("title", val)
        elif key == "contributor.author":
            d.setdefault("author", val)
        elif key == "description.advisor":
            d.setdefault("advisor", val)
        elif key == "description.abstract":
            d.setdefault("abstract", val)
        elif key == "description":
            d.setdefault("note", val)
        elif key == "subject":
            d["subjects"].append(val)
        elif key == "date.issued":
            d.setdefault("date_issued", val)
        elif key == "identifier.uri":
            d.setdefault("uri", val)
        elif key == "type.degree":
            d.setdefault("degree", val)
        elif key == "language.iso":
            d.setdefault("language", val)
    return d


# ── Handle file table ──────────────────────────────────────────────────────────

_FILE_ROW_RE = re.compile(
    r'<td headers="t1"[^>]*>([^<]*)</td>'          # filename
    r'<td headers="t2"[^>]*>([^<]*)</td>'          # size
    r'<td headers="t3"[^>]*>([^<]*)</td>'          # access
    r'<td headers="t4"[^>]*>([^<]*)</td>'          # label (Lýsing)
    r'<td headers="t5"[^>]*>([^<]*)</td>'          # type
    r'<td[^>]*>(.*?)</td>',                          # action cell (bitstream link)
    re.DOTALL,
)


async def fetch_handle_files(client: httpx.AsyncClient, handle: str) -> dict:
    resp = await get_with_retry(client, f"{BASE}/handle/1946/{handle}")
    h = resp.text
    rows = []
    for fn, size, access, label, ftype, action in _FILE_ROW_RE.findall(h):
        href_m = re.search(r'href="([^"]+)"', action)
        rows.append({
            "filename": html.unescape(fn).strip(),
            "size": html.unescape(size).strip(),
            "access": html.unescape(access).strip(),
            "label": html.unescape(label).strip(),
            "href": href_m.group(1) if href_m else None,
        })
    full = next((x for x in rows if x["label"].lower() in _FULLTEXT_LABELS), None)
    chosen = None
    embargo = None
    if full and full["access"].lower().startswith("opinn") and full["href"]:
        chosen = full
    elif full:
        m = re.search(r"(\d{2}\.\d{2}\.\d{4})", full["access"])
        embargo = m.group(1) if m else None
    return {"rows": rows, "chosen": chosen, "locked": chosen is None, "embargo": embargo}


# ── PDF text ───────────────────────────────────────────────────────────────────

def clean_body(text: str) -> str:
    text = text.replace("\x00", "")              # PG rejects NUL in TEXT/JSONB
    text = re.sub(r"\.{4,}", " ", text)          # TOC dot-leaders
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def pdf_bytes_to_text(raw: bytes) -> str | None:
    """Extract text from PDF bytes via poppler `pdftotext` (reads/writes stdio).

    Poppler is markedly more robust than PyMuPDF on Skemman's theses — some PDFs
    render blank under MuPDF yet extract cleanly here (e.g. handle 45928).
    """
    try:
        proc = subprocess.run(
            ["pdftotext", "-q", "-enc", "UTF-8", "-", "-"],
            input=raw, capture_output=True, timeout=180,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("pdftotext failed: %s", exc)
        return None
    text = proc.stdout.decode("utf-8", "replace")
    return clean_body(text) or None


async def fetch_pdf(client: httpx.AsyncClient, href: str) -> tuple[str | None, bytes | None]:
    """Download the PDF and return (clean_text, raw_bytes). (None, None) on error."""
    try:
        resp = await client.get(BASE + href, timeout=90.0)
        resp.raise_for_status()
        raw = resp.content
    except Exception as exc:  # noqa: BLE001
        log.warning("PDF fetch failed for %s: %s", href, exc)
        return None, None
    text = await asyncio.to_thread(pdf_bytes_to_text, raw)
    return text, raw


# ── Checkpoint ─────────────────────────────────────────────────────────────────

def _load_checkpoint() -> dict:
    if _CHECKPOINT_FILE.exists():
        return json.loads(_CHECKPOINT_FILE.read_text())
    return {"completed": [], "imported": 0}


def _save_checkpoint(state: dict) -> None:
    _CHECKPOINT_DIR.mkdir(exist_ok=True)
    _CHECKPOINT_FILE.write_text(json.dumps(state, indent=2))


# ── DB helpers ─────────────────────────────────────────────────────────────────

async def _ensure_source(session: AsyncSession, config: SourceConfig) -> uuid.UUID:
    result = await session.execute(
        select(Source).where(Source.short_name == config.short_name)
    )
    source = result.scalar_one_or_none()
    if source is None:
        source_id = uuid.uuid4()
        session.add(Source(
            id=source_id,
            short_name=config.short_name,
            display_name=config.display_name,
            base_url=BASE,
        ))
        await session.commit()
        return source_id
    return source.id


async def _upsert_doc(session: AsyncSession, doc: Document) -> None:
    def _v(val: Any) -> Any:
        return sa_null() if val is None else val

    values: dict[str, Any] = {
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
        "validation_errors": _v(doc.validation_errors),
    }
    update_cols = {k: v for k, v in values.items() if k not in ("id", "source_id", "external_id")}
    update_cols["updated_at"] = func.now()
    await session.execute(
        pg_insert(Document)
        .values(**values)
        .on_conflict_do_update(constraint="uq_doc_source_external", set_=update_cols)
    )


# ── Build a raw dict for the extractor ─────────────────────────────────────────

def build_raw(dim: dict, files: dict, browse: tuple[str, str, str, str],
              body: str | None) -> dict:
    date_s, hd, title, author = browse
    return {
        "handle": f"1946/{hd}",
        "title": dim.get("title") or title,
        "author": dim.get("author") or author,
        "advisor": dim.get("advisor"),
        "abstract": dim.get("abstract"),
        "note": dim.get("note"),
        "subjects": dim.get("subjects") or [],
        "date_issued": dim.get("date_issued"),
        "degree": dim.get("degree"),
        "uri": dim.get("uri"),
        "language": dim.get("language"),
        "browse_date": date_s,
        "pdf_text": body,                       # extractor strips this from raw_api_data
        "locked": files["locked"],
        "embargo_until": files["embargo"],
    }


# ── Main ───────────────────────────────────────────────────────────────────────

async def process_year(
    client: httpx.AsyncClient,
    config: SourceConfig,
    source_id: uuid.UUID | None,
    rows: list[tuple[str, str, str, str]],
    *,
    dry_run: bool,
    limit: int | None,
    taken: set[str],
    checkpoint: dict,
) -> dict:
    completed = set(checkpoint["completed"])
    stats = {"total": 0, "open": 0, "locked": 0, "errors": 0, "skipped": 0}

    for date_s, hd, title, author in rows:
        if limit is not None and stats["total"] >= limit:
            break
        if not dry_run and hd in completed:
            stats["skipped"] += 1
            continue

        dim = await fetch_oai_dim(client, hd)
        files = await fetch_handle_files(client, hd)

        body, pdf_bytes = None, None
        if files["chosen"]:
            body, pdf_bytes = await fetch_pdf(client, files["chosen"]["href"])

        raw = build_raw(dim, files, (date_s, hd, title, author), body)
        fields = Extractor(config).extract(raw)
        doc = Document(
            id=uuid.uuid4(),
            source_id=source_id or uuid.uuid4(),
            external_id=raw["handle"],
            url=raw.get("uri") or f"{BASE}/handle/{raw['handle']}",
            **fields,
        )
        errors = validate(doc, config)
        doc.validation_errors = errors or None

        stats["total"] += 1
        locked = doc.body_text is None
        stats["locked" if locked else "open"] += 1
        if errors:
            stats["errors"] += 1

        deg = raw.get("degree")
        stem_preview = thesis_stem(doc.case_number or title, raw.get("author"), deg)
        if dry_run:
            _print_dry(hd, date_s, doc, raw, stem_preview, errors)
        else:
            vf = None
            if doc.body_text:
                vf = unique_verdict_filename(stem_preview, taken)
                taken.add(vf)

            upserted = False
            async with _db_conn.AsyncSessionLocal() as session:
                try:
                    await _upsert_doc(session, doc)
                    await session.commit()
                    upserted = True
                except Exception as exc:  # noqa: BLE001
                    log.error("Upsert failed for %s: %s", hd, exc)
                    await session.rollback()

            # Only persist files + verdict_filename + checkpoint when the row landed.
            if not upserted:
                await asyncio.sleep(_REQUEST_DELAY)
                continue

            if vf and pdf_bytes:
                pdf_path = config.pdf_path(vf)
                pdf_path.parent.mkdir(parents=True, exist_ok=True)
                pdf_path.write_bytes(pdf_bytes)
            if vf:
                try:
                    write_markdown(doc, config, vf=vf)
                except Exception as exc:  # noqa: BLE001
                    log.warning("write_markdown failed for %s: %s", hd, exc)
                    vf = None
            if vf:
                async with _db_conn.AsyncSessionLocal() as session:
                    await session.execute(
                        update(Document)
                        .where(
                            Document.source_id == doc.source_id,
                            Document.external_id == doc.external_id,
                        )
                        .values(verdict_filename=vf)
                    )
                    await session.commit()

            completed.add(hd)
            checkpoint["completed"] = sorted(completed)
            checkpoint["imported"] = checkpoint.get("imported", 0) + 1
            _save_checkpoint(checkpoint)

        await asyncio.sleep(_REQUEST_DELAY)

    return stats


def _print_dry(hd: str, date_s: str, doc: Document, raw: dict,
               stem: str, errors: list[dict]) -> None:
    body_status = (
        f"{len(doc.body_text)} chars" if doc.body_text
        else f"LOCKED (embargo {raw.get('embargo_until')})"
    )
    plf = doc.plaintiffs[0] if doc.plaintiffs else {}
    print("─" * 78)
    print(f"1946/{hd}  [{date_s}]")
    print(f"  case_number : {(doc.case_number or '')[:68]}")
    print(f"  date        : {doc.document_date}    case_type: {doc.case_type}")
    print(f"  author      : {plf.get('name')}   (advisor: {plf.get('lawyer')})")
    print(f"  keywords    : {doc.keywords}")
    sm = doc.summary or ""
    print(f"  summary     : {len(sm)} chars | {sm[:80]!r}")
    print(f"  body_text   : {body_status}")
    print(f"  stem        : {stem}")
    if errors:
        print(f"  ⚠ errors    : {[e['field'] + ':' + e['message'] for e in errors]}")


async def main(year: int | None, limit: int | None, dry_run: bool) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if shutil.which("pdftotext") is None:
        raise SystemExit("pdftotext (poppler) not found on PATH — install poppler (brew install poppler).")
    config = get_config("logfraediritgerdir")

    source_id = None
    taken: set[str] = set()
    checkpoint = {"completed": [], "imported": 0}

    if not dry_run:
        await init_db()
        async with _db_conn.AsyncSessionLocal() as session:
            source_id = await _ensure_source(session, config)
        async with _db_conn.AsyncSessionLocal() as session:
            existing = (await session.execute(
                select(Document.verdict_filename)
                .where(Document.source_id == source_id)
                .where(Document.verdict_filename.isnot(None))
            )).scalars().all()
        taken = set(existing)
        checkpoint = _load_checkpoint()

    async with make_client() as client:
        htmltext = await load_browse(client)
        all_rows = parse_browse(htmltext)
        by_year: dict[int, list] = {}
        for row in all_rows:
            y = year_of(row[0])
            if y is not None:
                by_year.setdefault(y, []).append(row)

        if year is None:
            years = sorted(by_year)
        else:
            years = [year]

        grand = {"total": 0, "open": 0, "locked": 0, "errors": 0, "skipped": 0}
        for y in years:
            rows = by_year.get(y, [])
            print("=" * 78)
            print(f"YEAR {y}  —  {len(rows)} theses{'  [DRY-RUN]' if dry_run else ''}")
            stats = await process_year(
                client, config, source_id, rows,
                dry_run=dry_run, limit=limit, taken=taken, checkpoint=checkpoint,
            )
            for k in grand:
                grand[k] += stats[k]
            print(f"  → {stats}")

    print("=" * 78)
    print(f"DONE  {grand}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=None, help="Import a single year (default: all)")
    parser.add_argument("--limit", type=int, default=None, help="Stop after N theses per year")
    parser.add_argument("--dry-run", action="store_true", help="Extract+validate+report, no DB/disk writes")
    args = parser.parse_args()
    asyncio.run(main(year=args.year, limit=args.limit, dry_run=args.dry_run))
