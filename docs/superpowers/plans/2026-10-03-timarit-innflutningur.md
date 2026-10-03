# Tímarit og fræðigreinar — Import Implementation Plan (part 1 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import ~1,660 journal PDFs (Úlfljótur, Tímarit lögfræðinga, Lögmannablaðið, Lögrétta, Lögfræðingur, Lögbrú, Stjórnmál og stjórnsýsla, Rannsóknir í félagsvísindum, Úlfljótur vefrit, ýmsar) as one `documents` row per **article**, with authoritative bibliographic metadata, a bibliographic `urlausn`, page-aware passages whose anchor is the printed page ("bls. 23"), a YAML review manifest per issue kept in git, and a resumable four-stage pipeline (`discover` → `analyze` → review → `import`).

**Architecture:** A new package `engine/timarit/` holds pure, testable units: filename identification (`naming.py`), printed-page mapping (`pagemap.py`), TOC parsing (`toc.py`), the Úlfljótur spreadsheet (`ulfljotur_xlsx.py`), leitir.is article records (`leitir_articles.py`), author/type/language helpers, the review manifest model (`manifest.py`), a Claude fallback (`llm_toc.py`), page-wise text extraction (`extract.py`), PDF cutting (`splitter.py`) and the per-issue orchestrator (`analyze.py`). `scripts/import_timarit.py` drives the stages and tracks state in a new `journal_issues` table. Articles reuse the book pattern in `documents` (`case_number` = title, `plaintiffs` = authors) plus six new columns; `passages` gain `page_from`/`page_to` and `passage_anchor()` learns "bls.".

**Tech Stack:** Python 3.13, uv, SQLAlchemy 2 async + asyncpg, alembic (0005), PostgreSQL 17, PyMuPDF (`fitz`), pdfplumber, openpyxl (new dependency), PyYAML, httpx, anthropic, FastAPI, React 19 + Vite + Vitest, `mcp` 2.x.

**Spec:** `docs/superpowers/specs/2026-10-03-timarit-innflutningur-design.md`

## Global Constraints

