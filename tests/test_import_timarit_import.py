# tests/test_import_timarit_import.py
"""Task 20: `import_timarit.py import`. A fake session (no DB) — the shape test_import_haestirettur uses — answers the
importer's queries by their SQL text; one test runs the whole import against the real schema inside a transaction that
is always rolled back. Fixtures are padded (R7/Task 9): every article page carries >= 50 characters, so it has a text
layer and docling OCR never runs; the one test without a text layer stubs the OCR callable."""
import argparse
import os
import uuid
from pathlib import Path

import fitz
import pytest
from sqlalchemy import exc as sa_exc, text
from sqlalchemy.ext.asyncio import create_async_engine

import engine.config.sources as sources_config
from engine.timarit.manifest import ArticleEntry, IssueManifest, load_manifest, manifest_sha256, save_manifest
from engine.timarit.splitter import sha256_bytes
from scripts import import_timarit as imp
from scripts.import_timarit import article_pdf_bytes, build_article_raw, import_manifest

_PAD = "Meginmál greinarinnar heldur áfram hér með nægum texta til að síðan hafi textalag."
SOURCE_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
ISSUE_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
DOC_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
OTHER_ISSUE = uuid.UUID("44444444-4444-4444-4444-444444444444")


def _pdf(pages):
    d = fitz.open()
    for t in pages:
        page = d.new_page()
        if t:
            page.insert_text((72, 72), t, fontsize=11)
    return d.tobytes()


ISSUE = _pdf(["Kápa", "Efnisyfirlit",
              f"7\nJón Jónsson\nFyrsta greinin\nTexti fyrstu greinar sem er nógu langur.\n{_PAD}",
              f"8\nFramhald.\n{_PAD}",
              f"9\nAnna\nÖnnur\nTexti.\n{_PAD}"])


class _Res:
    def __init__(self, *, scalar=None, first=None, rows=()):
        self._scalar, self._first, self._rows = scalar, first, list(rows)

    def scalar_one_or_none(self):
        return self._scalar

    def scalar_one(self):
        return self._scalar

    def first(self):
        return self._first

    def all(self):
        return self._rows


class _Session:
    """Answers by SQL text and records every statement. `docs` are the source's stored documents as
    (external_id, journal_issue_id, verdict_filename)."""
    def __init__(self, *, issue_row=(ISSUE_ID, None), docs=()):
        self.statements = []
        self.committed = self.rolled_back = 0
        self.issue_row = issue_row
        self.docs = list(docs)

    async def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.statements.append((sql, params if params is not None else _bound(stmt)))
        if "FROM sources" in sql and "short_name" in sql:
            return _Res(scalar=SOURCE_ID)
        if "FROM journal_issues" in sql:
            return _Res(first=self.issue_row)
        if sql.startswith("INSERT INTO documents"):
            return _Res(scalar=DOC_ID)
        if "FROM documents" in sql:
            return _Res(rows=self.docs)
        return _Res()

    async def flush(self):
        pass

    async def commit(self):
        self.committed += 1

    async def rollback(self):
        self.rolled_back += 1

    def upserts(self):
        return [(s, p) for s, p in self.statements if s.startswith("INSERT INTO documents")]

    def issue_updates(self):
        return [(s, p) for s, p in self.statements if s.startswith("UPDATE journal_issues")]


def _bound(stmt):
    """Bound values of a Core statement (the INSERT's column values), so tests can read what was upserted."""
    try:
        return stmt.compile().params
    except Exception:  # noqa: BLE001 — plain text() statements carry their params separately
        return None


