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
        if sql.startswith("DELETE FROM documents"):       # --prune: what the WHERE clause would delete and return
            gone = [(eid, vf) for eid, jid, vf in self.docs
                    if jid == params["issue"] and eid in params["ids"]]
            self.docs = [d for d in self.docs if d[0] not in {eid for eid, _ in gone}]
            return _Res(rows=gone)
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


_SHARED = _pdf([f"3\nAlfa fyrri greinin\nrefur sjóbjörn sérhljóði\n{_PAD}", f"4\nAlfa framhald\nrefur sjóbjörn sérhljóði\n{_PAD}",
                f"5\nBeta síðari greinin\nsnæugla fjallalambið\n{_PAD}", f"6\nBeta framhald\nsnæugla fjallalambið\n{_PAD}"])


def _pages_of(data: bytes) -> list[str]:
    with fitz.open(stream=data, filetype="pdf") as d:
        return [p.get_text() for p in d]


def test_two_articles_sharing_an_own_file_each_get_only_their_pages():
    """Task 22e: an own file that holds more than one item (Úlfljótur 1957 187.pdf) is cut to pdf_pages."""
    m = IssueManifest(source="ulfljotur", year=1957, volume=11, issue=None, label="L", file=None)
    first = article_pdf_bytes(m, ArticleEntry(source_file="s.pdf", pdf_pages=[0, 1]), lambda rel: _SHARED)
    second = article_pdf_bytes(m, ArticleEntry(source_file="s.pdf", pdf_pages=[2, 3]), lambda rel: _SHARED)
    one, two = " ".join(_pages_of(first)), " ".join(_pages_of(second))
    assert len(_pages_of(first)) == 2 and len(_pages_of(second)) == 2
    assert "Alfa" in one and "sjóbjörn" in one and "Beta" not in one and "snæugla" not in one
    assert "Beta" in two and "snæugla" in two and "Alfa" not in two and "sjóbjörn" not in two


def test_pages_sharing_a_boundary_page_are_both_cut():
    m = IssueManifest(source="ulfljotur", year=1957, volume=11, issue=None, label="L", file=None)
    a = article_pdf_bytes(m, ArticleEntry(source_file="s.pdf", pdf_pages=[0, 2]), lambda rel: _SHARED)
    b = article_pdf_bytes(m, ArticleEntry(source_file="s.pdf", pdf_pages=[2, 3]), lambda rel: _SHARED)
    assert len(_pages_of(a)) == 3 and len(_pages_of(b)) == 2 and "Beta" in _pages_of(a)[2] and "Beta" in _pages_of(b)[0]


@pytest.mark.parametrize("pages", [None, [0, 3], [0, 9]])
def test_an_own_file_that_the_pages_cover_whole_is_copied_unchanged(pages):
    """pdf_pages None, [0, n-1] or running past the end: the bytes are the file's own (spec §4.5), sha256 equal."""
    m = IssueManifest(source="ulfljotur", year=1957, volume=11, issue=None, label="L", file=None)
    got = article_pdf_bytes(m, ArticleEntry(source_file="s.pdf", pdf_pages=pages), lambda rel: _SHARED)
    assert got == _SHARED and sha256_bytes(got) == sha256_bytes(_SHARED)


@pytest.mark.parametrize("pages", [[1, 3], [0, 2], [3, 3]])
def test_an_own_file_that_the_pages_do_not_cover_is_cut(pages):
    m = IssueManifest(source="ulfljotur", year=1957, volume=11, issue=None, label="L", file=None)
    got = article_pdf_bytes(m, ArticleEntry(source_file="s.pdf", pdf_pages=pages), lambda rel: _SHARED)
    assert got != _SHARED and len(_pages_of(got)) == pages[1] - pages[0] + 1


def test_a_page_range_outside_an_own_file_is_an_error():
    m = IssueManifest(source="ulfljotur", year=1957, volume=11, issue=None, label="L", file=None)
    with pytest.raises(ValueError):
        article_pdf_bytes(m, ArticleEntry(source_file="s.pdf", pdf_pages=[5, 6]), lambda rel: _SHARED)


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


@pytest.mark.parametrize("date, prov, precision", [
    ("2022-01-21", {"date": "page"}, "day"), ("2022-01-21", {}, "day"), ("2022-03-05", {"date": "user"}, "day"),
    ("2019-01-01", {"date": "user"}, "year"), ("2019-01-01", {"date": "page+user"}, "day"),     # R44
    ("2020-01-01", {"date": "pdf_metadata"}, "year"), (None, {}, "year")])