- The live DB is read-only for every task except controller-run, user-approved steps: applying alembic 0005 (Task 1 Step 7), the pilot import (Task 21) and the bulk import (Task 22). DB-backed tests skip when `DATABASE_URL` is unset or the `journal_issues` table does not exist yet.
- Never touch the dev servers on ports 8077/5173. Never print `.env`. The database listens on **port 5433**; scripts load `.env` with `uv run --env-file .env …`. Tests: `uv run --env-file .env pytest -q …` from the repo root; frontend: `cd frontend && npx vitest run && npx tsc -b`.
- `Lausnir_Data/dropfolder_timarit/` is the user's archive: **read-only** for every task — copy, never move or delete. The Úlfljótur spreadsheet is `Lausnir_Data/dropfolder_timarit/_meta/Ulfljotur_efnisyfirlit.xlsx`.
- Every filename and folder name read from disk is normalised to Unicode **NFC** before matching or storing (macOS returns NFD).
- Article identity: `external_id = f"{year}-{volume}-{issue}-{page_start}"` with `issue` as written ("1", "3-4"); `volume`/`issue` missing → the literal `x`; Vefrit: `f"{date:%Y-%m-%d}-{slug}"`; standalone works in `fraedigreinar_ymsar`: `slug`. `UNIQUE (source_id, external_id)` is the dedupe boundary.
- `urlausn` for journals: `{Höfundur}: „{Titill}“ {citation_name}, {N}. árg. {M}. {issue_term} ({ár}), bls. {a}–{b}`; omit a part when its value is NULL; more than two authors → `{first} o.fl.`; no author → start with `„{Titill}“`; Vefrit → `… {citation_name}, {d. mánuður ár}` (no pages). En dash `–` between pages.
- `verdict_filename` for journals: `{court-without-dots-ascii}_{volume}-{issue}_{year}_bls-{a}-{b}`; missing volume/issue → `x`; no pages → `_{slug40}`.
- Provenance labels on every manifest value: `xlsx`, `leitir`, `toc`, `filename`, `page`, `llm`, `user`. A value with provenance `user` is never overwritten by `analyze`. `llm` caps article confidence at `0.70`.
- `journal_issues.status ∈ {pending, review, approved, imported, failed}`; `article_type ∈ {Fræðigrein, Ritstjórnargrein, Ritdómur, Viðtal, Frétt, Dómareifun, Annað}`.
- Manifests live in the repo at `data/timarit/manifests/{source}/{year}_{volume}_{issue}.yaml` (`x` for a missing volume/issue); the unmatched list is `data/timarit/_unmatched.yaml`.
- Tunables live in `engine/timarit/config.py` with the values below as defaults; Task 21 (pilot) replaces them with the user's choices and records both in this plan: confidence weights `title_on_page 0.35, printed_page_match 0.30, author_on_page 0.15, external_record 0.20`; `APPROVE_THRESHOLD = 0.85`; `OCR_BIN_RATIO_THRESHOLD = None` (re-OCR disabled until the pilot); `PAGE_MAP_MIN_QUALITY = 0.5`.
- User-facing strings (frontend, MCP descriptions, CLI messages that the user reads) in Icelandic; code, comments and log messages in English, as in the rest of `engine/`.
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01Q7ipnjdQK1b8qMbFKVqLU5`.

## Review Focus

1. **An issue whose printed page numbers restart or are missing on most pages** (scanned Lögmannablaðið 1995–97, Lögrétta's `a 9` markers): the page map must report low `quality`, the anchor must fall back to `section_path`/`hluti N` rather than emit a wrong "bls.", and the issue must land in `review`. Tests: Task 8 (`test_fit_offset_refuses_when_fewer_than_three_agree`, `test_quality_counts_consistent_pages_only`), Task 4 (`test_anchor_prefers_paragraph_then_page_then_section`).
2. **A TOC entry whose start page maps outside the PDF** (typo'd S&S filename `189-2010`, annual volume with missing pages): `pdf_pages` must be clipped or left `null` and the article flagged, never raise. Tests: Task 7 (`test_identify_sands_keeps_filename_pages_as_hint_only`), Task 17 (`test_article_with_start_page_beyond_pdf_is_flagged_not_cut`).
3. **Two files that are the same bytes under different names** (Úlfljótur 1957/1958, Lögfræðingur 2010): only one is analysed, the other is recorded as `duplicate_of`. Test: Task 18 (`test_discover_records_byte_duplicates_once`).
4. **A manifest edited by the user and re-analysed**: `user` values survive, `status: approved` survives, and a later re-import happens only when `manifest_sha256` differs. Tests: Task 14 (`test_merge_keeps_user_values_and_approved_status`), Task 20 (`test_import_skips_imported_issue_with_unchanged_manifest`).
5. **An English-language article** (Stjórnmál og stjórnsýsla Vol21): `lang='en'`, BÍN lemmatisation skipped, `fts_is` still populated with the literal words so keyword search hits. Tests: Task 13 (`test_detect_lang_english`), Task 4 (`test_lemmatize_rows_skips_bin_for_english`).

## File structure

| Path | Responsibility |
|---|---|
| `alembic/versions/0005_timarit.py` | New columns on `documents` and `passages`; table `journal_issues` |
| `engine/database/models.py` | `Document` + 6 columns; `Passage` + 2; new `JournalIssue` |
| `engine/config/sources.py` | `SourceConfig` fields `kind`, `issue_term`, `citation_name`, `lang_default`, `volume_base_year`; ten journal configs; `TIMARIT_DROPFOLDER_DIR` |
| `engine/config/source_groups.py` | `_TIMARIT_SOURCES`, tree node `timarit` |
| `engine/timarit/config.py` | Tunables (weights, thresholds, cache dirs) |
| `engine/timarit/naming.py` | NFC + filename/folder patterns → `FileIdentity`; rejection reasons |
| `engine/timarit/pagemap.py` | Printed page numbers → `PageMap` |
| `engine/timarit/toc.py` | TOC text → `TocEntry` list |
| `engine/timarit/ulfljotur_xlsx.py` | Spreadsheet rows; first-page matching |
| `engine/timarit/leitir_articles.py` | leitir.is article search + `ispartof` parser + disk cache |
| `engine/timarit/authors.py` | Clean names, split, strip titles |
| `engine/timarit/article_type.py` | Section/title → article type |
| `engine/timarit/lang.py` | is/en detection |
| `engine/timarit/manifest.py` | `ArticleEntry`, `IssueManifest`, YAML I/O, confidence, user-preserving merge |
| `engine/timarit/llm_toc.py` | Claude fallback for TOC parsing, cached |
| `engine/timarit/extract.py` | Page-wise text, running-line strip, dehyphenation, `page_offsets`, OCR gate, BÍN ratio |
| `engine/timarit/splitter.py` | Cut an article's pages into its own PDF |
| `engine/timarit/analyze.py` | Per-issue orchestration: page map → list → verify → confidence → status |
| `engine/processors/pdf_parser.py` | `parse_pdf_pages()` returning text + per-page offsets |
| `engine/processors/segmenter.py` | `max_chars` guard |
| `engine/search/passage_index.py` | `page_from`/`page_to`, lang-aware lemmas, anchor with "bls." |
| `engine/search/passage_search.py`, `engine/search/queries.py` | Page columns in projections; `_citation` with journal fields; `get_document` journal block |
| `engine/processors/extractor.py` | `_extract_timarit` |
| `engine/processors/renderer.py` | Journal `to_urlausn`, `verdict_filename`, markdown header, page markers |
| `engine/processors/validator.py` | Journal rules |
| `scripts/import_timarit.py` | CLI: `discover`, `analyze`, `import`, `status` |
| `scripts/backfill_passages.py` | Pass `lang` and `page_offsets` to workers |
| `frontend/src/api/types.ts`, `frontend/src/components/DocPanel.tsx` | `journal` block in header |
| `engine/mcp/server.py` | One sentence in `INSTRUCTIONS` |
| `tests/test_timarit_*.py`, `tests/fixtures/timarit/` | Unit tests and real-text fixtures |
| `docs/wiki/*.md`, `sources_catalogue.md` | Documentation |

---

### Task 1: Migration 0005 and ORM columns

**Files:**
- Create: `alembic/versions/0005_timarit.py`
- Modify: `engine/database/models.py` (`Document`, `Passage`, new `JournalIssue`)
- Test: `tests/test_models_journal.py`

**Interfaces:**
- Produces: ORM attributes `Document.volume: int|None`, `Document.issue: str|None`, `Document.page_start: int|None`, `Document.page_end: int|None`, `Document.lang: str|None`, `Document.page_offsets: list[dict]|None`, `Document.journal_issue_id: uuid.UUID|None`; `Passage.page_from: int|None`, `Passage.page_to: int|None`; class `JournalIssue` with columns listed in spec §4.3 and `__tablename__ = "journal_issues"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models_journal.py
"""Schema for journals (spec 2026-10-03 §4): six document columns, two passage
columns, and the journal_issues table — checked on the ORM, no DB needed."""
from engine.database.models import Base, Document, JournalIssue, Passage


def test_document_has_journal_columns():
    cols = Document.__table__.c
    for name in ("volume", "issue", "page_start", "page_end", "lang", "page_offsets", "journal_issue_id"):
        assert name in cols, name
    assert str(cols["volume"].type).upper() == "SMALLINT"
    assert str(cols["page_start"].type).upper() == "INTEGER"
    assert cols["page_offsets"].type.__class__.__name__ == "JSONB"
    fk = next(iter(cols["journal_issue_id"].foreign_keys))
    assert fk.column.table.name == "journal_issues"
    assert fk.ondelete == "SET NULL"


def test_passage_has_page_columns():
    cols = Passage.__table__.c
    assert "page_from" in cols and "page_to" in cols
    assert str(cols["page_from"].type).upper() == "SMALLINT"


def test_journal_issues_table():
    t = JournalIssue.__table__
    assert t.name == "journal_issues"
    for name in ("id", "source_id", "year", "volume", "issue", "label", "file_path", "sha256",
                 "page_count", "toc_text", "manifest_path", "manifest_sha256", "status",
                 "confidence", "error", "created_at", "updated_at"):
        assert name in t.c, name
    assert t.c["status"].nullable is False
    assert t.c["manifest_path"].nullable is False
    assert "journal_issues" in Base.metadata.tables
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest -q tests/test_models_journal.py`
Expected: FAIL — `ImportError: cannot import name 'JournalIssue'`.

- [ ] **Step 3: Add the ORM model and columns**

In `engine/database/models.py`, inside `class Document`, after `citation_hash`:

```python
    # ── Journals (spec 2026-10-03 §4.2) ──────────────────────────────────────
    volume: Mapped[int | None] = mapped_column(SmallInteger)      # árgangur
    issue: Mapped[str | None] = mapped_column(Text)               # "1", "3-4"; NULL for annual volumes / web articles
    page_start: Mapped[int | None] = mapped_column(Integer)       # printed first page
    page_end: Mapped[int | None] = mapped_column(Integer)
    lang: Mapped[str | None] = mapped_column(Text)                # 'is' | 'en' | …; BÍN lemmatisation only for 'is'
    # [{"char": 0, "pdf_page": 0, "printed": 7}, …] — where each PDF page starts in
    # body_text and which printed number it carries. Bridge from text to page for
    # every PDF source (books and theses get it in part 2).
    page_offsets: Mapped[list[Any] | None] = mapped_column(JSONB)
    journal_issue_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("journal_issues.id", ondelete="SET NULL"))
```

Inside `class Passage`, after `word_count`:

```python
    page_from: Mapped[int | None] = mapped_column(SmallInteger)   # printed page range, from documents.page_offsets
    page_to: Mapped[int | None] = mapped_column(SmallInteger)
```

New class before `class Passage` (needs `Float`, `Integer`, `Text`, `UUID`, `JSONB` already imported; add `Float` to the `sqlalchemy` import if missing):

```python
class JournalIssue(Base):
    """One hefti or árgangur of a journal (spec 2026-10-03 §4.3).

    The row is both the user's overview and the pipeline's checkpoint: `status`
    walks pending → review/approved → imported, `manifest_sha256` says which
    version of the YAML review file was imported.
    """
    __tablename__ = "journal_issues"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False)
    year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    volume: Mapped[int | None] = mapped_column(SmallInteger)
    issue: Mapped[str | None] = mapped_column(Text)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    file_path: Mapped[str | None] = mapped_column(Text)           # RAW copy of the issue PDF, relative to DATA_DIR
    sha256: Mapped[str | None] = mapped_column(Text)
    page_count: Mapped[int | None] = mapped_column(Integer)
    toc_text: Mapped[str | None] = mapped_column(Text)
    manifest_path: Mapped[str] = mapped_column(Text, nullable=False)  # relative to repo root
    manifest_sha256: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)        # pending|review|approved|imported|failed
    confidence: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_journal_issue_source", "source_id", "year"),
        Index("ix_journal_issue_status", "status"),
    )
```

- [ ] **Step 4: Write the migration**

```python
# alembic/versions/0005_timarit.py
"""journals: documents bibliographic columns, passages page range, journal_issues

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

_DOC_COLS = [
    sa.Column("volume", sa.SmallInteger(), nullable=True),
    sa.Column("issue", sa.Text(), nullable=True),
    sa.Column("page_start", sa.Integer(), nullable=True),
    sa.Column("page_end", sa.Integer(), nullable=True),
    sa.Column("lang", sa.Text(), nullable=True),
    sa.Column("page_offsets", JSONB(), nullable=True),
]


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table("journal_issues"):
        op.create_table(
            "journal_issues",
            sa.Column("id", sa.UUID(), nullable=False),
            sa.Column("source_id", sa.UUID(), nullable=False),
            sa.Column("year", sa.SmallInteger(), nullable=False),
            sa.Column("volume", sa.SmallInteger(), nullable=True),
            sa.Column("issue", sa.Text(), nullable=True),
            sa.Column("label", sa.Text(), nullable=False),
            sa.Column("file_path", sa.Text(), nullable=True),
            sa.Column("sha256", sa.Text(), nullable=True),
            sa.Column("page_count", sa.Integer(), nullable=True),
            sa.Column("toc_text", sa.Text(), nullable=True),
            sa.Column("manifest_path", sa.Text(), nullable=False),
            sa.Column("manifest_sha256", sa.Text(), nullable=True),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_journal_issue_source", "journal_issues", ["source_id", "year"])
        op.create_index("ix_journal_issue_status", "journal_issues", ["status"])
        # Expression index: NULL volume/issue must still be unique per (source, year).
        op.execute(
            "CREATE UNIQUE INDEX uq_journal_issue ON journal_issues "
            "(source_id, year, coalesce(volume, -1), coalesce(issue, ''))"
        )
    doc_cols = {c["name"] for c in insp.get_columns("documents")}
    for col in _DOC_COLS:
        if col.name not in doc_cols:
            op.add_column("documents", col)
    if "journal_issue_id" not in doc_cols:
        op.add_column("documents", sa.Column("journal_issue_id", sa.UUID(), nullable=True))
        op.create_foreign_key("fk_doc_journal_issue", "documents", "journal_issues",
                              ["journal_issue_id"], ["id"], ondelete="SET NULL")
        op.create_index("ix_doc_journal_issue", "documents", ["journal_issue_id"])
    pas_cols = {c["name"] for c in insp.get_columns("passages")}
    for name in ("page_from", "page_to"):
        if name not in pas_cols:
            op.add_column("passages", sa.Column(name, sa.SmallInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("passages", "page_to")
    op.drop_column("passages", "page_from")
    op.drop_index("ix_doc_journal_issue", table_name="documents")
    op.drop_constraint("fk_doc_journal_issue", "documents", type_="foreignkey")
    op.drop_column("documents", "journal_issue_id")
    for col in reversed(_DOC_COLS):
        op.drop_column("documents", col.name)
    op.execute("DROP INDEX IF EXISTS uq_journal_issue")
    op.drop_index("ix_journal_issue_status", table_name="journal_issues")
    op.drop_index("ix_journal_issue_source", table_name="journal_issues")
    op.drop_table("journal_issues")
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest -q tests/test_models_journal.py tests/test_models_passage.py tests/test_citations_schema.py`
Expected: PASS.

- [ ] **Step 6: Check the migration compiles offline**

Run: `uv run --env-file .env alembic upgrade 0005 --sql | head -60`
Expected: SQL with `CREATE TABLE journal_issues`, `ALTER TABLE documents ADD COLUMN volume …`, `ALTER TABLE passages ADD COLUMN page_from …`. (Offline mode skips the inspector guards; that is fine for a syntax check.)

- [ ] **Step 7: USER GATE — apply to the live DB**

Controller asks the user, then runs: `uv run --env-file .env alembic upgrade head` and verifies with `psql -d lausnir_v2 -c "\d journal_issues"` and `psql -d lausnir_v2 -Atc "select column_name from information_schema.columns where table_name='passages' and column_name like 'page_%'"`. Expected: two rows `page_from`, `page_to`.

- [ ] **Step 8: Commit**

```bash
git add alembic/versions/0005_timarit.py engine/database/models.py tests/test_models_journal.py
git commit -m "feat(timarit): schema 0005 — journal_issues, bibliographic columns, passage page range"
```

---

### Task 2: Journal sources and the `timarit` scope group

**Files:**
- Modify: `engine/config/sources.py` (dataclass fields; `TIMARIT_DROPFOLDER_DIR`; ten configs appended before `]` of `_SOURCES`)
- Modify: `engine/config/source_groups.py` (`_TIMARIT_SOURCES`, `_ASSIGNED`, tree node, `validate_catalog`)
- Modify: `pyproject.toml` (add `"openpyxl"` to `dependencies`), then `uv sync`
- Test: `tests/test_timarit_sources.py`

**Interfaces:**
- Produces: `SourceConfig.kind: str` (`"ruling"` default, `"book"`, `"journal"`), `SourceConfig.issue_term: str | None`, `SourceConfig.citation_name: str | None`, `SourceConfig.lang_default: str`, `SourceConfig.volume_base_year: int | None`; constant `TIMARIT_DROPFOLDER_DIR: str`; constant `JOURNAL_SOURCES: tuple[str, ...]` in `engine/config/sources.py` listing the ten short names; `source_groups.SCOPE_TREE` node `{"key": "timarit", "label": "Tímarit og fræðigreinar", …}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_timarit_sources.py
from engine.config.sources import (
    JOURNAL_SOURCES, SOURCE_REGISTRY, TIMARIT_DROPFOLDER_DIR, _DATA_DIR, get_config,
)
from engine.config.source_groups import SCOPE_TREE, resolve_scope_keys, validate_catalog

EXPECTED = {
    "timarit_logfraedinga": ("TL", "hefti", 1951),
    "ulfljotur": ("Úlfljótur", "tbl.", 1947),
    "ulfljotur_vefrit": ("Úlfljótur vefrit", None, None),
    "logmannabladid": ("Lögmannablaðið", "tbl.", 1995),
    "logretta": ("Lögrétta", "tbl.", 2004),
    "logfraedingur": ("Lögfræðingur", "tbl.", None),
    "logbru": ("Lögbrú", "tbl.", None),
    "stjornmal_stjornsysla": ("Stjórnmál og stjórnsýsla", "tbl.", 2005),
    "rannsoknir_lagadeild": ("Rannsóknir í félagsvísindum", None, None),
    "fraedigreinar_ymsar": ("Fræðigrein", None, None),
}


def test_ten_journal_sources_registered_as_journals():
    assert tuple(EXPECTED) == JOURNAL_SOURCES
    for short, (abbr, term, base) in EXPECTED.items():
        cfg = get_config(short)
        assert cfg.kind == "journal", short
        assert cfg.abbreviation == abbr
        assert cfg.issue_term == term
        assert cfg.volume_base_year == base
        assert cfg.case_number_is_title is True
        assert cfg.has_footnotes is True
        assert cfg.parse_parties == "none"
        assert cfg.lang_default == "is"
        assert set(cfg.verdict_types_allowed) == {
            "Fræðigrein", "Ritstjórnargrein", "Ritdómur", "Viðtal", "Frétt", "Dómareifun", "Annað"}


def test_defaults_for_existing_kinds():
    assert get_config("haestirettur").kind == "ruling"
    assert get_config("logfraedibaekur").kind == "book"
    assert get_config("logfraediritgerdir").kind == "book"
    assert get_config("logmannabladid").verdict_type_default == "Annað"
    assert get_config("timarit_logfraedinga").verdict_type_default == "Fræðigrein"
    assert get_config("timarit_logfraedinga").citation_name == "Tímarit lögfræðinga"


def test_dropfolder_under_data_dir():
    assert TIMARIT_DROPFOLDER_DIR == f"{_DATA_DIR}/dropfolder_timarit"


def test_timarit_group_in_tree_and_catalog_valid():
    node = next(c for c in SCOPE_TREE if c["key"] == "timarit")
    assert node["label"] == "Tímarit og fræðigreinar"
    assert {leaf["key"] for leaf in node["children"]} == set(JOURNAL_SOURCES)
    validate_catalog()
    assert set(resolve_scope_keys(["timarit"])) == set(JOURNAL_SOURCES)
```

If `resolve_scope_keys` does not exist in `source_groups.py`, replace the last assertion with the module's existing key→sources helper (check `grep -n "^def " engine/config/source_groups.py`); the intent is "the `timarit` key resolves to exactly the ten sources".

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest -q tests/test_timarit_sources.py`
Expected: FAIL — `ImportError: cannot import name 'JOURNAL_SOURCES'`.

- [ ] **Step 3: Add the dataclass fields and constants**

In `engine/config/sources.py` after `DROPFOLDER_DIR`:

```python
TIMARIT_DROPFOLDER_DIR: str = os.path.join(_DATA_DIR, "dropfolder_timarit")
```

In `SourceConfig`, after `stjornarradid_source`:

```python
    # ── Kind of source (spec 2026-10-03 §4.1) ────────────────────────────────
    kind: str = "ruling"              # 'ruling' | 'book' | 'journal'
    issue_term: str | None = None     # 'hefti' | 'tbl.' — wording in urlausn; None → no issue level
    citation_name: str | None = None  # name used in urlausn; defaults to display_name
    lang_default: str = "is"
    # Year of volume 1. Used ONLY as a last resort when no registry, TOC or printed
    # header gives the volume — and then the article is flagged for review: Úlfljótur
    # skipped a year after 1957, Lögfræðingur's 2nd volume is 2008 (spec §3).
    volume_base_year: int | None = None
```

Set `kind="book"` on the `logfraediritgerdir` and `logfraedibaekur` configs.

- [ ] **Step 4: Add the ten journal configs**

Before the closing `]` of `_SOURCES` (after the `logfraedibaekur` entry):

```python
    # ── Tímarit og fræðigreinar (spec 2026-10-03) ─────────────────────────────
    *[
        SourceConfig(
            short_name=short, display_name=display, abbreviation=abbr,
            instance_tier=1, has_lower_court=False, parse_parties="none",
            verdict_type_default=default_type,
            verdict_types_allowed=["Fræðigrein", "Ritstjórnargrein", "Ritdómur",
                                   "Viðtal", "Frétt", "Dómareifun", "Annað"],
            case_number_prefix="", pdf_crop=None, h1_use_display_name=True,
            case_number_is_title=True, has_footnotes=True,
            kind="journal", issue_term=term, citation_name=cite, volume_base_year=base,
        )
        for short, display, abbr, cite, term, base, default_type in [
            ("timarit_logfraedinga", "Tímarit lögfræðinga", "TL", "Tímarit lögfræðinga", "hefti", 1951, "Fræðigrein"),
            ("ulfljotur", "Úlfljótur", "Úlfljótur", "Úlfljótur", "tbl.", 1947, "Fræðigrein"),
            ("ulfljotur_vefrit", "Úlfljótur — vefrit", "Úlfljótur vefrit", "Úlfljótur vefrit", None, None, "Fræðigrein"),
            ("logmannabladid", "Lögmannablaðið", "Lögmannablaðið", "Lögmannablaðið", "tbl.", 1995, "Annað"),
            ("logretta", "Lögrétta", "Lögrétta", "Lögrétta", "tbl.", 2004, "Fræðigrein"),
            ("logfraedingur", "Lögfræðingur (Þemis, HA)", "Lögfræðingur", "Lögfræðingur", "tbl.", None, "Fræðigrein"),
            ("logbru", "Lögbrú", "Lögbrú", "Lögbrú", "tbl.", None, "Fræðigrein"),
            ("stjornmal_stjornsysla", "Stjórnmál og stjórnsýsla", "Stjórnmál og stjórnsýsla", "Stjórnmál og stjórnsýsla", "tbl.", 2005, "Fræðigrein"),
            ("rannsoknir_lagadeild", "Rannsóknir í félagsvísindum — Lagadeild", "Rannsóknir í félagsvísindum", "Rannsóknir í félagsvísindum", None, None, "Fræðigrein"),
            ("fraedigreinar_ymsar", "Ýmsar fræðigreinar", "Fræðigrein", None, None, None, "Fræðigrein"),
        ]
    ],
```

After `SOURCE_REGISTRY` is defined:

```python
JOURNAL_SOURCES: tuple[str, ...] = tuple(s.short_name for s in _SOURCES if s.kind == "journal")
```

- [ ] **Step 5: Add the scope group**

In `engine/config/source_groups.py`: import `JOURNAL_SOURCES`; add `_TIMARIT_SOURCES = list(JOURNAL_SOURCES)`; include it in `_ASSIGNED`; insert this node into `SCOPE_TREE` after the `baekur` node:

```python
    {
        "key": "timarit", "label": "Tímarit og fræðigreinar",
        "children": [_source_leaf(s) for s in _TIMARIT_SOURCES],
    },
```

and add `"timarit": _TIMARIT_SOURCES` to the `cats` dict in `validate_catalog()`. Update the module docstring's category list (five → six).

- [ ] **Step 6: Add openpyxl and sync**

Add `"openpyxl",` to `dependencies` in `pyproject.toml`; run `uv sync`.

- [ ] **Step 7: Run tests**

Run: `uv run pytest -q tests/test_timarit_sources.py tests/test_sources.py tests/test_source_groups.py`
Expected: PASS. If `test_tree_has_five_categories` fails, update it to six and name the new one.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock engine/config/sources.py engine/config/source_groups.py tests/test_timarit_sources.py tests/test_source_groups.py
git commit -m "feat(timarit): ten journal sources, kind/issue_term/citation_name on SourceConfig, timarit scope group"
```

---

### Task 3: Segmenter `max_chars` guard

**Files:**
- Modify: `engine/processors/segmenter.py`
- Test: `tests/test_segmenter.py`

**Interfaces:**
- Produces: `segment(text, *, target_words=250, max_words=400, max_chars=6000)`; a block exceeding either limit is exploded on sentences, then on whitespace runs.

Why: the longest book passage today is 57,168 chars with only 343 words (an index page padded with whitespace), so the word guard never fired.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_segmenter.py
def test_whitespace_padded_block_is_split_on_chars_not_only_words():
    # 300 short entries separated by 180 spaces: ~600 words, ~60k chars.
    entry = "mál C-120/99 Ítalía gegn ráðinu 264" + " " * 180
    text = entry * 300
    out = segment(text, max_words=400, max_chars=6000)
    assert len(out) > 1
    assert all(len(p.text) <= 6000 for p in out)
    assert all(text[p.char_start:p.char_end] == p.text for p in out)


def test_max_chars_default_leaves_normal_prose_alone():
    text = "Þetta er venjuleg málsgrein. " * 40
    assert len(segment(text)) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest -q tests/test_segmenter.py -k "whitespace_padded or max_chars_default"`
Expected: FAIL — `TypeError: segment() got an unexpected keyword argument 'max_chars'`.

- [ ] **Step 3: Implement**

In `segmenter.py` add `_WS_RUN_RE = re.compile(r'\s+')` next to `_WORD_RE`. Change `_explode` to take `max_chars` and cut on whitespace runs when a sentence piece is too long in characters:

```python
def _explode(text: str, b: _Block, max_words: int, max_chars: int) -> list[_Block]:
    """Split an oversize block into sentence pieces, then word/whitespace pieces,
    so every piece is ≤ max_words AND ≤ max_chars."""
    body = text[b.start:b.end]
    cuts = [0] + [m.end() for m in _SENTENCE_END_RE.finditer(body)] + [len(body)]
    pieces: list[tuple[int, int]] = []
    for a, z in zip(cuts, cuts[1:]):
        seg = body[a:z]
        if _word_count(seg) <= max_words and len(seg) <= max_chars:
            pieces.append((a, z))
            continue
        # One sentence over a limit: cut on whitespace runs, greedily.
        start = a
        acc_words, acc_chars = 0, 0
        for m in _WS_RUN_RE.finditer(seg):
            tok_end = a + m.start()
            tok_words = 1
            if acc_words + tok_words > max_words or (tok_end - start) > max_chars:
                if tok_end > start:
                    pieces.append((start, tok_end))
                start = a + m.start()
                acc_words, acc_chars = 0, 0
            acc_words += tok_words
            acc_chars = tok_end - start
        if start < z:
            pieces.append((start, z))
    out: list[_Block] = []
    for a, z in pieces:
        seg = body[a:z]
        lead = len(seg) - len(seg.lstrip())
        trail = len(seg) - len(seg.rstrip())
        s, e = b.start + a + lead, b.start + z - trail
        if s < e:
            out.append(_Block(s, e, b.kind, b.para, _word_count(text[s:e])))
    return out
```

In `segment()`: signature `def segment(text: str, *, target_words: int = 250, max_words: int = 400, max_chars: int = 6000)`; in `add()`, flush when `cur and (cur_words + b.words > max_words or (b.end - cur[0].start) > max_chars)`; in the loop replace `if b.words > max_words:` with `if b.words > max_words or (b.end - b.start) > max_chars:` and pass `max_chars` to `_explode`. Update the module docstring: "never exceed `max_words` or `max_chars`".

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q tests/test_segmenter.py tests/test_passage_index.py`
Expected: PASS (all existing segmenter tests unchanged).

- [ ] **Step 5: Commit**

```bash
git add engine/processors/segmenter.py tests/test_segmenter.py
git commit -m "fix(segmenter): cap passages by characters too — a whitespace-padded index page was one 57k-char passage"
```

---

### Task 4: Passages with page range, lang-aware lemmas, "bls." anchor

**Files:**
- Modify: `engine/search/passage_index.py`
- Modify: `engine/search/passage_search.py:419-420,498` (projections), `engine/search/queries.py:771` (citation anchor)
- Modify: `scripts/backfill_passages.py` (`_work`, the batch SELECT)
- Test: `tests/test_passage_index.py`

**Interfaces:**
- Consumes: `Document.page_offsets` (Task 1) as `list[dict]` of `{"char": int, "pdf_page": int, "printed": int | None}`.
- Produces: `passage_anchor(layer, para_from, para_to, section_path, ordinal, page_from=None, page_to=None) -> str`; `pages_for_span(page_offsets, char_start, char_end) -> tuple[int | None, int | None]`; `build_passage_rows(summary, body, lower, *, page_offsets=None)` rows gain `page_from`/`page_to`; `lemmatize_rows(rows, lang="is")`; `rebuild_passages(conn, doc_id, *, rows_with_lemmas=None)` reads `lang`, `page_offsets`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_passage_index.py
from engine.search.passage_index import pages_for_span, lemmatize_rows

OFFSETS = [{"char": 0, "pdf_page": 0, "printed": 7}, {"char": 100, "pdf_page": 1, "printed": 8},
           {"char": 250, "pdf_page": 2, "printed": None}, {"char": 400, "pdf_page": 3, "printed": 10}]


def test_pages_for_span_single_and_range():
    assert pages_for_span(OFFSETS, 10, 50) == (7, 7)
    assert pages_for_span(OFFSETS, 90, 120) == (7, 8)
    assert pages_for_span(OFFSETS, 260, 300) == (None, None)   # page without a printed number
    assert pages_for_span(OFFSETS, 120, 450) == (8, 10)        # skips the unnumbered page
    assert pages_for_span(None, 0, 10) == (None, None)
    assert pages_for_span([], 0, 10) == (None, None)


def test_anchor_prefers_paragraph_then_page_then_section():
    assert passage_anchor("body", 4, 4, "Niðurstaða", 3, page_from=12, page_to=12) == "4. mgr."
    assert passage_anchor("body", None, None, "2.1 Söguleg þróun", 3, page_from=12, page_to=12) == "bls. 12"
    assert passage_anchor("body", None, None, None, 3, page_from=12, page_to=14) == "bls. 12–14"
    assert passage_anchor("body", None, None, "2.1 Söguleg þróun", 3, page_from=None, page_to=None) == "2.1 Söguleg þróun"
    assert passage_anchor("summary", None, None, None, 0, page_from=1, page_to=1) == "Reifun"


def test_rows_carry_page_range_from_offsets():
    body = ("Fyrsta síða. " * 8).strip() + "\n\n" + ("Önnur síða. " * 8).strip()
    offsets = [{"char": 0, "pdf_page": 0, "printed": 7},
               {"char": body.index("Önnur"), "pdf_page": 1, "printed": 8}]
    rows = build_passage_rows(None, body, None, page_offsets=offsets)
    assert rows[0]["page_from"] == 7 and rows[-1]["page_to"] == 8


def test_rows_without_offsets_have_null_pages():
    rows = build_passage_rows(None, "Texti hér.", None)
    assert rows[0]["page_from"] is None and rows[0]["page_to"] is None


def test_lemmatize_rows_skips_bin_for_english():
    rows = [{"text": "The courts decided the matter", "ordinal": 0}]
    assert lemmatize_rows(rows, lang="en")[0]["lemmas"] == "the courts decided the matter"
    is_rows = [{"text": "dómarnir ákváðu", "ordinal": 0}]
    assert lemmatize_rows(is_rows, lang="is")[0]["lemmas"] == "dómari ákveða"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_passage_index.py`
Expected: FAIL — `ImportError: cannot import name 'pages_for_span'`.

- [ ] **Step 3: Implement in `passage_index.py`**

```python
def pages_for_span(page_offsets: list[dict] | None, char_start: int, char_end: int
                   ) -> tuple[int | None, int | None]:
    """Printed page range covering [char_start, char_end). Pages whose printed
    number is unknown are skipped; if none of the covered pages has one → (None, None)."""
    if not page_offsets:
        return None, None
    starts = sorted(page_offsets, key=lambda p: p["char"])
    covered: list[int] = []
    for i, p in enumerate(starts):
        p_start = p["char"]
        p_end = starts[i + 1]["char"] if i + 1 < len(starts) else float("inf")
        if p_end <= char_start or p_start >= char_end:
            continue
        if p.get("printed") is not None:
            covered.append(int(p["printed"]))
    if not covered:
        return None, None
    return min(covered), max(covered)


def passage_anchor(layer: str, para_from: int | None, para_to: int | None,
                   section_path: str | None, ordinal: int,
                   page_from: int | None = None, page_to: int | None = None) -> str:
    """Human-readable citation label. Derived, never stored (RENDER layer).
    Order: paragraph number (rulings) → printed page (journals, books) → section → hluti N."""
    if layer == "summary":
        return "Reifun"
    if para_from is not None and para_to is not None:
        return f"{para_from}. mgr." if para_from == para_to else f"{para_from}.–{para_to}. mgr."
    if page_from is not None:
        if page_to is None or page_to == page_from:
            return f"bls. {page_from}"
        return f"bls. {page_from}–{page_to}"
    if section_path:
        return section_path
    return f"hluti {ordinal + 1}"
```

`build_passage_rows(summary, body, lower, *, page_offsets=None)`: for `layer == "body"` set `"page_from", "page_to" = pages_for_span(page_offsets, p.char_start, p.char_end)`, otherwise `None, None`.

```python
_LATIN_WORD_RE = re.compile(r"\w+", re.UNICODE)

def lemmatize_rows(rows: list[dict[str, Any]], lang: str = "is") -> list[dict[str, Any]]:
    """CPU-bound; safe in a worker process. BÍN only for Icelandic — on English
    text it 'lemmatises' real words into noise. Other languages keep their
    lowercased tokens so keyword search still matches literally."""
    if lang == "is":
        return [{**r, "lemmas": lemmatize_text(r["text"])} for r in rows]
    return [{**r, "lemmas": " ".join(t.lower() for t in _LATIN_WORD_RE.findall(r["text"]))} for r in rows]
```

`_INSERT_SQL`: add `page_from, page_to` columns and `:page_from, :page_to` values. `rebuild_passages`: SELECT also `d.lang, d.page_offsets`; when `rows_with_lemmas is None`: `rows_with_lemmas = lemmatize_rows(build_passage_rows(summary, body, lower, page_offsets=page_offsets), lang=lang or "is")`. Rows supplied by a caller that lack the keys get `.setdefault("page_from", None)` / `page_to` before insert.

- [ ] **Step 4: Projections and callers**

- `engine/search/passage_search.py`: add `bp.page_from, bp.page_to` to the SELECT at ~419 and `p.page_from, p.page_to` at ~498; pass `page_from=r["page_from"], page_to=r["page_to"]` in both `passage_anchor(...)` calls (~456, ~508).
- `engine/search/queries.py` ~771: the citations LATERAL that selects `p_layer, para_from, para_to, section_path, ordinal` also selects `p.page_from, p.page_to`; pass them to `passage_anchor`.
- `scripts/backfill_passages.py`: batch SELECT adds `d.lang, d.page_offsets`; `_work` item becomes `(doc_id, summary, body, lower, short_name, lang, page_offsets)` and calls `lemmatize_rows(build_passage_rows(summary, body, lower, page_offsets=page_offsets), lang=lang or "is")`.

- [ ] **Step 5: Run tests**

Run: `uv run --env-file .env pytest -q tests/test_passage_index.py tests/test_passage_search.py tests/test_api_passages.py tests/test_api_citations.py tests/test_passage_index_db.py tests/test_mcp_tools_unit.py`
Expected: PASS. Fixtures that script the passage SELECTs (`_row()` in `test_api_citations.py`, fakes in `test_api_passages.py`/`test_mcp_tools_unit.py`) need `"page_from": None, "page_to": None` added where the query now reads those keys; do that rather than making the code tolerate missing keys.

- [ ] **Step 6: Commit**

```bash
git add engine/search/passage_index.py engine/search/passage_search.py engine/search/queries.py scripts/backfill_passages.py tests/
git commit -m "feat(passages): printed page range per passage, 'bls.' anchor, BÍN only for Icelandic"
```

---

### Task 5: Renderer — journal `urlausn`, filename, markdown header, page markers

**Files:**
- Modify: `engine/processors/renderer.py`
- Test: `tests/test_renderer_journal.py`

**Interfaces:**
- Consumes: `Document.volume/issue/page_start/page_end/plaintiffs/page_offsets`, `SourceConfig.kind/issue_term/citation_name` (Tasks 1–2).
- Produces: `to_urlausn()` and `verdict_filename()` branch on `config.kind == "journal"`; `to_markdown()` writes a journal header and `<!-- bls. N -->` markers; helper `journal_citation(doc, config) -> str`; `_slug(s, n=40) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_renderer_journal.py
from datetime import date

from engine.config.sources import get_config
from engine.database.models import Document
from engine.processors.renderer import to_markdown, to_urlausn, verdict_filename

TL = get_config("timarit_logfraedinga")
ULF = get_config("ulfljotur")
VEF = get_config("ulfljotur_vefrit")


def _doc(**over):
    base = dict(
        case_number="Hvenær er vanhæfi smitandi?", court="TL", verdict_type="Fræðigrein",
        document_date=date(2010, 1, 1), volume=60, issue="1", page_start=7, page_end=47,
        plaintiffs=[{"name": "Kjartan Bjarni Björgvinsson", "lawyer": None}],
        body_text="Fyrsta síða.\n\nÖnnur síða.", external_id="2010-60-1-7",
        page_offsets=[{"char": 0, "pdf_page": 0, "printed": 7}, {"char": 14, "pdf_page": 1, "printed": 8}],
    )
    base.update(over)
    return Document(**base)


def test_urlausn_full_bibliographic_form():
    assert to_urlausn(_doc(), TL) == (
        "Kjartan Bjarni Björgvinsson: „Hvenær er vanhæfi smitandi?“ "
        "Tímarit lögfræðinga, 60. árg. 1. hefti (2010), bls. 7–47")


def test_urlausn_ulfljotur_uses_tbl_and_three_authors_become_ofl():
    d = _doc(court="Úlfljótur", volume=57, issue="2", page_start=165, page_end=192,
             plaintiffs=[{"name": "A B", "lawyer": None}, {"name": "C D", "lawyer": None}, {"name": "E F", "lawyer": None}],
             case_number="Það sem barninu er fyrir bestu", document_date=date(2004, 1, 1))
    assert to_urlausn(d, ULF) == "A B o.fl.: „Það sem barninu er fyrir bestu“ Úlfljótur, 57. árg. 2. tbl. (2004), bls. 165–192"


def test_urlausn_two_authors_both_named_and_missing_parts_omitted():
    d = _doc(plaintiffs=[{"name": "A B", "lawyer": None}, {"name": "C D", "lawyer": None}], issue=None, page_end=None)
    assert to_urlausn(d, TL) == "A B og C D: „Hvenær er vanhæfi smitandi?“ Tímarit lögfræðinga, 60. árg. (2010), bls. 7"


def test_urlausn_no_author_starts_with_title():
    assert to_urlausn(_doc(plaintiffs=None), TL).startswith("„Hvenær er vanhæfi smitandi?“ Tímarit lögfræðinga")


def test_urlausn_vefrit_uses_full_date_and_no_pages():
    d = _doc(court="Úlfljótur vefrit", volume=None, issue=None, page_start=None, page_end=None,
             document_date=date(2022, 1, 21), case_number="Loftslagsváin og dómstólar",
             plaintiffs=[{"name": "Kári Hólmar Ragnarsson", "lawyer": None}])
    assert to_urlausn(d, VEF) == "Kári Hólmar Ragnarsson: „Loftslagsváin og dómstólar“ Úlfljótur vefrit, 21. janúar 2022"


def test_verdict_filename_journal_and_vefrit():
    assert verdict_filename(_doc(), TL) == "TL_60-1_2010_bls-7-47"
    assert verdict_filename(_doc(issue="3-4", page_end=None), TL) == "TL_60-3-4_2010_bls-7"
    d = _doc(court="Úlfljótur vefrit", volume=None, issue=None, page_start=None, page_end=None,
             document_date=date(2022, 1, 21), case_number="Loftslagsváin og dómstólar")
    assert verdict_filename(d, VEF) == "UlfljoturVefrit_x-x_2022_loftslagsvain-og-domstolar"


def test_markdown_header_and_page_markers():
    md = to_markdown(_doc(), TL)
    assert md.startswith("# Hvenær er vanhæfi smitandi?\n")
    assert "Kjartan Bjarni Björgvinsson" in md.split("\n## ")[0]
    assert "Tímarit lögfræðinga, 60. árg. 1. hefti (2010), bls. 7–47" in md
    assert "<!-- bls. 7 -->\nFyrsta síða." in md
    assert "<!-- bls. 8 -->\nÖnnur síða." in md


def test_ruling_urlausn_and_filename_unchanged():
    from engine.config.sources import get_config as gc
    d = Document(court="Lrd.", case_number="177/2024", verdict_type="Dómur", document_date=date(2024, 3, 6))
    assert to_urlausn(d, gc("landsrettur")) == "Lrd. 177/2024 6. mars 2024 – Dómur"
    assert verdict_filename(d, gc("landsrettur")) == "Lrd_177-2024_D_06-03-2024"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_renderer_journal.py`
Expected: FAIL — urlausn assertions (current output `"TL Hvenær er vanhæfi smitandi? 1. janúar 2010 – Fræðigrein"`).

- [ ] **Step 3: Implement**

In `renderer.py`:

```python
def _author_part(plaintiffs) -> str | None:
    names = [p.get("name") for p in (plaintiffs or []) if isinstance(p, dict) and p.get("name")]
    if not names:
        return None
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} og {names[1]}"
    return f"{names[0]} o.fl."


def journal_citation(doc: "Document", config: "SourceConfig") -> str:
    """'Höfundur: „Titill“ Tímarit, 60. árg. 1. hefti (2010), bls. 7–47' (spec §4.5)."""
    author = _author_part(doc.plaintiffs)
    title = f"„{(doc.case_number or '').strip()}“"
    head = f"{author}: {title}" if author else title
    name = config.citation_name or config.display_name
    tail: list[str] = []
    if config.issue_term is None and doc.document_date and doc.volume is None:
        # Web articles cite by date of publication.
        return f"{head} {name}, {_date_str(doc.document_date)}"
    bib: list[str] = []
    if doc.volume is not None:
        bib.append(f"{doc.volume}. árg.")
    if doc.issue and config.issue_term:
        bib.append(f"{doc.issue}. {config.issue_term}")
    year = f"({doc.document_date.year})" if doc.document_date else ""
    bib_s = " ".join(bib)
    tail.append(f"{name}, {bib_s} {year}".replace("  ", " ").strip().rstrip(","))
    if doc.page_start is not None:
        pages = f"bls. {doc.page_start}" if doc.page_end in (None, doc.page_start) else f"bls. {doc.page_start}–{doc.page_end}"
        tail.append(pages)
    return f"{head} " + ", ".join(tail)
```

In `to_urlausn()`, first line: `if config.kind == "journal": return journal_citation(doc, config)`.

```python
_SLUG_RE = re.compile(r"[^a-z0-9]+")

def _slug(s: str, n: int = 40) -> str:
    return _SLUG_RE.sub("-", _ascii(s or "").lower()).strip("-")[:n].rstrip("-") or "x"
```

In `verdict_filename()`, after `court_part`: 

```python
    if config.kind == "journal":
        vol = str(doc.volume) if doc.volume is not None else "x"
        iss = (doc.issue or "x").replace("/", "-").replace(" ", "")
        year = str(doc.document_date.year) if doc.document_date else "x"
        if doc.page_start is not None:
            pages = f"bls-{doc.page_start}" + (f"-{doc.page_end}" if doc.page_end not in (None, doc.page_start) else "")
        else:
            pages = _slug(doc.case_number or doc.external_id or "")
        return f"{court_part}_{vol}-{iss}_{year}_{pages}"
```

(`_ascii("Úlfljótur vefrit")` with spaces removed gives `UlfljoturVefrit`, matching the test.)

In `to_markdown()`, at the top: `if config.kind == "journal": return _journal_markdown(doc, config)`:

```python
def _journal_markdown(doc: "Document", config: "SourceConfig") -> str:
    name = config.citation_name or config.display_name
    authors = ", ".join(p.get("name", "") for p in (doc.plaintiffs or []) if p.get("name"))
    lines = [f"# {doc.case_number or ''}"]
    if authors:
        lines.append(f"**{authors}**")
    lines.append(f"*{journal_citation(doc, config)}*")
    lines.append(f"##### {name} · {doc.verdict_type or config.verdict_type_default}")
    if doc.keywords:
        lines.append(f"### Lykilorð\n{'. '.join(doc.keywords)}")
    if doc.summary:
        lines.append(f"### Ágrip\n{doc.summary}")
    header = "\n\n".join(lines) + "\n\n"
    return header + "## Meginmál\n\n" + _with_page_markers(doc.body_text or "", doc.page_offsets) + "\n"


def _with_page_markers(body: str, page_offsets) -> str:
    """Insert `<!-- bls. N -->` before the first character of every page that has a
    printed number. Derived (RENDER); offsets index body_text, not this string."""
    if not body or not page_offsets:
        return body.strip()
    out, last = [], 0
    for p in sorted(page_offsets, key=lambda p: p["char"]):
        c = min(max(int(p["char"]), 0), len(body))
        out.append(body[last:c])
        if p.get("printed") is not None:
            out.append(f"\n<!-- bls. {p['printed']} -->\n" if c > 0 else f"<!-- bls. {p['printed']} -->\n")
        last = c
    out.append(body[last:])
    return "".join(out).strip()
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q tests/test_renderer_journal.py tests/test_renderer*.py tests/test_search_queries.py`
Expected: PASS; ruling tests unchanged.

- [ ] **Step 5: Commit**

```bash
git add engine/processors/renderer.py tests/test_renderer_journal.py
git commit -m "feat(renderer): bibliographic urlausn, filename and markdown for journal articles"
```

---

### Task 6: `_extract_timarit`, validator rules, upsert parity

**Files:**
- Modify: `engine/processors/extractor.py` (new function + registry entries for all ten journals)
- Modify: `engine/processors/validator.py`
- Modify: `tests/test_import_upsert_parity.py` (add the journal importer once Task 20 exists — note here, do in Task 20)
- Test: `tests/test_extractor_timarit.py`, `tests/test_validator_timarit.py`

**Interfaces:**
- Consumes: manifest article dict (Task 14 shape) — this task defines the raw dict contract:
  `raw = {"title", "authors": list[str], "author_titles": list[str], "article_type", "page_start", "page_end", "lang", "volume", "issue", "year", "date" (ISO str|None), "summary", "keywords": list[str], "pdf_text", "page_offsets", "source_filename", "pdf_pages", "provenance", "checks", "confidence", "leitir_record_id", "xlsx_row", "ocr_source", "date_precision"}`.
- Produces: `_extract_timarit(raw, config) -> dict` returning every `Document` column the importer writes: `case_number, document_date, court, verdict_type, instance_tier, case_type, plaintiffs, defendants, keywords, summary, body_text, lower_body_text, volume, issue, page_start, page_end, lang, page_offsets, raw_api_data`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_extractor_timarit.py
from datetime import date

from engine.config.sources import get_config
from engine.processors.extractor import Extractor

RAW = {
    "title": "Hvenær er vanhæfi smitandi?", "authors": ["Kjartan Bjarni Björgvinsson"], "author_titles": [],
    "article_type": "Fræðigrein", "page_start": 7, "page_end": 47, "lang": "is",
    "volume": 60, "issue": "1", "year": 2010, "date": None, "summary": "Ágrip.", "keywords": ["vanhæfi"],
    "pdf_text": "Texti greinar.", "page_offsets": [{"char": 0, "pdf_page": 0, "printed": 7}],
    "source_filename": "Tímarit Lögfræðinga/TL 2010_01.pdf", "pdf_pages": [8, 48],
    "provenance": {"title": "leitir"}, "checks": {}, "confidence": 0.97,
    "leitir_record_id": "991001868109706886", "xlsx_row": None, "ocr_source": "embedded", "date_precision": "year",
}


def test_extract_maps_bibliographic_fields():
    f = Extractor(get_config("timarit_logfraedinga")).extract(RAW)
    assert f["case_number"] == "Hvenær er vanhæfi smitandi?"
    assert f["plaintiffs"] == [{"name": "Kjartan Bjarni Björgvinsson", "lawyer": None}]
    assert f["court"] == "TL" and f["verdict_type"] == "Fræðigrein"
    assert f["document_date"] == date(2010, 1, 1)
    assert (f["volume"], f["issue"], f["page_start"], f["page_end"], f["lang"]) == (60, "1", 7, 47, "is")
    assert f["page_offsets"] == RAW["page_offsets"]
    assert f["body_text"] == "Texti greinar." and f["summary"] == "Ágrip." and f["keywords"] == ["vanhæfi"]
    assert f["instance_tier"] is None and f["defendants"] is None and f["lower_body_text"] is None
    assert "pdf_text" not in f["raw_api_data"] and f["raw_api_data"]["leitir_record_id"] == "991001868109706886"


def test_extract_exact_date_wins_over_year():
    f = Extractor(get_config("ulfljotur_vefrit")).extract({**RAW, "date": "2022-01-21", "year": 2022})
    assert f["document_date"] == date(2022, 1, 21)


def test_extract_unknown_article_type_falls_back_to_source_default():
    f = Extractor(get_config("logmannabladid")).extract({**RAW, "article_type": None})
    assert f["verdict_type"] == "Annað"


def test_all_journals_registered():
    from engine.config.sources import JOURNAL_SOURCES
    for s in JOURNAL_SOURCES:
        Extractor(get_config(s)).extract(RAW)   # must not raise NotImplementedError
```

```python
# tests/test_validator_timarit.py
from datetime import date

from engine.config.sources import get_config
from engine.database.models import Document
from engine.processors.validator import validate

TL = get_config("timarit_logfraedinga")


def _doc(**over):
    d = dict(case_number="Titill", court="TL", verdict_type="Fræðigrein", document_date=date(2010, 1, 1),
             plaintiffs=[{"name": "A B", "lawyer": None}], body_text="x" * 300, page_start=7, page_end=47, keywords=["a"])
    d.update(over)
    return Document(**d)


def _fields(errors):
    return {e["field"] for e in errors}


def test_clean_article_validates():
    assert validate(_doc(), TL) == []


def test_missing_author_is_an_error_for_fraedigrein_but_not_for_frett():
    assert "plaintiffs" in _fields(validate(_doc(plaintiffs=None), TL))
    assert "plaintiffs" not in _fields(validate(_doc(plaintiffs=None, verdict_type="Frétt"), TL))


def test_page_order_and_title_checked():
    assert "page_start" in _fields(validate(_doc(page_start=50, page_end=47), TL))
    assert "case_number" in _fields(validate(_doc(case_number=None), TL))


def test_no_case_number_shape_or_keyword_rules_for_journals():
    errs = validate(_doc(case_number="37/1993 og annað", keywords=None), TL)
    assert "keywords" not in _fields(errs) and "case_number" not in _fields(errs)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_extractor_timarit.py tests/test_validator_timarit.py`
Expected: FAIL — `NotImplementedError: No extractor registered for 'timarit_logfraedinga'`; validator returns `keywords`/`plaintiffs` differences.

- [ ] **Step 3: Implement the extractor**

In `extractor.py` before the registry:

```python
def _extract_timarit(raw: dict, config: SourceConfig) -> dict:
    """Map one manifest article (scripts/import_timarit.py) to NORM fields.

    Bibliographic values arrive already verified against the PDF (engine/timarit/
    analyze.py); this only shapes them. raw_api_data keeps the manifest line
    minus the text, so the provenance of every value survives in the row.
    """
    title = (raw.get("title") or "").strip() or None
    authors = [a for a in (raw.get("authors") or []) if a]
    iso = raw.get("date")
    doc_date = _parse_icelandic_date(iso) if iso else (date(int(raw["year"]), 1, 1) if raw.get("year") else None)
    article_type = raw.get("article_type") or config.verdict_type_default
    if article_type not in config.verdict_types_allowed:
        article_type = config.verdict_type_default
    raw_meta = {k: v for k, v in raw.items() if k not in ("pdf_text", "page_offsets")}
    return {
        "case_number": title,
        "document_date": doc_date,
        "court": config.abbreviation,
        "verdict_type": article_type,
        "instance_tier": None,
        "case_type": None,
        "plaintiffs": [{"name": a, "lawyer": None} for a in authors] or None,
        "defendants": None,
        "keywords": raw.get("keywords") or None,
        "summary": raw.get("summary") or None,
        "body_text": raw.get("pdf_text") or None,
        "lower_body_text": None,
        "volume": raw.get("volume"),
        "issue": raw.get("issue"),
        "page_start": raw.get("page_start"),
        "page_end": raw.get("page_end"),
        "lang": raw.get("lang") or config.lang_default,
        "page_offsets": raw.get("page_offsets"),
        "raw_api_data": raw_meta,
    }
```

Registry: `**{s: _extract_timarit for s in ("timarit_logfraedinga", "ulfljotur", "ulfljotur_vefrit", "logmannabladid", "logretta", "logfraedingur", "logbru", "stjornmal_stjornsysla", "rannsoknir_lagadeild", "fraedigreinar_ymsar")},` inside `_EXTRACTORS`.

- [ ] **Step 4: Validator**

In `validate()`, add journal branches:
- keywords: `if not doc.keywords and config.kind != "journal": err("keywords", "Missing")`.
- after the parties block: 
```python
    if config.kind == "journal":
        if not doc.plaintiffs and (doc.verdict_type in ("Fræðigrein", "Ritdómur")):
            err("plaintiffs", "Höfund vantar")
        if doc.page_start is not None and doc.page_end is not None and doc.page_end < doc.page_start:
            err("page_start", f"page_end ({doc.page_end}) < page_start ({doc.page_start})")
```
The body-length rule stays (a one-page "Frá ritstjóra" is legitimately flagged, never blocked).

- [ ] **Step 5: Run tests**

Run: `uv run pytest -q tests/test_extractor_timarit.py tests/test_validator_timarit.py tests/test_sources.py tests/test_extractor_baekur.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add engine/processors/extractor.py engine/processors/validator.py tests/test_extractor_timarit.py tests/test_validator_timarit.py
git commit -m "feat(timarit): journal extractor and validation rules"
```

---

### Task 7: `engine/timarit/naming.py` — NFC and filename identity

**Files:**
- Create: `engine/timarit/__init__.py` (empty), `engine/timarit/config.py`, `engine/timarit/naming.py`
- Create: `tests/fixtures/timarit/filenames.txt` (every file under the dropfolder, relative, NFC — generated once by the command in Step 1)
- Test: `tests/test_timarit_naming.py`

**Interfaces:**
- Produces:
```python
@dataclass(frozen=True)
class FileIdentity:
    source: str; rel_path: str          # rel_path is NFC, relative to TIMARIT_DROPFOLDER_DIR
    granularity: str                    # "article" | "issue" | "volume"
    year: int | None; year_end: int | None; volume: int | None; issue: str | None
    page_start: int | None; page_end: int | None
    title_hint: str | None; file_key: str | None   # file_key: numeric stem such as "173"

def nfc(s: str) -> str
def identify(rel_path: str) -> FileIdentity | None
def reject_reason(rel_path: str) -> str | None     # "not_pdf" | "working_files" | "deferred_mde" | "no_pattern" | None
def issue_key(fid: FileIdentity) -> tuple[str, int | None, int | None, str | None]   # (source, year, volume, issue)
def roman_to_int(s: str) -> int
```
- `engine/timarit/config.py` holds: `WEIGHTS = {"title_on_page": 0.35, "printed_page_match": 0.30, "author_on_page": 0.15, "external_record": 0.20}`, `APPROVE_THRESHOLD = 0.85`, `LLM_CONFIDENCE_CAP = 0.70`, `OCR_BIN_RATIO_THRESHOLD: float | None = None`, `PAGE_MAP_MIN_QUALITY = 0.5`, `MANIFEST_ROOT = Path("data/timarit/manifests")`, `UNMATCHED_PATH = Path("data/timarit/_unmatched.yaml")`, `CACHE_DIR = Path(TIMARIT_DROPFOLDER_DIR) / "_meta" / "cache"`, `JOURNALS_ROOT = "Tímarit og fræðigreinar/"`.

- [ ] **Step 1: Generate the filename fixture (read-only)**

```bash
mkdir -p tests/fixtures/timarit
cd /Volumes/RuleOfLaw/Lausnir_Data/dropfolder_timarit && find . -type f ! -name '.DS_Store' | sed 's|^\./||' | uv run --directory /Volumes/RuleOfLaw/Lausnir python -c "import sys,unicodedata; [print(unicodedata.normalize('NFC', l.rstrip('\n'))) for l in sys.stdin]" | sort > /Volumes/RuleOfLaw/Lausnir/tests/fixtures/timarit/filenames.txt
cd /Volumes/RuleOfLaw/Lausnir && wc -l tests/fixtures/timarit/filenames.txt && grep -c '\.pdf$\|\.PDF$' tests/fixtures/timarit/filenames.txt
```
Expected: about 1,690 lines; 1,663 PDFs (case-insensitive).

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_timarit_naming.py
import unicodedata
from collections import Counter
from pathlib import Path

import pytest

from engine.timarit.naming import FileIdentity, identify, issue_key, nfc, reject_reason, roman_to_int

FIX = Path(__file__).parent / "fixtures" / "timarit" / "filenames.txt"
ALL = [l for l in FIX.read_text(encoding="utf-8").splitlines() if l]
PDFS = [p for p in ALL if p.lower().endswith(".pdf")]


def test_nfc_normalises_macos_decomposed_names():
    nfd = unicodedata.normalize("NFD", "Lögmannablaðið")
    assert nfd != "Lögmannablaðið" and nfc(nfd) == "Lögmannablaðið"


def test_every_pdf_is_identified_or_rejected_with_a_reason():
    for p in PDFS:
        fid, why = identify(p), reject_reason(p)
        assert (fid is None) != (why is None), p
        assert why in (None, "working_files", "deferred_mde", "no_pattern"), p
    assert not [p for p in PDFS if reject_reason(p) == "no_pattern"]


def test_non_pdfs_are_rejected_as_not_pdf():
    for p in ALL:
        if not p.lower().endswith(".pdf") and not p.startswith("_meta/"):
            assert reject_reason(p) == "not_pdf", p


def test_counts_per_source_match_the_survey():
    c = Counter((f.source, f.granularity) for f in map(identify, PDFS) if f)
    assert c[("ulfljotur", "article")] == 790 and c[("ulfljotur", "issue")] == 77
    assert c[("ulfljotur_vefrit", "article")] == 42
    assert c[("stjornmal_stjornsysla", "article")] == 329
    assert c[("timarit_logfraedinga", "article")] == 6
    assert c[("timarit_logfraedinga", "issue")] + c[("timarit_logfraedinga", "volume")] == 173
    assert c[("logmannabladid", "issue")] == 117
    assert c[("logretta", "issue")] == 34
    assert c[("logfraedingur", "volume")] == 6
    assert c[("logbru", "issue")] == 2
    assert c[("rannsoknir_lagadeild", "volume")] == 11
    assert c[("fraedigreinar_ymsar", "article")] == 2
    rej = Counter(reject_reason(p) for p in PDFS if identify(p) is None)
    assert rej["working_files"] == 11 and rej["deferred_mde"] == 63


@pytest.mark.parametrize("path,expect", [
    ("Tímarit og fræðigreinar/Tímarit Lögfræðinga/TL 2010_01.pdf",
     dict(source="timarit_logfraedinga", granularity="issue", year=2010, issue="1", volume=None)),
    ("Tímarit og fræðigreinar/Tímarit Lögfræðinga/TL 1985.pdf",
     dict(source="timarit_logfraedinga", granularity="volume", year=1985, issue=None)),
    ("Tímarit og fræðigreinar/Tímarit Lögfræðinga/TL 1971_1972.pdf",
     dict(granularity="volume", year=1971, year_end=1972)),
    ("Tímarit og fræðigreinar/Tímarit Lögfræðinga/TL 1975_03/1975_98.pdf",
     dict(granularity="article", year=1975, issue="3", page_start=98)),
    ("Tímarit og fræðigreinar/Úlfljótur/Tímarit/Ulfljotur_1947 01 arg/173.pdf",
     dict(source="ulfljotur", granularity="article", year=1947, volume=1, file_key="173")),
    ("Tímarit og fræðigreinar/Úlfljótur/Tímarit/Ulfljotur_2002_1_tbl/3076.pdf",
     dict(granularity="article", year=2002, issue="1", volume=None, file_key="3076")),
    ("Tímarit og fræðigreinar/Úlfljótur/Tímarit/Ulfljotur_2002_1_tbl/Ulfljotur_2002_1_tbl TU.pdf",
     dict(granularity="issue", year=2002, issue="1")),
    ("Tímarit og fræðigreinar/Úlfljótur/Tímarit/Ulfljotur_1998 51 arg/Lagadeild á tímamótum.pdf",
     dict(granularity="article", year=1998, volume=51, title_hint="Lagadeild á tímamótum", file_key=None)),
    ("Tímarit og fræðigreinar/Úlfljótur/Tímarit/Ulfljotur_2012_4._tbl.pdf", dict(granularity="issue", year=2012, issue="4")),
    ("Tímarit og fræðigreinar/Úlfljótur/Tímarit/Ulfljotur_2021-1tbl.pdf", dict(granularity="issue", year=2021, issue="1")),
    ("Tímarit og fræðigreinar/Úlfljótur/Tímarit/Ulfljotur_afmaelistimarit_2007.pdf", dict(granularity="issue", year=2007, issue="afmaelisrit")),
    ("Tímarit og fræðigreinar/Úlfljótur/Vefrit/Loftslagsváin og dómstólar.pdf",
     dict(source="ulfljotur_vefrit", granularity="article", title_hint="Loftslagsváin og dómstólar")),
    ("Tímarit og fræðigreinar/Lögmannablaðið/Lögmannablaðið 2011 03 .pdf",
     dict(source="logmannabladid", granularity="issue", year=2011, issue="3")),
    ("Tímarit og fræðigreinar/Lögrétta/Lögrétta 2010 1.tbl.pdf", dict(source="logretta", year=2010, issue="1")),
    ("Tímarit og fræðigreinar/Lögfræðingur UNAK/Lögfræðingur 4 arg 2010.pdf",
     dict(source="logfraedingur", granularity="volume", volume=4, year=2010)),
    ("Tímarit og fræðigreinar/Lögbrú/2013-01-Lögbrú.pdf", dict(source="logbru", year=2013, issue="1")),
    ("Tímarit og fræðigreinar/Rannsóknir í félagsvísingum Lagadeild/Rannsóknir í félagsvísingum Lagadeild 2007 - VIII.pdf",
     dict(source="rannsoknir_lagadeild", granularity="volume", year=2007, volume=8, issue=None)),
    ("Tímarit og fræðigreinar/Rannsóknir í félagsvísingum Lagadeild/Rannsóknir í félagsvísingum Lagadeild 2010 - XI - Trausti Fannar.pdf",
     dict(year=2010, volume=11, issue="trausti-fannar")),
    ("Tímarit og fræðigreinar/Stjórnmál og Stjórnsýsla/Vol01/81-106_The_Icelandic_Supreme_Court_and_the_Cons.pdf",
     dict(source="stjornmal_stjornsysla", granularity="article", volume=1, year=None, page_start=81, page_end=106,
          title_hint="The Icelandic Supreme Court and the Cons")),
    ("Tímarit og fræðigreinar/Stjórnmál og Stjórnsýsla/Vol14 Power and democracy in Iceland/1-34_The_Icelandic_power_structure_revisited.pdf",
     dict(volume=14, issue="power-and-democracy-in-iceland", page_start=1, page_end=34)),
    ("Tímarit og fræðigreinar/Ýmsar fræðigreinar/Þjóðareign - þýðing og áhrif stjórnarskrárákvæðis.pdf",
     dict(source="fraedigreinar_ymsar", granularity="article")),
])
def test_identify_examples(path, expect):
    fid = identify(path)
    assert fid is not None, path
    for k, v in expect.items():
        assert getattr(fid, k) == v, (k, getattr(fid, k))


def test_identify_sands_keeps_filename_pages_as_hint_only():
    fid = identify("Tímarit og fræðigreinar/Stjórnmál og Stjórnsýsla/Vol13/189-2010_The_opening_of_Costco_in_Iceland.pdf")
    assert (fid.page_start, fid.page_end) == (189, 2010)   # verified against the PDF later, not trusted here


def test_rejections():
    assert reject_reason("Tímarit og fræðigreinar/Lögfræðingur UNAK/Önnur Gögn/Hvað-með-föðurinn.pdf") == "working_files"
    assert reject_reason("MDE Dómareifanir 2005-2021/Domareifanir_2015_1.pdf") == "deferred_mde"
    assert reject_reason("Tímarit og fræðigreinar/Lögbrú/notes.docx") == "not_pdf"
    assert reject_reason("Tímarit og fræðigreinar/Lögbrú/2013-01-Lögbrú.pdf") is None


def test_issue_key_groups_articles_of_one_issue():
    a = identify("Tímarit og fræðigreinar/Úlfljótur/Tímarit/Ulfljotur_2002_1_tbl/3076.pdf")
    b = identify("Tímarit og fræðigreinar/Úlfljótur/Tímarit/Ulfljotur_2002_1_tbl/3077.pdf")
    assert issue_key(a) == issue_key(b) == ("ulfljotur", 2002, None, "1")


def test_roman():
    assert [roman_to_int(s) for s in ("IV", "VIII", "IX", "XII")] == [4, 8, 9, 12]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_timarit_naming.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.timarit'`.

- [ ] **Step 4: Implement `config.py` and `naming.py`**

```python
# engine/timarit/config.py
"""Tunables for the journal pipeline (spec 2026-10-03 §5.4, §6). The defaults
here are starting points; Task 21 (pilot) replaces them with the user's choices."""
from pathlib import Path

from engine.config.sources import TIMARIT_DROPFOLDER_DIR

JOURNALS_ROOT = "Tímarit og fræðigreinar/"
WEIGHTS = {"title_on_page": 0.35, "printed_page_match": 0.30, "author_on_page": 0.15, "external_record": 0.20}
APPROVE_THRESHOLD = 0.85
LLM_CONFIDENCE_CAP = 0.70
OCR_BIN_RATIO_THRESHOLD: float | None = None   # None → never re-OCR a PDF that has a text layer
PAGE_MAP_MIN_QUALITY = 0.5
MANIFEST_ROOT = Path("data/timarit/manifests")
UNMATCHED_PATH = Path("data/timarit/_unmatched.yaml")
CACHE_DIR = Path(TIMARIT_DROPFOLDER_DIR) / "_meta" / "cache"
XLSX_PATH = Path(TIMARIT_DROPFOLDER_DIR) / "_meta" / "Ulfljotur_efnisyfirlit.xlsx"
```

```python
# engine/timarit/naming.py
"""Filename and folder patterns of the user's archive → FileIdentity (spec §5.1, §7.1).

Every pattern is anchored on the archive layout surveyed 2026-10-01; a file that
matches nothing is reported, never guessed at.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from engine.timarit.config import JOURNALS_ROOT

_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def roman_to_int(s: str) -> int:
    total = 0
    for i, ch in enumerate(s.upper()):
        v = _ROMAN[ch]
        total += -v if i + 1 < len(s) and _ROMAN[s[i + 1].upper()] > v else v
    return total


def _slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


@dataclass(frozen=True)
class FileIdentity:
    source: str
    rel_path: str
    granularity: str                    # article | issue | volume
    year: int | None = None
    year_end: int | None = None
    volume: int | None = None
    issue: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    title_hint: str | None = None
    file_key: str | None = None


J = re.escape(JOURNALS_ROOT)
_TL_TOP = re.compile(J + r"Tímarit Lögfræðinga/TL (\d{4})(?:_(\d{4}))?(?:_(\d{2}))?\.pdf$")
_TL_ART = re.compile(J + r"Tímarit Lögfræðinga/TL (\d{4})_(\d{2})/(\d{4})_(\d+)\.pdf$")
_ULF_DIR = re.compile(J + r"Úlfljótur/Tímarit/Ulfljotur_(\d{4})(?: (\d{2}) arg|_(\d)_tbl)/([^/]+)\.pdf$")
_ULF_ISSUE = re.compile(J + r"Úlfljótur/Tímarit/Ulfljotur_(\d{4})[_-](\d)\.?_?tbl\.pdf$")
_ULF_AFM = re.compile(J + r"Úlfljótur/Tímarit/Ulfljotur_afmaelistimarit_(\d{4})\.pdf$")
_VEFRIT = re.compile(J + r"Úlfljótur/Vefrit/([^/]+)\.pdf$")
_LMBL = re.compile(J + r"Lögmannablaðið/Lögmannablaðið (\d{4}) (\d{2}) ?\.pdf$")
_LOGR = re.compile(J + r"Lögrétta/Lögrétta (\d{4}) (\d)\.tbl\.pdf$")
_LOGF = re.compile(J + r"Lögfræðingur UNAK/Lögfræðingur (\d) arg (\d{4})\.pdf$")
_LOGB = re.compile(J + r"Lögbrú/(\d{4})-(\d{2})-Lögbrú\.pdf$")
_RANN = re.compile(J + r"Rannsóknir í félagsvísingum Lagadeild/Rannsóknir í félagsvísingum Lagadeild (\d{4}) - ([IVXLC]+)(?: - (.+))?\.pdf$")
_SOGS = re.compile(J + r"Stjórnmál og Stjórnsýsla/Vol(\d{2})(?: ([^/]+))?/(\d+)-(\d+)_(.+)\.pdf$")
_YMSAR = re.compile(J + r"Ýmsar fræðigreinar/([^/]+)\.pdf$")
_NUMERIC_KEY = re.compile(r"^\d+(?:-\d+)*$")


def reject_reason(rel_path: str) -> str | None:
    p = nfc(rel_path)
    if not p.lower().endswith(".pdf"):
        return "not_pdf"
    if p.startswith("MDE "):
        return "deferred_mde"
    if "/Önnur Gögn/" in p:
        return "working_files"
    return None if identify(p) is not None else "no_pattern"


def identify(rel_path: str) -> FileIdentity | None:
    p = nfc(rel_path)
    if not p.lower().endswith(".pdf") or p.startswith("MDE ") or "/Önnur Gögn/" in p:
        return None
    if m := _TL_ART.match(p):
        return FileIdentity("timarit_logfraedinga", p, "article", year=int(m[1]), issue=str(int(m[2])), page_start=int(m[4]))
    if m := _TL_TOP.match(p):
        issue = str(int(m[3])) if m[3] else None
        return FileIdentity("timarit_logfraedinga", p, "issue" if issue else "volume",
                            year=int(m[1]), year_end=int(m[2]) if m[2] else None, issue=issue)
    if m := _ULF_DIR.match(p):
        year, vol, iss, stem = int(m[1]), m[2], m[3], m[4]
        if " TU" in stem or stem.lower().startswith("ulfljotur_"):
            return FileIdentity("ulfljotur", p, "issue", year=year, volume=int(vol) if vol else None, issue=iss)
        key = stem if _NUMERIC_KEY.match(stem) else None
        return FileIdentity("ulfljotur", p, "article", year=year, volume=int(vol) if vol else None, issue=iss,
                            title_hint=None if key else stem, file_key=key)
    if m := _ULF_ISSUE.match(p):
        return FileIdentity("ulfljotur", p, "issue", year=int(m[1]), issue=m[2])
    if m := _ULF_AFM.match(p):
        return FileIdentity("ulfljotur", p, "issue", year=int(m[1]), issue="afmaelisrit")
    if m := _VEFRIT.match(p):
        return FileIdentity("ulfljotur_vefrit", p, "article", title_hint=m[1])
    if m := _LMBL.match(p):
        return FileIdentity("logmannabladid", p, "issue", year=int(m[1]), issue=str(int(m[2])))
    if m := _LOGR.match(p):
        return FileIdentity("logretta", p, "issue", year=int(m[1]), issue=m[2])
    if m := _LOGF.match(p):
        return FileIdentity("logfraedingur", p, "volume", year=int(m[2]), volume=int(m[1]))
    if m := _LOGB.match(p):
        return FileIdentity("logbru", p, "issue", year=int(m[1]), issue=str(int(m[2])))
    if m := _RANN.match(p):
        return FileIdentity("rannsoknir_lagadeild", p, "volume", year=int(m[1]), volume=roman_to_int(m[2]),
                            issue=_slug(m[3]) if m[3] else None)
    if m := _SOGS.match(p):
        return FileIdentity("stjornmal_stjornsysla", p, "article", volume=int(m[1]),
                            issue=_slug(m[2]) if m[2] else None, page_start=int(m[3]), page_end=int(m[4]),
                            title_hint=m[5].replace("_", " ").strip())
    if m := _YMSAR.match(p):
        return FileIdentity("fraedigreinar_ymsar", p, "article", title_hint=m[1])
    return None


def issue_key(fid: FileIdentity) -> tuple[str, int | None, int | None, str | None]:
    return (fid.source, fid.year, fid.volume, fid.issue)
```

- [ ] **Step 5: Run tests; fix pattern misses**

Run: `uv run pytest -q tests/test_timarit_naming.py -x`
Expected: PASS. If `test_every_pdf_is_identified_or_rejected_with_a_reason` fails, print the offending paths (`[p for p in PDFS if reject_reason(p) == "no_pattern"]`) and extend the pattern that should have matched — do not loosen `_NUMERIC_KEY`.

- [ ] **Step 6: Commit**

```bash
git add engine/timarit/__init__.py engine/timarit/config.py engine/timarit/naming.py tests/fixtures/timarit/filenames.txt tests/test_timarit_naming.py
git commit -m "feat(timarit): filename identity for every archive pattern, NFC-normalised, with rejection reasons"
```

---

### Task 8: `engine/timarit/pagemap.py` — printed page numbers

**Files:**
- Create: `engine/timarit/pagemap.py`
- Test: `tests/test_timarit_pagemap.py`

**Interfaces:**
- Produces:
```python
@dataclass
class PageMap:
    offset: int | None            # printed = pdf_index + offset
    printed: list[int | None]     # per pdf page: read number, or inferred from offset, or None
    read: list[bool]              # True where a number was actually read
    quality: float                # share of pages whose read number agrees with the offset
    def pdf_index_for(self, printed_page: int) -> int | None
    def as_dict(self) -> dict     # {"offset": …, "quality": …}

PATTERNS: dict[str, list[re.Pattern]]      # per source short_name; "default" key
def read_page_numbers(page_texts: list[str], patterns: list[re.Pattern]) -> list[int | None]
def fit_offset(read: list[int | None]) -> int | None
def build_page_map(page_texts: list[str], source: str) -> PageMap
```

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_timarit_pagemap.py
from engine.timarit.pagemap import PATTERNS, build_page_map, fit_offset, read_page_numbers


def _pages(nums, header=""):
    return [f"{header}{n}\nTexti síðu.\nMeira." if n is not None else "Kápa án tölu" for n in nums]


def test_read_bare_number_first_or_last_line():
    pages = ["7\nTexti", "Texti\n8", "Enginn\ntala hér", "  9  \nTexti"]
    assert read_page_numbers(pages, PATTERNS["default"]) == [7, 8, None, 9]


def test_fit_offset_takes_the_majority_and_ignores_outliers():
    assert fit_offset([None, 7, 8, 9, 100, 11]) == 6          # idx 1→7, 2→8, 3→9, 5→11 all offset 6
    assert fit_offset([None, None, None]) is None


def test_fit_offset_refuses_when_fewer_than_three_agree():
    assert fit_offset([None, 7, None, 22]) is None


def test_build_map_infers_missing_pages_and_scores_quality():
    pm = build_page_map(_pages([None, 2, 3, None, 5, 6]), "timarit_logfraedinga")
    assert pm.offset == 1
    assert pm.printed == [1, 2, 3, 4, 5, 6]
    assert pm.read == [False, True, True, False, True, True]
    assert abs(pm.quality - 4 / 6) < 1e-9
    assert pm.pdf_index_for(5) == 4 and pm.pdf_index_for(99) is None


def test_quality_counts_consistent_pages_only():
    pm = build_page_map(_pages([2, 3, 4, 40, 6]), "default")
    assert pm.offset == 2 and abs(pm.quality - 4 / 5) < 1e-9
    assert pm.printed[3] == 5           # outlier replaced by the inferred value


def test_unmappable_document_has_no_offset_and_zero_quality():
    pm = build_page_map(["Kápa", "Auglýsing", "Texti"], "default")
    assert pm.offset is None and pm.quality == 0.0 and pm.printed == [None, None, None]


def test_source_specific_patterns():
    assert read_page_numbers(["Úlfljótur 173\nFrá ritstjóra"], PATTERNS["ulfljotur"]) == [173]
    assert read_page_numbers(["a 9\nÚrræði lánveitanda"], PATTERNS["logretta"]) == [9]
    assert read_page_numbers(["3 / LÖGBRÚ\nGREIN BLS."], PATTERNS["logbru"]) == [3]
    assert read_page_numbers(["81 Samræmi laga og stjórnarskrár\nSvandís"], PATTERNS["stjornmal_stjornsysla"]) == [81]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_timarit_pagemap.py`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# engine/timarit/pagemap.py
"""Printed page numbers of a PDF (spec §5.2).

Reads the number from the first/last lines of each page with per-journal
patterns, fits the single offset printed = pdf_index + k that most pages
support, and infers the rest. Pages that disagree with the fit are treated as
misreads (adverts, covers, OCR noise). Nothing is stored for a document whose
numbers cannot be fitted — the anchor then falls back to the section.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

_BARE = re.compile(r"^\s*(\d{1,4})\s*$")
_LEADING = re.compile(r"^\s*(\d{1,4})\s+\S")          # "81 Samræmi laga …" (first line only)

PATTERNS: dict[str, list[re.Pattern]] = {
    "default": [_BARE],
    "ulfljotur": [_BARE, re.compile(r"^\s*Úlfljótur\s+(\d{1,4})\s*$", re.I), re.compile(r"^\s*(\d{1,4})\s+Úlfljótur\s*$", re.I)],
    "logretta": [_BARE, re.compile(r"^\s*[a-z]\s+(\d{1,4})\s*$")],
    "logbru": [_BARE, re.compile(r"^\s*(\d{1,4})\s*/\s*LÖGBRÚ\s*$", re.I)],
    "stjornmal_stjornsysla": [_BARE, _LEADING],
    "timarit_logfraedinga": [_BARE, _LEADING],
}
MIN_AGREEING = 3