@pytest.fixture
def raw_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(sources_config, "RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setattr(sources_config, "MARKDOWN_DIR", str(tmp_path / "markdown"))
    return tmp_path / "raw"


def _md_dir(raw_dir: Path) -> Path:
    return raw_dir.parent / "markdown"


def _manifest(tmp_path, name="2010_60_1.yaml", **over):
    p = tmp_path / "timarit_logfraedinga" / name
    base = dict(source="timarit_logfraedinga", year=2010, volume=60, issue="1",
                label="Tímarit lögfræðinga, 60. árg. 1. hefti (2010)", file="x/TL 2010_01.pdf",
                files=["x/TL 2010_01.pdf"], status="approved", pages=5)
    base.update(over)
    m = IssueManifest(**base)
    m.articles = [
        ArticleEntry(title="Fyrsta greinin", authors=["Jón Jónsson"], article_type="Fræðigrein", page_start=7, page_end=8,
                     pdf_pages=[2, 3], lang="is", provenance={"title": "toc"}, checks={"printed_page_match": True},
                     confidence=0.9),
        ArticleEntry(title="Önnur", authors=["Anna"], article_type="Fræðigrein", page_start=9, page_end=9, pdf_pages=[4, 4],
                     lang="is", checks={"printed_page_match": True}, confidence=0.9),
        ArticleEntry(title="Utan skjals", page_start=400, pdf_pages=None, confidence=0.1),
    ]
    save_manifest(m, p)
    return p, m


def _run(p, session, raw_dir, *, dry_run=False, read=lambda rel: ISSUE):
    return import_manifest(p, session=session, read_bytes=read, raw_dir=raw_dir, dry_run=dry_run, workers=1)


# --- the pieces ---------------------------------------------------------------------------------------------

def test_article_pdf_bytes_cuts_issue_or_reads_own_file():
    m = IssueManifest(source="timarit_logfraedinga", year=2010, volume=60, issue="1", label="L", file="issue.pdf")
    a = ArticleEntry(pdf_pages=[2, 3])
    assert fitz.open(stream=article_pdf_bytes(m, a, lambda rel: ISSUE), filetype="pdf").page_count == 2
    m2 = IssueManifest(source="ulfljotur", year=1947, volume=1, issue=None, label="L", file=None)
    own = _pdf([f"173\nTexti\n{_PAD}"])
    assert article_pdf_bytes(m2, ArticleEntry(source_file="173.pdf", pdf_pages=[0, 0]), lambda rel: own) == own
    with pytest.raises(ValueError):                         # neither an issue PDF nor a file of its own
        article_pdf_bytes(m2, ArticleEntry(pdf_pages=[0, 0]), lambda rel: own)


def test_build_article_raw_matches_extractor_contract():
    from engine.timarit.extract import ExtractedText
    m = IssueManifest(source="timarit_logfraedinga", year=2010, volume=60, issue="1", label="L", file="x.pdf")
    a = ArticleEntry(title="T", authors=["A"], article_type="Fræðigrein", page_start=7, page_end=8, pdf_pages=[2, 3], lang="is",
                     provenance={"title": "toc"}, checks={"title_on_page": True}, confidence=0.9,
                     note="athugað af notanda", analysis_notes=["upphafsbls. staðfest"])
    raw = build_article_raw(m, a, ExtractedText("Texti", [{"char": 0, "pdf_page": 0, "printed": 7}], "embedded", 0.9), "x.pdf")
    for k in ("title", "authors", "article_type", "page_start", "page_end", "lang", "volume", "issue", "year", "date",
              "summary", "keywords", "pdf_text", "page_offsets", "source_filename", "pdf_pages", "provenance",
              "checks", "confidence", "leitir_record_id", "xlsx_row", "ocr_source", "date_precision"):
        assert k in raw, k
    assert raw["pdf_text"] == "Texti" and raw["year"] == 2010 and raw["date_precision"] == "year" and raw["ocr_source"] == "embedded"
    assert raw["note"] == "athugað af notanda" and raw["analysis_notes"] == ["upphafsbls. staðfest"]       # R33
    a.issue = "4"                                                                                          # R31
    assert build_article_raw(m, a, ExtractedText("T"), "x.pdf")["issue"] == "4"


# --- import_manifest: the happy path ----------------------------------------------------------------------

async def test_import_writes_documents_pdfs_markdown_and_marks_imported(tmp_path, raw_dir):
    p, m = _manifest(tmp_path)
    session = _Session()
    counts = await _run(p, session, raw_dir)
    assert counts["imported"] == 2 and counts["skipped_no_pages"] == 1
    upserts = session.upserts()
    assert len(upserts) == 2 and all("journal_issue_id" in s and "page_offsets" in s and "volume" in s for s, _ in upserts)
    first = upserts[0][1]
    assert first["external_id"] == "2010-60-1-7" and first["journal_issue_id"] == ISSUE_ID and first["source_id"] == SOURCE_ID
    assert first["verdict_filename"] == "TL_60-1_2010_bls-7-8" and first["issue"] == "1" and first["volume"] == 60
    assert first["page_offsets"][0] == {"char": 0, "pdf_page": 0, "printed": 7}
    assert first["raw_api_data"]["source_filename"] == "x/TL 2010_01.pdf" and "pdf_text" not in first["raw_api_data"]
    assert (raw_dir / "timarit_logfraedinga" / "TL_60-1_2010_bls-7-8.pdf").exists()
    cut = fitz.open(raw_dir / "timarit_logfraedinga" / "TL_60-1_2010_bls-7-8.pdf")
    assert cut.page_count == 2 and "Fyrsta greinin" in cut[0].get_text()
    assert (raw_dir / "timarit_logfraedinga" / "hefti" / "2010_60_1.pdf").read_bytes() == ISSUE
    md = (_md_dir(raw_dir) / "timarit_logfraedinga" / "TL_60-1_2010_bls-7-8.md").read_text(encoding="utf-8")
    assert md.startswith("# Fyrsta greinin") and "<!-- bls. 7 -->" in md and "<!-- bls. 8 -->" in md
    out = load_manifest(p)
    assert out.status == "imported"
    assert any("ekki flutt inn" in n for n in out.articles[2].analysis_notes)            # the skipped article says why
    (sql, params), = session.issue_updates()
    assert "imported" in sql
    assert params["sha"] == manifest_sha256(p) and params["id"] == ISSUE_ID              # the saved file's hash
    assert params["fp"] == "raw/timarit_logfraedinga/hefti/2010_60_1.pdf"                 # relative to DATA_DIR
    assert params["s"] == sha256_bytes(ISSUE) and params["pc"] == 5


async def test_import_skips_imported_issue_with_unchanged_manifest(tmp_path, raw_dir):
    p, m = _manifest(tmp_path, status="imported")
    session = _Session(issue_row=(ISSUE_ID, manifest_sha256(p)))
    counts = await _run(p, session, raw_dir)
    assert counts == {"unchanged": 1} and not session.upserts() and not raw_dir.exists()


async def test_imported_issue_whose_manifest_changed_is_imported_again(tmp_path, raw_dir):
    p, m = _manifest(tmp_path, status="imported")
    session = _Session(issue_row=(ISSUE_ID, "an-older-version"))
    counts = await _run(p, session, raw_dir)
    assert counts["imported"] == 2 and len(session.upserts()) == 2


async def test_dry_run_writes_nothing(tmp_path, raw_dir, capsys):
    p, _ = _manifest(tmp_path)
    before = p.read_bytes()
    session = _Session()
    counts = await _run(p, session, raw_dir, dry_run=True)
    assert counts["imported"] == 2 and not raw_dir.exists() and not _md_dir(raw_dir).exists()
    assert not session.upserts() and not session.issue_updates() and session.committed == 0
    assert not [s for s, _ in session.statements if not s.startswith("SELECT")]          # reads only
    assert p.read_bytes() == before                                                       # manifest untouched
    out = capsys.readouterr().out
    assert "2010-60-1-7" in out and "TL_60-1_2010_bls-7-8" in out and "stafir" in out


# --- identity: duplicates in a manifest, collisions across manifests, file names ------------------------------

async def test_duplicate_ids_inside_a_manifest_become_two_documents(tmp_path, raw_dir):
    p, m = _manifest(tmp_path)
    m.articles[1].page_start = 7                       # same start page in the same issue: assign_external_ids → -2
    save_manifest(m, p)
    session = _Session()
    await _run(p, session, raw_dir)
    ids = [params["external_id"] for _, params in session.upserts()]
    assert ids == ["2010-60-1-7", "2010-60-1-7-2"]


async def test_cross_manifest_collision_is_not_overwritten(tmp_path, raw_dir):
    p, _ = _manifest(tmp_path)
    session = _Session(docs=[("2010-60-1-7", OTHER_ISSUE, "TL_60-1_2010_bls-7-8")])
    counts = await _run(p, session, raw_dir)
    assert counts["collision"] == 1 and counts["imported"] == 1
    assert [params["external_id"] for _, params in session.upserts()] == ["2010-60-1-9"]
    assert "auðkenni 2010-60-1-7 er þegar til í öðru hefti" in load_manifest(p).articles[0].analysis_notes
    assert not (raw_dir / "timarit_logfraedinga" / "TL_60-1_2010_bls-7-8.pdf").exists()   # the other issue's file stays


async def test_reimport_updates_own_rows_and_file_names_avoid_other_issues(tmp_path, raw_dir):
    p, _ = _manifest(tmp_path)
    session = _Session(docs=[("2010-60-1-7", ISSUE_ID, "TL_60-1_2010_bls-7-8"),      # this issue's own row: updated
                             ("1999-49-2-9", OTHER_ISSUE, "TL_60-1_2010_bls-9")])     # another issue holds this name
    counts = await _run(p, session, raw_dir)
    assert counts["imported"] == 2 and "collision" not in counts
    names = [params["verdict_filename"] for _, params in session.upserts()]
    assert names == ["TL_60-1_2010_bls-7-8", "TL_60-1_2010_bls-9_2"]


async def test_article_issue_is_carried_into_document_filename_and_citation(tmp_path, raw_dir):
    """R31: an Úlfljótur volume file (manifest issue None) whose article belongs to tbl. 4."""
    p = tmp_path / "ulfljotur" / "1947_1_x.yaml"
    m = IssueManifest(source="ulfljotur", year=1947, volume=1, issue=None, label="Úlfljótur, 1. árg. (1947)",
                      file="u/1947.pdf", files=["u/1947.pdf"], status="approved", pages=2)
    m.articles = [ArticleEntry(title="Um lögin", authors=["Ólafur Jóhannesson"], article_type="Fræðigrein", page_start=3,
                               page_end=4, issue="4", pdf_pages=[0, 1], checks={"printed_page_match": True}, confidence=0.9)]
    save_manifest(m, p)
    pdf = _pdf([f"3\nÓlafur Jóhannesson\nUm lögin\n{_PAD}", f"4\nFramhald.\n{_PAD}"])
    session = _Session()
    await _run(p, session, raw_dir, read=lambda rel: pdf)
    (_, params), = session.upserts()
    assert params["issue"] == "4" and params["external_id"] == "1947-1-4-3"
    assert params["verdict_filename"] == "Ulfljotur_1-4_1947_bls-3-4"
    md = (_md_dir(raw_dir) / "ulfljotur" / "Ulfljotur_1-4_1947_bls-3-4.md").read_text(encoding="utf-8")
    assert "Úlfljótur, 1. árg. 4. tbl. (1947), bls. 3–4" in md
    assert (raw_dir / "ulfljotur" / "hefti" / "1947_1_x.pdf").exists()


# --- text ---------------------------------------------------------------------------------------------------

async def test_printed_start_is_passed_only_when_the_start_page_was_verified(tmp_path, raw_dir, monkeypatch):
    p, m = _manifest(tmp_path)
    m.articles[1].checks = {"printed_page_match": False}
    save_manifest(m, p)
    seen = []
    real = imp.extract_article

    def spy(pdf, **kw):
        seen.append(kw["printed_start"])
        assert kw["footnotes"] is True and kw["ocr_bin_threshold"] == imp.C.OCR_BIN_RATIO_THRESHOLD
        assert kw["ocr"] is imp._ocr
        return real(pdf, **kw)
    monkeypatch.setattr(imp, "extract_article", spy)
    await _run(p, _Session(), raw_dir)
    assert seen == [7, None]


async def test_an_article_without_text_is_not_imported(tmp_path, raw_dir, monkeypatch):
    """R19: no text layer and no OCR text → ocr_source "none" → the article fails, no empty document."""
    p, m = _manifest(tmp_path)
    issue = _pdf(["Kápa", "Efnisyfirlit", "", "", f"9\nAnna\nÖnnur\nTexti.\n{_PAD}"])   # article 1's pages are scans
    calls = []
    monkeypatch.setattr(imp, "_ocr", lambda b: calls.append(len(b)))                      # never the real docling
    counts = await _run(p, _Session(), raw_dir, read=lambda rel: issue)
    assert counts["no_text"] == 1 and counts["imported"] == 1 and len(calls) == 1
    assert "enginn texti — þarf OCR" in load_manifest(p).articles[0].analysis_notes


async def test_import_notes_are_replaced_on_the_next_import(tmp_path, raw_dir):
    p, m = _manifest(tmp_path)
    m.articles[0].analysis_notes = ["enginn texti — þarf OCR", "upphafsbls. staðfest"]
    save_manifest(m, p)
    await _run(p, _Session(), raw_dir)
    assert load_manifest(p).articles[0].analysis_notes == ["upphafsbls. staðfest"]


async def test_a_bad_page_range_fails_that_article_only(tmp_path, raw_dir):
    p, m = _manifest(tmp_path)
    m.articles[0].pdf_pages = [40, 41]                                                     # beyond the 5-page PDF
    save_manifest(m, p)
    counts = await _run(p, _Session(), raw_dir)
    assert counts["failed"] == 1 and counts["imported"] == 1
    assert any(n.startswith("innflutningur mistókst") for n in load_manifest(p).articles[0].analysis_notes)


async def test_an_issue_with_nothing_imported_fails(tmp_path, raw_dir, monkeypatch):
    p, m = _manifest(tmp_path)
    monkeypatch.setattr(imp, "_ocr", lambda b: None)
    blank = _pdf(["", "", "", "", ""])
    session = _Session()
    counts = await _run(p, session, raw_dir, read=lambda rel: blank)
    assert counts.get("imported", 0) == 0 and counts["no_text"] == 2
    out = load_manifest(p)
    assert out.status == "failed" and "engin grein" in out.error
    (sql, params), = session.issue_updates()
    assert "failed" in sql and params["error"] == out.error
    assert not (raw_dir / "timarit_logfraedinga" / "hefti").exists()


# --- guards ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("over,key", [
    ({"status": "review"}, "not_approved"),
    ({"error": imp._CHANGED_AFTER_APPROVAL}, "source_changed"),
    ({"error": "frumskjal breyttist eftir samþykkt — yfirfarðu og greindu aftur; fleiri en eitt heftisskjal"},
     "source_changed"),
    ({"error": imp._DISCOVERY_ERROR + " fyrir sama hefti: a.pdf; b.pdf"}, "discovery_error"),
    ({"pages": None}, "not_analysed"),
])
async def test_guarded_manifests_are_not_imported(tmp_path, raw_dir, over, key):
    p, _ = _manifest(tmp_path, **over)
    before = p.read_bytes()
    session = _Session()
    assert await _run(p, session, raw_dir) == {key: 1}
    assert not session.upserts() and not raw_dir.exists() and p.read_bytes() == before