def test_build_article_raw_date_precision_is_day_only_for_a_date_that_was_printed(date, prov, precision):
    # Task 22d: a vefrit article with no printed date takes the year from the PDF's metadata (2020-01-01): "year"
    from engine.timarit.extract import ExtractedText
    m = IssueManifest(source="ulfljotur_vefrit", year=None, volume=None, issue=None, label="L", file=None)
    a = ArticleEntry(title="T", date=date, provenance=prov)
    raw = build_article_raw(m, a, ExtractedText("Texti"), "x.pdf")
    assert raw["date_precision"] == precision and raw["date"] == date


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


def _shared_manifest(tmp_path):
    """Two items in one own file (Úlfljótur 1957 187.pdf: an article and the item the user added after it)."""
    p = tmp_path / "ulfljotur" / "1957_11_x.yaml"
    m = IssueManifest(source="ulfljotur", year=1957, volume=11, issue=None, label="Úlfljótur, 11. árg. (1957)",
                      file=None, files=["u/187.pdf"], status="approved")
    m.articles = [
        ArticleEntry(title="Alfa fyrri greinin", authors=["Jón Jónsson"], article_type="Fræðigrein", page_start=3, page_end=4,
                     issue="1", pdf_pages=[0, 1], lang="is", source_file="u/187.pdf", checks={"printed_page_match": True},
                     confidence=0.9),
        ArticleEntry(title="Beta síðari greinin", authors=["Anna"], article_type="Fræðigrein", page_start=5, page_end=6,
                     issue="1", pdf_pages=[2, 3], lang="is", source_file="u/187.pdf", provenance={"pages": "user"},
                     checks={}, confidence=0.9),
    ]
    save_manifest(m, p)
    return p


async def test_items_sharing_an_own_file_are_imported_with_their_own_pages_only(tmp_path, raw_dir):
    """Task 22e: the RAW copy, the text and the page anchors of each item are those of its pdf_pages; the manifest
    still names the whole file as the source."""
    p = _shared_manifest(tmp_path)
    session = _Session()
    counts = await _run(p, session, raw_dir, read=lambda rel: _SHARED)
    assert counts["imported"] == 2
    (_, a), (_, b) = session.upserts()
    assert "Alfa" in a["body_text"] and "sjóbjörn" in a["body_text"] and "Beta" not in a["body_text"] and "snæugla" not in a["body_text"]
    assert "Beta" in b["body_text"] and "snæugla" in b["body_text"] and "Alfa" not in b["body_text"] and "sjóbjörn" not in b["body_text"]
    assert [o["pdf_page"] for o in a["page_offsets"]] == [0, 1] and [o["pdf_page"] for o in b["page_offsets"]] == [0, 1]
    assert [o["printed"] for o in a["page_offsets"]] == [3, 4]              # verified start: counted from page_start
    assert a["raw_api_data"]["source_filename"] == b["raw_api_data"]["source_filename"] == "u/187.pdf"
    assert a["raw_api_data"]["pdf_pages"] == [0, 1] and b["raw_api_data"]["pdf_pages"] == [2, 3]
    for params, mine, other in ((a, "Alfa", "Beta"), (b, "Beta", "Alfa")):
        pages = _pages_of((raw_dir / "ulfljotur" / f"{params['verdict_filename']}.pdf").read_bytes())
        assert len(pages) == 2 and all(mine in t for t in pages) and not any(other in t for t in pages)
        md = (_md_dir(raw_dir) / "ulfljotur" / f"{params['verdict_filename']}.md").read_text(encoding="utf-8")
        assert mine in md and other not in md


async def test_page_anchors_of_a_cut_own_file_count_from_the_cut_and_its_start_page(tmp_path, raw_dir):
    """A cut item whose start page was verified numbers its pages from its page_start, its pdf_page from the cut."""
    p = _shared_manifest(tmp_path)
    m = load_manifest(p)
    m.articles[1].checks = {"printed_page_match": True}
    save_manifest(m, p)
    session = _Session()
    await _run(p, session, raw_dir, read=lambda rel: _SHARED)
    (_, _), (_, b) = session.upserts()
    assert b["page_offsets"] == [{"char": 0, "pdf_page": 0, "printed": 5}, {"char": b["page_offsets"][1]["char"], "pdf_page": 1, "printed": 6}]
    md = (_md_dir(raw_dir) / "ulfljotur" / f"{b['verdict_filename']}.md").read_text(encoding="utf-8")
    assert "<!-- bls. 5 -->" in md and "<!-- bls. 6 -->" in md