def _candidates(page_text: str) -> list[str]:
    lines = [l for l in page_text.splitlines() if l.strip()]
    if not lines:
        return []
    return [lines[0], lines[-1]] if len(lines) > 1 else [lines[0]]


def read_page_numbers(page_texts: list[str], patterns: list[re.Pattern]) -> list[int | None]:
    out: list[int | None] = []
    for t in page_texts:
        found = None
        for i, line in enumerate(_candidates(t)):
            for pat in patterns:
                if pat is _LEADING and i != 0:
                    continue
                m = pat.match(line)
                if m:
                    found = int(m.group(1))
                    break
            if found is not None:
                break
        out.append(found)
    return out


def fit_offset(read: list[int | None]) -> int | None:
    votes = Counter(n - i for i, n in enumerate(read) if n is not None)
    if not votes:
        return None
    offset, support = votes.most_common(1)[0]
    return offset if support >= MIN_AGREEING else None


@dataclass
class PageMap:
    offset: int | None
    printed: list[int | None]
    read: list[bool]
    quality: float

    def pdf_index_for(self, printed_page: int) -> int | None:
        if self.offset is None:
            return None
        idx = printed_page - self.offset
        return idx if 0 <= idx < len(self.printed) else None

    def as_dict(self) -> dict:
        return {"offset": self.offset, "quality": round(self.quality, 3), "pages": len(self.printed)}


def build_page_map(page_texts: list[str], source: str) -> PageMap:
    patterns = PATTERNS.get(source, PATTERNS["default"])
    read = read_page_numbers(page_texts, patterns)
    offset = fit_offset(read)
    n = len(page_texts)
    if offset is None:
        return PageMap(None, [None] * n, [False] * n, 0.0)
    consistent = [r is not None and r - i == offset for i, r in enumerate(read)]
    printed = [i + offset if i + offset > 0 else None for i in range(n)]
    return PageMap(offset, printed, consistent, sum(consistent) / n if n else 0.0)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q tests/test_timarit_pagemap.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/timarit/pagemap.py tests/test_timarit_pagemap.py
git commit -m "feat(timarit): printed page map with offset fit and quality score"
```

---

### Task 9: `parse_pdf_pages()` and `engine/timarit/extract.py`

**Files:**
- Modify: `engine/processors/pdf_parser.py` (factor the page loop; add `parse_pdf_pages`)
- Create: `engine/timarit/extract.py`
- Test: `tests/test_pdf_parser_pages.py`, `tests/test_timarit_extract.py`

**Interfaces:**
- Produces in `pdf_parser.py`: `parse_pdf_pages(content: bytes, *, footnotes: bool = False, page_filter: Callable[[list[str]], list[str]] | None = None) -> tuple[str, list[tuple[int, int]]]` — body text plus `(pdf_page_index, char_start)` for every page that contributed text, in document order. `parse_pdf()` keeps its signature and behaviour.
- Produces in `extract.py`:
```python
@dataclass
class ExtractedText:
    body: str
    page_offsets: list[dict]      # {"char", "pdf_page", "printed"}
    ocr_source: str               # "embedded" | "tesseract-isl"
    bin_ratio: float              # share of sampled words BÍN knows (0..1)
    page_texts: list[str]         # raw per-page text (for page map / verification)

def strip_running_lines(pages: list[str], *, min_share: float = 0.3, min_pages: int = 3) -> list[str]
def dehyphenate(text: str) -> str
def bin_known_ratio(text: str, sample_words: int = 2000) -> float
def has_text_layer(page_texts: list[str]) -> bool
def extract_article(pdf_bytes: bytes, *, source: str, footnotes: bool, ocr: Callable[[bytes], str | None] | None = None,
                    ocr_bin_threshold: float | None = None) -> ExtractedText
```
Tests build PDFs with PyMuPDF (`fitz.open(); page.insert_text(...)`) so no fixture binaries are needed.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pdf_parser_pages.py
import fitz

from engine.processors.pdf_parser import parse_pdf, parse_pdf_pages


def _pdf(pages: list[str]) -> bytes:
    doc = fitz.open()
    for t in pages:
        p = doc.new_page()
        p.insert_text((72, 72), t, fontsize=11)
    return doc.tobytes()


def test_offsets_point_at_each_pages_first_char():
    body, offs = parse_pdf_pages(_pdf(["Fyrsta síða.", "Önnur síða.", "Þriðja síða."]))
    assert [i for i, _ in offs] == [0, 1, 2]
    for (i, c), word in zip(offs, ("Fyrsta", "Önnur", "Þriðja")):
        assert body[c:].startswith(word), (i, body[c:c + 20])


def test_blank_page_contributes_no_offset():
    body, offs = parse_pdf_pages(_pdf(["Fyrsta.", "", "Þriðja."]))
    assert [i for i, _ in offs] == [0, 2]


def test_page_filter_runs_before_join_and_offsets_follow():
    drop_first_line = lambda pages: [p.split("\n", 1)[1] if "\n" in p else p for p in pages]
    body, offs = parse_pdf_pages(_pdf(["HAUS\nEfni eitt.", "HAUS\nEfni tvö."]), page_filter=drop_first_line)
    assert "HAUS" not in body and body[offs[1][1]:].startswith("Efni tvö")


def test_parse_pdf_unchanged():
    pdf = _pdf(["Fyrsta síða.", "Önnur síða."])
    assert parse_pdf(pdf) == parse_pdf_pages(pdf)[0]
```