async def test_an_issue_pdf_that_changed_since_analysis_is_not_imported(tmp_path, raw_dir):
    p, _ = _manifest(tmp_path, sha256="0" * 64)
    session = _Session()
    assert await _run(p, session, raw_dir) == {"source_changed": 1}
    assert not session.upserts() and not raw_dir.exists()


# --- the upsert -------------------------------------------------------------------------------------------------

async def test_upsert_resets_passage_hash_and_returns_the_stored_id(tmp_path, raw_dir):
    from engine.database.models import Document
    session = _Session()
    doc = Document(id=uuid.uuid4(), source_id=SOURCE_ID, external_id="e", case_number="T", page_offsets=[], lang="is")
    assert await imp._upsert_doc(session, doc) == DOC_ID
    (sql, _), = session.upserts()
    assert "ON CONFLICT ON CONSTRAINT uq_doc_source_external" in sql
    set_clause = sql.split("DO UPDATE SET", 1)[1]
    assert "passage_hash" in set_clause and "page_offsets" in set_clause and "journal_issue_id" in set_clause
    assert "RETURNING documents.id" in sql


# --- workers ------------------------------------------------------------------------------------------------------

async def test_workers_run_in_a_process_pool(tmp_path, raw_dir):
    p, _ = _manifest(tmp_path)
    session = _Session()
    counts = await import_manifest(p, session=session, read_bytes=lambda rel: ISSUE, raw_dir=raw_dir, dry_run=False,
                                   workers=2)
    assert counts["imported"] == 2
    assert [params["external_id"] for _, params in session.upserts()] == ["2010-60-1-7", "2010-60-1-9"]   # article order