async def test_the_dry_run_says_which_pages_of_the_file_each_item_gets(tmp_path, raw_dir, capsys):
    p = _shared_manifest(tmp_path)
    before = p.read_bytes()
    session = _Session()
    counts = await _run(p, session, raw_dir, dry_run=True, read=lambda rel: _SHARED)
    assert counts["imported"] == 2 and not raw_dir.exists() and p.read_bytes() == before and not session.upserts()
    lines = [ln for ln in capsys.readouterr().out.splitlines() if "stafir" in ln]
    assert len(lines) == 2
    assert "2 síður" in lines[0] and "úr 187.pdf, pdf-síður 1–2 af 4" in lines[0]
    assert "2 síður" in lines[1] and "úr 187.pdf, pdf-síður 3–4 af 4" in lines[1]


async def test_an_own_file_covered_whole_is_stored_unchanged(tmp_path, raw_dir, capsys):
    p = tmp_path / "ulfljotur" / "1947_1_x.yaml"
    m = IssueManifest(source="ulfljotur", year=1947, volume=1, issue=None, label="Úlfljótur, 1. árg. (1947)",
                      file=None, files=["u/173.pdf"], status="approved")
    m.articles = [ArticleEntry(title="Alfa fyrri greinin", authors=["Jón Jónsson"], article_type="Fræðigrein", page_start=3,
                               page_end=6, issue="1", pdf_pages=[0, 3], lang="is", source_file="u/173.pdf",
                               checks={"printed_page_match": True}, confidence=0.9)]
    save_manifest(m, p)
    session = _Session()
    await _run(p, session, raw_dir, dry_run=True, read=lambda rel: _SHARED)
    out = capsys.readouterr().out
    assert "4 síður" in out and "úr 173.pdf" not in out
    await _run(p, session, raw_dir, read=lambda rel: _SHARED)
    (_, params), = session.upserts()
    assert (raw_dir / "ulfljotur" / f"{params['verdict_filename']}.pdf").read_bytes() == _SHARED


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
    base = dict(source=None, issue=None, limit=None, dry_run=False, workers=1, command="import", prune=False)
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


# --- Task 20a: --prune and the lost commit reply ------------------------------------------------------------------

STALE = ("2010-60-1-5", ISSUE_ID, "TL_60-1_2010_bls-5")          # this issue's row; the manifest no longer has the id


def _touch(raw_dir: Path, vf: str) -> tuple[Path, Path]:
    """The derived files of a row (markdown and article PDF), as an earlier import left them."""
    md = _md_dir(raw_dir) / "timarit_logfraedinga" / f"{vf}.md"
    pdf = raw_dir / "timarit_logfraedinga" / f"{vf}.pdf"
    for q in (md, pdf):
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_bytes(b"old")
    return md, pdf


def _deletes(session):
    return [(s, p) for s, p in session.statements if s.startswith("DELETE FROM documents")]


async def _prune(p, session, raw_dir, *, dry_run=False, read=lambda rel: ISSUE):
    files = imp._FileLog()
    counts = await import_manifest(p, session=session, read_bytes=read, raw_dir=raw_dir, dry_run=dry_run, workers=1,
                                   prune=True, files=files)
    return counts, files


async def test_prune_deletes_only_this_issues_ids_the_manifest_no_longer_has(tmp_path, raw_dir):
    p, _ = _manifest(tmp_path)
    other = ("2010-60-2-3", OTHER_ISSUE, "TL_60-2_2010_bls-3")               # another issue's row
    session = _Session(docs=[("2010-60-1-7", ISSUE_ID, "TL_60-1_2010_bls-7-8"), STALE, other])
    stale_files, other_files = _touch(raw_dir, STALE[2]), _touch(raw_dir, other[2])
    counts, files = await _prune(p, session, raw_dir)
    assert counts["pruned"] == 1 and counts["imported"] == 2 and "orphaned" not in counts
    (sql, params), = _deletes(session)
    assert params["ids"] == ["2010-60-1-5"] and params["issue"] == ISSUE_ID and params["source"] == SOURCE_ID
    assert "journal_issue_id = :issue" in sql and "source_id = :source" in sql and "RETURNING" in sql
    kinds = [s.split()[0] for s, _ in session.statements]
    assert kinds.index("DELETE") > max(i for i, k in enumerate(kinds) if k == "INSERT")            # after the upserts
    assert all(q.exists() for q in stale_files + other_files)                  # files go only once the caller committed
    files.apply_removals()
    assert not any(q.exists() for q in stale_files) and all(q.exists() for q in other_files)
    out = load_manifest(p)
    assert out.error is None and session.issue_updates()[0][1]["error"] is None        # nothing stale is left to list