```python
# tests/test_timarit_extract.py
import fitz

from engine.timarit.extract import (
    bin_known_ratio, dehyphenate, extract_article, has_text_layer, strip_running_lines,
)


def _pdf(pages):
    doc = fitz.open()
    for t in pages:
        doc.new_page().insert_text((72, 72), t, fontsize=11)
    return doc.tobytes()


def test_strip_running_lines_removes_repeated_headers_and_page_numbers():
    pages = [f"Tímarit lögfræðinga\nEfni {i}.\n{i + 6}" for i in range(6)]
    out = strip_running_lines(pages)
    assert all("Tímarit lögfræðinga" not in p for p in out)
    assert all(not p.splitlines()[-1].strip().isdigit() for p in out)
    assert out[2].strip() == "Efni 2."


def test_strip_running_lines_keeps_lines_that_appear_rarely():
    pages = ["Sérstök fyrirsögn\nEfni.", "Önnur\nEfni.", "Þriðja\nEfni.", "Fjórða\nEfni."]
    assert strip_running_lines(pages)[0].startswith("Sérstök fyrirsögn")


def test_dehyphenate_joins_lowercase_continuations_only():
    assert dehyphenate("breyting-\nar á lögum") == "breytingar á lögum"
    assert dehyphenate("Reykjavíkur-\nBorg") == "Reykjavíkur-\nBorg"        # capital: a real hyphenated name
    assert dehyphenate("nr. 42-\n2000") == "nr. 42-\n2000"


def test_bin_ratio_separates_icelandic_from_noise():
    assert bin_known_ratio("dómstóll kvað upp úrskurð í málinu") > 0.8
    assert bin_known_ratio("T^/Teginreglur xq zzv fjkl") < 0.5


def test_has_text_layer():
    assert has_text_layer(["Texti sem er lengri en fimmtíu stafir á þessari síðu, vel og lengi."])
    assert not has_text_layer(["", "  ", "ab"])


def test_extract_article_builds_page_offsets_with_printed_numbers():
    pdf = _pdf(["7\nFyrsta síða greinar.", "8\nÖnnur síða greinar.", "9\nÞriðja síða."])
    ex = extract_article(pdf, source="timarit_logfraedinga", footnotes=False)
    assert ex.ocr_source == "embedded"
    assert [p["printed"] for p in ex.page_offsets] == [7, 8, 9]
    assert ex.body[ex.page_offsets[1]["char"]:].startswith("Önnur síða")
    assert "7\n" not in ex.body          # page numbers stripped as running lines


def test_extract_article_falls_back_to_ocr_when_no_text_layer():
    pdf = _pdf(["", "", ""])
    ex = extract_article(pdf, source="default", footnotes=False, ocr=lambda b: "OCR texti síðu.")
    assert ex.ocr_source == "tesseract-isl" and ex.body == "OCR texti síðu."
    assert ex.page_offsets == []       # OCR output carries no page boundaries


def test_extract_article_reocrs_below_bin_threshold():
    pdf = _pdf(["xq zzv fjkl qwrt", "zzz qqq xxx"])
    ex = extract_article(pdf, source="default", footnotes=False, ocr=lambda b: "góður texti", ocr_bin_threshold=0.5)
    assert ex.ocr_source == "tesseract-isl"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_pdf_parser_pages.py tests/test_timarit_extract.py`
Expected: FAIL — `ImportError: cannot import name 'parse_pdf_pages'`.

- [ ] **Step 3: Refactor `pdf_parser.py`**

Extract the body of the `with pdfplumber.open(...)` loop into:

```python
def _extract_pages(content: bytes, *, header_pt, footer_pt, skip_header_on_first, heading_sizes,
                   heading_fonts, extract_tables, footnotes, _plain_structured
                   ) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """Per-page text as (pdf_page_index, text) for pages that have text, plus
    the collected footnotes. Exactly the loop parse_pdf has always run."""
```

`parse_pdf()` becomes: `indexed, notes = _extract_pages(...)`; `pages = [t for _, t in indexed]`; the rest unchanged.

```python
def _join_pages_with_offsets(pages: list[str]) -> tuple[str, list[int]]:
    """_join_pages, also returning the start offset of every page in the result.
    Same three rules; additionally a page ending in '-' followed by a lowercase
    start is joined without the newline (word broken across the page break)."""
    if not pages:
        return "", []
    result, starts = pages[0], [0]
    for nxt in pages[1:]:
        first_word = nxt.split()[0] if nxt.split() else ""
        if _NEW_PARA.match(nxt) or _HEADING_LINE.match(nxt):
            sep = "\n\n"
        elif first_word and first_word[0].islower():
            sep = "" if result.endswith("-") else "\n"
            if sep == "":
                result = result[:-1]
        else:
            sep = "\n\n"
        result += sep
        starts.append(len(result))
        result += nxt
    return result, starts


def parse_pdf_pages(content: bytes, *, footnotes: bool = False,
                    page_filter=None) -> tuple[str, list[tuple[int, int]]]:
    """parse_pdf for journals: returns (body, [(pdf_page_index, char_start), …]).
    `page_filter` sees the list of page texts before joining (running-line strip,
    dehyphenation) and must return a list of the same length."""
    indexed, notes = _extract_pages(content, header_pt=0, footer_pt=0, skip_header_on_first=False,
                                    heading_sizes=None, heading_fonts=None, extract_tables=False,
                                    footnotes=footnotes, _plain_structured=False)
    idxs = [i for i, _ in indexed]
    pages = [t for _, t in indexed]
    if page_filter is not None:
        pages = page_filter(pages)
        if len(pages) != len(idxs):
            raise ValueError("page_filter must preserve the number of pages")
    keep = [(i, p) for i, p in zip(idxs, pages) if p and p.strip()]
    body, starts = _join_pages_with_offsets([p.strip() for _, p in keep])
    if footnotes and not _numbering_restarts(body):
        block = _render_footnotes(notes, body)
        if block:
            body = f"{body}\n\n{block}"
    return body, [(i, s) for (i, _), s in zip(keep, starts)]
```

(When `_numbering_restarts(body)` is true the per-contribution path is not taken here: a journal article is one contribution; notes are simply appended as collected.)

- [ ] **Step 4: Implement `extract.py`**

```python
# engine/timarit/extract.py
"""Page-wise text extraction for journal articles (spec §6)."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

from engine.processors.lemmatizer import _WORD_RE, _bin
from engine.processors.pdf_parser import parse_pdf_pages
from engine.timarit.pagemap import build_page_map

_HYPHEN_BREAK = re.compile(r"(\w)-\n([a-záéíóúýðþæö])")
_DIGITS = re.compile(r"\d+")
_MIN_PAGE_CHARS = 50


@dataclass
class ExtractedText:
    body: str
    page_offsets: list[dict] = field(default_factory=list)
    ocr_source: str = "embedded"
    bin_ratio: float = 0.0
    page_texts: list[str] = field(default_factory=list)


def _edge_lines(page: str) -> list[str]:
    lines = [l.strip() for l in page.splitlines() if l.strip()]
    return lines[:2] + lines[-2:] if len(lines) > 4 else lines


def _shape(line: str) -> str:
    return _DIGITS.sub("#", line).strip().lower()


def strip_running_lines(pages: list[str], *, min_share: float = 0.3, min_pages: int = 3) -> list[str]:
    """Drop header/footer lines that recur on ≥ min_share of pages (and ≥ min_pages):
    journal name, author running head, bare page numbers (digits are shape-folded)."""
    if len(pages) < min_pages:
        return pages
    freq = Counter()
    for p in pages:
        for shape in set(map(_shape, _edge_lines(p))):
            freq[shape] += 1
    running = {s for s, n in freq.items() if n >= max(min_pages, int(round(min_share * len(pages))))}
    out = []
    for p in pages:
        lines = p.splitlines()
        edge_idx = set(range(min(2, len(lines)))) | set(range(max(0, len(lines) - 2), len(lines)))
        kept = [l for i, l in enumerate(lines) if not (i in edge_idx and l.strip() and _shape(l) in running)]
        out.append("\n".join(kept))
    return out


def dehyphenate(text: str) -> str:
    return _HYPHEN_BREAK.sub(r"\1\2", text)


def bin_known_ratio(text: str, sample_words: int = 2000) -> float:
    words = [w for w in _WORD_RE.findall(text) if len(w) > 2][:sample_words]
    if not words:
        return 0.0
    known = sum(1 for w in words if _bin.lookup(w)[1])
    return known / len(words)


def has_text_layer(page_texts: list[str]) -> bool:
    return any(len(t.strip()) >= _MIN_PAGE_CHARS for t in page_texts)


def _page_texts(pdf_bytes: bytes) -> list[str]:
    import fitz
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        return [page.get_text() for page in doc]


def extract_article(pdf_bytes: bytes, *, source: str, footnotes: bool,
                    ocr: Callable[[bytes], str | None] | None = None,
                    ocr_bin_threshold: float | None = None) -> ExtractedText:
    page_texts = _page_texts(pdf_bytes)
    page_map = build_page_map(page_texts, source)

    def _ocr() -> ExtractedText:
        text = (ocr(pdf_bytes) if ocr else None) or ""
        return ExtractedText(text.strip(), [], "tesseract-isl", bin_known_ratio(text), page_texts)

    if not has_text_layer(page_texts):
        return _ocr()

    def _filter(pages: list[str]) -> list[str]:
        return [dehyphenate(p) for p in strip_running_lines(pages)]

    body, offsets = parse_pdf_pages(pdf_bytes, footnotes=footnotes, page_filter=_filter)
    ratio = bin_known_ratio(body)
    if ocr_bin_threshold is not None and ratio < ocr_bin_threshold and ocr is not None:
        return _ocr()
    page_offsets = [{"char": c, "pdf_page": i, "printed": page_map.printed[i] if i < len(page_map.printed) else None}
                    for i, c in offsets]
    return ExtractedText(body, page_offsets, "embedded", ratio, page_texts)
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest -q tests/test_pdf_parser_pages.py tests/test_timarit_extract.py tests/test_pdf_parser.py`
Expected: PASS; `test_pdf_parser.py` unchanged (the refactor must not alter `parse_pdf` output).

- [ ] **Step 6: Commit**

```bash
git add engine/processors/pdf_parser.py engine/timarit/extract.py tests/test_pdf_parser_pages.py tests/test_timarit_extract.py
git commit -m "feat(timarit): page-wise extraction with offsets, running-line strip, dehyphenation and OCR gate"
```

---

### Task 10: `engine/timarit/toc.py` — table-of-contents parser

**Files:**
- Create: `engine/timarit/toc.py`
- Create: `tests/fixtures/timarit/toc/` with four real TOC texts (Step 1)
- Test: `tests/test_timarit_toc.py`

**Interfaces:**
- Produces:
```python
@dataclass
class TocEntry:
    title: str; authors: list[str]; page_start: int | None
    section: str | None = None        # TOC section heading in effect ("Fræðigreinar", "Ritdómar", "1. hefti")
    issue: str | None = None          # for annual volumes: hefti the entry belongs to

def find_toc_pages(page_texts: list[str], max_scan: int = 6) -> list[int]
def parse_toc(text: str, *, style: str) -> list[TocEntry]      # style: "page_first" | "page_last"
def toc_style(source: str) -> str
```
`page_first`: `7 Kjartan Bjarni Björgvinsson: Hvenær er …` (TL issues, where the number precedes the entry). `page_last`: `Markmið lagakennslu ........ 1`, `Pétur Fannar Gíslason, Um sönnunarkröfur … 5`, `Elín Sif Kjartansdóttir Frá ritstjóra . . . . 5` (TL annual volumes, Lögbrú, Lögfræðingur, Lögmannablaðið).

- [ ] **Step 1: Extract the fixture texts (read-only)**

```bash
mkdir -p tests/fixtures/timarit/toc
cd /Volumes/RuleOfLaw/Lausnir && uv run python - <<'EOF'
import fitz
B = "/Volumes/RuleOfLaw/Lausnir_Data/dropfolder_timarit/Tímarit og fræðigreinar/"
jobs = {
 "tl_2010_01.txt": (B + "Tímarit Lögfræðinga/TL 2010_01.pdf", [0]),
 "tl_1985.txt": (B + "Tímarit Lögfræðinga/TL 1985.pdf", [1, 2, 3]),
 "logbru_2013_01.txt": (B + "Lögbrú/2013-01-Lögbrú.pdf", [2]),
 "logfraedingur_2010.txt": (B + "Lögfræðingur UNAK/Lögfræðingur 4 arg 2010.pdf", [2]),
}
for name, (path, pages) in jobs.items():
    d = fitz.open(path)
    text = "\n".join(d[i].get_text() for i in pages)
    open(f"tests/fixtures/timarit/toc/{name}", "w", encoding="utf-8").write(text)
    print(name, len(text))
EOF
```
Expected: four files, each a few thousand characters. Look at each once; the tests below quote entries that are in them (from the survey): TL 2010 p0 begins `Lögfræðingafélag Íslands Tímarit lögfræðinga 1 Ný löggjöf um skipun dómara 7 Kjartan Bjarni Björgvinsson: Hvenær er vanhæfi smitandi? …`; TL 1985 has `EFNISYFIRLIT 1. hefti Markmið lagakennslu ….. 1` and `Almennar reglur um sameign … eftir Gauk Jörundsson…… 7`; Lögbrú `Pétur Fannar Gíslason, Um sönnunarkröfur og sönnunarmat í sakamálum 5`; Lögfræðingur `Elín Sif Kjartansdóttir Frá ritstjóra. . . 5`.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_timarit_toc.py
from pathlib import Path

from engine.timarit.toc import TocEntry, find_toc_pages, parse_toc, toc_style

FIX = Path(__file__).parent / "fixtures" / "timarit" / "toc"


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def test_tl_issue_page_first():
    entries = parse_toc(_load("tl_2010_01.txt"), style="page_first")
    titles = {e.title.split("?")[0].strip(): e for e in entries}
    kb = next(e for e in entries if e.authors == ["Kjartan Bjarni Björgvinsson"])
    assert kb.page_start == 7 and kb.title.startswith("Hvenær er vanhæfi smitandi")
    first = entries[0]
    assert first.page_start == 1 and first.title.startswith("Ný löggjöf um skipun dómara") and first.authors == []
    assert all(e.page_start is not None for e in entries)
    assert [e.page_start for e in entries] == sorted(e.page_start for e in entries)


def test_tl_annual_page_last_with_eftir_authors_and_hefti_sections():
    entries = parse_toc(_load("tl_1985.txt"), style="page_last")
    e = next(x for x in entries if x.title.startswith("Almennar reglur um sameign"))
    assert e.page_start == 7 and e.authors == ["Gaukur Jörundsson"]      # 'eftir Gauk Jörundsson' → nominative via BÍN
    assert e.issue == "1"
    assert next(x for x in entries if x.title.startswith("Markmið lagakennslu")).page_start == 1


def test_logbru_comma_separated_author_then_title():
    entries = parse_toc(_load("logbru_2013_01.txt"), style="page_last")
    e = next(x for x in entries if "sönnunarkröfur" in x.title)
    assert e.authors == ["Pétur Fannar Gíslason"] and e.page_start == 5
    s = next(x for x in entries if "forvirkar" in x.title)
    assert s.authors == ["Snorri Snorrason"]          # 'hdl.' stripped


def test_logfraedingur_dotted_leaders():
    entries = parse_toc(_load("logfraedingur_2010.txt"), style="page_last")
    e = next(x for x in entries if x.title.startswith("Frá ritstjóra"))
    assert e.authors == ["Elín Sif Kjartansdóttir"] and e.page_start == 5


def test_find_toc_pages_picks_pages_with_many_page_numbers():
    pages = ["Kápa", "Efnisyfirlit\nA ... 5\nB ... 17\nC ... 33\nD ... 49", "Meginmál án tölusettra lína."]
    assert find_toc_pages(pages) == [1]
    assert find_toc_pages(["Bara texti", "Meira"]) == []


def test_styles_per_source():
    assert toc_style("timarit_logfraedinga") == "page_first"
    assert toc_style("logbru") == "page_last"


def test_parse_toc_ignores_lines_without_a_page_number():
    text = "RITSTJÓRI: Jón Jónsson\nAðalsteinn: Grein ein 12\nFRAMKVÆMDASTJÓRI: X\n"
    assert [e.title for e in parse_toc(text, style="page_last")] == ["Grein ein"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_timarit_toc.py`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 4: Implement**

```python
# engine/timarit/toc.py
"""Table-of-contents text → TocEntry list (spec §5.1).

Two layouts cover the archive. page_first: each entry starts with its page number
(TL issues since 2003). page_last: each entry ends with its page number, often
after dotted leaders (TL annual volumes, Lögbrú, Lögfræðingur, Lögmannablaðið).
Authors are split from titles by ':' or ', ' or 'eftir …'; an 'eftir' name is in
the dative/accusative and is lemmatised to the nominative with BÍN word by word.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from engine.processors.lemmatizer import _lemmatize_word
from engine.timarit.authors import clean_author, split_authors

_LEADERS = re.compile(r"(?:\s*\.\s*){3,}|\.{3,}|…+")
_PAGE_LAST = re.compile(r"^(?P<body>.+?)(?:\s*\.{2,}\s*|\s+)(?P<page>\d{1,4})\s*$")
_PAGE_FIRST_SPLIT = re.compile(r"(?:(?<=\s)|^)(\d{1,4})\s+(?=[A-ZÁÉÍÓÚÝÞÆÖ„])")
_HEFTI = re.compile(r"^\s*(\d)\.\s*hefti\b", re.I)
_SECTION = re.compile(r"^(Fræðigreinar|Ritdómar|Ritstjórnargrein(?:ar)?|Viðtal|Viðtöl|Fréttir|Dómareifanir|Annað efni|Greinar|Erindi)\s*$", re.I)
_EFTIR = re.compile(r"\s+eftir\s+(?P<name>[A-ZÁÉÍÓÚÝÞÆÖ][^\d]{3,80}?)\s*$")
_ROLE_WORDS = ("ritstjóri", "framkvæmdastjóri", "ráðgjafarráð", "útgefandi", "ábyrgðarmaður", "ritnefnd")

_STYLE = {"timarit_logfraedinga": "page_first"}


@dataclass
class TocEntry:
    title: str
    authors: list[str]
    page_start: int | None
    section: str | None = None
    issue: str | None = None


def toc_style(source: str) -> str:
    return _STYLE.get(source, "page_last")


def find_toc_pages(page_texts: list[str], max_scan: int = 6) -> list[int]:
    """Pages in the front matter where ≥ 4 lines end (or start) with a 1–4 digit number."""
    out = []
    for i, t in enumerate(page_texts[:max_scan]):
        lines = [l.strip() for l in t.splitlines() if l.strip()]
        hits = sum(1 for l in lines if re.search(r"\s\d{1,4}$", l) or re.match(r"^\d{1,4}\s+\S", l))
        if hits >= 4:
            out.append(i)
    return out


def _nominative(name: str) -> str:
    return " ".join(_lemmatize_word(w).capitalize() if w[:1].isupper() else w for w in name.split())


def _split_author_title(body: str) -> tuple[list[str], str]:
    body = _LEADERS.sub(" ", body).strip(" .:")
    m = _EFTIR.search(body)
    if m:
        return [_nominative(clean_author(m.group("name"))[0])], body[:m.start()].strip(" .,:")
    if ":" in body:
        a, t = body.split(":", 1)
        if len(a.split()) <= 6:
            return split_authors(a), t.strip()
    if "," in body:
        a, t = body.split(",", 1)
        if 2 <= len(a.split()) <= 5 and a.split()[0][:1].isupper():
            return split_authors(a), t.strip()
    # "Elín Sif Kjartansdóttir Frá ritstjóra": name = leading capitalised words up to the first
    # word that is not a capitalised name-like token when ≥ 2 such words precede a lowercase/common word.
    words = body.split()
    i = 0
    while i < len(words) and i < 5 and re.match(r"^[A-ZÁÉÍÓÚÝÞÆÖ][a-záéíóúýðþæö\.]+$", words[i]):
        i += 1
    if 2 <= i < len(words) and words[i][:1].isupper() is False or (i >= 2 and i < len(words) and words[i] in ("Frá", "Ávarp", "Um")):
        return split_authors(" ".join(words[:i])), " ".join(words[i:])
    return [], body


def parse_toc(text: str, *, style: str) -> list[TocEntry]:
    entries: list[TocEntry] = []
    section: str | None = None
    issue: str | None = None
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if style == "page_first":
        joined = " ".join(lines)
        parts = _PAGE_FIRST_SPLIT.split(joined)
        # parts = [prefix, page, body, page, body, …]
        for page, body in zip(parts[1::2], parts[2::2]):
            body = body.strip()
            if not body or any(body.lower().startswith(r) for r in _ROLE_WORDS):
                continue
            authors, title = _split_author_title(body)
            entries.append(TocEntry(title, authors, int(page), section, issue))
        return entries
    for line in lines:
        if m := _HEFTI.match(line):
            issue = m.group(1)
            continue
        if m := _SECTION.match(line):
            section = m.group(1)
            continue
        if any(line.lower().startswith(r) for r in _ROLE_WORDS):
            continue
        m = _PAGE_LAST.match(line)
        if not m:
            continue
        authors, title = _split_author_title(m.group("body"))
        if not title:
            continue
        entries.append(TocEntry(title, authors, int(m.group("page")), section, issue))
    return entries
```

`authors.py` is Task 13's module; Task 10 depends on it, so implement Task 13's `clean_author`/`split_authors` first if executing in order — or run Tasks 10 and 13 together. (The task list below places Task 13 before 10 in dependency terms; executors: do 13, then 10.)

- [ ] **Step 5: Run tests; refine the regexes against the fixtures**

Run: `uv run pytest -q tests/test_timarit_toc.py -x`
Expected: PASS. The fixtures are OCR output; where a test fails on one entry, inspect the fixture line and widen the specific regex (leaders, role words), never the page-number anchor.

- [ ] **Step 6: Commit**

```bash
git add engine/timarit/toc.py tests/fixtures/timarit/toc tests/test_timarit_toc.py
git commit -m "feat(timarit): TOC parser for page-first and page-last layouts"
```

---

### Task 11: `engine/timarit/ulfljotur_xlsx.py` — the Úlfljótur registry

**Files:**
- Create: `engine/timarit/ulfljotur_xlsx.py`
- Test: `tests/test_timarit_ulfljotur_xlsx.py`

**Interfaces:**
- Produces:
```python
@dataclass(frozen=True)
class XlsxArticle:
    title: str; authors: list[str]; volume: int; issue: str; page_start: int; row: int

def load_xlsx(path: Path) -> list[XlsxArticle]
def articles_for(rows: list[XlsxArticle], *, volume: int | None = None, issue: str | None = None) -> list[XlsxArticle]
def title_similarity(a: str, b: str) -> float                   # 0..1, OCR tolerant
def match_first_page(first_page_text: str, printed_page: int | None, candidates: list[XlsxArticle]) -> tuple[XlsxArticle | None, float]
def year_for_volume(volume: int) -> int                          # 1947 + volume - 1, +1 after volume 11 (1958 skipped)
```

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_timarit_ulfljotur_xlsx.py
import openpyxl
import pytest

from engine.timarit.ulfljotur_xlsx import (
    articles_for, load_xlsx, match_first_page, title_similarity, year_for_volume,
)

HEAD = ("Grein", "Höfundur", "Höfundur II", "Höfundur III", "Árgangur", "Tölublað", "Bls")
ROWS = [
    ("Almenn og sérstök skilyrði miskabótaréttar", "Guðmundur Sigurðsson", None, None, "76. árg", "1. tölublað", 3),
    ("Teitur Gissurarson", "Um endurupptöku einkamála", None, None, "76. árg", "1. tölublað", 71),   # swapped
    ("Bótaregla 1. mgr. 51. gr. skipulagslaga", "Arnar Heimir Lárusson", "Jörgen Már Ágústsson", None, "76. árg", "2. tölublað", 119),
    ("Nokkrar hugleiðingar um breytingar á erfðalöggjöfinni", "Ísleifur Árnason", None, None, "1. árg", "3-4. tölublað", 173),
]


@pytest.fixture
def xlsx(tmp_path):
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(HEAD)
    for r in ROWS:
        ws.append(r)
    p = tmp_path / "efnisyfirlit.xlsx"; wb.save(p)
    return p


def test_load_parses_volume_issue_and_authors(xlsx):
    rows = load_xlsx(xlsx)
    assert rows[0].volume == 76 and rows[0].issue == "1" and rows[0].page_start == 3 and rows[0].row == 2
    assert rows[2].authors == ["Arnar Heimir Lárusson", "Jörgen Már Ágústsson"]
    assert rows[3].issue == "3-4" and rows[3].volume == 1


def test_load_fixes_swapped_title_and_author(xlsx):
    r = load_xlsx(xlsx)[1]
    assert r.title == "Um endurupptöku einkamála" and r.authors == ["Teitur Gissurarson"]


def test_articles_for_filters(xlsx):
    rows = load_xlsx(xlsx)
    assert [r.page_start for r in articles_for(rows, volume=76, issue="1")] == [3, 71]
    assert len(articles_for(rows, volume=76)) == 3


def test_title_similarity_tolerates_ocr_noise():
    assert title_similarity("Nokkrar hugleiðingar um breytingar á erfðalöggjöfinni",
                            "Nokkrar hugleiðingar um breytingar á erfðalöggjöfinni.") > 0.95
    assert title_similarity("Nokkrar hugleiðingar um breytingar á erfðalöggjöfinni",
                            "Nokkrar hugleidingar um breytingar a erfdalöggjöfinni") > 0.85
    assert title_similarity("Nokkrar hugleiðingar um breytingar á erfðalöggjöfinni", "Viðskiptabréfsreglur um hlutabréf") < 0.4


def test_match_first_page_uses_title_and_printed_page(xlsx):
    rows = articles_for(load_xlsx(xlsx), volume=1)
    page = "ULFLJOTUR Reykjavik, desember 1947, 4. tbl., I. árg. PRÓFESSOR ÍSLEIFUR ARNASON: Nokkrar hugleiðingar um breytingar á erfðalöggjöfinni. T^/Teginreglur…"
    hit, score = match_first_page(page, 173, rows)
    assert hit is rows[0] and score >= 0.9
    miss, score2 = match_first_page("Alls ótengdur texti um sjávarútveg", 500, rows)
    assert miss is None and score2 < 0.5


def test_year_for_volume_handles_the_skipped_year():
    assert year_for_volume(1) == 1947 and year_for_volume(6) == 1952 and year_for_volume(11) == 1957
    assert year_for_volume(12) == 1959 and year_for_volume(54) == 2001 and year_for_volume(76) == 2023
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_timarit_ulfljotur_xlsx.py`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# engine/timarit/ulfljotur_xlsx.py
"""The Úlfljótur table of contents the user keeps (1,258 articles, 1.–76. árg.).

Columns: Grein | Höfundur | Höfundur II | Höfundur III | Árgangur ('76. árg') |
Tölublað ('1. tölublað', '3-4. tölublað') | Bls (int). Two rows have title and
author swapped; a cell that looks like a person's name in the title column
while the author column does not is swapped back.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import openpyxl

from engine.timarit.authors import clean_author, looks_like_person

_ARG = re.compile(r"(\d+)\.?\s*árg", re.I)
_TBL = re.compile(r"(\d+(?:-\d+)?)\.?\s*tölubla", re.I)


@dataclass(frozen=True)
class XlsxArticle:
    title: str
    authors: list[str]
    volume: int
    issue: str
    page_start: int
    row: int


def load_xlsx(path: Path) -> list[XlsxArticle]:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    out: list[XlsxArticle] = []
    for n, row in enumerate(ws.iter_rows(values_only=True), start=1):
        if n == 1 or not any(c is not None for c in row):
            continue
        title, a1, a2, a3, arg, tbl, bls = (list(row) + [None] * 7)[:7]
        title = str(title or "").strip()
        authors = [str(a).strip() for a in (a1, a2, a3) if a and str(a).strip()]
        if authors and looks_like_person(title) and not looks_like_person(authors[0]):
            title, authors[0] = authors[0], title
        m_arg, m_tbl = _ARG.search(str(arg or "")), _TBL.search(str(tbl or ""))
        if not (m_arg and m_tbl and bls is not None):
            continue
        out.append(XlsxArticle(title, [clean_author(a)[0] for a in authors],
                               int(m_arg.group(1)), m_tbl.group(1), int(bls), n))
    return out


def articles_for(rows: list[XlsxArticle], *, volume: int | None = None, issue: str | None = None) -> list[XlsxArticle]:
    sel = [r for r in rows if (volume is None or r.volume == volume) and (issue is None or r.issue == issue or issue in r.issue.split("-"))]
    return sorted(sel, key=lambda r: r.page_start)


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", s.replace("ð", "d").replace("þ", "th").replace("æ", "ae")).strip()


def title_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _fold(a), _fold(b)).ratio()