# --- cmd_import --------------------------------------------------------------------------------------------------

def _args(**kw):
    base = dict(source=None, issue=None, limit=None, dry_run=False, workers=1, command="import")
    base.update(kw)
    return argparse.Namespace(**base)


def _repo(tmp_path, monkeypatch):
    drop, repo = tmp_path / "drop", tmp_path / "repo"
    (drop / "x").mkdir(parents=True)
    (drop / "x" / "TL 2010_01.pdf").write_bytes(ISSUE)
    monkeypatch.setattr(imp, "DROP", drop)
    monkeypatch.setattr(imp, "REPO", repo)
    root = repo / imp.C.MANIFEST_ROOT
    good, _ = _manifest(root)
    bad, _ = _manifest(root, "2010_60_2.yaml", issue="2", file="x/missing.pdf", files=["x/missing.pdf"])
    return good, bad


async def test_cmd_import_commits_per_manifest_and_isolates_a_failing_issue(tmp_path, raw_dir, monkeypatch, capsys):
    good, bad = _repo(tmp_path, monkeypatch)
    sessions = []

    class _Ctx(_Session):
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    async def init_db(*a, **k):
        pass

    async def dispose_db():
        pass
    monkeypatch.setattr(imp._db, "init_db", init_db)
    monkeypatch.setattr(imp._db, "dispose_db", dispose_db)
    monkeypatch.setattr(imp._db, "AsyncSessionLocal", lambda: sessions.append(_Ctx()) or sessions[-1])
    monkeypatch.setattr(sources_config, "RAW_DIR", str(raw_dir))
    await imp.cmd_import(_args())
    s, = sessions
    assert s.committed == 2 and s.rolled_back == 1                 # good: commit; bad: rollback, then its failure committed
    assert load_manifest(good).status == "imported"
    out = load_manifest(bad)
    assert out.status == "failed" and out.error.startswith("innflutningur: FileNotFoundError")
    failed = [params for sql, params in s.statements if sql.startswith("UPDATE journal_issues") and "failed" in sql]
    assert failed and failed[-1]["error"] == out.error
    assert "import:" in capsys.readouterr().out