async def test_without_prune_nothing_is_deleted_and_the_stale_row_is_still_listed(tmp_path, raw_dir):
    p, _ = _manifest(tmp_path)
    session = _Session(docs=[STALE])
    counts = await _run(p, session, raw_dir)
    assert counts["orphaned"] == 1 and "pruned" not in counts and not _deletes(session)
    assert "úreltar færslur: 2010-60-1-5" in load_manifest(p).error


async def test_articles_that_failed_had_no_text_or_collided_are_not_pruned(tmp_path, raw_dir, monkeypatch):
    """Only ids absent from the manifest's article set go. Every id below is in the manifest, so its row stays."""
    p, m = _manifest(tmp_path)
    m.articles[0].pdf_pages = [40, 41]                                       # -7: cannot be cut → failed
    m.articles.append(ArticleEntry(title="Þriðja", page_start=8, page_end=8, pdf_pages=[3, 3], lang="is"))   # -8: fine
    m.articles.append(ArticleEntry(title="Fjórða", page_start=11, page_end=11, pdf_pages=[2, 2], lang="is"))  # -11: taken
    save_manifest(m, p)
    monkeypatch.setattr(imp, "_ocr", lambda b: None)
    issue = _pdf(["Kápa", "Efnisyfirlit", f"11\nFjórða\n{_PAD}", f"8\nÞriðja\n{_PAD}", ""])      # -9: a scan → no_text
    session = _Session(docs=[("2010-60-1-7", ISSUE_ID, "a"), ("2010-60-1-9", ISSUE_ID, "b"),
                             ("2010-60-1-400", ISSUE_ID, "c"),                       # no pdf_pages
                             ("2010-60-1-11", OTHER_ISSUE, "d"), STALE])
    counts, _ = await _prune(p, session, raw_dir, read=lambda rel: issue)
    assert (counts["failed"], counts["no_text"], counts["skipped_no_pages"], counts["collision"]) == (1, 1, 1, 1)
    assert counts["imported"] == 1 and counts["pruned"] == 1
    (_, params), = _deletes(session)
    assert params["ids"] == ["2010-60-1-5"]


async def test_nothing_is_pruned_from_an_issue_where_nothing_was_imported(tmp_path, raw_dir, monkeypatch):
    p, _ = _manifest(tmp_path)
    monkeypatch.setattr(imp, "_ocr", lambda b: None)
    session = _Session(docs=[STALE])
    counts, _ = await _prune(p, session, raw_dir, read=lambda rel: _pdf(["", "", "", "", ""]))
    assert not _deletes(session) and counts["orphaned"] == 1 and "pruned" not in counts
    out = load_manifest(p)
    assert out.status == "failed" and "2010-60-1-5" in out.error


async def test_dry_run_with_prune_lists_what_would_go_and_deletes_nothing(tmp_path, raw_dir, capsys):
    p, _ = _manifest(tmp_path)
    before = p.read_bytes()
    session = _Session(docs=[STALE])
    stale_files = _touch(raw_dir, STALE[2])
    counts, files = await _prune(p, session, raw_dir, dry_run=True)
    out = capsys.readouterr().out
    assert counts["pruned"] == 1 and "orphaned" not in counts
    assert "2010-60-1-5" in out and "yrði eytt" in out and "TL_60-1_2010_bls-5" in out
    assert not _deletes(session) and not [s for s, _ in session.statements if not s.startswith("SELECT")]
    files.apply_removals()
    assert all(q.exists() for q in stale_files) and p.read_bytes() == before