def match_first_page(first_page_text: str, printed_page: int | None,
                     candidates: list[XlsxArticle]) -> tuple[XlsxArticle | None, float]:
    """Best candidate whose title appears on the page. Score = best windowed
    similarity of the title inside the first 600 folded characters, +0.1 when
    the printed page equals the registry's start page (capped at 1.0)."""
    head = _fold(first_page_text[:1200])
    best, best_score = None, 0.0
    for c in candidates:
        t = _fold(c.title)
        if not t:
            continue
        window = len(t)
        scores = [difflib.SequenceMatcher(None, t, head[i:i + window]).ratio()
                  for i in range(0, max(1, len(head) - window + 1), max(1, window // 4))]
        s = max(scores) if scores else 0.0
        if printed_page is not None and printed_page == c.page_start:
            s = min(1.0, s + 0.1)
        if s > best_score:
            best, best_score = c, s
    return (best, best_score) if best_score >= 0.6 else (None, best_score)


def year_for_volume(volume: int) -> int:
    """1. árg. = 1947; no volume appeared in 1958, so volumes ≥ 12 shift by one year."""
    return 1947 + volume - 1 + (1 if volume >= 12 else 0)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q tests/test_timarit_ulfljotur_xlsx.py`
Expected: PASS.

- [ ] **Step 5: Sanity-check against the real spreadsheet (read-only)**

Run: `uv run python -c "from pathlib import Path; from engine.timarit.ulfljotur_xlsx import load_xlsx; r = load_xlsx(Path('/Volumes/RuleOfLaw/Lausnir_Data/dropfolder_timarit/_meta/Ulfljotur_efnisyfirlit.xlsx')); print(len(r), r[0], r[2])"`
Expected: `1258 …`; row 4 (`Teitur Gissurarson`) comes back with the title `Um endurupptöku einkamála`.

- [ ] **Step 6: Commit**

```bash
git add engine/timarit/ulfljotur_xlsx.py tests/test_timarit_ulfljotur_xlsx.py
git commit -m "feat(timarit): Úlfljótur registry loader and first-page matcher"
```

---

### Task 12: `engine/timarit/leitir_articles.py` — article records from leitir.is

**Files:**
- Create: `engine/timarit/leitir_articles.py`
- Test: `tests/test_timarit_leitir.py`

**Interfaces:**
- Produces:
```python
@dataclass(frozen=True)
class LeitirArticle:
    title: str; authors: list[str]; journal: str | None; year: int | None
    volume: int | None; issue: str | None; page_start: int | None; page_end: int | None; record_id: str | None

def parse_ispartof(s: str) -> dict          # keys: year, volume, issue, page_start, page_end, journal, record_id
async def search_articles(client: httpx.AsyncClient, query: str, *, cache_dir: Path, limit: int = 10,
                          journal_filter: str | None = None) -> list[LeitirArticle]
def best_match(records: list[LeitirArticle], *, title: str | None, page_start: int | None, year: int | None) -> LeitirArticle | None
```
Throttle: a module-level `asyncio.Lock` + `_MIN_INTERVAL = 1.0` s between uncached requests. Cache: `cache_dir / f"{sha1(query|limit)}.json"`, written only on HTTP 200.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_timarit_leitir.py
import json
from pathlib import Path

import httpx
import pytest

from engine.timarit.leitir_articles import LeitirArticle, best_match, parse_ispartof, search_articles

DOC = {"pnx": {"display": {
    "type": ["article"],
    "title": ["Hvenær er vanhæfi smitandi? : hugleiðingar í kjölfar dóms Hæstaréttar frá 17. desember 2009 í máli nr. 665/2008 "],
    "creator": ["Kjartan Bjarni Björgvinsson 1976- höfundur$$QKjartan Bjarni Björgvinsson"],
    "ispartof": ["2010; 60 (1): bls. 7-47 Tímarit lögfræðinga :$$QTímarit lögfræðinga $$Z991001868109706886"],
}}}


def test_parse_ispartof_full_form():
    d = parse_ispartof(DOC["pnx"]["display"]["ispartof"][0])
    assert d == {"year": 2010, "volume": 60, "issue": "1", "page_start": 7, "page_end": 47,
                 "journal": "Tímarit lögfræðinga", "record_id": "991001868109706886"}


def test_parse_ispartof_without_issue_or_pages():
    d = parse_ispartof("Skírnir : tímarit Hins íslenska bókmenntafélags. 1929; 103: bls. 151-170$$QSkírnir : tímarit Hins íslenska bókmenntafélags.$$Z991001811659706886")
    assert (d["year"], d["volume"], d["issue"], d["page_start"], d["page_end"]) == (1929, 103, None, 151, 170)
    assert parse_ispartof("Eitthvað óþekkt")["year"] is None


async def test_search_encodes_query_caches_and_filters(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"docs": [DOC, {"pnx": {"display": {"type": ["book"], "title": ["Bók"]}}}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    res = await search_articles(client, "Hvenær er vanhæfi smitandi", cache_dir=tmp_path, journal_filter="Tímarit lögfræðinga")
    assert len(res) == 1 and res[0].authors == ["Kjartan Bjarni Björgvinsson"] and res[0].page_start == 7
    assert "Hvenær er vanhæfi smitandi" not in str(calls[0].url)      # percent-encoded
    assert "Hvenær%20er%20vanh%C3%A6fi" in str(calls[0].url) or "Hvenær+er" in str(calls[0].url.query)
    res2 = await search_articles(client, "Hvenær er vanhæfi smitandi", cache_dir=tmp_path, journal_filter="Tímarit lögfræðinga")
    assert len(calls) == 1 and res2 == res                               # served from cache
    assert len(list(Path(tmp_path).glob("*.json"))) == 1


async def test_search_http_error_returns_empty_and_caches_nothing(tmp_path):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(400)))
    assert await search_articles(client, "x", cache_dir=tmp_path) == []
    assert not list(Path(tmp_path).glob("*.json"))


def test_best_match_prefers_title_then_page_then_year():
    a = LeitirArticle("Hvenær er vanhæfi smitandi?", ["K"], "TL", 2010, 60, "1", 7, 47, "1")
    b = LeitirArticle("Eru valdmörk á hreyfingu?", ["D"], "TL", 2010, 60, "1", 49, 70, "2")
    assert best_match([a, b], title="Hvenær er vanhæfi smitandi", page_start=None, year=2010) is a
    assert best_match([a, b], title=None, page_start=49, year=2010) is b
    assert best_match([a, b], title="Alls annað", page_start=None, year=1999) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_timarit_leitir.py`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# engine/timarit/leitir_articles.py
"""Article records from leitir.is (Primo VE public API) — spec §5.1.

Same endpoint as engine.processors.book_metadata.lookup_leitir; this one searches
for articles and parses `display.ispartof`, which carries year, volume, issue and
page range: '2010; 60 (1): bls. 7-47 Tímarit lögfræðinga :$$Q…$$Z<record>'.
Every query is cached on disk so re-runs cost nothing and are reproducible, and
uncached requests are spaced ≥ 1 s apart.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from engine.processors.book_metadata import _clean_primo_name
from engine.timarit.ulfljotur_xlsx import title_similarity

log = logging.getLogger(__name__)

_URL = "https://leitir.is/primaws/rest/pub/pnxs"
_PARAMS = {"scope": "10000_MYLIB", "tab": "Everything", "vid": "354ILC_NETWORK:10000_UNION",
           "inst": "354ILC_NETWORK", "lang": "is", "offset": "0"}
_ISPARTOF = re.compile(
    r"(?P<pre>.*?)(?P<year>\d{4});\s*(?P<vol>\d+)?\s*(?:\((?P<issue>[\d\-/]+)\))?\s*:?\s*(?:bls\.\s*(?P<p1>\d+)(?:-(?P<p2>\d+))?)?(?P<post>.*)")
_RECORD = re.compile(r"\$\$Z(\d+)")
_QNAME = re.compile(r"\$\$Q([^$]+)")
_MIN_INTERVAL = 1.0
_lock = asyncio.Lock()
_last_call = 0.0


@dataclass(frozen=True)
class LeitirArticle:
    title: str
    authors: list[str]
    journal: str | None
    year: int | None
    volume: int | None
    issue: str | None
    page_start: int | None
    page_end: int | None
    record_id: str | None


def parse_ispartof(s: str) -> dict:
    out = {"year": None, "volume": None, "issue": None, "page_start": None, "page_end": None, "journal": None, "record_id": None}
    m = _ISPARTOF.match(s or "")
    if not m or not m.group("year"):
        return out
    out["year"] = int(m.group("year"))
    out["volume"] = int(m.group("vol")) if m.group("vol") else None
    out["issue"] = m.group("issue")
    out["page_start"] = int(m.group("p1")) if m.group("p1") else None
    out["page_end"] = int(m.group("p2")) if m.group("p2") else out["page_start"] if m.group("p1") else None
    q = _QNAME.search(s)
    journal = q.group(1) if q else (m.group("pre") or m.group("post"))
    out["journal"] = re.sub(r"\s*:\s*$", "", journal.split("$$")[0]).strip() or None
    r = _RECORD.search(s)
    out["record_id"] = r.group(1) if r else None
    return out


def _to_article(doc: dict) -> LeitirArticle | None:
    disp = doc.get("pnx", {}).get("display", {})
    if "article" not in (disp.get("type") or []):
        return None
    title = (disp.get("title") or [""])[0].strip().rstrip(" .")
    authors = [_clean_primo_name(c) for c in (disp.get("creator") or []) if c and c.strip()]
    part = parse_ispartof((disp.get("ispartof") or [""])[0])
    return LeitirArticle(title, authors, part["journal"], part["year"], part["volume"], part["issue"],
                         part["page_start"], part["page_end"], part["record_id"])


async def search_articles(client: httpx.AsyncClient, query: str, *, cache_dir: Path, limit: int = 10,
                          journal_filter: str | None = None) -> list[LeitirArticle]:
    global _last_call
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(f"{query}|{limit}".encode("utf-8")).hexdigest()
    path = cache_dir / f"{key}.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        async with _lock:
            wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                resp = await client.get(_URL, params={**_PARAMS, "q": f"any,contains,{query}", "limit": str(limit)}, timeout=20.0)
                _last_call = time.monotonic()
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                log.warning("leitir.is article search failed for %r: %s", query, exc)
                return []
        data = resp.json()
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    arts = [a for a in map(_to_article, data.get("docs") or []) if a]
    if journal_filter:
        arts = [a for a in arts if a.journal and title_similarity(a.journal, journal_filter) > 0.8]
    return arts


def best_match(records: list[LeitirArticle], *, title: str | None, page_start: int | None,
               year: int | None) -> LeitirArticle | None:
    best, best_s = None, 0.0
    for r in records:
        s = 0.0
        if title:
            s += 0.7 * title_similarity(title, r.title)
        if page_start is not None and r.page_start == page_start:
            s += 0.25
        if year is not None and r.year == year:
            s += 0.05
        if s > best_s:
            best, best_s = r, s
    return best if best_s >= 0.55 else None
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q tests/test_timarit_leitir.py`
Expected: PASS.

- [ ] **Step 5: Live probe (one request, cached under `_meta/cache/leitir`)**

Run: `uv run python -c "import asyncio, httpx; from pathlib import Path; from engine.timarit.leitir_articles import search_articles; from engine.timarit.config import CACHE_DIR; print(asyncio.run(search_articles(httpx.AsyncClient(), 'Hvenær er vanhæfi smitandi', cache_dir=CACHE_DIR/'leitir', journal_filter='Tímarit lögfræðinga')))"`
Expected: one `LeitirArticle(... year=2010, volume=60, issue='1', page_start=7, page_end=47 ...)`.

- [ ] **Step 6: Commit**

```bash
git add engine/timarit/leitir_articles.py tests/test_timarit_leitir.py
git commit -m "feat(timarit): leitir.is article search with ispartof parsing, disk cache and throttle"
```

---

### Task 13: Authors, article type, language

**Files:**
- Create: `engine/timarit/authors.py`, `engine/timarit/article_type.py`, `engine/timarit/lang.py`
- Test: `tests/test_timarit_helpers.py`

**Interfaces:**
- Produces: `clean_author(raw: str) -> tuple[str, str | None]` (name, titles joined by ", "); `split_authors(s: str) -> list[str]`; `looks_like_person(s: str) -> bool`; `infer_article_type(section: str | None, title: str, default: str) -> str`; `detect_lang(text: str, default: str = "is") -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_timarit_helpers.py
from engine.timarit.article_type import infer_article_type
from engine.timarit.authors import clean_author, looks_like_person, split_authors
from engine.timarit.lang import detect_lang


def test_clean_author_strips_titles_and_roles():
    assert clean_author("Dr. Margrét Einarsdóttir") == ("Margrét Einarsdóttir", "Dr.")
    assert clean_author("Snorri Snorrason hdl.") == ("Snorri Snorrason", "hdl.")
    assert clean_author("Stefán A. Svensson hrl., LL.M.") == ("Stefán A. Svensson", "hrl., LL.M.")
    assert clean_author("Þyrí Halla Steingrímsdóttir, cand. jur. og löglærður fulltrúi hjá DP lögmönnum") == ("Þyrí Halla Steingrímsdóttir", "cand. jur.")
    assert clean_author("PRÓFESSOR ÍSLEIFUR ARNASON") == ("Ísleifur Arnason", "prófessor")
    assert clean_author("Kjartan Bjarni Björgvinsson") == ("Kjartan Bjarni Björgvinsson", None)


def test_split_authors():
    assert split_authors("Arnar Heimir Lárusson og Jörgen Már Ágústsson") == ["Arnar Heimir Lárusson", "Jörgen Már Ágústsson"]
    assert split_authors("A B, C D og E F") == ["A B", "C D", "E F"]
    assert split_authors("Hafsteinn Einarsson") == ["Hafsteinn Einarsson"]


def test_looks_like_person():
    assert looks_like_person("Teitur Gissurarson") and looks_like_person("dr. Hafsteinn Dan Kristjánsson")
    assert not looks_like_person("Um endurupptöku einkamála") and not looks_like_person("Efnisþáttur Tamílamálsins")


def test_infer_article_type():
    assert infer_article_type("Ritdómar", "Bók um kröfurétt", "Fræðigrein") == "Ritdómur"
    assert infer_article_type(None, "Frá ritstjóra", "Fræðigrein") == "Ritstjórnargrein"
    assert infer_article_type(None, "Viðtal við Markús Sigurbjörnsson", "Annað") == "Viðtal"
    assert infer_article_type("Dómareifanir", "Hrd. 2010", "Fræðigrein") == "Dómareifun"
    assert infer_article_type(None, "Hvenær er vanhæfi smitandi?", "Fræðigrein") == "Fræðigrein"
    assert infer_article_type(None, "Aðalfundur LMFÍ 2015", "Annað") == "Annað"


def test_detect_lang():
    assert detect_lang("Dómstóllinn taldi að skilyrði væru ekki uppfyllt og vísaði málinu frá.") == "is"
    assert detect_lang("The court held that the conditions were not met and dismissed the case.") == "en"
    assert detect_lang("x y z", default="is") == "is"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_timarit_helpers.py`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# engine/timarit/authors.py
"""Author strings as journals print them → clean names + titles (spec §5.5)."""
from __future__ import annotations

import re

_TITLE_WORDS = r"(?:dr\.|prófessor|professor|dósent|lektor|aðjúnkt|hrl\.|hdl\.|lögmaður|lögfræðingur|cand\.\s*jur\.|cand\.\s*mag\.|LL\.M\.|LLM|ph\.?d\.?|mag\.\s*jur\.|héraðsdómari|hæstaréttardómari|ritstjóri|stud\.\s*jur\.)"
_TITLE_RE = re.compile(_TITLE_WORDS, re.I)
_TRAILING_ROLE = re.compile(r"\s+(?:og|við|hjá)\s.*$", re.I)
_NAME_TOKEN = re.compile(r"^[A-ZÁÉÍÓÚÝÞÆÖ][a-záéíóúýðþæö]+(?:-[A-ZÁÉÍÓÚÝÞÆÖ][a-záéíóúýðþæö]+)?\.?$")
_SPLIT = re.compile(r"\s*,\s*|\s+og\s+|\s*&\s*|\s*;\s*")


def _titlecase_if_shouting(s: str) -> str:
    return " ".join(w.capitalize() for w in s.split()) if s.isupper() else s


def clean_author(raw: str) -> tuple[str, str | None]:
    s = " ".join((raw or "").split())
    titles = [m.group(0) for m in _TITLE_RE.finditer(s)]
    name = _TITLE_RE.sub("", s)
    name = _TRAILING_ROLE.sub("", name)
    name = re.sub(r"[,;:]+", ",", name).strip(" ,.-")
    name = re.sub(r"\s+,", ",", name)
    name = name.split(",")[0].strip() if "," in name and len(name.split(",")[0].split()) >= 2 else name
    name = _titlecase_if_shouting(name)
    norm_titles = []
    for t in titles:
        t = t.strip()
        norm_titles.append(t.lower() if t.lower() in ("prófessor", "dósent", "lektor") else t)
    return name, (", ".join(norm_titles) or None)


def split_authors(s: str) -> list[str]:
    parts = [p.strip() for p in _SPLIT.split(s or "") if p and p.strip()]
    return [clean_author(p)[0] for p in parts] or ([clean_author(s)[0]] if s and s.strip() else [])


def looks_like_person(s: str) -> bool:
    name, _ = clean_author(s or "")
    toks = name.split()
    return 2 <= len(toks) <= 4 and all(_NAME_TOKEN.match(t) for t in toks)
```

```python
# engine/timarit/article_type.py
import re

_RULES = [
    ("Ritdómur", re.compile(r"ritdóm|bókarfregn|ritfregn", re.I)),
    ("Ritstjórnargrein", re.compile(r"frá ritstjór|ritstjórnargrein|leiðari|ávarp ritstjór", re.I)),
    ("Viðtal", re.compile(r"\bviðtal\b|\bviðtöl\b|í spjalli", re.I)),
    ("Dómareifun", re.compile(r"dómareifan|reifun dóm|dómar hæstaréttar|úr dómasafni", re.I)),
    ("Frétt", re.compile(r"\bfrétt|af félagsstarf|aðalfund|félagsmenn|dagskrá|tilkynning", re.I)),
]


def infer_article_type(section: str | None, title: str, default: str) -> str:
    """Section heading of the TOC first, then the title; else the source default."""
    for kind, pat in _RULES:
        if section and pat.search(section):
            return kind
    for kind, pat in _RULES:
        if pat.search(title or ""):
            return kind
    return default
```

```python
# engine/timarit/lang.py
"""Cheap is/en decision from closed-class words; anything unclear → default."""
import re

_IS = {"og", "að", "í", "á", "er", "sem", "um", "við", "ekki", "til", "af", "var", "með", "fyrir", "því", "þess", "hafi", "væri", "málinu", "dómstóllinn"}
_EN = {"the", "and", "of", "to", "in", "that", "is", "was", "for", "with", "court", "held", "not", "by", "this", "which"}
_W = re.compile(r"[a-záéíóúýðþæö]+", re.I)


def detect_lang(text: str, default: str = "is") -> str:
    words = [w.lower() for w in _W.findall(text or "")[:3000]]
    is_n = sum(w in _IS for w in words)
    en_n = sum(w in _EN for w in words)
    if is_n + en_n < 3:
        return default
    return "en" if en_n > 2 * is_n else "is" if is_n >= en_n else default
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q tests/test_timarit_helpers.py`
Expected: PASS. Adjust `_TRAILING_ROLE`/title list only to satisfy the listed cases.

- [ ] **Step 5: Commit**

```bash
git add engine/timarit/authors.py engine/timarit/article_type.py engine/timarit/lang.py tests/test_timarit_helpers.py
git commit -m "feat(timarit): author cleaning, article type inference, language detection"
```

---

### Task 14: `engine/timarit/manifest.py` — the review file

**Files:**
- Create: `engine/timarit/manifest.py`
- Test: `tests/test_timarit_manifest.py`

**Interfaces:**
- Produces:
```python
@dataclass
class ArticleEntry:
    title: str | None = None; authors: list[str] = []; author_titles: list[str] = []
    article_type: str | None = None; page_start: int | None = None; page_end: int | None = None
    pdf_pages: list[int] | None = None        # [first_idx, last_idx] inclusive, 0-based, in the issue PDF
    lang: str | None = None; date: str | None = None     # ISO; only when the article prints one (vefrit)
    summary: str | None = None; keywords: list[str] = []
    provenance: dict[str, str] = {}; checks: dict[str, bool] = {}; confidence: float = 0.0
    leitir_record_id: str | None = None; xlsx_row: int | None = None
    source_file: str | None = None            # for article-granularity PDFs: rel path of this article's own PDF
    note: str = ""

@dataclass
class IssueManifest:
    source: str; year: int | None; volume: int | None; issue: str | None; label: str
    file: str | None = None                   # rel path (NFC) of the issue/volume PDF, None when only article PDFs exist
    sha256: str | None = None; pages: int | None = None
    year_end: int | None = None
    page_map: dict = {}; status: str = "pending"; confidence: float = 0.0; error: str | None = None
    files: list[str] = []                     # every PDF that belongs to this issue (NFC rel paths)
    articles: list[ArticleEntry] = []

def manifest_path(root: Path, source: str, year: int | None, volume: int | None, issue: str | None) -> Path
def issue_label(cfg: SourceConfig, year, volume, issue) -> str
def load_manifest(path: Path) -> IssueManifest
def save_manifest(m: IssueManifest, path: Path) -> None
def manifest_sha256(path: Path) -> str
def article_confidence(checks: dict[str, bool], *, llm: bool = False) -> float
def issue_status(m: IssueManifest) -> str                 # "approved" | "review" (never touches imported/failed)
def external_id(m: IssueManifest, a: ArticleEntry) -> str
def merge_preserving_user(old: IssueManifest | None, new: IssueManifest) -> IssueManifest
```

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_timarit_manifest.py
from pathlib import Path

import yaml

from engine.config.sources import get_config
from engine.timarit import config as C
from engine.timarit.manifest import (
    ArticleEntry, IssueManifest, article_confidence, external_id, issue_label, issue_status,
    load_manifest, manifest_path, manifest_sha256, merge_preserving_user, save_manifest,
)


def _m(**over):
    base = dict(source="timarit_logfraedinga", year=2010, volume=60, issue="1",
                label="Tímarit lögfræðinga, 60. árg. 1. hefti (2010)", file="Tímarit og fræðigreinar/Tímarit Lögfræðinga/TL 2010_01.pdf",
                pages=109, articles=[ArticleEntry(title="A", authors=["X Y"], page_start=7, page_end=47, pdf_pages=[8, 48],
                                                  provenance={"title": "leitir"}, checks={"title_on_page": True, "printed_page_match": True,
                                                  "author_on_page": True, "external_record": True})])
    base.update(over)
    return IssueManifest(**base)


def test_manifest_path_uses_x_for_missing_parts(tmp_path):
    assert manifest_path(tmp_path, "timarit_logfraedinga", 2010, 60, "1") == tmp_path / "timarit_logfraedinga" / "2010_60_1.yaml"
    assert manifest_path(tmp_path, "ulfljotur", 2002, None, "3-4").name == "2002_x_3-4.yaml"
    assert manifest_path(tmp_path, "logfraedingur", 2010, 4, None).name == "2010_4_x.yaml"


def test_issue_label_per_source():
    assert issue_label(get_config("timarit_logfraedinga"), 2010, 60, "1") == "Tímarit lögfræðinga, 60. árg. 1. hefti (2010)"
    assert issue_label(get_config("ulfljotur"), 2004, 57, "2") == "Úlfljótur, 57. árg. 2. tbl. (2004)"
    assert issue_label(get_config("timarit_logfraedinga"), 1985, 35, None) == "Tímarit lögfræðinga, 35. árg. (1985)"
    assert issue_label(get_config("rannsoknir_lagadeild"), 2007, 8, None) == "Rannsóknir í félagsvísindum, 8. árg. (2007)"


def test_roundtrip_yaml_keeps_everything(tmp_path):
    p = tmp_path / "m.yaml"
    save_manifest(_m(), p)
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert data["articles"][0]["pdf_pages"] == [8, 48] and data["status"] == "pending"
    assert load_manifest(p) == _m()


def test_article_confidence_weights_and_llm_cap():
    all_true = {"title_on_page": True, "printed_page_match": True, "author_on_page": True, "external_record": True}
    assert abs(article_confidence(all_true) - 1.0) < 1e-9
    assert abs(article_confidence({**all_true, "external_record": False}) - 0.80) < 1e-9
    assert article_confidence(all_true, llm=True) == C.LLM_CONFIDENCE_CAP
    assert article_confidence({}) == 0.0


def test_issue_status_requires_every_article_above_threshold_and_coverage():
    m = _m()
    m.articles[0].confidence = 0.95
    assert issue_status(m) == "approved"
    m.articles.append(ArticleEntry(title="B", page_start=49, page_end=70, pdf_pages=[50, 71], confidence=0.6))
    assert issue_status(m) == "review"
    m.articles[1].confidence = 0.95
    m.articles[1].pdf_pages = [60, 71]      # gap of 11 pages between 48 and 60
    assert issue_status(m) == "review"


def test_external_id_forms():
    assert external_id(_m(), _m().articles[0]) == "2010-60-1-7"
    v = _m(source="ulfljotur_vefrit", year=2022, volume=None, issue=None,
           articles=[ArticleEntry(title="Loftslagsváin og dómstólar", date="2022-01-21")])
    assert external_id(v, v.articles[0]) == "2022-01-21-loftslagsvain-og-domstolar"
    r = _m(source="rannsoknir_lagadeild", year=2007, volume=8, issue=None, articles=[ArticleEntry(title="T", page_start=33)])
    assert external_id(r, r.articles[0]) == "2007-8-x-33"


def test_merge_keeps_user_values_and_approved_status():
    old = _m(status="approved")
    old.articles[0].title = "Titill lagaður af notanda"
    old.articles[0].provenance["title"] = "user"
    old.articles[0].note = "lagað 2.10."
    new = _m(status="review")
    new.articles[0].title = "Titill úr nýrri greiningu"
    merged = merge_preserving_user(old, new)
    assert merged.articles[0].title == "Titill lagaður af notanda" and merged.articles[0].provenance["title"] == "user"
    assert merged.status == "approved" and merged.articles[0].note == "lagað 2.10."
    assert merge_preserving_user(None, new) == new


def test_merge_matches_articles_by_page_start_when_titles_differ():
    old = _m(); old.articles[0].authors = ["Notandi Lagaði"]; old.articles[0].provenance["authors"] = "user"
    new = _m(); new.articles[0].title = "Annar titill"; new.articles[0].authors = ["Vél"]
    assert merge_preserving_user(old, new).articles[0].authors == ["Notandi Lagaði"]


def test_manifest_sha256_changes_with_content(tmp_path):
    p = tmp_path / "m.yaml"; save_manifest(_m(), p); h1 = manifest_sha256(p)
    m = _m(); m.articles[0].note = "x"; save_manifest(m, p)
    assert manifest_sha256(p) != h1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_timarit_manifest.py`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# engine/timarit/manifest.py
"""The per-issue review file (spec §7.2) and the rules around it: confidence,
status, identity, and the merge that keeps the user's corrections."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from engine.config.sources import SourceConfig
from engine.timarit import config as C

USER = "user"
_MAX_GAP_PAGES = 6     # cover, TOC, adverts between articles; more → review


@dataclass
class ArticleEntry:
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    author_titles: list[str] = field(default_factory=list)
    article_type: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    pdf_pages: list[int] | None = None
    lang: str | None = None
    date: str | None = None
    summary: str | None = None
    keywords: list[str] = field(default_factory=list)
    provenance: dict[str, str] = field(default_factory=dict)
    checks: dict[str, bool] = field(default_factory=dict)
    confidence: float = 0.0
    leitir_record_id: str | None = None
    xlsx_row: int | None = None
    source_file: str | None = None
    note: str = ""


@dataclass
class IssueManifest:
    source: str
    year: int | None
    volume: int | None
    issue: str | None
    label: str
    file: str | None = None
    sha256: str | None = None
    pages: int | None = None
    year_end: int | None = None
    page_map: dict = field(default_factory=dict)
    status: str = "pending"
    confidence: float = 0.0
    error: str | None = None
    files: list[str] = field(default_factory=list)
    articles: list[ArticleEntry] = field(default_factory=list)


def _x(v) -> str:
    return "x" if v is None else str(v)


def manifest_path(root: Path, source: str, year, volume, issue) -> Path:
    return root / source / f"{_x(year)}_{_x(volume)}_{_x(issue)}.yaml"


def issue_label(cfg: SourceConfig, year, volume, issue) -> str:
    name = cfg.citation_name or cfg.display_name
    parts = []
    if volume is not None:
        parts.append(f"{volume}. árg.")
    if issue is not None and cfg.issue_term:
        parts.append(f"{issue}. {cfg.issue_term}")
    bib = " ".join(parts)
    return f"{name}, {bib} ({year})".replace(",  (", ", (").replace(", (", " (") if not bib else f"{name}, {bib} ({year})"


def load_manifest(path: Path) -> IssueManifest:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    arts = [ArticleEntry(**a) for a in data.pop("articles", []) or []]
    return IssueManifest(**data, articles=arts)


def save_manifest(m: IssueManifest, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(asdict(m), allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")


def manifest_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def article_confidence(checks: dict[str, bool], *, llm: bool = False) -> float:
    score = sum(w for k, w in C.WEIGHTS.items() if checks.get(k))
    return min(score, C.LLM_CONFIDENCE_CAP) if llm else score


def _coverage_ok(m: IssueManifest) -> bool:
    spans = sorted(a.pdf_pages for a in m.articles if a.pdf_pages)
    if m.file is None or not spans:
        return True
    prev_end = spans[0][1]
    for s, e in spans[1:]:
        if s - prev_end - 1 > _MAX_GAP_PAGES:
            return False
        prev_end = max(prev_end, e)
    return True


def issue_status(m: IssueManifest) -> str:
    if not m.articles:
        return "review"
    if all(a.confidence >= C.APPROVE_THRESHOLD for a in m.articles) and _coverage_ok(m):
        return "approved"
    return "review"


def _slug(s: str, n: int = 40) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:n].rstrip("-") or "x"


def external_id(m: IssueManifest, a: ArticleEntry) -> str:
    if a.date and a.page_start is None:
        return f"{a.date}-{_slug(a.title or '')}"
    if a.page_start is None and not m.file and m.year is None:
        return _slug(a.title or a.source_file or "")
    return f"{_x(m.year)}-{_x(m.volume)}-{_x(m.issue)}-{_x(a.page_start)}"


def _same_article(a: ArticleEntry, b: ArticleEntry) -> bool:
    if a.page_start is not None and a.page_start == b.page_start:
        return True
    if a.source_file and a.source_file == b.source_file:
        return True
    return bool(a.title and b.title and a.title.strip().lower() == b.title.strip().lower())


def merge_preserving_user(old: IssueManifest | None, new: IssueManifest) -> IssueManifest:
    """New analysis never overwrites what the user wrote: any field whose
    provenance is 'user' keeps the old value, notes carry over, and an issue the
    user approved (or that was imported) keeps that status."""
    if old is None:
        return new
    for na in new.articles:
        oa = next((o for o in old.articles if _same_article(o, na)), None)
        if oa is None:
            continue
        for fld, prov in oa.provenance.items():
            if prov == USER and hasattr(na, fld):
                setattr(na, fld, getattr(oa, fld))
                na.provenance[fld] = USER
        if oa.note and not na.note:
            na.note = oa.note
    if old.status in ("approved", "imported"):
        new.status = old.status
    return new
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q tests/test_timarit_manifest.py`
Expected: PASS. Fix `issue_label` so that `issue_label(cfg, 1985, 35, None) == "Tímarit lögfræðinga, 35. árg. (1985)"` and the no-volume case yields `"Lögbrú (2013)"`.

- [ ] **Step 5: Commit**

```bash
git add engine/timarit/manifest.py tests/test_timarit_manifest.py
git commit -m "feat(timarit): review manifest model, confidence, status and user-preserving merge"
```

---

### Task 15: `engine/timarit/llm_toc.py` — Claude fallback

**Files:**
- Create: `engine/timarit/llm_toc.py`
- Test: `tests/test_timarit_llm_toc.py`

**Interfaces:**
- Produces: `async def toc_via_llm(text: str, *, cache_dir: Path, client=None, model: str = "claude-haiku-4-5-20251001") -> list[TocEntry]`. `client` defaults to `AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])`; the response must be a JSON array of `{"title","authors","page_start","article_type"}`; anything unparsable → `[]`. Cached by sha1 of the prompt text.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_timarit_llm_toc.py
import json
from pathlib import Path
from types import SimpleNamespace

from engine.timarit.llm_toc import toc_via_llm


class _Client:
    def __init__(self, payload):
        self.payload, self.calls = payload, 0
        self.messages = self

    async def create(self, **kw):
        self.calls += 1
        return SimpleNamespace(content=[SimpleNamespace(text=self.payload)])


async def test_parses_json_array_and_caches(tmp_path):
    client = _Client(json.dumps([{"title": "Grein ein", "authors": ["A B"], "page_start": 5, "article_type": "Fræðigrein"}]))
    out = await toc_via_llm("EFNISYFIRLIT …", cache_dir=tmp_path, client=client)
    assert out[0].title == "Grein ein" and out[0].authors == ["A B"] and out[0].page_start == 5 and out[0].section == "Fræðigrein"
    again = await toc_via_llm("EFNISYFIRLIT …", cache_dir=tmp_path, client=client)
    assert again == out and client.calls == 1


async def test_tolerates_code_fences_and_garbage(tmp_path):
    fenced = _Client("```json\n[{\"title\": \"X\", \"authors\": [], \"page_start\": null, \"article_type\": null}]\n```")
    assert (await toc_via_llm("a", cache_dir=tmp_path, client=fenced))[0].title == "X"
    assert await toc_via_llm("b", cache_dir=tmp_path, client=_Client("ekki json")) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_timarit_llm_toc.py`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# engine/timarit/llm_toc.py
"""Last-resort TOC reading with Claude (spec §5.3). Only called when the regex
parser finds nothing or disagrees with the PDF; the result is marked `llm`,
capped at LLM_CONFIDENCE_CAP, and always reviewed by the user."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path

from engine.timarit.toc import TocEntry

log = logging.getLogger(__name__)

_PROMPT = (
    "Hér fyrir neðan er efnisyfirlit úr íslensku lögfræðitímariti, lesið úr PDF með OCR. "
    "Skilaðu EINGÖNGU JSON-fylki, einu hluti á grein, á forminu "
    '{"title": "...", "authors": ["Fullt nafn", ...], "page_start": 7, "article_type": "Fræðigrein"} '
    "þar sem article_type er eitt af: Fræðigrein, Ritstjórnargrein, Ritdómur, Viðtal, Frétt, Dómareifun, Annað. "
    "Slepptu auglýsingum, ritstjórnarupplýsingum og starfsheitum höfunda. page_start er null ef blaðsíðutal vantar.\n\n"
)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)


async def toc_via_llm(text: str, *, cache_dir: Path, client=None,
                      model: str = "claude-haiku-4-5-20251001") -> list[TocEntry]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    body = text[:12000]
    path = cache_dir / (hashlib.sha1((model + body).encode("utf-8")).hexdigest() + ".json")
    if path.exists():
        raw = path.read_text(encoding="utf-8")
    else:
        if client is None:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        try:
            resp = await client.messages.create(model=model, max_tokens=4000,
                                                messages=[{"role": "user", "content": _PROMPT + body}])
            raw = resp.content[0].text
        except Exception as exc:  # noqa: BLE001
            log.warning("llm toc failed: %s", exc)
            return []
        path.write_text(raw, encoding="utf-8")
    try:
        data = json.loads(_FENCE.sub("", raw.strip()))
    except json.JSONDecodeError:
        return []
    out: list[TocEntry] = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict) or not item.get("title"):
            continue
        ps = item.get("page_start")
        out.append(TocEntry(str(item["title"]).strip(), [str(a) for a in item.get("authors") or []],
                            int(ps) if isinstance(ps, int) else None, section=item.get("article_type")))
    return out
```

- [ ] **Step 4: Run tests and commit**

Run: `uv run pytest -q tests/test_timarit_llm_toc.py` → PASS.

```bash
git add engine/timarit/llm_toc.py tests/test_timarit_llm_toc.py
git commit -m "feat(timarit): cached Claude fallback for unreadable tables of contents"
```

---

### Task 16: `engine/timarit/splitter.py` — cut an article out of an issue

**Files:**
- Create: `engine/timarit/splitter.py`
- Test: `tests/test_timarit_splitter.py`

**Interfaces:**
- Produces: `cut_pages(pdf_bytes: bytes, first: int, last: int) -> bytes` (inclusive 0-based indices, clipped to the document; raises `ValueError` when `first > last` or `first` is beyond the last page); `page_count(pdf_bytes) -> int`; `sha256_bytes(b) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_timarit_splitter.py
import fitz
import pytest

from engine.timarit.splitter import cut_pages, page_count, sha256_bytes


def _pdf(n):
    d = fitz.open()
    for i in range(n):
        d.new_page().insert_text((72, 72), f"Síða {i}")
    return d.tobytes()


def test_cut_keeps_only_the_range_and_its_text():
    out = cut_pages(_pdf(6), 2, 4)
    assert page_count(out) == 3
    d = fitz.open(stream=out, filetype="pdf")
    assert "Síða 2" in d[0].get_text() and "Síða 4" in d[2].get_text()


def test_cut_clips_last_to_document_end():
    assert page_count(cut_pages(_pdf(6), 4, 99)) == 2


def test_cut_rejects_bad_ranges():
    with pytest.raises(ValueError):
        cut_pages(_pdf(3), 2, 1)
    with pytest.raises(ValueError):
        cut_pages(_pdf(3), 3, 5)


def test_sha256_is_stable():
    b = _pdf(1)
    assert sha256_bytes(b) == sha256_bytes(b) and len(sha256_bytes(b)) == 64
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_timarit_splitter.py` → `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# engine/timarit/splitter.py
"""Cut an article's pages out of an issue PDF (spec §4.5). The pages are copied
unchanged with PyMuPDF; the issue PDF itself is never modified."""
from __future__ import annotations

import hashlib

import fitz


def page_count(pdf_bytes: bytes) -> int:
    with fitz.open(stream=pdf_bytes, filetype="pdf") as d:
        return d.page_count


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def cut_pages(pdf_bytes: bytes, first: int, last: int) -> bytes:
    with fitz.open(stream=pdf_bytes, filetype="pdf") as src:
        n = src.page_count
        if first > last or first < 0 or first >= n:
            raise ValueError(f"bad page range {first}-{last} for a {n}-page PDF")
        last = min(last, n - 1)
        out = fitz.open()
        out.insert_pdf(src, from_page=first, to_page=last)
        data = out.tobytes(garbage=3, deflate=True)
        out.close()
        return data
```

- [ ] **Step 4: Run tests and commit**

Run: `uv run pytest -q tests/test_timarit_splitter.py` → PASS.

```bash
git add engine/timarit/splitter.py tests/test_timarit_splitter.py
git commit -m "feat(timarit): page-range cutter for article PDFs"
```

---

### Task 17: `engine/timarit/analyze.py` — one issue, end to end (pure orchestration)

**Files:**
- Create: `engine/timarit/analyze.py`
- Test: `tests/test_timarit_analyze.py`

**Interfaces:**
- Consumes: everything from Tasks 7–16.
- Produces:
```python
@dataclass
class AnalyzeDeps:                       # injected so tests need no network/LLM
    read_bytes: Callable[[str], bytes]   # rel path → PDF bytes
    leitir: Callable[[str, str | None], Awaitable[list[LeitirArticle]]] | None   # (query, journal) → records
    llm_toc: Callable[[str], Awaitable[list[TocEntry]]] | None
    xlsx_rows: list[XlsxArticle]

async def analyze_issue(m: IssueManifest, cfg: SourceConfig, deps: AnalyzeDeps) -> IssueManifest
def verify_article(a: ArticleEntry, first_page_text: str, printed_page: int | None) -> dict[str, bool]
def locate_articles(entries: list[ArticleEntry], page_map: PageMap, page_texts: list[str]) -> None   # fills pdf_pages in place
```
Algorithm (spec §5): read page texts → page map → build the candidate article list from the best registry for the source (xlsx for `ulfljotur`; filename pages for `stjornmal_stjornsysla`; TOC pages for the rest, with leitir.is enrichment for `timarit_logfraedinga`; first-page extraction for article-granularity PDFs and vefrit) → locate each article's start page → verify (`title_on_page`, `printed_page_match`, `author_on_page`, `external_record`) → confidence → `issue_status`. When the TOC parser yields nothing for an issue-granularity PDF and `deps.llm_toc` is set, call it and mark provenance `llm`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_timarit_analyze.py
import fitz
import pytest

from engine.config.sources import get_config
from engine.timarit.analyze import AnalyzeDeps, analyze_issue, locate_articles, verify_article
from engine.timarit.leitir_articles import LeitirArticle
from engine.timarit.manifest import ArticleEntry, IssueManifest
from engine.timarit.pagemap import build_page_map
from engine.timarit.toc import TocEntry


def _issue_pdf():
    """A 6-page TL-style issue: cover, TOC (page-first), three articles on printed pages 1, 5, 9."""
    pages = [
        "Lögfræðingafélag Íslands\nTímarit lögfræðinga",
        "1 Jón Jónsson: Fyrsta greinin 5 Anna Pálsdóttir: Önnur greinin um dóma 9 Ritdómar Páll Páls: Bók um kröfurétt",
        "1\nJón Jónsson\nFyrsta greinin\nTexti fyrstu greinar.", "2\nFramhald fyrstu greinar.",
        "5\nAnna Pálsdóttir\nÖnnur greinin um dóma\nTexti.", "9\nPáll Páls\nBók um kröfurétt\nRitdómur.",
    ]
    d = fitz.open()
    for t in pages:
        d.new_page().insert_text((72, 72), t, fontsize=11)
    return d.tobytes()


def _deps(pdf, leitir=None, llm=None, xlsx=()):
    async def _leitir(q, journal):
        return leitir or []
    async def _llm(text):
        return llm or []
    return AnalyzeDeps(read_bytes=lambda rel: pdf, leitir=_leitir if leitir is not None else None,
                       llm_toc=_llm if llm is not None else None, xlsx_rows=list(xlsx))


def _manifest(**over):
    base = dict(source="timarit_logfraedinga", year=2010, volume=60, issue="1",
                label="Tímarit lögfræðinga, 60. árg. 1. hefti (2010)", file="x/TL 2010_01.pdf", files=["x/TL 2010_01.pdf"])
    base.update(over)
    return IssueManifest(**base)


async def test_issue_is_split_into_three_located_articles():
    m = await analyze_issue(_manifest(), get_config("timarit_logfraedinga"), _deps(_issue_pdf()))
    assert m.pages == 6 and m.page_map["offset"] == -1
    assert [a.title for a in m.articles] == ["Fyrsta greinin", "Önnur greinin um dóma", "Bók um kröfurétt"]
    assert [a.page_start for a in m.articles] == [1, 5, 9]
    assert [a.pdf_pages for a in m.articles] == [[2, 3], [4, 4], [5, 5]]
    assert m.articles[0].authors == ["Jón Jónsson"] and m.articles[2].article_type == "Ritdómur"
    assert all(a.checks["title_on_page"] and a.checks["printed_page_match"] for a in m.articles)
    assert m.articles[0].provenance["title"] == "toc" and m.status in ("approved", "review")


async def test_leitir_record_raises_confidence_and_fills_page_end():
    rec = LeitirArticle("Fyrsta greinin", ["Jón Jónsson"], "Tímarit lögfræðinga", 2010, 60, "1", 1, 4, "991")
    m = await analyze_issue(_manifest(), get_config("timarit_logfraedinga"), _deps(_issue_pdf(), leitir=[rec]))
    a = m.articles[0]
    assert a.checks["external_record"] is True and a.leitir_record_id == "991" and a.page_end == 4
    assert a.provenance["pages"].startswith("leitir")


async def test_llm_fallback_when_toc_unreadable_marks_llm_and_review():
    d = fitz.open()
    for t in ["Kápa", "Efnisyfirlit sem les ekki ...", "1\nJón Jónsson\nFyrsta greinin\nTexti."]:
        d.new_page().insert_text((72, 72), t)
    llm = [TocEntry("Fyrsta greinin", ["Jón Jónsson"], 1, section="Fræðigrein")]
    m = await analyze_issue(_manifest(), get_config("timarit_logfraedinga"), _deps(d.tobytes(), llm=llm))
    assert m.articles[0].provenance["title"] == "llm" and m.articles[0].confidence <= 0.70 and m.status == "review"


async def test_article_with_start_page_beyond_pdf_is_flagged_not_cut():
    entries = [ArticleEntry(title="A", page_start=1), ArticleEntry(title="B", page_start=400)]
    texts = ["1\nA", "2\nx", "3\ny"]
    locate_articles(entries, build_page_map(texts, "default"), texts)
    assert entries[0].pdf_pages == [0, 2] and entries[1].pdf_pages is None and "utan skjals" in entries[1].note


def test_verify_article_checks():
    a = ArticleEntry(title="Fyrsta greinin", authors=["Jón Jónsson"], page_start=1)
    c = verify_article(a, "1\nJón Jónsson\nFyrsta greinin\nTexti", 1)
    assert c == {"title_on_page": True, "printed_page_match": True, "author_on_page": True, "external_record": False}
    c2 = verify_article(a, "Alls annað efni", None)
    assert not any(c2.values())


async def test_ulfljotur_article_pdfs_are_matched_to_the_spreadsheet():
    from engine.timarit.ulfljotur_xlsx import XlsxArticle
    rows = [XlsxArticle("Nokkrar hugleiðingar um breytingar á erfðalöggjöfinni", ["Ísleifur Árnason"], 1, "4", 173, 2),
            XlsxArticle("Viðskiptabréfsreglur um hlutabréf", ["Ólafur Lárusson"], 1, "1", 211, 3)]
    d = fitz.open(); d.new_page().insert_text((72, 72), "ULFLJOTUR desember 1947, 4. tbl., I. árg.\nPRÓFESSOR ÍSLEIFUR ARNASON:\nNokkrar hugleiðingar um breytingar á erfðalöggjöfinni.\n173")
    m = _manifest(source="ulfljotur", year=1947, volume=1, issue=None, label="Úlfljótur, 1. árg. (1947)", file=None,
                  files=["Úlfljótur/Tímarit/Ulfljotur_1947 01 arg/173.pdf"])
    m = await analyze_issue(m, get_config("ulfljotur"), _deps(d.tobytes(), xlsx=rows))
    a = m.articles[0]
    assert a.title == rows[0].title and a.xlsx_row == 2 and a.provenance["title"] == "xlsx"
    assert a.source_file.endswith("173.pdf") and a.checks["external_record"] and a.checks["printed_page_match"]
    assert m.issue is None and a.page_start == 173
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_timarit_analyze.py` → `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# engine/timarit/analyze.py
"""Per-issue analysis (spec §5): registry list → located in the PDF → verified → scored."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Awaitable, Callable

import fitz

from engine.config.sources import SourceConfig
from engine.timarit import config as C
from engine.timarit.article_type import infer_article_type
from engine.timarit.authors import clean_author
from engine.timarit.lang import detect_lang
from engine.timarit.leitir_articles import LeitirArticle, best_match
from engine.timarit.manifest import ArticleEntry, IssueManifest, article_confidence, issue_status
from engine.timarit.naming import identify
from engine.timarit.pagemap import PageMap, build_page_map
from engine.timarit.splitter import sha256_bytes
from engine.timarit.toc import TocEntry, find_toc_pages, parse_toc, toc_style
from engine.timarit.ulfljotur_xlsx import XlsxArticle, articles_for, match_first_page, title_similarity

_VEFRIT_DATE = re.compile(r"Birt\s+(\d{1,2})\.\s*([a-záéíóúýðþæö]+)\s+(\d{4})", re.I)
_VEFRIT_AUTHOR = re.compile(r"^\s*Eftir\s+(.+?)\s*[,.]?\s*$", re.I | re.M)
_MONTHS = {m: i for i, m in enumerate(["janúar", "febrúar", "mars", "apríl", "maí", "júní", "júlí", "ágúst",
                                        "september", "október", "nóvember", "desember"], start=1)}


@dataclass
class AnalyzeDeps:
    read_bytes: Callable[[str], bytes]
    leitir: Callable[[str, str | None], Awaitable[list[LeitirArticle]]] | None
    llm_toc: Callable[[str], Awaitable[list[TocEntry]]] | None
    xlsx_rows: list[XlsxArticle]


def _page_texts(pdf: bytes) -> list[str]:
    with fitz.open(stream=pdf, filetype="pdf") as d:
        return [p.get_text() for p in d]


def _fold(s: str) -> str:
    return re.sub(r"[^a-záéíóúýðþæö0-9 ]+", " ", (s or "").lower())


def _on_page(needle: str | None, page_text: str, threshold: float = 0.75) -> bool:
    if not needle:
        return False
    head = _fold(page_text[:1500])
    n = _fold(needle)
    if n in head:
        return True
    window = len(n)
    return any(title_similarity(n, head[i:i + window]) >= threshold
               for i in range(0, max(1, len(head) - window + 1), max(1, window // 4)))


def verify_article(a: ArticleEntry, first_page_text: str, printed_page: int | None) -> dict[str, bool]:
    return {
        "title_on_page": _on_page(a.title, first_page_text),
        "printed_page_match": a.page_start is not None and printed_page == a.page_start,
        "author_on_page": any(_on_page(x, first_page_text, 0.8) for x in a.authors),
        "external_record": a.provenance.get("title") in ("leitir", "xlsx") or a.leitir_record_id is not None or a.xlsx_row is not None,
    }


def locate_articles(entries: list[ArticleEntry], page_map: PageMap, page_texts: list[str]) -> None:
    """Fill pdf_pages from printed start pages; an entry whose start is outside the
    document gets no range and a note instead of raising."""
    n = len(page_texts)
    located = []
    for a in entries:
        idx = page_map.pdf_index_for(a.page_start) if a.page_start is not None else None
        if idx is None:
            a.pdf_pages = None
            if a.page_start is not None:
                a.note = (a.note + " " if a.note else "") + f"upphafsbls. {a.page_start} er utan skjals"
            continue
        located.append((idx, a))
    located.sort(key=lambda t: t[0])
    for k, (idx, a) in enumerate(located):
        end = located[k + 1][0] - 1 if k + 1 < len(located) else n - 1
        a.pdf_pages = [idx, max(idx, end)]
        if a.page_end is None and page_map.offset is not None:
            a.page_end = a.pdf_pages[1] + page_map.offset


def _entries_from_toc(toc: list[TocEntry], cfg: SourceConfig, prov: str) -> list[ArticleEntry]:
    out = []
    for t in toc:
        names, titles = [], []
        for raw in t.authors:
            n, tt = clean_author(raw)
            names.append(n)
            if tt:
                titles.append(tt)
        out.append(ArticleEntry(
            title=t.title, authors=names, author_titles=titles,
            article_type=infer_article_type(t.section, t.title, cfg.verdict_type_default),
            page_start=t.page_start,
            provenance={"title": prov, "authors": prov, "pages": prov, "article_type": "toc"}))
    return out


async def _enrich_with_leitir(entries: list[ArticleEntry], m: IssueManifest, cfg: SourceConfig, deps: AnalyzeDeps) -> None:
    if deps.leitir is None:
        return
    journal = cfg.citation_name or cfg.display_name
    for a in entries:
        q = " ".join(filter(None, [a.title, a.authors[0] if a.authors else None]))
        if not q:
            continue
        rec = best_match(await deps.leitir(q, journal), title=a.title, page_start=a.page_start, year=m.year)
        if rec is None:
            continue
        a.leitir_record_id = rec.record_id
        if rec.title and title_similarity(rec.title, a.title or "") >= 0.6:
            a.title = rec.title.split(" : ")[0].strip() if a.provenance.get("title") != "leitir" else a.title
            a.provenance["title"] = "leitir"
        if rec.authors:
            a.authors, a.provenance["authors"] = rec.authors, "leitir"
        if rec.page_start is not None and (a.page_start is None or rec.page_start == a.page_start):
            a.page_start = rec.page_start
            a.page_end = rec.page_end
            a.provenance["pages"] = "leitir+toc" if a.provenance.get("pages") == "toc" else "leitir"
        if m.volume is None and rec.volume is not None:
            m.volume = rec.volume


def _vefrit_entry(text: str, rel: str) -> ArticleEntry:
    title = next((l.strip().rstrip("*") for l in text.splitlines() if l.strip() and not l.strip().lower().startswith(("birt", "vefrit"))), "")
    m_date = _VEFRIT_DATE.search(text)
    date = f"{int(m_date[3]):04d}-{_MONTHS.get(m_date[2].lower(), 1):02d}-{int(m_date[1]):02d}" if m_date else None
    m_auth = _VEFRIT_AUTHOR.search(text)
    authors = [clean_author(m_auth[1])[0]] if m_auth else []
    return ArticleEntry(title=title, authors=authors, date=date, source_file=rel,
                        provenance={"title": "page", "authors": "page", "date": "page"})


async def analyze_issue(m: IssueManifest, cfg: SourceConfig, deps: AnalyzeDeps) -> IssueManifest:
    m.articles = []
    m.error = None
    # ── Article-granularity PDFs: one entry per file, matched to a registry ──
    if m.file is None:
        for rel in m.files:
            pdf = deps.read_bytes(rel)
            texts = _page_texts(pdf)
            first = texts[0] if texts else ""
            pm = build_page_map(texts, cfg.short_name)
            fid = identify(rel)
            if cfg.short_name == "ulfljotur":
                cands = articles_for(deps.xlsx_rows, volume=m.volume, issue=m.issue)
                hit, score = match_first_page(first, pm.printed[0] if pm.printed else None, cands)
                if hit:
                    a = ArticleEntry(title=hit.title, authors=hit.authors, page_start=hit.page_start, xlsx_row=hit.row,
                                     provenance={"title": "xlsx", "authors": "xlsx", "pages": "xlsx"})
                else:
                    a = ArticleEntry(title=(fid.title_hint if fid else None), provenance={"title": "filename"},
                                     note=f"engin röð í töflu passaði (best {score:.2f})")
            elif cfg.short_name == "ulfljotur_vefrit":
                a = _vefrit_entry(first, rel)
            else:
                a = ArticleEntry(title=(fid.title_hint if fid else None), page_start=(fid.page_start if fid else None),
                                 page_end=(fid.page_end if fid else None),
                                 provenance={"title": "filename", "pages": "filename"})
                if texts:
                    head = [l.strip() for l in first.splitlines() if l.strip()]
                    if head and cfg.short_name == "stjornmal_stjornsysla" and len(head) > 2:
                        a.title, a.provenance["title"] = head[1] if head[0].isdigit() else head[0], "page"
            a.source_file = rel
            a.pdf_pages = [0, len(texts) - 1] if texts else None
            a.article_type = a.article_type or infer_article_type(None, a.title or "", cfg.verdict_type_default)
            a.lang = detect_lang(" ".join(texts[:3]), cfg.lang_default)
            printed0 = pm.printed[0] if pm.printed else None
            if a.page_start is None and printed0 is not None:
                a.page_start, a.provenance["pages"] = printed0, "page"
            a.checks = verify_article(a, first, printed0)
            a.confidence = article_confidence(a.checks)
            m.articles.append(a)
        if cfg.short_name == "timarit_logfraedinga":
            await _enrich_with_leitir(m.articles, m, cfg, deps)
            for a in m.articles:
                a.confidence = article_confidence(a.checks | {"external_record": a.leitir_record_id is not None})
        m.confidence = min((a.confidence for a in m.articles), default=0.0)
        m.status = issue_status(m) if m.status not in ("approved", "imported") else m.status
        return m

    # ── Issue / volume PDF ─────────────────────────────────────────────────────
    pdf = deps.read_bytes(m.file)
    texts = _page_texts(pdf)
    m.sha256, m.pages = sha256_bytes(pdf), len(texts)
    pm = build_page_map(texts, cfg.short_name)
    m.page_map = pm.as_dict()
    entries: list[ArticleEntry] = []
    llm_used = False
    if cfg.short_name == "ulfljotur" and deps.xlsx_rows:
        for r in articles_for(deps.xlsx_rows, volume=m.volume, issue=m.issue):
            entries.append(ArticleEntry(title=r.title, authors=r.authors, page_start=r.page_start, xlsx_row=r.row,
                                        provenance={"title": "xlsx", "authors": "xlsx", "pages": "xlsx"}))
    else:
        toc_pages = find_toc_pages(texts)
        toc_text = "\n".join(texts[i] for i in toc_pages)
        toc = parse_toc(toc_text, style=toc_style(cfg.short_name)) if toc_text else []
        if not toc and deps.llm_toc is not None:
            toc = await deps.llm_toc("\n".join(texts[:4]))
            llm_used = True
        entries = _entries_from_toc(toc, cfg, "llm" if llm_used else "toc")
        if cfg.short_name == "timarit_logfraedinga":
            await _enrich_with_leitir(entries, m, cfg, deps)
    locate_articles(entries, pm, texts)
    for a in entries:
        first = texts[a.pdf_pages[0]] if a.pdf_pages else ""
        printed = pm.printed[a.pdf_pages[0]] if a.pdf_pages else None
        a.article_type = a.article_type or infer_article_type(None, a.title or "", cfg.verdict_type_default)
        a.lang = detect_lang(" ".join(texts[a.pdf_pages[0]:a.pdf_pages[1] + 1][:3]) if a.pdf_pages else "", cfg.lang_default)
        a.checks = verify_article(a, first, printed)
        a.confidence = article_confidence(a.checks, llm=llm_used)
    m.articles = entries
    m.confidence = min((a.confidence for a in entries), default=0.0)
    if m.status not in ("approved", "imported"):
        m.status = "review" if (llm_used or pm.quality < C.PAGE_MAP_MIN_QUALITY) else issue_status(m)
    return m
```

- [ ] **Step 4: Run tests; fix the two `_on_page` thresholds against the fixture texts**

Run: `uv run pytest -q tests/test_timarit_analyze.py -x`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/timarit/analyze.py tests/test_timarit_analyze.py
git commit -m "feat(timarit): per-issue analysis — registry list, location in PDF, verification, confidence"
```

---

### Task 18: `scripts/import_timarit.py` — `discover` and `status`

**Files:**
- Create: `scripts/import_timarit.py`
- Test: `tests/test_import_timarit_discover.py`

**Interfaces:**
- Produces CLI `uv run --env-file .env python scripts/import_timarit.py {discover|analyze|import|status} [--source S] [--issue ÁR[_ÁRG[_HEFTI]]] [--dry-run] [--workers N] [--limit N]`.
- Pure function `plan_discovery(rel_paths: list[str], read_bytes) -> tuple[list[IssueManifest], list[dict]]` returns manifests (one per issue key, with `files`) and unmatched records `{"path", "reason", "duplicate_of"?}`. Byte duplicates are detected with sha256 over the file; the first path in sorted order wins.
- DB side: `upsert_issue_row(session, m, cfg, manifest_rel_path)` inserts/updates `journal_issues` on `(source_id, year, coalesce(volume,-1), coalesce(issue,''))` and never downgrades `status` from `approved`/`imported`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_import_timarit_discover.py
import yaml

from engine.timarit.manifest import IssueManifest
from scripts.import_timarit import plan_discovery, write_unmatched

FILES = {
    "Tímarit og fræðigreinar/Tímarit Lögfræðinga/TL 2010_01.pdf": b"A",
    "Tímarit og fræðigreinar/Tímarit Lögfræðinga/TL 1985.pdf": b"B",
    "Tímarit og fræðigreinar/Úlfljótur/Tímarit/Ulfljotur_1957 11 arg/1041.pdf": b"C",
    "Tímarit og fræðigreinar/Úlfljótur/Tímarit/Ulfljotur_1958 11 arg/1041.pdf": b"C",      # byte duplicate
    "Tímarit og fræðigreinar/Úlfljótur/Tímarit/Ulfljotur_1957 11 arg/1043.pdf": b"D",
    "Tímarit og fræðigreinar/Lögfræðingur UNAK/Önnur Gögn/Hvað-með-föðurinn.pdf": b"E",
    "Tímarit og fræðigreinar/Lögbrú/LÖGFRÆÐINGUR.docx": b"F",
    "MDE Dómareifanir 2005-2021/Domareifanir_2015_1.pdf": b"G",
}


def test_discover_groups_files_into_issues_and_records_rejections(tmp_path):
    manifests, unmatched = plan_discovery(sorted(FILES), lambda rel: FILES[rel])
    keys = {(m.source, m.year, m.volume, m.issue): m for m in manifests}
    assert keys[("timarit_logfraedinga", 2010, None, "1")].file.endswith("TL 2010_01.pdf")
    assert keys[("timarit_logfraedinga", 1985, None, None)].file.endswith("TL 1985.pdf")
    ulf = keys[("ulfljotur", 1957, 11, None)]
    assert ulf.file is None and sorted(ulf.files) == [
        "Tímarit og fræðigreinar/Úlfljótur/Tímarit/Ulfljotur_1957 11 arg/1041.pdf",
        "Tímarit og fræðigreinar/Úlfljótur/Tímarit/Ulfljotur_1957 11 arg/1043.pdf"]
    assert ("ulfljotur", 1958, 11, None) not in keys
    reasons = {u["path"]: u for u in unmatched}
    assert reasons["Tímarit og fræðigreinar/Úlfljótur/Tímarit/Ulfljotur_1958 11 arg/1041.pdf"]["reason"] == "duplicate_of"
    assert reasons["Tímarit og fræðigreinar/Úlfljótur/Tímarit/Ulfljotur_1958 11 arg/1041.pdf"]["duplicate_of"].endswith("1957 11 arg/1041.pdf")
    assert reasons["Tímarit og fræðigreinar/Lögfræðingur UNAK/Önnur Gögn/Hvað-með-föðurinn.pdf"]["reason"] == "working_files"
    assert reasons["Tímarit og fræðigreinar/Lögbrú/LÖGFRÆÐINGUR.docx"]["reason"] == "not_pdf"
    assert reasons["MDE Dómareifanir 2005-2021/Domareifanir_2015_1.pdf"]["reason"] == "deferred_mde"
    assert all(m.status == "pending" for m in manifests)


def test_discover_records_byte_duplicates_once(tmp_path):
    manifests, unmatched = plan_discovery(sorted(FILES), lambda rel: FILES[rel])
    all_files = [f for m in manifests for f in (m.files or [m.file])]
    assert len(all_files) == len(set(all_files)) and not any("1958" in f for f in all_files)


def test_write_unmatched_yaml(tmp_path):
    p = tmp_path / "_unmatched.yaml"
    write_unmatched([{"path": "a.docx", "reason": "not_pdf"}], p)
    assert yaml.safe_load(p.read_text(encoding="utf-8")) == [{"path": "a.docx", "reason": "not_pdf"}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_import_timarit_discover.py` → `ModuleNotFoundError: No module named 'scripts.import_timarit'`.

- [ ] **Step 3: Implement the module skeleton, `plan_discovery`, `write_unmatched`, `discover`, `status`**

```python
# scripts/import_timarit.py
"""Journals: discover → analyze → (review) → import  (spec 2026-10-03 §7).

    uv run --env-file .env python scripts/import_timarit.py discover
    uv run --env-file .env python scripts/import_timarit.py analyze --source timarit_logfraedinga --workers 6
    uv run --env-file .env python scripts/import_timarit.py import --dry-run
    uv run --env-file .env python scripts/import_timarit.py status

The archive in TIMARIT_DROPFOLDER_DIR is read-only; manifests live in
data/timarit/manifests (git); state lives in journal_issues.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import sys
from collections import defaultdict
from pathlib import Path
from typing import Callable

import yaml
from sqlalchemy import select, text

sys.path.insert(0, str(Path(__file__).parent.parent))

import engine.database.connection as _db
from engine.config.sources import TIMARIT_DROPFOLDER_DIR, get_config
from engine.database.models import JournalIssue, Source
from engine.timarit import config as C
from engine.timarit.manifest import (IssueManifest, issue_label, load_manifest, manifest_path,
                                     merge_preserving_user, save_manifest)
from engine.timarit.naming import identify, issue_key, nfc, reject_reason

log = logging.getLogger("import_timarit")
REPO = Path(__file__).resolve().parent.parent
DROP = Path(TIMARIT_DROPFOLDER_DIR)


def _list_archive() -> list[str]:
    return sorted(nfc(str(p.relative_to(DROP))) for p in DROP.rglob("*")
                  if p.is_file() and p.name != ".DS_Store" and "_meta" not in p.parts)


def plan_discovery(rel_paths: list[str], read_bytes: Callable[[str], bytes]
                   ) -> tuple[list[IssueManifest], list[dict]]:
    unmatched: list[dict] = []
    seen_hash: dict[str, str] = {}
    groups: dict[tuple, list] = defaultdict(list)
    for rel in rel_paths:
        rel = nfc(rel)
        why = reject_reason(rel)
        if why:
            unmatched.append({"path": rel, "reason": why})
            continue
        h = hashlib.sha256(read_bytes(rel)).hexdigest()
        if h in seen_hash:
            unmatched.append({"path": rel, "reason": "duplicate_of", "duplicate_of": seen_hash[h]})
            continue
        seen_hash[h] = rel
        fid = identify(rel)
        groups[issue_key(fid)].append(fid)
    manifests: list[IssueManifest] = []
    for (source, year, volume, issue), fids in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0, kv[0][2] or 0, kv[0][3] or "")):
        cfg = get_config(source)
        containers = [f for f in fids if f.granularity in ("issue", "volume")]
        year_end = next((f.year_end for f in fids if f.year_end), None)
        m = IssueManifest(source=source, year=year, volume=volume, issue=issue,
                          label=issue_label(cfg, year, volume, issue), year_end=year_end,
                          file=containers[0].rel_path if containers else None,
                          files=sorted(f.rel_path for f in fids))
        if len(containers) > 1:
            m.error = "fleiri en eitt heftisskjal fyrir sama hefti: " + "; ".join(c.rel_path for c in containers)
        manifests.append(m)
    return manifests, unmatched


def write_unmatched(items: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(items, allow_unicode=True, sort_keys=False), encoding="utf-8")


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
    sid = await _source_id(session, m.source)
    await session.execute(text("""
        INSERT INTO journal_issues (id, source_id, year, volume, issue, label, file_path, sha256, page_count,
                                    manifest_path, status, confidence, error)
        VALUES (gen_random_uuid(), :sid, :year, :volume, :issue, :label, :file_path, :sha256, :pages,
                :manifest, :status, :confidence, :error)
        ON CONFLICT (source_id, year, coalesce(volume, -1), coalesce(issue, ''))
        DO UPDATE SET label = EXCLUDED.label, file_path = EXCLUDED.file_path, sha256 = EXCLUDED.sha256,
                      page_count = EXCLUDED.page_count, manifest_path = EXCLUDED.manifest_path,
                      confidence = EXCLUDED.confidence, error = EXCLUDED.error, updated_at = now(),
                      status = CASE WHEN journal_issues.status IN ('approved', 'imported') THEN journal_issues.status
                                    ELSE EXCLUDED.status END
    """), {"sid": sid, "year": m.year or 0, "volume": m.volume, "issue": m.issue, "label": m.label,
           "file_path": m.file, "sha256": m.sha256, "pages": m.pages, "manifest": manifest_rel,
           "status": m.status, "confidence": m.confidence, "error": m.error})


async def cmd_discover(args) -> None:
    manifests, unmatched = plan_discovery(_list_archive(), lambda rel: (DROP / rel).read_bytes())
    write_unmatched(unmatched, REPO / C.UNMATCHED_PATH)
    written = 0
    await _db.init_db(create_tables=False)
    async with _db.AsyncSessionLocal() as session:
        for m in manifests:
            if args.source and m.source != args.source:
                continue
            path = manifest_path(REPO / C.MANIFEST_ROOT, m.source, m.year, m.volume, m.issue)
            old = load_manifest(path) if path.exists() else None
            m = merge_preserving_user(old, m)
            if old is not None:
                m.articles = old.articles          # discover never discards an analysis
                m.status = old.status if old.status != "pending" else m.status
            if not args.dry_run:
                save_manifest(m, path)
                await upsert_issue_row(session, m, str(path.relative_to(REPO)))
            written += 1
        if not args.dry_run:
            await session.commit()
    await _db.dispose_db()
    log.info("discover: %d hefti, %d skrár óþekktar/tvíteknar → %s", written, len(unmatched), C.UNMATCHED_PATH)


async def cmd_status(args) -> None:
    await _db.init_db(create_tables=False)
    async with _db.AsyncSessionLocal() as session:
        rows = (await session.execute(text("""
            SELECT s.short_name, ji.status, count(*) FROM journal_issues ji JOIN sources s ON s.id = ji.source_id
            GROUP BY 1, 2 ORDER BY 1, 2"""))).all()
        for short, status, n in rows:
            print(f"{short:28s} {status:9s} {n:5d}")
        failed = (await session.execute(text(
            "SELECT label, error FROM journal_issues WHERE status = 'failed' ORDER BY label"))).all()
        for label, err in failed:
            print(f"  ✗ {label}: {err}")
    await _db.dispose_db()


def main(argv=None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["discover", "analyze", "import", "status"])
    ap.add_argument("--source")
    ap.add_argument("--issue", help="ÁR[_ÁRG[_HEFTI]] (x = vantar)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int)
    args = ap.parse_args(argv)
    cmd = {"discover": cmd_discover, "status": cmd_status, "analyze": cmd_analyze, "import": cmd_import}[args.command]
    asyncio.run(cmd(args))


if __name__ == "__main__":
    main()
```

`cmd_analyze` and `cmd_import` are defined in Tasks 19 and 20; until then add `async def cmd_analyze(args): raise SystemExit("analyze: sjá Task 19")` and the same for `cmd_import` so the module imports.

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q tests/test_import_timarit_discover.py` → PASS.

- [ ] **Step 5: Dry-run discover on the real archive (read-only; no DB write)**

Run: `uv run --env-file .env python scripts/import_timarit.py discover --dry-run 2>&1 | tail -3`
Expected: `discover: N hefti, 100 skrár óþekktar/tvíteknar` where N is about 1,000 and the unmatched count equals 63 (MDE) + 11 (Önnur Gögn) + 12 (byte duplicates, including Úlfljótur 1958 and Lögfræðingur 2010) + non-PDFs. Then run without `--dry-run` once the user agrees; this writes ~1,000 YAML files under `data/timarit/manifests/` and the `journal_issues` rows.

- [ ] **Step 6: Commit**

```bash
git add scripts/import_timarit.py tests/test_import_timarit_discover.py
git commit -m "feat(timarit): import_timarit.py discover/status — issues from the archive, duplicates and rejections recorded"
```

---

### Task 19: `import_timarit.py analyze`

**Files:**
- Modify: `scripts/import_timarit.py` (replace the `cmd_analyze` stub)
- Modify: `engine/timarit/analyze.py` (store `bin_ratio` in `page_map` so the pilot can choose the OCR threshold)
- Test: `tests/test_import_timarit_analyze.py`

**Interfaces:**
- Produces: `async def analyze_manifests(paths: list[Path], *, deps_factory: Callable[[], AnalyzeDeps], save: bool) -> dict[str, int]` (counts by resulting status) and `cmd_analyze(args)` which selects manifests by `--source`/`--issue`/`--limit` whose status is `pending` or `review` (never `approved`/`imported`, which only the user or `import` moves), runs `analyze_issue`, merges with `merge_preserving_user`, saves, and updates `journal_issues` (`status`, `confidence`, `sha256`, `page_count`, `error`). A failing issue gets `status: failed` and the exception's first line in `error`; the run continues.
- Real deps: `read_bytes = lambda rel: (DROP / rel).read_bytes()`; `leitir` wraps `search_articles(client, q, cache_dir=C.CACHE_DIR / "leitir", journal_filter=journal)` with one shared `httpx.AsyncClient`; `llm_toc` wraps `toc_via_llm(text, cache_dir=C.CACHE_DIR / "llm")` only when `ANTHROPIC_API_KEY` is set (else `None`); `xlsx_rows = load_xlsx(C.XLSX_PATH)` when the file exists.
- `analyze` is sequential (page texts via PyMuPDF are fast; leitir.is is throttled anyway); `--workers` applies to `import`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_import_timarit_analyze.py
import fitz

from engine.timarit.analyze import AnalyzeDeps
from engine.timarit.manifest import ArticleEntry, IssueManifest, load_manifest, save_manifest
from scripts.import_timarit import analyze_manifests


def _pdf():
    d = fitz.open()
    for t in ["Kápa", "1 Jón Jónsson: Fyrsta greinin 3 Anna Pálsdóttir: Önnur greinin",
              "1\nJón Jónsson\nFyrsta greinin\nTexti.", "2\nFramhald.", "3\nAnna Pálsdóttir\nÖnnur greinin\nTexti."]:
        d.new_page().insert_text((72, 72), t)
    return d.tobytes()


def _deps(pdf):
    return lambda: AnalyzeDeps(read_bytes=lambda rel: pdf, leitir=None, llm_toc=None, xlsx_rows=[])


async def test_analyze_writes_articles_and_status_and_keeps_user_edits(tmp_path):
    p = tmp_path / "timarit_logfraedinga" / "2010_60_1.yaml"
    m = IssueManifest(source="timarit_logfraedinga", year=2010, volume=60, issue="1",
                      label="Tímarit lögfræðinga, 60. árg. 1. hefti (2010)", file="x.pdf", files=["x.pdf"])
    m.articles = [ArticleEntry(title="Titill frá notanda", page_start=1, provenance={"title": "user"})]
    save_manifest(m, p)
    counts = await analyze_manifests([p], deps_factory=_deps(_pdf()), save=True)
    out = load_manifest(p)
    assert sum(counts.values()) == 1 and out.status in ("approved", "review")
    assert len(out.articles) == 2 and out.pages == 5 and "bin_ratio" in out.page_map
    assert out.articles[0].title == "Titill frá notanda" and out.articles[0].provenance["title"] == "user"
    assert out.articles[1].title == "Önnur greinin" and out.articles[1].pdf_pages == [4, 4]


async def test_analyze_marks_failed_and_continues(tmp_path):
    good = tmp_path / "a" / "2010_60_1.yaml"; bad = tmp_path / "a" / "2010_60_2.yaml"
    save_manifest(IssueManifest(source="timarit_logfraedinga", year=2010, volume=60, issue="1", label="A", file="x.pdf", files=["x.pdf"]), good)
    save_manifest(IssueManifest(source="timarit_logfraedinga", year=2010, volume=60, issue="2", label="B", file="missing.pdf", files=["missing.pdf"]), bad)
    pdf = _pdf()

    def read(rel):
        if rel == "missing.pdf":
            raise FileNotFoundError(rel)
        return pdf
    counts = await analyze_manifests([bad, good], deps_factory=lambda: AnalyzeDeps(read, None, None, []), save=True)
    assert counts.get("failed") == 1 and load_manifest(bad).status == "failed" and "FileNotFoundError" in load_manifest(bad).error
    assert load_manifest(good).status in ("approved", "review")


async def test_analyze_never_touches_approved_or_imported(tmp_path):
    p = tmp_path / "a" / "2010_60_1.yaml"
    save_manifest(IssueManifest(source="timarit_logfraedinga", year=2010, volume=60, issue="1", label="A", file="x.pdf", files=["x.pdf"], status="imported"), p)
    counts = await analyze_manifests([p], deps_factory=_deps(_pdf()), save=True)
    assert counts == {"skipped": 1} and load_manifest(p).status == "imported" and load_manifest(p).articles == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest -q tests/test_import_timarit_analyze.py` → `ImportError: cannot import name 'analyze_manifests'`.

- [ ] **Step 3: Implement**

In `engine/timarit/analyze.py`, after `m.page_map = pm.as_dict()` (issue path) and in the article-granularity path after the loop, add `m.page_map["bin_ratio"] = round(bin_known_ratio(" ".join(texts[:20])), 3)` (import `bin_known_ratio` from `engine.timarit.extract`; for article-granularity use the concatenation of each file's first page).

In `scripts/import_timarit.py`:

```python
import os
import httpx
from engine.timarit.analyze import AnalyzeDeps, analyze_issue
from engine.timarit.leitir_articles import search_articles
from engine.timarit.llm_toc import toc_via_llm
from engine.timarit.ulfljotur_xlsx import load_xlsx


def _select_manifests(args) -> list[Path]:
    root = REPO / C.MANIFEST_ROOT
    paths = sorted(root.glob("*/*.yaml"))
    if args.source:
        paths = [p for p in paths if p.parent.name == args.source]
    if args.issue:
        paths = [p for p in paths if p.stem == args.issue or p.stem.startswith(args.issue + "_")]
    return paths[: args.limit] if args.limit else paths


def _real_deps_factory(client: httpx.AsyncClient):
    xlsx_rows = load_xlsx(C.XLSX_PATH) if C.XLSX_PATH.exists() else []

    async def leitir(q, journal):
        return await search_articles(client, q, cache_dir=C.CACHE_DIR / "leitir", journal_filter=journal)

    async def llm(text):
        return await toc_via_llm(text, cache_dir=C.CACHE_DIR / "llm")

    return lambda: AnalyzeDeps(read_bytes=lambda rel: (DROP / rel).read_bytes(), leitir=leitir,
                               llm_toc=llm if os.environ.get("ANTHROPIC_API_KEY") else None, xlsx_rows=xlsx_rows)


async def analyze_manifests(paths: list[Path], *, deps_factory, save: bool, session=None) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for path in paths:
        m = load_manifest(path)
        if m.status in ("approved", "imported"):
            counts["skipped"] += 1
            continue
        cfg = get_config(m.source)
        old = load_manifest(path)
        try:
            new = await analyze_issue(m, cfg, deps_factory())
            new = merge_preserving_user(old, new)
        except Exception as exc:  # noqa: BLE001 — one bad issue must not stop the run
            log.exception("analyze failed for %s", path.name)
            new = old
            new.status, new.error = "failed", f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"
        counts[new.status] += 1
        if save:
            save_manifest(new, path)
            if session is not None:
                await upsert_issue_row(session, new, str(path.relative_to(REPO)))
        log.info("%-9s %s  (%d greinar, öryggi %.2f)", new.status, new.label, len(new.articles), new.confidence)
    return dict(counts)


async def cmd_analyze(args) -> None:
    paths = _select_manifests(args)
    await _db.init_db(create_tables=False)
    async with httpx.AsyncClient(headers={"User-Agent": "Lausnir/2.0"}) as client, _db.AsyncSessionLocal() as session:
        counts = await analyze_manifests(paths, deps_factory=_real_deps_factory(client), save=not args.dry_run,
                                         session=None if args.dry_run else session)
        if not args.dry_run:
            await session.commit()
    await _db.dispose_db()
    log.info("analyze: %s", dict(counts))
```

`upsert_issue_row` updates `status` only when the row is not `approved`/`imported` — a `failed` or `review` result from analyze therefore never demotes an approved issue, matching `merge_preserving_user`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q tests/test_import_timarit_analyze.py tests/test_timarit_analyze.py` → PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/import_timarit.py engine/timarit/analyze.py tests/test_import_timarit_analyze.py
git commit -m "feat(timarit): analyze command — manifests analysed, merged with user edits, failures isolated"
```

---

### Task 20: `import_timarit.py import` — documents, PDFs, markdown

**Files:**
- Modify: `scripts/import_timarit.py` (replace the `cmd_import` stub; add `_upsert_doc`, `build_article_raw`, `import_manifest`)
- Modify: `tests/test_import_upsert_parity.py` (add the journal importer to the field-parity test)
- Test: `tests/test_import_timarit_import.py`

**Interfaces:**
- Produces:
```python
def build_article_raw(m: IssueManifest, a: ArticleEntry, ex: ExtractedText, source_file: str) -> dict   # the raw dict _extract_timarit consumes (Task 6 contract)
def article_pdf_bytes(m: IssueManifest, a: ArticleEntry, read_bytes) -> bytes      # cut from issue or the article's own file
def _work(item: tuple) -> tuple[str, dict | None, str | None]                       # worker: (key, raw-extraction fields) or error
async def import_manifest(path: Path, *, session, read_bytes, raw_dir: Path, dry_run: bool, workers: int) -> dict[str, int]
async def _upsert_doc(session, doc: Document) -> uuid.UUID        # returns the row id actually stored (existing id on conflict)
```
- Behaviour: only manifests with `status == "approved"`, or `status == "imported"` whose current `manifest_sha256` differs from `journal_issues.manifest_sha256`, are imported. Per article: bytes → `extract_article(..., footnotes=cfg.has_footnotes, ocr=docling_ocr_pdf if needed, ocr_bin_threshold=C.OCR_BIN_RATIO_THRESHOLD)` in a worker process → `Extractor(cfg).extract(raw)` → `Document` → `validate` → `_upsert_doc` → `write_markdown` → article PDF written to `cfg.pdf_path(vf)` → `documents.journal_issue_id` set. Issue PDF copied once to `raw/{source}/hefti/{year}_{volume}_{issue}.pdf` (`x` for missing). Then `journal_issues.status = 'imported'`, `manifest_sha256`, `file_path`, `sha256`; manifest `status: imported` saved. Articles with `pdf_pages is None` are skipped with a note counted as `skipped_no_pages`. The archive is never written to.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_import_timarit_import.py
"""Import with a fake session (no DB) — the same shape test_import_haestirettur uses."""
import uuid
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest

import engine.config.sources as sources_config
from engine.timarit.manifest import ArticleEntry, IssueManifest, save_manifest
from scripts.import_timarit import article_pdf_bytes, build_article_raw, import_manifest


def _pdf(pages):
    d = fitz.open()
    for t in pages:
        d.new_page().insert_text((72, 72), t)
    return d.tobytes()


ISSUE = _pdf(["Kápa", "Efnisyfirlit", "7\nJón Jónsson\nFyrsta greinin\nTexti fyrstu greinar sem er nógu langur.", "8\nFramhald.", "9\nAnna\nÖnnur\nTexti."])


class _Session:
    def __init__(self):
        self.statements = []
        self.committed = 0

    async def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.statements.append((sql, params))
        res = SimpleNamespace()
        if sql.startswith("SELECT id FROM sources") or "FROM sources" in sql and "short_name" in sql:
            res.scalar_one_or_none = lambda: uuid.UUID("11111111-1111-1111-1111-111111111111")
        elif "FROM journal_issues" in sql:
            res.first = lambda: (uuid.UUID("22222222-2222-2222-2222-222222222222"), None)
        elif "INSERT INTO documents" in sql or "ON CONFLICT" in sql:
            res.scalar_one = lambda: uuid.UUID("33333333-3333-3333-3333-333333333333")
        return res

    async def flush(self): pass
    async def commit(self): self.committed += 1


@pytest.fixture
def raw_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(sources_config, "RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setattr(sources_config, "MARKDOWN_DIR", str(tmp_path / "markdown"))
    return tmp_path / "raw"


def _manifest(tmp_path):
    p = tmp_path / "timarit_logfraedinga" / "2010_60_1.yaml"
    m = IssueManifest(source="timarit_logfraedinga", year=2010, volume=60, issue="1",
                      label="Tímarit lögfræðinga, 60. árg. 1. hefti (2010)", file="x/TL 2010_01.pdf", files=["x/TL 2010_01.pdf"],
                      status="approved", pages=5)
    m.articles = [
        ArticleEntry(title="Fyrsta greinin", authors=["Jón Jónsson"], article_type="Fræðigrein", page_start=7, page_end=8,
                     pdf_pages=[2, 3], lang="is", provenance={"title": "toc"}, confidence=0.9),
        ArticleEntry(title="Önnur", authors=["Anna"], article_type="Fræðigrein", page_start=9, page_end=9, pdf_pages=[4, 4], lang="is", confidence=0.9),
        ArticleEntry(title="Utan skjals", page_start=400, pdf_pages=None, confidence=0.1),
    ]
    save_manifest(m, p)
    return p, m


def test_article_pdf_bytes_cuts_issue_or_reads_own_file():
    m = IssueManifest(source="timarit_logfraedinga", year=2010, volume=60, issue="1", label="L", file="issue.pdf")
    a = ArticleEntry(pdf_pages=[2, 3])
    assert fitz.open(stream=article_pdf_bytes(m, a, lambda rel: ISSUE), filetype="pdf").page_count == 2
    m2 = IssueManifest(source="ulfljotur", year=1947, volume=1, issue=None, label="L", file=None)
    own = _pdf(["173\nTexti"])
    assert article_pdf_bytes(m2, ArticleEntry(source_file="173.pdf", pdf_pages=[0, 0]), lambda rel: own) == own


def test_build_article_raw_matches_extractor_contract():
    from engine.timarit.extract import ExtractedText
    m = IssueManifest(source="timarit_logfraedinga", year=2010, volume=60, issue="1", label="L", file="x.pdf")
    a = ArticleEntry(title="T", authors=["A"], article_type="Fræðigrein", page_start=7, page_end=8, pdf_pages=[2, 3], lang="is",
                     provenance={"title": "toc"}, checks={"title_on_page": True}, confidence=0.9)
    raw = build_article_raw(m, a, ExtractedText("Texti", [{"char": 0, "pdf_page": 0, "printed": 7}], "embedded", 0.9), "x.pdf")
    for k in ("title", "authors", "article_type", "page_start", "page_end", "lang", "volume", "issue", "year", "date",
              "summary", "keywords", "pdf_text", "page_offsets", "source_filename", "pdf_pages", "provenance",
              "checks", "confidence", "leitir_record_id", "xlsx_row", "ocr_source", "date_precision"):
        assert k in raw, k
    assert raw["pdf_text"] == "Texti" and raw["year"] == 2010 and raw["date_precision"] == "year" and raw["ocr_source"] == "embedded"


async def test_import_writes_documents_pdfs_markdown_and_marks_imported(tmp_path, raw_dir):
    p, m = _manifest(tmp_path)
    session = _Session()
    counts = await import_manifest(p, session=session, read_bytes=lambda rel: ISSUE, raw_dir=raw_dir, dry_run=False, workers=1)
    assert counts["imported"] == 2 and counts["skipped_no_pages"] == 1
    upserts = [s for s, _ in session.statements if "INSERT INTO documents" in s]
    assert len(upserts) == 2 and all("journal_issue_id" in s and "page_offsets" in s and "volume" in s for s in upserts)
    assert (raw_dir / "timarit_logfraedinga" / "TL_60-1_2010_bls-7-8.pdf").exists()
    assert (raw_dir / "timarit_logfraedinga" / "hefti" / "2010_60_1.pdf").read_bytes() == ISSUE
    md = (Path(str(raw_dir).replace("/raw", "/markdown")) / "timarit_logfraedinga" / "TL_60-1_2010_bls-7-8.md").read_text(encoding="utf-8")
    assert md.startswith("# Fyrsta greinin") and "<!-- bls. 7 -->" in md
    from engine.timarit.manifest import load_manifest
    assert load_manifest(p).status == "imported"
    status_updates = [s for s, _ in session.statements if "UPDATE journal_issues" in s]
    assert status_updates and "imported" in str(status_updates[-1])


async def test_import_skips_imported_issue_with_unchanged_manifest(tmp_path, raw_dir):
    p, m = _manifest(tmp_path)
    m.status = "imported"; save_manifest(m, p)
    from engine.timarit.manifest import manifest_sha256
    session = _Session()
    session_sha = manifest_sha256(p)

    async def execute(stmt, params=None):
        sql = " ".join(str(stmt).split())
        if "FROM journal_issues" in sql:
            return SimpleNamespace(first=lambda: (uuid.uuid4(), session_sha))
        return await _Session.execute(session, stmt, params)
    session.execute = execute
    counts = await import_manifest(p, session=session, read_bytes=lambda rel: ISSUE, raw_dir=raw_dir, dry_run=False, workers=1)
    assert counts == {"unchanged": 1}


async def test_dry_run_writes_nothing(tmp_path, raw_dir):
    p, _ = _manifest(tmp_path)
    session = _Session()
    counts = await import_manifest(p, session=session, read_bytes=lambda rel: ISSUE, raw_dir=raw_dir, dry_run=True, workers=1)
    assert counts["imported"] == 2 and not raw_dir.exists()
    assert not [s for s, _ in session.statements if "INSERT INTO documents" in s]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_import_timarit_import.py` → `ImportError: cannot import name 'article_pdf_bytes'`.

- [ ] **Step 3: Implement**

Add to `scripts/import_timarit.py`:

```python
import shutil
import uuid
from datetime import datetime
from multiprocessing import Pool

from sqlalchemy import func, null as sa_null
from sqlalchemy.dialects.postgresql import insert as pg_insert

import engine.config.sources as sources_config
from engine.database.models import Document
from engine.processors.extractor import Extractor
from engine.processors.pdf_parser import docling_ocr_pdf
from engine.processors.renderer import unique_verdict_filename, verdict_filename, write_markdown
from engine.processors.validator import validate
from engine.timarit.extract import ExtractedText, extract_article
from engine.timarit.manifest import ArticleEntry, external_id, manifest_sha256
from engine.timarit.splitter import cut_pages, sha256_bytes


def article_pdf_bytes(m: IssueManifest, a: ArticleEntry, read_bytes) -> bytes:
    if m.file is None or a.source_file:
        return read_bytes(a.source_file)
    first, last = a.pdf_pages
    return cut_pages(read_bytes(m.file), first, last)


def build_article_raw(m: IssueManifest, a: ArticleEntry, ex: ExtractedText, source_file: str) -> dict:
    return {
        "title": a.title, "authors": a.authors, "author_titles": a.author_titles, "article_type": a.article_type,
        "page_start": a.page_start, "page_end": a.page_end, "lang": a.lang, "volume": m.volume, "issue": m.issue,
        "year": m.year, "date": a.date, "summary": a.summary, "keywords": a.keywords,
        "pdf_text": ex.body, "page_offsets": ex.page_offsets, "source_filename": source_file, "pdf_pages": a.pdf_pages,
        "provenance": a.provenance, "checks": a.checks, "confidence": a.confidence,
        "leitir_record_id": a.leitir_record_id, "xlsx_row": a.xlsx_row, "ocr_source": ex.ocr_source,
        "date_precision": "day" if a.date else "year", "bin_ratio": round(ex.bin_ratio, 3),
        "issue_label": m.label, "issue_file": m.file,
    }


def _work(item: tuple) -> tuple[int, dict | None, bytes | None, str | None]:
    """Worker: (index, pdf_bytes, source, footnotes) → (index, ExtractedText as dict, pdf_bytes, error)."""
    i, pdf, source, footnotes = item
    try:
        ex = extract_article(pdf, source=source, footnotes=footnotes,
                             ocr=lambda b: docling_ocr_pdf(b, timeout=900), ocr_bin_threshold=C.OCR_BIN_RATIO_THRESHOLD)
        return i, {"body": ex.body, "page_offsets": ex.page_offsets, "ocr_source": ex.ocr_source, "bin_ratio": ex.bin_ratio}, pdf, None
    except Exception as exc:  # noqa: BLE001
        return i, None, None, f"{type(exc).__name__}: {exc}"


async def _upsert_doc(session, doc: Document) -> uuid.UUID:
    def _v(val):
        return sa_null() if val is None else val
    values = {
        "id": doc.id, "source_id": doc.source_id, "external_id": doc.external_id, "url": _v(doc.url),
        "raw_api_data": _v(doc.raw_api_data), "case_number": _v(doc.case_number), "document_date": _v(doc.document_date),
        "court": _v(doc.court), "verdict_type": _v(doc.verdict_type), "instance_tier": _v(doc.instance_tier),
        "case_type": _v(doc.case_type), "plaintiffs": _v(doc.plaintiffs), "defendants": _v(doc.defendants),
        "keywords": _v(doc.keywords), "summary": _v(doc.summary), "body_text": _v(doc.body_text),
        "lower_body_text": _v(doc.lower_body_text), "volume": _v(doc.volume), "issue": _v(doc.issue),
        "page_start": _v(doc.page_start), "page_end": _v(doc.page_end), "lang": _v(doc.lang),
        "page_offsets": _v(doc.page_offsets), "journal_issue_id": _v(doc.journal_issue_id),
        "verdict_filename": _v(doc.verdict_filename), "validation_errors": _v(doc.validation_errors),
    }
    update_cols = {k: v for k, v in values.items() if k not in ("id", "source_id", "external_id")}
    update_cols["updated_at"] = func.now()
    stmt = (pg_insert(Document).values(**values)
            .on_conflict_do_update(constraint="uq_doc_source_external", set_=update_cols)
            .returning(Document.id))
    return (await session.execute(stmt)).scalar_one()


async def _issue_row(session, m: IssueManifest):
    return (await session.execute(text("""
        SELECT ji.id, ji.manifest_sha256 FROM journal_issues ji JOIN sources s ON s.id = ji.source_id
        WHERE s.short_name = :s AND ji.year = :y AND coalesce(ji.volume, -1) = coalesce(:v, -1)
          AND coalesce(ji.issue, '') = coalesce(:i, '')"""),
        {"s": m.source, "y": m.year or 0, "v": m.volume, "i": m.issue})).first()


async def import_manifest(path: Path, *, session, read_bytes, raw_dir: Path, dry_run: bool, workers: int) -> dict[str, int]:
    m = load_manifest(path)
    cfg = get_config(m.source)
    counts: dict[str, int] = defaultdict(int)
    row = await _issue_row(session, m)
    issue_id, stored_sha = (row[0], row[1]) if row else (None, None)
    current_sha = manifest_sha256(path)
    if m.status == "imported" and stored_sha == current_sha:
        return {"unchanged": 1}
    if m.status not in ("approved", "imported"):
        return {"not_approved": 1}
    source_id = await _source_id(session, m.source)

    jobs = []
    for i, a in enumerate(m.articles):
        if a.pdf_pages is None:
            counts["skipped_no_pages"] += 1
            continue
        jobs.append((i, article_pdf_bytes(m, a, read_bytes), m.source, cfg.has_footnotes))
    results = (Pool(processes=workers).imap_unordered(_work, jobs) if workers > 1 else map(_work, jobs))

    taken: set[str] = set()
    issue_rel = None
    if m.file and not dry_run:
        dest = raw_dir / m.source / "hefti" / f"{m.year or 'x'}_{m.volume if m.volume is not None else 'x'}_{m.issue or 'x'}.pdf"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            dest.write_bytes(read_bytes(m.file))
        issue_rel = str(dest.relative_to(raw_dir.parent))
        m.sha256 = m.sha256 or sha256_bytes(dest.read_bytes())

    for i, exd, pdf, err in results:
        a = m.articles[i]
        if err:
            a.note = (a.note + " " if a.note else "") + f"innflutningur mistókst: {err}"
            counts["failed"] += 1
            continue
        ex = ExtractedText(exd["body"], exd["page_offsets"], exd["ocr_source"], exd["bin_ratio"])
        src_file = a.source_file or m.file
        fields = Extractor(cfg).extract(build_article_raw(m, a, ex, src_file))
        doc = Document(id=uuid.uuid4(), source_id=source_id, external_id=external_id(m, a), url=None,
                       journal_issue_id=issue_id, **fields)
        doc.validation_errors = validate(doc, cfg) or None
        vf = unique_verdict_filename(verdict_filename(doc, cfg), taken)
        taken.add(vf)
        doc.verdict_filename = vf
        if dry_run:
            print(f"  {doc.external_id:>22}  {vf}  {len(doc.body_text or '')} stafir  {doc.validation_errors or ''}")
            counts["imported"] += 1
            continue
        await _upsert_doc(session, doc)
        write_markdown(doc, cfg, vf=vf)
        pdf_path = cfg.pdf_path(vf)
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(pdf)
        counts["imported"] += 1

    if not dry_run:
        m.status = "imported"
        save_manifest(m, path)
        await session.execute(text("""
            UPDATE journal_issues SET status = 'imported', manifest_sha256 = :sha, file_path = coalesce(:fp, file_path),
                   sha256 = coalesce(:s, sha256), page_count = coalesce(:pc, page_count), updated_at = now()
            WHERE id = :id"""), {"sha": manifest_sha256(path), "fp": issue_rel, "s": m.sha256, "pc": m.pages, "id": issue_id})
    return dict(counts)


async def cmd_import(args) -> None:
    paths = _select_manifests(args)
    await _db.init_db(create_tables=False)
    totals: dict[str, int] = defaultdict(int)
    async with _db.AsyncSessionLocal() as session:
        for path in paths:
            try:
                c = await import_manifest(path, session=session, read_bytes=lambda rel: (DROP / rel).read_bytes(),
                                          raw_dir=Path(sources_config.RAW_DIR), dry_run=args.dry_run, workers=args.workers)
                if not args.dry_run:
                    await session.commit()
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                log.exception("import failed for %s", path.name)
                m = load_manifest(path); m.status, m.error = "failed", f"{type(exc).__name__}: {str(exc).splitlines()[0]}"
                save_manifest(m, path)
                c = {"failed_issue": 1}
            for k, v in c.items():
                totals[k] += v
            log.info("%s → %s", path.stem, dict(c))
    await _db.dispose_db()
    log.info("import: %s", dict(totals))
```

The dry-run output is read by the user, hence Icelandic; log lines are English like the rest of the scripts.

- [ ] **Step 4: Upsert parity**

In `tests/test_import_upsert_parity.py` add after `SOURCES`:

```python
# Journals: scripts/import_timarit.py::_upsert_doc must write every field
# _extract_timarit returns (same guard, new importer).
JOURNALS = [("timarit", "_extract_timarit")]
```
and change the first test's decorator to `@pytest.mark.parametrize("short_name,extract_fn", SOURCES + JOURNALS)`. The `case_type` test stays on `SOURCES` only.

- [ ] **Step 5: Run tests**

Run: `uv run pytest -q tests/test_import_timarit_import.py tests/test_import_upsert_parity.py tests/test_import_timarit_discover.py tests/test_import_timarit_analyze.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/import_timarit.py tests/test_import_timarit_import.py tests/test_import_upsert_parity.py
git commit -m "feat(timarit): import command — articles cut, extracted with page offsets, upserted, rendered; issue PDF to RAW"
```

---

### Task 21: API, frontend and MCP touch points

**Files:**
- Modify: `engine/search/queries.py` (`_citation` signature; `get_document` SELECT + return; search projections), `engine/search/passage_search.py` (projection + `_citation` call)
- Modify: `frontend/src/api/types.ts`, `frontend/src/components/DocPanel.tsx`
- Modify: `engine/mcp/server.py` (`INSTRUCTIONS`)
- Test: `tests/test_api_citations.py` (DOC_ROW gains keys), `tests/test_api_journal.py`, `frontend/src/components/DocPanel.test.tsx`, `tests/test_mcp_server.py`

**Interfaces:**
- `_citation(short_name, court, case_number, document_date, verdict_type, *, plaintiffs=None, volume=None, issue=None, page_start=None, page_end=None) -> str` — passes the extra fields into the `Document` it builds so `to_urlausn` can render journals.
- `get_document(...)` returns additionally: `"volume", "issue", "page_start", "page_end", "lang"` and `"journal": {"label": str, "year": int, "volume": int|None, "issue": str|None, "page_start": int|None, "page_end": int|None} | None` (NULL for non-journals). SELECT adds `d.volume, d.issue, d.page_start, d.page_end, d.lang, ji.label AS issue_label` via `LEFT JOIN journal_issues ji ON ji.id = d.journal_issue_id`.
- Search projections (`search_documents` page query and both SELECTs in `search_by_passages`) add `d.plaintiffs, d.volume, d.issue, d.page_start, d.page_end` and pass them to `_citation`.
- `types.ts`: `DocumentDetail` gains `volume: number | null; issue: string | null; page_start: number | null; page_end: number | null; lang: string | null; journal: JournalRef | null;` with `export interface JournalRef { label: string; year: number; volume: number | null; issue: string | null; page_start: number | null; page_end: number | null }`.
- `DocPanel`: when `doc.journal` is set, the meta line is `[doc.journal.label, pages]` where `pages = "bls. 7–47"` (en dash) or `"bls. 7"`; the h1 stays the title and authors render from `plaintiffs` as today.
- `INSTRUCTIONS`: append to the Icelandic part: `"Greinar úr tímaritum (scope `timarit`) eru vitnaðar með `urlausn` greinarinnar og `anchor` sem er blaðsíðutal (bls. 23)."` and to the English part: `"Journal articles (scope `timarit`) are cited with the article's `urlausn` plus a page anchor (bls. 23)."`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_api_journal.py
"""get_document for a journal article and the journal-aware _citation (no DB)."""
import uuid
from datetime import date

from engine.search.queries import _citation, get_document
from tests.test_api_citations import KeyedSession, _Result, DOC_ROW

JOURNAL_ROW = {**DOC_ROW, "source": "timarit_logfraedinga", "source_display": "Tímarit lögfræðinga", "court": "TL",
               "case_number": "Hvenær er vanhæfi smitandi?", "verdict_type": "Fræðigrein", "document_date": date(2010, 1, 1),
               "plaintiffs": [{"name": "Kjartan Bjarni Björgvinsson", "lawyer": None}], "defendants": None,
               "volume": 60, "issue": "1", "page_start": 7, "page_end": 47, "lang": "is",
               "issue_label": "Tímarit lögfræðinga, 60. árg. 1. hefti (2010)"}


def test_citation_renders_journal_form_when_given_bibliographic_fields():
    s = _citation("timarit_logfraedinga", "TL", "Hvenær er vanhæfi smitandi?", date(2010, 1, 1), "Fræðigrein",
                  plaintiffs=JOURNAL_ROW["plaintiffs"], volume=60, issue="1", page_start=7, page_end=47)
    assert s == "Kjartan Bjarni Björgvinsson: „Hvenær er vanhæfi smitandi?“ Tímarit lögfræðinga, 60. árg. 1. hefti (2010), bls. 7–47"
    assert _citation("landsrettur", "Lrd.", "177/2024", date(2024, 3, 6), "Dómur") == "Lrd. 177/2024 6. mars 2024 – Dómur"


async def test_get_document_exposes_journal_block():
    s = KeyedSession(doc=_Result(first=JOURNAL_ROW), links=_Result(rows=[]), out=_Result(rows=[]), in_=_Result(rows=[]), unresolved=_Result(first=0))
    doc = await get_document(s, JOURNAL_ROW["id"])
    assert doc["journal"] == {"label": "Tímarit lögfræðinga, 60. árg. 1. hefti (2010)", "year": 2010, "volume": 60,
                              "issue": "1", "page_start": 7, "page_end": 47}
    assert (doc["volume"], doc["issue"], doc["page_start"], doc["page_end"], doc["lang"]) == (60, "1", 7, 47, "is")
    assert doc["urlausn"].startswith("Kjartan Bjarni Björgvinsson: „Hvenær")
    assert doc["case_number_is_title"] is True


async def test_get_document_journal_is_null_for_rulings():
    s = KeyedSession(doc=_Result(first={**DOC_ROW, "volume": None, "issue": None, "page_start": None, "page_end": None, "lang": None, "issue_label": None}),
                     links=_Result(rows=[]), out=_Result(rows=[]), in_=_Result(rows=[]), unresolved=_Result(first=0))
    assert (await get_document(s, DOC_ROW["id"]))["journal"] is None
```

Add `"volume": None, "issue": None, "page_start": None, "page_end": None, "lang": None, "issue_label": None` to `DOC_ROW` in `tests/test_api_citations.py` (the keyed fake must mirror the real row).

```tsx
// append to frontend/src/components/DocPanel.test.tsx
describe("journal articles", () => {
  it("shows the issue label and page range instead of a case number", async () => {
    const article: DocumentDetail = {
      ...doc, source: "timarit_logfraedinga", source_display: "Tímarit lögfræðinga", case_number_is_title: true,
      case_number: "Hvenær er vanhæfi smitandi?", court: "TL", verdict_type: "Fræðigrein", document_date: "2010-01-01",
      plaintiffs: [{ name: "Kjartan Bjarni Björgvinsson", lawyer: null }], defendants: [],
      urlausn: "Kjartan Bjarni Björgvinsson: „Hvenær er vanhæfi smitandi?“ Tímarit lögfræðinga, 60. árg. 1. hefti (2010), bls. 7–47",
      volume: 60, issue: "1", page_start: 7, page_end: 47, lang: "is",
      journal: { label: "Tímarit lögfræðinga, 60. árg. 1. hefti (2010)", year: 2010, volume: 60, issue: "1", page_start: 7, page_end: 47 },
    };
    renderWithProviders(<DocPanel doc={article} />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Hvenær er vanhæfi smitandi?");
    expect(screen.getByText("Tímarit lögfræðinga, 60. árg. 1. hefti (2010)")).toBeInTheDocument();
    expect(screen.getByText("bls. 7–47")).toBeInTheDocument();
    expect(screen.queryByText(/mál nr\./i)).not.toBeInTheDocument();
    expect(screen.queryByText("gegn")).not.toBeInTheDocument();
  });
});
```
Also add `volume: null, issue: null, page_start: null, page_end: null, lang: null, journal: null` to the existing `doc` fixture (typed `DocumentDetail`, so `tsc -b` demands it).

```python
# append to tests/test_mcp_server.py
def test_instructions_mention_journal_citation():
    assert "scope `timarit`" in srv.INSTRUCTIONS and "bls. 23" in srv.INSTRUCTIONS
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_api_journal.py tests/test_mcp_server.py -k "journal or instructions"` → FAIL (`_citation() got an unexpected keyword argument 'plaintiffs'`); `cd frontend && npx vitest run src/components/DocPanel.test.tsx` → FAIL (`bls. 7–47` not found) and `npx tsc -b` → type errors on the fixture.

- [ ] **Step 3: Implement backend**

`queries.py`:

```python
def _citation(short_name, court, case_number, document_date, verdict_type, *,
              plaintiffs=None, volume=None, issue=None, page_start=None, page_end=None) -> str:
    doc = Document(court=court, case_number=case_number, document_date=document_date, verdict_type=verdict_type,
                   plaintiffs=plaintiffs, volume=volume, issue=issue, page_start=page_start, page_end=page_end)
    ...  # unchanged try/except
```

`get_document`: SELECT adds `d.volume, d.issue, d.page_start, d.page_end, d.lang, ji.label AS issue_label` and `LEFT JOIN journal_issues ji ON ji.id = d.journal_issue_id`; the return dict adds the five columns, the `journal` block (`None` unless `cfg and cfg.kind == "journal"`), and passes the bibliographic kwargs to `_citation`. Search projections: add the five columns and kwargs in `search_documents` (page query + its `_citation` call) and in both `search_by_passages` SELECTs/`_citation` calls. `_citation_ref` (citations) is unchanged — citations are between rulings.

- [ ] **Step 4: Implement frontend and MCP**

`types.ts`: add `JournalRef` and the six fields to `DocumentDetail`. `DocPanel.tsx`: 

```tsx
const pages = doc.journal?.page_start != null
  ? doc.journal.page_end != null && doc.journal.page_end !== doc.journal.page_start
    ? `bls. ${doc.journal.page_start}–${doc.journal.page_end}`
    : `bls. ${doc.journal.page_start}`
  : null;
const metaParts = doc.journal
  ? [doc.journal.label, pages]
  : doc.case_number_is_title ? [doc.source_display, dateLabel] : [dateLabel, doc.verdict_type];
```
and render `metaParts` where the existing ternary builds the meta line. `server.py`: append the two sentences to `INSTRUCTIONS`.

- [ ] **Step 5: Run all affected tests**

Run: `uv run --env-file .env pytest -q tests/test_api_journal.py tests/test_api_citations.py tests/test_api_passages.py tests/test_search_queries.py tests/test_passage_search.py tests/test_mcp_server.py tests/test_mcp_tools_unit.py` → PASS. `cd frontend && npx vitest run && npx tsc -b` → PASS.

- [ ] **Step 6: Commit**

```bash
git add engine/search/queries.py engine/search/passage_search.py engine/mcp/server.py frontend/src/api/types.ts frontend/src/components/DocPanel.tsx frontend/src/components/DocPanel.test.tsx tests/test_api_journal.py tests/test_api_citations.py tests/test_mcp_server.py
git commit -m "feat(timarit): journal block in get_document, bibliographic urlausn in search, reader header, MCP hint"
```

---

### Task 22: Pilot — five issues per journal, thresholds chosen by the user (USER GATE)

**Files:**
- Create: `scripts/timarit_pilot_report.py`
- Modify: `engine/timarit/config.py` (values the user chooses), this plan (`## Pilot results` section appended)

**Interfaces:** `timarit_pilot_report.py [--manifests data/timarit/manifests]` prints, per source: issues analysed, status counts, article count, confidence histogram (10 bins), page-map quality histogram, `bin_ratio` histogram, and the ten lowest-confidence articles with their `checks`.

- [ ] **Step 1: Discover for real (writes manifests + rows)**

Run (after the user agrees; DB write): `uv run --env-file .env python scripts/import_timarit.py discover` then `git add data/timarit && git commit -m "data(timarit): discovered issues (manifests, pending)"`.

- [ ] **Step 2: Analyse the pilot set**

```bash
for key in 1955_x_1 1985_x_x 2004_x_1 2010_x_1 2024_x_1; do uv run --env-file .env python scripts/import_timarit.py analyze --source timarit_logfraedinga --issue $key; done
for key in 1947_1_x 1975_28_x 2004_x_2 2010_x_1 2019_x_1; do uv run --env-file .env python scripts/import_timarit.py analyze --source ulfljotur --issue $key; done
for src in logmannabladid logretta logfraedingur logbru rannsoknir_lagadeild stjornmal_stjornsysla ulfljotur_vefrit fraedigreinar_ymsar; do uv run --env-file .env python scripts/import_timarit.py analyze --source $src --limit 5; done
```
(`--issue` matches the manifest stem `ÁR_ÁRG_HEFTI`; adjust a key if `status` shows it does not exist.)

- [ ] **Step 3: Write the report script**

```python
# scripts/timarit_pilot_report.py
"""Distributions from analysed manifests, for choosing the thresholds (spec §9)."""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.timarit.manifest import load_manifest


def _hist(values, lo=0.0, hi=1.0, bins=10) -> str:
    counts = [0] * bins
    for v in values:
        counts[min(bins - 1, int((v - lo) / (hi - lo) * bins))] += 1
    return " ".join(f"{lo + i * (hi - lo) / bins:.1f}:{c}" for i, c in enumerate(counts))


def main() -> None:
    ap = argparse.ArgumentParser(); ap.add_argument("--manifests", default="data/timarit/manifests")
    args = ap.parse_args()
    by_src = defaultdict(list)
    for p in sorted(Path(args.manifests).glob("*/*.yaml")):
        m = load_manifest(p)
        if m.articles or m.status == "failed":
            by_src[m.source].append(m)
    for src, ms in sorted(by_src.items()):
        arts = [a for m in ms for a in m.articles]
        print(f"\n== {src}: {len(ms)} hefti, {len(arts)} greinar, staða {dict(Counter(m.status for m in ms))}")
        print("  öryggi greina   ", _hist([a.confidence for a in arts]))
        print("  gæði blaðsíðukorts", _hist([m.page_map.get('quality', 0.0) for m in ms]))
        print("  BÍN-hlutfall      ", _hist([m.page_map.get('bin_ratio', 0.0) for m in ms]))
        print("  uppruni titla     ", dict(Counter(a.provenance.get("title") for a in arts)))
        for a in sorted(arts, key=lambda a: a.confidence)[:10]:
            print(f"    {a.confidence:.2f} {a.checks} {str(a.title)[:60]!r} {a.note}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the report; the user chooses**

Run: `uv run python scripts/timarit_pilot_report.py`. Present the three histograms per source to the user and ask for: `APPROVE_THRESHOLD`, `OCR_BIN_RATIO_THRESHOLD` (or keep `None`), `PAGE_MAP_MIN_QUALITY`. Write the chosen values into `engine/timarit/config.py` with a comment `# chosen 2026-10-xx from the pilot (see plan § Pilot results)`.

- [ ] **Step 5: Review the pilot manifests with the user; import the pilot**

The user edits/approves the pilot manifests in `review`. Then: `uv run --env-file .env python scripts/import_timarit.py import --dry-run --source timarit_logfraedinga` (check filenames and lengths), then without `--dry-run` for every pilot source; then `uv run --env-file .env python scripts/backfill_passages.py --source timarit_logfraedinga` (repeat per source) and open two articles in the reader (`http://127.0.0.1:8077/api/document/{id}` and the frontend) to confirm header, PDF and `bls.` anchors (`curl -s "http://127.0.0.1:8077/api/search?q=vanhæfi&scope=timarit" | python3 -m json.tool | grep -m3 anchor`).

- [ ] **Step 6: Record and commit**

Append `## Pilot results (date)` to this plan: issues analysed, review share per source, chosen thresholds, time per issue. Commit: `git add engine/timarit/config.py data/timarit docs/superpowers/plans/2026-10-03-timarit-innflutningur.md && git commit -m "feat(timarit): pilot — thresholds chosen, pilot issues imported"`.

---

### Task 23: Bulk analyse, review, import and acceptance (USER GATE)

- [ ] **Step 1: Analyse everything**

`uv run --env-file .env python scripts/import_timarit.py analyze 2>&1 | tee /Volumes/RuleOfLaw/Lausnir_Data/logs/timarit/analyze_$(date +%F).log` (create the logs dir first). Then `status`. Commit the manifests: `git add data/timarit && git commit -m "data(timarit): analysed manifests"`.

- [ ] **Step 2: Review**

The user works through `review` manifests (`grep -l "status: review" data/timarit/manifests/*/*.yaml`), setting `status: approved` and fixing lines (any field they change gets `provenance.<field>: user`). Commit their edits as `data(timarit): review …`.

- [ ] **Step 3: Import, passages, citations**

```bash
uv run --env-file .env python scripts/import_timarit.py import --workers 8 2>&1 | tee /Volumes/RuleOfLaw/Lausnir_Data/logs/timarit/import_$(date +%F).log
uv run --env-file .env python scripts/backfill_passages.py --workers 12
uv run --env-file .env python scripts/build_citations.py --all && uv run --env-file .env python scripts/build_citations.py --relink-unresolved
psql -d lausnir_v2 -c "VACUUM (ANALYZE) passages; VACUUM (ANALYZE) documents;"
```
After a full passages rebuild of this size, check `ix_passage_fts_is` size (`\di+ ix_passage_fts_is`) and `REINDEX INDEX CONCURRENTLY` if it grew past ~700 MB (wiki 09).

- [ ] **Step 4: Acceptance queries (spec §12)**

```sql
-- 1. every archive PDF accounted for: imported, duplicate, or unmatched
--    (compare: count of PDFs in tests/fixtures/timarit/filenames.txt vs. sum below)
SELECT count(*) FROM journal_issues WHERE status = 'imported';
SELECT status, count(*) FROM journal_issues GROUP BY 1;
-- 2. every article has its bibliographic fields
SELECT s.short_name, count(*) total,
       count(*) FILTER (WHERE d.case_number IS NULL) no_title,
       count(*) FILTER (WHERE d.plaintiffs IS NULL AND d.verdict_type IN ('Fræðigrein','Ritdómur')) no_author,
       count(*) FILTER (WHERE d.page_start IS NULL AND s.short_name NOT IN ('ulfljotur_vefrit','fraedigreinar_ymsar')) no_pages,
       count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM passages p WHERE p.document_id = d.id AND p.page_from IS NOT NULL)) no_page_anchor
FROM documents d JOIN sources s ON s.id = d.source_id JOIN journal_issues ji ON ji.id = d.journal_issue_id
GROUP BY 1 ORDER BY 1;
```
Plus `python3 -c` over `data/timarit/_unmatched.yaml` to print reason counts. Record all numbers in this plan under `## Niðurstöður keyrslu`.

- [ ] **Step 5: 100-article audit**

`psql -d lausnir_v2 -Atc "SELECT d.id, d.external_id, d.verdict_filename FROM documents d JOIN sources s ON s.id=d.source_id WHERE s.kind IS NULL AND s.short_name IN (…journals…) ORDER BY md5(d.id::text) LIMIT 100"` (stratify by source: `ROW_NUMBER() OVER (PARTITION BY s.short_name ORDER BY md5(d.id::text))` and take the first N per source proportional to size). For each: open the article PDF (`raw/{source}/{vf}.pdf`) and compare `urlausn` (title, author, volume, issue, pages) with the first page. Tally correct/wrong in `docs/snapshots/timarit_audit_2026-10.md`. Gate: ≥ 98 % correct; otherwise fix the responsible component (naming/toc/xlsx/leitir), re-analyse the affected issues, re-import, re-audit the failures.

- [ ] **Step 6: Commit**

`git add data/timarit docs/snapshots/timarit_audit_*.md docs/superpowers/plans/2026-10-03-timarit-innflutningur.md && git commit -m "data(timarit): bulk import results and 100-article audit"`.

---

### Task 24: Documentation and memory

**Files:**
- Modify: `docs/wiki/02-gagnagrunnur.md` (new columns, `journal_issues`, counts), `docs/wiki/03-heimildir.md` (group `timarit`, ten sources, `kind`), `docs/wiki/04-innflutningur.md` (`import_timarit.py`, the four stages, manifests in git, archive read-only), `docs/wiki/05-leit.md` (`bls.` anchors, `lang`, `scope=timarit`), `docs/wiki/06-api.md` (`journal` block, new fields), `docs/wiki/07-framendi.md` (journal header), `docs/wiki/09-gildrur.md` (NFD names, volume ≠ year, leitir URL encoding, OCR quality bands, page map fallback, `max_chars`), `docs/wiki/10-mcp.md` (hint), `sources_catalogue.md` (section 11 with the ten journals and counts from the DB), `docs/wiki/README.md` (index line if the wiki gets a page for journals — optional).
- Create: memory file `project_timarit_import.md` in the memory directory + one line in `MEMORY.md`.

- [ ] **Step 1: Write the wiki changes** — each page gets the facts from the tasks above and the numbers from Task 23; the gotchas page gets one `###` per item in the list.
- [ ] **Step 2: Catalogue** — generate the per-journal counts with `psql -d lausnir_v2 -c "SELECT s.short_name, count(*), min(document_date), max(document_date) FROM documents d JOIN sources s ON s.id=d.source_id WHERE s.short_name IN (…) GROUP BY 1"` and paste a table.
- [ ] **Step 3: Memory** — `project_timarit_import.md` (type: project): what was built, the thresholds chosen and why, the three rules that bit (NFD, volume ≠ year, leitir encoding), and the two follow-ups (MDE `mde_is`; articles as citing documents).
- [ ] **Step 4: Commit**: `git add docs sources_catalogue.md && git commit -m "docs(timarit): wiki, catalogue and gotchas for the journal import"`.

---

## Self-review notes

- **Spec coverage:** §4.1 → Task 2; §4.2–4.4 → Tasks 1, 4, 6; §4.5 → Tasks 5, 16, 20; §5.1 → Tasks 7, 10, 11, 12, 17; §5.2 → Task 8; §5.3 → Task 15; §5.4 → Tasks 14, 17, 22; §5.5 → Task 13; §5.6 → Tasks 18, 20 (`UNIQUE (source_id, external_id)`); §6 → Tasks 3, 4, 9, 6 (validator); §7 → Tasks 18–20; §8 → Task 21; §9 → Task 22; §10 → every task's tests; §11 → Task 24; §12 → Task 23; §13 risks map to Review Focus 1–5 and Task 17's flagging; §14 out of scope untouched.
- **Type consistency:** `ArticleEntry`/`IssueManifest` field names used in Tasks 17, 19, 20 match Task 14; `ExtractedText` fields (`body, page_offsets, ocr_source, bin_ratio, page_texts`) match Tasks 9 and 20; `PageMap.printed/offset/quality/pdf_index_for/as_dict` match Tasks 8 and 17; `TocEntry(title, authors, page_start, section, issue)` matches Tasks 10, 15, 17; `_citation` kwargs match Tasks 21 and the renderer's `Document` fields from Task 1.
- **Known seam:** Task 10 imports `engine.timarit.authors` (Task 13) — execute Task 13 before Task 10, or together.
- **Placeholders:** none; tunables are real defaults in `config.py` with the pilot task to replace them.