async def test_cmd_import_dry_run_commits_nothing(tmp_path, raw_dir, monkeypatch, capsys):
    good, bad = _repo(tmp_path, monkeypatch)
    before = {q: q.read_bytes() for q in (good, bad)}
    sessions = []

    class _Ctx(_Session):
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    async def noop(*a, **k):
        pass
    monkeypatch.setattr(imp._db, "init_db", noop)
    monkeypatch.setattr(imp._db, "dispose_db", noop)
    monkeypatch.setattr(imp._db, "AsyncSessionLocal", lambda: sessions.append(_Ctx()) or sessions[-1])
    await imp.cmd_import(_args(dry_run=True))
    s, = sessions
    assert s.committed == 0 and not [q for q, _ in s.statements if not q.startswith("SELECT")]
    assert {q: q.read_bytes() for q in (good, bad)} == before and not raw_dir.exists()
    assert "Þurrkeyrsla" in capsys.readouterr().out


def test_limit_counts_manifests_import_would_act_on(tmp_path, monkeypatch):
    monkeypatch.setattr(imp, "REPO", tmp_path)
    root = tmp_path / imp.C.MANIFEST_ROOT
    for stem, status in (("2010_x_1", "review"), ("2010_x_2", "approved"), ("2010_x_3", "imported")):
        save_manifest(IssueManifest(source="timarit_logfraedinga", year=2010, volume=None, issue=stem[-1], label="L",
                                    status=status), root / "timarit_logfraedinga" / f"{stem}.yaml")
    assert [p.stem for p in imp._select_manifests(_args(limit=2))] == ["2010_x_2", "2010_x_3"]
    assert [p.stem for p in imp._select_manifests(_args(limit=2, command="analyze"))] == ["2010_x_1"]