async def test_a_file_that_a_remaining_row_still_uses_is_kept(tmp_path, raw_dir):
    p, _ = _manifest(tmp_path)
    same_as_new = ("2010-60-1-5", ISSUE_ID, "TL_60-1_2010_bls-7-8")          # the name this run gives article -7
    shared = ("2010-60-1-6", ISSUE_ID, "TL_shared")
    held = ("1999-49-2-1", OTHER_ISSUE, "TL_shared")                          # another issue's row has it too
    lone = ("2010-60-1-4", ISSUE_ID, "TL_lone")
    session = _Session(docs=[same_as_new, shared, held, lone])
    kept = _touch(raw_dir, "TL_shared")
    gone = _touch(raw_dir, "TL_lone")
    counts, files = await _prune(p, session, raw_dir)
    assert counts["pruned"] == 3
    files.apply_removals()
    assert all(q.exists() for q in kept) and not any(q.exists() for q in gone)
    assert (raw_dir / "timarit_logfraedinga" / "TL_60-1_2010_bls-7-8.pdf").read_bytes().startswith(b"%PDF")  # the new file


async def test_pruned_files_are_removed_after_a_successful_commit_only(tmp_path, raw_dir, monkeypatch):
    good, _ = _repo(tmp_path, monkeypatch)
    seen = []

    class Committing(_Ctx):
        async def commit(self):
            seen.append([q.exists() for q in stale_files])                    # at commit time the files are still there
            await super().commit()
    stale_files = _touch(raw_dir, STALE[2])
    counts, raised = await imp._import_one(good, Committing(docs=[STALE]), _args(prune=True), None)
    assert not raised and counts["pruned"] == 1 and seen == [[True, True]]
    assert not any(q.exists() for q in stale_files)

    class CommitFails(_Ctx):
        async def commit(self):
            raise RuntimeError("could not serialize access due to concurrent update")
    stale_files = _touch(raw_dir, STALE[2])
    counts, raised = await imp._import_one(good, CommitFails(docs=[STALE]), _args(prune=True), None)
    assert raised and counts == {"failed_issue": 1}
    assert all(q.exists() for q in stale_files)                                # the rows are back, so are their files


async def test_prune_reaches_an_issue_whose_manifest_has_not_changed(tmp_path, raw_dir):
    """The usual order: an import left the stale row behind and said so; the manifest has not changed since, so the
    issue counts as unchanged. `--prune` has to look at it anyway (and only because it has stale rows)."""
    p, _ = _manifest(tmp_path, status="imported")
    session = _Session(issue_row=(ISSUE_ID, manifest_sha256(p)), docs=[STALE])
    assert await _run(p, session, raw_dir) == {"unchanged": 1}
    session = _Session(issue_row=(ISSUE_ID, manifest_sha256(p)), docs=[STALE])
    counts, _ = await _prune(p, session, raw_dir)
    assert counts["pruned"] == 1 and counts["imported"] == 2
    session = _Session(issue_row=(ISSUE_ID, manifest_sha256(p)), docs=[("2010-60-1-7", ISSUE_ID, "x")])
    counts, _ = await _prune(p, session, raw_dir)
    assert counts == {"unchanged": 1} and not session.upserts()


def test_deferred_removals_are_forgotten_by_undo_and_never_reach_into_the_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(imp, "DROP", tmp_path / "drop")
    mine, theirs = tmp_path / "raw" / "a.pdf", tmp_path / "drop" / "b.pdf"
    for q in (mine, theirs):
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_bytes(b"x")
    files = imp._FileLog()
    files.defer_removal(mine)
    files.undo()                                                       # the commit failed: nothing is to be deleted
    assert files.apply_removals() == 0 and mine.exists()
    files.defer_removal(theirs)
    files.defer_removal(tmp_path / "raw" / "missing.pdf")              # a file that is already gone is fine
    assert files.apply_removals() == 0 and theirs.exists()             # the archive is read only


def _main(monkeypatch, argv):
    seen = []

    async def spy(args):
        seen.append(args)
    monkeypatch.setattr(imp, "_setup_logging", lambda command: [])
    for name in ("cmd_import", "cmd_status", "cmd_discover", "cmd_analyze"):
        monkeypatch.setattr(imp, name, spy)
    imp.main(argv)
    return seen[0]


def test_prune_is_an_import_option_and_ignored_with_a_warning_elsewhere(monkeypatch, capsys):
    assert _main(monkeypatch, ["import", "--prune"]).prune is True
    assert _main(monkeypatch, ["import"]).prune is False
    capsys.readouterr()
    args = _main(monkeypatch, ["status", "--prune"])
    assert args.prune is False and "--prune" in capsys.readouterr().err


async def test_the_run_summary_counts_pruned_and_hints_at_prune_otherwise(tmp_path, raw_dir, monkeypatch, capsys):
    good, bad = _repo(tmp_path, monkeypatch)
    _fake_db(monkeypatch, lambda: _Ctx(docs=[STALE]))
    await imp.cmd_import(_args(prune=True))
    out = capsys.readouterr().out
    assert "úreltum greinum eytt" in out and "1" in out and "--prune" not in out
    p, _ = _manifest(tmp_path / "again")
    monkeypatch.setattr(imp, "_select_manifests", lambda args: [p])
    _fake_db(monkeypatch, lambda: _Ctx(docs=[STALE]))
    await imp.cmd_import(_args())
    out = capsys.readouterr().out
    assert "eldri greinar ekki í skránni" in out and "--prune" in out


@pytest.mark.parametrize("failure", [ConnectionResetError(54, "Connection reset by peer"),
                                     sa_exc.InterfaceError("COMMIT", {}, Exception("connection is closed"))])
async def test_a_lost_commit_reply_keeps_the_files_and_stops_the_run(tmp_path, raw_dir, monkeypatch, failure):
    """The server may have committed before the reply was lost; the files are right if it did, and if it did not the
    next run re-imports over the same paths (the stored manifest_sha256 differs). Files to prune stay either way."""
    good, _ = _repo(tmp_path, monkeypatch)
    stale_files = _touch(raw_dir, STALE[2])

    class ReplyLost(_Ctx):
        async def commit(self):
            raise failure
    session = ReplyLost(docs=[STALE])
    with pytest.raises(imp._StopRun, match="gagnagrunn"):
        await imp._import_one(good, session, _args(prune=True), None)
    assert "markdown/timarit_logfraedinga/TL_60-1_2010_bls-7-8.md" in _written(raw_dir)
    assert "raw/timarit_logfraedinga/hefti/2010_60_1.pdf" in _written(raw_dir)
    assert all(q.exists() for q in stale_files)
    assert not [s for s, _ in session.statements if s.startswith("UPDATE") and "'failed'" in s]


async def test_a_commit_that_fails_with_the_database_down_keeps_the_files_too(tmp_path, raw_dir, monkeypatch):
    good, _ = _repo(tmp_path, monkeypatch)

    class Down(_Ctx):
        async def commit(self):
            raise OSError("Multiple exceptions: [Errno 61] Connect call failed ('127.0.0.1', 5433)")

        async def execute(self, stmt, params=None):
            if " ".join(str(stmt).split()) == "SELECT 1":
                raise OSError("Connect call failed")
            return await super().execute(stmt, params)
    with pytest.raises(imp._StopRun):
        await imp._import_one(good, Down(), _args(), None)
    assert len(_written(raw_dir)) == 5                       # two articles' markdown and PDF, and the issue PDF: all kept
    assert any(f.endswith("TL_60-1_2010_bls-7-8.md") for f in _written(raw_dir))


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