# --- hardening: files after the database work, a durable partial signal, environment errors stop the run ----------

class _Ctx(_Session):
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _fake_db(monkeypatch, factory=_Ctx):
    sessions = []

    async def noop(*a, **k):
        pass
    monkeypatch.setattr(imp._db, "init_db", noop)
    monkeypatch.setattr(imp._db, "dispose_db", noop)
    monkeypatch.setattr(imp._db, "AsyncSessionLocal", lambda: sessions.append(factory()) or sessions[-1])
    return sessions


def _written(raw_dir):
    """Every file under RAW and MARKDOWN, relative to their common parent."""
    return sorted(str(q.relative_to(raw_dir.parent)) for d in (raw_dir, _md_dir(raw_dir)) if d.exists()
                  for q in d.rglob("*") if q.is_file())


@pytest.mark.parametrize("fail_on,after", [("INSERT INTO documents", 1), ("UPDATE journal_issues", 0)])
async def test_a_database_failure_inside_a_manifest_leaves_no_files(tmp_path, raw_dir, fail_on, after):
    p, _ = _manifest(tmp_path)

    class Failing(_Session):
        async def execute(self, stmt, params=None):
            sql = " ".join(str(stmt).split())
            if sql.startswith(fail_on) and sum(s.startswith(fail_on) for s, _ in self.statements) == after:
                raise RuntimeError("duplicate key value violates unique constraint")
            return await super().execute(stmt, params)
    with pytest.raises(RuntimeError):
        await _run(p, Failing(), raw_dir)
    assert _written(raw_dir) == []


async def test_a_failed_commit_removes_the_manifests_files_and_restores_older_ones(tmp_path, raw_dir, monkeypatch):
    good, _ = _repo(tmp_path, monkeypatch)
    older = _md_dir(raw_dir) / "timarit_logfraedinga" / "TL_60-1_2010_bls-7-8.md"
    older.parent.mkdir(parents=True)
    older.write_text("eldri útgáfa", encoding="utf-8")

    class CommitFails(_Ctx):
        async def commit(self):
            self.committed += 1
            if self.committed == 1:
                raise RuntimeError("could not serialize access due to concurrent update")
    counts, raised = await imp._import_one(good, CommitFails(), _args(), None)
    assert raised and counts == {"failed_issue": 1}
    assert _written(raw_dir) == ["markdown/timarit_logfraedinga/TL_60-1_2010_bls-7-8.md"]
    assert older.read_text(encoding="utf-8") == "eldri útgáfa"
    assert load_manifest(good).status == "failed"


def test_import_summary_counts_what_did_not_reach_the_database():
    assert imp._import_summary({"imported": 3, "no_text": 1, "collision": 1, "skipped_no_pages": 2}, ["2010-60-1-5"]) == (
        "innflutningur: 2 greinar fluttar ekki inn (enginn texti 1, árekstur 1); úreltar færslur: 2010-60-1-5"
        " — sjá analysis_notes")
    assert imp._import_summary({"imported": 30, "failed": 21}, []) == (
        "innflutningur: 21 grein flutt ekki inn (villa 21) — sjá analysis_notes")
    assert imp._import_summary({"imported": 2, "skipped_no_pages": 1}, []) is None       # approved as such
    assert imp._import_summary({"imported": 2}, ["a", "b"]) == "innflutningur: úreltar færslur: a, b"
    assert imp._import_summary({"no_text": 2, "skipped_no_pages": 1}, []) == (
        "innflutningur: engin grein var flutt inn (enginn texti 2, engar síður 1) — sjá analysis_notes")


async def test_a_partial_import_says_so_in_error_and_a_clean_one_clears_it(tmp_path, raw_dir, capsys):
    p, m = _manifest(tmp_path, error="athugasemd greiningar")
    session = _Session(docs=[("2010-60-1-7", OTHER_ISSUE, "TL_60-1_2010_bls-7-8"),      # collision
                             ("2010-60-1-5", ISSUE_ID, "TL_60-1_2010_bls-5")])           # no longer in the manifest
    await _run(p, session, raw_dir)
    out = load_manifest(p)
    summary = "innflutningur: 1 grein flutt ekki inn (árekstur 1); úreltar færslur: 2010-60-1-5 — sjá analysis_notes"
    assert out.status == "imported" and out.error == "athugasemd greiningar | " + summary
    (_, params), = session.issue_updates()
    assert params["error"] == out.error
    session = _Session(docs=[("2010-60-1-7", OTHER_ISSUE, "TL_60-1_2010_bls-7-8")])
    await _run(p, session, raw_dir, dry_run=True)
    assert "→ innflutningur: 1 grein flutt ekki inn (árekstur 1) — sjá analysis_notes" in capsys.readouterr().out
    session = _Session()                                                                 # the collision resolved
    await _run(p, session, raw_dir)
    assert load_manifest(p).error == "athugasemd greiningar"
    assert session.issue_updates()[0][1]["error"] == "athugasemd greiningar"


async def test_raw_inside_the_archive_stops_the_run_before_anything(tmp_path, raw_dir, monkeypatch):
    good, bad = _repo(tmp_path, monkeypatch)
    before = {q: q.read_bytes() for q in (good, bad)}
    monkeypatch.setattr(sources_config, "RAW_DIR", str(tmp_path / "drop" / "raw"))

    async def no_db(*a, **k):
        raise AssertionError("no database work expected")
    monkeypatch.setattr(imp._db, "init_db", no_db)
    with pytest.raises(SystemExit, match="safninu"):
        await imp.cmd_import(_args())
    assert {q: q.read_bytes() for q in (good, bad)} == before
    with pytest.raises(SystemExit, match="safninu"):                 # import_manifest itself refuses it too
        await _run(good, _Session(), tmp_path / "drop" / "raw")


@pytest.mark.parametrize("error", [
    sa_exc.OperationalError("INSERT", {}, Exception("server closed the connection unexpectedly")),
    sa_exc.InterfaceError("INSERT", {}, Exception("connection is closed")),
    sa_exc.DBAPIError("INSERT", {}, Exception("connection was closed in the middle of operation"),
                      connection_invalidated=True),
    ConnectionResetError(54, "Connection reset by peer"),
])
async def test_a_lost_database_connection_stops_the_run_without_failing_manifests(tmp_path, raw_dir, monkeypatch,
                                                                                    capsys, error):
    good, bad = _repo(tmp_path, monkeypatch)
    before = {q: q.read_bytes() for q in (good, bad)}

    class Lost(_Ctx):
        async def execute(self, stmt, params=None):
            if str(stmt).startswith("INSERT INTO documents"):
                raise error
            return await super().execute(stmt, params)
    sessions = _fake_db(monkeypatch, Lost)
    with pytest.raises(SystemExit, match="gagnagrunn"):
        await imp.cmd_import(_args())
    assert {q: q.read_bytes() for q in (good, bad)} == before                     # no manifest marked failed
    assert not [s for s, _ in sessions[0].statements if s.startswith("UPDATE")]
    assert _written(raw_dir) == [] and "import:" in capsys.readouterr().out       # the summary is still printed


async def test_a_database_that_stops_answering_stops_the_run(tmp_path, raw_dir, monkeypatch):
    """A plain OSError says nothing by its type; the SELECT 1 that fails after it does."""
    good, bad = _repo(tmp_path, monkeypatch)
    before = {q: q.read_bytes() for q in (good, bad)}

    class Down(_Ctx):
        async def execute(self, stmt, params=None):
            sql = " ".join(str(stmt).split())
            if sql.startswith("INSERT INTO documents") or sql == "SELECT 1":
                raise OSError("Multiple exceptions: [Errno 61] Connect call failed ('127.0.0.1', 5433)")
            return await super().execute(stmt, params)
    _fake_db(monkeypatch, Down)
    with pytest.raises(SystemExit, match="gagnagrunn"):
        await imp.cmd_import(_args())
    assert {q: q.read_bytes() for q in (good, bad)} == before


def _row_of_manifests(tmp_path, monkeypatch, pattern):
    """One manifest per letter of `pattern`, in order: g = its PDF exists, b = its PDF is missing."""
    drop, repo = tmp_path / "drop", tmp_path / "repo"
    (drop / "x").mkdir(parents=True)
    (drop / "x" / "TL 2010_01.pdf").write_bytes(ISSUE)
    monkeypatch.setattr(imp, "DROP", drop)
    monkeypatch.setattr(imp, "REPO", repo)
    paths = []
    for i, kind in enumerate(pattern, start=1):
        f = "x/TL 2010_01.pdf" if kind == "g" else f"x/missing_{i}.pdf"
        p, _ = _manifest(repo / imp.C.MANIFEST_ROOT, f"2010_60_{i:02d}.yaml", issue=f"{i:02d}", file=f, files=[f])
        paths.append(p)
    return paths