@needs_db
async def test_prune_against_the_real_schema(tmp_path, raw_dir):
    """The DELETE is valid SQL for asyncpg, takes the rows of this issue and source only, and the FKs do the rest:
    passages, the row's own citations and its document_links go (ON DELETE CASCADE), a citation of another document
    that pointed at it is kept with to_doc_id NULL (ON DELETE SET NULL; build_citations re-resolves such rows)."""
    eng = create_async_engine(os.environ["DATABASE_URL"])
    conn = await eng.connect()

    async def one(sql, **params):
        return (await conn.execute(text(sql), params)).scalar_one()

    async def doc(source, issue, eid, vf):
        return await one("INSERT INTO documents (id, source_id, external_id, journal_issue_id, verdict_filename) "
                         "VALUES (gen_random_uuid(), :s, :e, :j, :v) RETURNING id", s=source, e=eid, j=issue, v=vf)

    async def passage(doc_id):
        await conn.execute(text(
            "INSERT INTO passages (id, document_id, ordinal, layer, section_kind, char_start, char_end, text, "
            "word_count, fts_is) VALUES (gen_random_uuid(), :d, 0, 'body', 'body', 0, 1, 'x', 1, to_tsvector('simple', 'x'))"),
            {"d": doc_id})

    async def citation(from_id, to_id, start):
        await conn.execute(text(
            "INSERT INTO citations (id, from_doc_id, layer, char_start, char_end, raw_text, target_court, status, to_doc_id) "
            "VALUES (gen_random_uuid(), :f, 'body', :c, :c + 1, 'x', 'x', 'resolved', :t)"), {"f": from_id, "t": to_id, "c": start})

    try:
        await conn.begin()
        for short in ("timarit_logfraedinga", "timarit_prune_other"):
            await conn.execute(text("INSERT INTO sources (id, short_name, display_name) VALUES (gen_random_uuid(), :n, 'x') "
                                    "ON CONFLICT (short_name) DO NOTHING"), {"n": short})
        s = _ConnSession(conn)
        p, m = _manifest(tmp_path)
        rel = imp._manifest_rel(p)
        await imp.upsert_issue_row(s, m, rel)
        assert (await _run(p, s, raw_dir))["imported"] == 2
        src = await one("SELECT id FROM sources WHERE short_name = 'timarit_logfraedinga'")
        other_src = await one("SELECT id FROM sources WHERE short_name = 'timarit_prune_other'")
        issue = await one("SELECT id FROM journal_issues WHERE manifest_path = :p", p=rel)
        m2 = load_manifest(p)
        m2.issue = "2"
        other_p = tmp_path / "other" / "timarit_logfraedinga" / "2010_60_2.yaml"
        save_manifest(m2, other_p)
        await imp.upsert_issue_row(s, m2, imp._manifest_rel(other_p))
        issue2 = await one("SELECT id FROM journal_issues WHERE manifest_path = :p", p=imp._manifest_rel(other_p))

        kept = await one("SELECT id FROM documents WHERE source_id = :s AND external_id = '2010-60-1-7'", s=src)
        stale = await doc(src, issue, "2010-60-1-5", "TL_stale")
        other_issue = await doc(src, issue2, "2010-60-2-5", "TL_other_issue")            # same source, another issue
        other_source = await doc(other_src, issue, "2010-60-1-5", "TL_other_source")     # same id and issue, another source
        for d in (stale, other_issue, other_source):
            await passage(d)
        await citation(stale, kept, 10)                      # the stale row cites a kept one: goes with the row
        await citation(kept, stale, 20)                      # a kept row cites the stale one: stays, target cleared
        await citation(other_issue, stale, 30)
        for a, b in ((kept, stale), (stale, kept)):
            await conn.execute(text("INSERT INTO document_links (id, from_doc_id, to_doc_id, relation) "
                                    "VALUES (gen_random_uuid(), :a, :b, 'cites')"), {"a": a, "b": b})

        # the clause alone keeps a row out of reach: asked for with the wrong issue, or the wrong source, nothing goes
        assert await imp._prune_rows(s, src, issue2, ["2010-60-1-5"]) == []
        assert await imp._prune_rows(s, await one("SELECT gen_random_uuid()"), issue, ["2010-60-1-5"]) == []
        assert await one("SELECT count(*) FROM documents WHERE external_id = '2010-60-1-5'") == 2

        counts, files = await _prune(p, s, raw_dir)
        assert counts["pruned"] == 1 and counts["imported"] == 2 and "orphaned" not in counts
        assert await one("SELECT count(*) FROM documents WHERE id = :d", d=stale) == 0
        for table, col in (("passages", "document_id"), ("citations", "from_doc_id"), ("document_links", "from_doc_id"),
                           ("document_links", "to_doc_id")):
            assert await one(f"SELECT count(*) FROM {table} WHERE {col} = :d", d=stale) == 0, (table, col)
        rows = (await conn.execute(text("SELECT from_doc_id, to_doc_id FROM citations WHERE char_start IN (20, 30) "
                                        "ORDER BY char_start"))).all()
        assert [tuple(r) for r in rows] == [(kept, None), (other_issue, None)]          # kept, dangling for build_citations
        for d in (kept, other_issue, other_source):
            assert await one("SELECT count(*) FROM documents WHERE id = :d", d=d) == 1
        for d in (other_issue, other_source):
            assert await one("SELECT count(*) FROM passages WHERE document_id = :d", d=d) == 1
        assert await one("SELECT count(*) FROM documents WHERE journal_issue_id = :j", j=issue) == 3   # 2 articles + other source
        assert len(files._removals) == 2                                                  # TL_stale.md and TL_stale.pdf
    finally:
        await conn.rollback()
        await conn.close()
        await eng.dispose()