async def test_five_failed_issues_in_a_row_stop_the_run(tmp_path, raw_dir, monkeypatch, capsys):
    paths = _row_of_manifests(tmp_path, monkeypatch, "bbbbbb")
    _fake_db(monkeypatch)
    with pytest.raises(SystemExit, match="5 hefti í röð"):
        await imp.cmd_import(_args())
    assert [load_manifest(p).status for p in paths] == ["failed"] * 5 + ["approved"]
    assert "import: 5 af 6 hefti" in capsys.readouterr().out


async def test_a_success_resets_the_count_of_failures_in_a_row(tmp_path, raw_dir, monkeypatch):
    paths = _row_of_manifests(tmp_path, monkeypatch, "bbbbgbbbb")
    _fake_db(monkeypatch)
    await imp.cmd_import(_args())
    assert [load_manifest(p).status for p in paths] == ["failed"] * 4 + ["imported"] + ["failed"] * 4


# --- the real schema (always rolled back) ------------------------------------------------------------------------

needs_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs DATABASE_URL")


class _ConnSession:
    """A rolled-back connection standing in for the session: commit and rollback are no-ops here."""
    def __init__(self, conn):
        self.conn = conn

    async def execute(self, *a, **k):
        return await self.conn.execute(*a, **k)

    async def commit(self):
        pass

    async def rollback(self):
        pass


@needs_db
async def test_import_against_the_real_schema(tmp_path, raw_dir):
    eng = create_async_engine(os.environ["DATABASE_URL"])
    conn = await eng.connect()
    try:
        await conn.begin()
        await conn.execute(text(
            "INSERT INTO sources (id, short_name, display_name) VALUES (gen_random_uuid(), 'timarit_logfraedinga', 'x') "
            "ON CONFLICT (short_name) DO NOTHING"))
        s = _ConnSession(conn)
        p, m = _manifest(tmp_path)
        rel = imp._manifest_rel(p)
        await imp.upsert_issue_row(s, m, rel)
        counts = await _run(p, s, raw_dir)
        assert counts["imported"] == 2
        rows = (await conn.execute(text("""
            SELECT d.id, d.external_id, d.volume, d.issue, d.page_start, d.page_end, d.lang, d.page_offsets,
                   d.verdict_filename, d.passage_hash, ji.status, ji.manifest_sha256, ji.file_path
            FROM documents d JOIN journal_issues ji ON ji.id = d.journal_issue_id
            WHERE ji.manifest_path = :p ORDER BY d.external_id"""), {"p": rel})).all()
        assert [r.external_id for r in rows] == ["2010-60-1-7", "2010-60-1-9"]
        r = rows[0]
        assert (r.volume, r.issue, r.page_start, r.page_end, r.lang) == (60, "1", 7, 8, "is")
        assert r.page_offsets[0]["printed"] == 7 and r.verdict_filename == "TL_60-1_2010_bls-7-8"
        assert r.status == "imported" and r.manifest_sha256 == manifest_sha256(p)
        assert r.file_path == "raw/timarit_logfraedinga/hefti/2010_60_1.pdf"

        # re-import after an edit: the same rows are updated (same ids), passage_hash is reset so passages rebuild
        await conn.execute(text("UPDATE documents SET passage_hash = 'old' WHERE id = :id"), {"id": r.id})
        m2 = load_manifest(p)
        m2.articles[0].title = "Fyrsta greinin, leiðrétt"
        save_manifest(m2, p)
        await _run(p, s, raw_dir)
        again = (await conn.execute(text(
            "SELECT id, case_number, passage_hash FROM documents WHERE id = :id"), {"id": r.id})).one()
        assert again.case_number == "Fyrsta greinin, leiðrétt" and again.passage_hash is None

        # the same article id from another issue is a collision: the stored row is left alone (the other manifest is
        # issue 2 of the same volume whose articles name issue 1 as their own, R31, so their ids are this issue's)
        other = tmp_path / "other" / "timarit_logfraedinga" / "2010_60_2.yaml"
        m3 = load_manifest(p)
        m3.status, m3.issue = "approved", "2"
        for a in m3.articles:
            a.issue = "1"
        save_manifest(m3, other)
        await imp.upsert_issue_row(s, m3, imp._manifest_rel(other))
        counts = await _run(other, s, raw_dir)
        assert counts["collision"] == 2
        kept = (await conn.execute(text("SELECT case_number FROM documents WHERE id = :id"), {"id": r.id})).scalar()
        assert kept == "Fyrsta greinin, leiðrétt"
    finally:
        await conn.rollback()
        await conn.close()
        await eng.dispose()
