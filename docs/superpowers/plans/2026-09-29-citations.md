# Case-to-case Citations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract references to other rulings from every court document, resolve them to documents in the corpus, store them in a `citations` table with derived `cites` edges in `document_links`, and surface "cites" / "cited by" in the API, frontend and MCP server.

**Architecture:** A pure extractor (`engine/processors/citations.py`) turns text into `RawCitation`s; a pure resolver (`engine/processors/citation_resolver.py`) maps them onto an in-memory index of court documents, never guessing between candidates; a DB writer (`engine/processors/citation_build.py::rebuild_citations`) owns one document's rows idempotently; `scripts/build_citations.py` drives it with a process pool like `backfill_passages.py`. Read paths (API/MCP) join `citations`/`document_links` and locate the passage from `char_start` at read time.

**Tech Stack:** Python 3.13, uv, SQLAlchemy 2 async + asyncpg, alembic (0004), PostgreSQL 17, FastAPI, React 19 + Vite + Vitest, `mcp` 2.x.

**Spec:** `docs/superpowers/specs/2026-09-29-citations-design.md`

## Global Constraints

- Live DB is read-only for every task except two controller-run, user-approved steps: applying alembic 0004 (Task 4 Step 6) and the first full `build_citations.py --all` (Task 5 Step 4). Until 0004 is applied, DB-backed tests that need the new table are skipped via `pytest.mark.skipif(not _has_citations_table())`.
- Never touch dev servers on ports 8077/5173. Never print `.env`. Tests: `set -a; . ./.env; set +a; uv run pytest -q …` from the repo root; frontend: `cd frontend && npx vitest run && npx tsc -b`.
- Resolution never guesses: exactly one candidate → `resolved`; more → `ambiguous`; a date that empties the candidate set → `unresolved` (spec §6.3).
- `target_court` values are exactly the strings stored in `documents.court`: `Hrd.`, `Lrd.`, `Hérd.`, `Hérd. Rvk.`, `Hérd. Reykn.`, `Hérd. Suðl.`, `Hérd. Norðeyst.`, `Hérd. Norðvest.`, `Hérd. Vestl.`, `Hérd. Austl.`, `Hérd. Vestfj.`, `Féld.`, `Ld.`, `Eud.`, `Hrd. málsk.`.
- `status ∈ {resolved, ambiguous, unresolved, self, pre_coverage}`; `method ∈ {casenum_date, casenum_verdict, casenum_unique, NULL}`; `confidence ∈ {1.0, 0.9, 0.8, NULL}`.
- `cites` edges: one-way, from citing to cited, only from `layer IN ('summary','body')`, `method='citation'`, confidence = max over the citations forming the edge. Existing appeal relations are never modified.
- `char_start`/`char_end` are the case number's span in the layer text; `raw_text` runs from the court word (or abbreviation) to the end of the number, ≤ 240 chars; `UNIQUE (from_doc_id, layer, char_start)`.
- User-facing strings (frontend, MCP descriptions, tool errors) in Icelandic.
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` and the `Claude-Session:` line copied from `git log -1 --format=%B 19dc663 | grep '^Claude-Session:'`.

## Review Focus

1. **A Hæstiréttur number with leading zeros in the corpus (`055/2001`) cited as `55/2001`** must resolve; test in Task 3 (`test_norm_case_number`, `test_resolve_leading_zero`).
2. **Enumeration `í málum nr. 116/1999, 274/1999 og 3/2000` after one court word** must yield three rows with distinct `char_start` and the same court; test in Task 2.
3. **`sl.` in a month later than the citing document's month** must resolve to the previous year; test in Task 2 (`test_relative_sl_previous_year`).
4. **Rebuilding one document must leave other documents' citations and all appeal edges untouched**; test in Task 4 DB test (`test_rebuild_is_scoped`).
5. **A citing document with no text at all** (`body_text`, `summary`, `lower_body_text` all NULL) must produce zero rows, a written `citation_hash`, and no error; test in Task 4.

---

### Task 1: Migration 0004, ORM model, `norm_case_number`

**Files:**
- Create: `alembic/versions/0004_citations.py`
- Modify: `engine/database/models.py` (add `Citation` model after `DocumentLink`; add `citation_hash` column to `Document`; add index `ix_link_to_rel` to `DocumentLink.__table_args__`)
- Create: `engine/processors/citation_resolver.py` (only `norm_case_number` in this task; the rest in Task 3)
- Test: `tests/test_citations_schema.py`

**Interfaces:**
- Produces: `Citation` ORM class (`__tablename__ = "citations"`), `Document.citation_hash: Mapped[str | None]`, `def norm_case_number(s: str | None) -> str | None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_citations_schema.py
"""Schema contract for citations (alembic 0004) and case-number normalisation."""
import importlib.util
import pathlib

from sqlalchemy import inspect

from engine.database.models import Base, Citation, Document, DocumentLink
from engine.processors.citation_resolver import norm_case_number


def test_citation_model_columns():
    cols = {c.name: c for c in Citation.__table__.columns}
    assert set(cols) == {"id", "from_doc_id", "layer", "char_start", "char_end", "raw_text",
                         "target_court", "target_case_number", "target_date", "target_verdict",
                         "to_doc_id", "status", "method", "confidence", "created_at"}
    assert cols["to_doc_id"].nullable and cols["target_case_number"].nullable
    assert not cols["raw_text"].nullable and not cols["status"].nullable
    uniques = [tuple(c.name for c in u.columns) for u in Citation.__table__.constraints
               if u.__class__.__name__ == "UniqueConstraint"]
    assert ("from_doc_id", "layer", "char_start") in uniques
    fks = {fk.column.table.name: fk.ondelete for fk in Citation.__table__.foreign_keys}
    assert fks == {"documents": "CASCADE"} or set(fks) == {"documents"}


def test_document_has_citation_hash_and_link_index():
    assert "citation_hash" in {c.name for c in Document.__table__.columns}
    idx = {i.name: tuple(c.name for c in i.columns) for i in DocumentLink.__table__.indexes}
    assert idx["ix_link_to_rel"] == ("to_doc_id", "relation")


def test_migration_0004_declares_objects():
    p = pathlib.Path("alembic/versions/0004_citations.py")
    src = p.read_text(encoding="utf-8")
    assert 'revision = "0004"' in src and 'down_revision = "0003"' in src
    for needle in ('"citations"', "citation_hash", "ix_link_to_rel", "ix_cit_to", "ix_cit_target", "uq_cit_doc_layer_start"):
        assert needle in src, needle
    spec = importlib.util.spec_from_file_location("m0004", p)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    assert callable(mod.upgrade) and callable(mod.downgrade)


def test_norm_case_number():
    assert norm_case_number("055/2001") == "55/2001"
    assert norm_case_number("243 /2002") == "243/2002"
    assert norm_case_number("E-0012/2020") == "E-12/2020"
    assert norm_case_number("e-12/2020") == "E-12/2020"
    assert norm_case_number(" 7/2022 ") == "7/2022"
    assert norm_case_number("2023-65") == "2023-65"
    assert norm_case_number("0/2020") == "0/2020"
    assert norm_case_number(None) is None
    assert norm_case_number("") is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest -q tests/test_citations_schema.py`
Expected: FAIL with `ImportError: cannot import name 'Citation'`.

- [ ] **Step 3: Implement the model and column**

Append to `engine/database/models.py` after `DocumentLink` (imports `Float`, `Integer`, `Date`, `Text`, `UUID`, `ForeignKey`, `Index`, `UniqueConstraint`, `func` already exist in the file — reuse them):

```python
class Citation(Base):
    """One reference in a document's text to another ruling (spec 2026-09-29-citations-design).

    char_start/char_end are the case number's span inside the given layer text
    (summary / body / lower_body — same layers and offsets as passages).
    to_doc_id is set only when the resolver found exactly one candidate; the
    row is kept either way so unresolved references can be re-resolved later
    without re-extracting. The passage containing a citation is looked up at
    read time from (from_doc_id, layer, char_start) — nothing is stored, because
    passages are rebuilt by delete+insert and are not contiguous.
    """
    __tablename__ = "citations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    from_doc_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    layer: Mapped[str] = mapped_column(Text, nullable=False)          # summary | body | lower_body
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    target_court: Mapped[str] = mapped_column(Text, nullable=False)   # exactly documents.court values
    target_case_number: Mapped[str | None] = mapped_column(Text)      # normalised; NULL for reporter form
    target_date: Mapped[date | None] = mapped_column(Date)
    target_verdict: Mapped[str | None] = mapped_column(Text)          # Dómur | Úrskurður | Ákvörðun
    to_doc_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(Text, nullable=False)         # resolved|ambiguous|unresolved|self|pre_coverage
    method: Mapped[str | None] = mapped_column(Text)                  # casenum_date|casenum_verdict|casenum_unique
    confidence: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        UniqueConstraint("from_doc_id", "layer", "char_start", name="uq_cit_doc_layer_start"),
        Index("ix_cit_from", "from_doc_id", "layer", "char_start"),
        Index("ix_cit_to", "to_doc_id", postgresql_where=text("to_doc_id IS NOT NULL")),
        Index("ix_cit_target", "target_court", "target_case_number"),
        Index("ix_cit_status", "status"),
    )
```

(If `date` / `text` are not imported in models.py, add `from datetime import date` and `from sqlalchemy import text`.) Add to `Document`, next to `passage_hash`:

```python
    citation_hash: Mapped[str | None] = mapped_column(Text)   # sha256 of summary|body|lower; see build_citations.py
```

Add to `DocumentLink.__table_args__`: `Index("ix_link_to_rel", "to_doc_id", "relation"),`.

- [ ] **Step 4: Write the migration**

```python
# alembic/versions/0004_citations.py
"""citations table, documents.citation_hash, ix_link_to_rel

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "citation_hash" not in {c["name"] for c in insp.get_columns("documents")}:
        op.add_column("documents", sa.Column("citation_hash", sa.Text(), nullable=True))
    if not insp.has_table("citations"):
        op.create_table(
            "citations",
            sa.Column("id", sa.UUID(), nullable=False),
            sa.Column("from_doc_id", sa.UUID(), nullable=False),
            sa.Column("layer", sa.Text(), nullable=False),
            sa.Column("char_start", sa.Integer(), nullable=False),
            sa.Column("char_end", sa.Integer(), nullable=False),
            sa.Column("raw_text", sa.Text(), nullable=False),
            sa.Column("target_court", sa.Text(), nullable=False),
            sa.Column("target_case_number", sa.Text(), nullable=True),
            sa.Column("target_date", sa.Date(), nullable=True),
            sa.Column("target_verdict", sa.Text(), nullable=True),
            sa.Column("to_doc_id", sa.UUID(), nullable=True),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("method", sa.Text(), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.ForeignKeyConstraint(["from_doc_id"], ["documents.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["to_doc_id"], ["documents.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("from_doc_id", "layer", "char_start", name="uq_cit_doc_layer_start"),
        )
        op.create_index("ix_cit_from", "citations", ["from_doc_id", "layer", "char_start"])
        op.create_index("ix_cit_to", "citations", ["to_doc_id"], postgresql_where=sa.text("to_doc_id IS NOT NULL"))
        op.create_index("ix_cit_target", "citations", ["target_court", "target_case_number"])
        op.create_index("ix_cit_status", "citations", ["status"])
    existing_link_idx = {i["name"] for i in insp.get_indexes("document_links")}
    if "ix_link_to_rel" not in existing_link_idx:
        op.create_index("ix_link_to_rel", "document_links", ["to_doc_id", "relation"])


def downgrade() -> None:
    op.drop_index("ix_link_to_rel", table_name="document_links")
    op.drop_index("ix_cit_status", table_name="citations")
    op.drop_index("ix_cit_target", table_name="citations")
    op.drop_index("ix_cit_to", table_name="citations")
    op.drop_index("ix_cit_from", table_name="citations")
    op.drop_table("citations")
    op.drop_column("documents", "citation_hash")
```

- [ ] **Step 5: Implement `norm_case_number`**

```python
# engine/processors/citation_resolver.py
"""Resolve extracted citations against the corpus (spec §6). Task 1 adds only
norm_case_number; CitationIndex/resolve follow in Task 3."""
from __future__ import annotations

import re

_LEADING_ZEROS = re.compile(r"^([A-ZÞÆÖ]{1,2}-)?0+(?=\d)")


def norm_case_number(s: str | None) -> str | None:
    """'055/2001' → '55/2001'; '243 /2002' → '243/2002'; 'e-0012/2020' → 'E-12/2020'.

    Applied both to documents.case_number when building the index and to the
    numbers read from text, so the two sides always compare like for like.
    Hæstiréttur rows are stored with leading zeros (055/2001) and four with a
    stray space; citations never write them that way.
    """
    if s is None:
        return None
    s = "".join(s.split()).upper()
    if not s:
        return None
    return _LEADING_ZEROS.sub(lambda m: m.group(1) or "", s)
```

- [ ] **Step 6: Run tests, full suite, commit**

Run: `set -a; . ./.env; set +a; uv run pytest -q tests/test_citations_schema.py && uv run pytest -q` → new tests PASS; suite green (597 + 4). Do NOT run `alembic upgrade`.

```bash
git add alembic/versions/0004_citations.py engine/database/models.py engine/processors/citation_resolver.py tests/test_citations_schema.py
git commit -m "feat(citations): schema 0004 (citations table, citation_hash, ix_link_to_rel) and norm_case_number"
```

---

### Task 2: Extractor `engine/processors/citations.py`

**Files:**
- Create: `engine/processors/citations.py`
- Test: `tests/test_citations_extract.py`

**Interfaces:**
- Produces:
```python
@dataclass(frozen=True)
class RawCitation:
    char_start: int; char_end: int; raw_text: str
    target_court: str; target_case_number: str | None
    target_date: date | None; target_verdict: str | None
    form: str  # 'prose' | 'abbrev' | 'reporter'

def extract_citations(text: str, *, doc_date: date | None) -> list[RawCitation]
COURT_WORDS: dict[str, str]   # regex fragment → target_court, in the order of spec §5.3
```
- Consumes: `norm_case_number` (Task 1).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_citations_extract.py
import datetime as dt

import pytest

from engine.processors.citations import RawCitation, extract_citations

D = dt.date(2022, 3, 15)


def one(text, **kw):
    out = extract_citations(text, doc_date=kw.pop("doc_date", D))
    assert len(out) == 1, out
    return out[0]


def test_prose_with_date():
    c = one("Með dómi Hæstaréttar 8. nóvember 2017 í máli nr. 700/2017 var því slegið föstu.")
    assert (c.target_court, c.target_case_number, c.target_date, c.target_verdict, c.form) == \
           ("Hrd.", "700/2017", dt.date(2017, 11, 8), "Dómur", "prose")
    assert c.raw_text.startswith("Hæstaréttar") and c.raw_text.endswith("700/2017")


def test_char_span_is_the_number():
    text = "sbr. dóm Hæstaréttar í máli nr. 190/1996. K ehf."
    c = one(text)
    assert text[c.char_start:c.char_end] == "190/1996"


def test_enumeration_yields_distinct_rows_same_court():
    text = ("Í dómum réttarins 21. október 1999 í máli nr. 116/1999, 9. desember sama ár í máli nr. 274/1999 "
            "og 3. febrúar 2000 í máli nr. 3/2000 var …")
    # 'réttarins' inherits the last court named earlier in the sentence; here none precedes, so no court → nothing.
    assert extract_citations(text, doc_date=D) == []
    text2 = "Hæstiréttur vísaði til dóma sinna í málum nr. 116/1999, 274/1999 og 3/2000."
    out = extract_citations(text2, doc_date=D)
    assert [c.target_case_number for c in out] == ["116/1999", "274/1999", "3/2000"]
    assert len({c.char_start for c in out}) == 3 and {c.target_court for c in out} == {"Hrd."}


def test_court_carried_forward_by_rettarins():
    text = ("Í dómi Hæstaréttar 21. október 1999 í máli nr. 116/1999 og dómi réttarins 9. desember sama ár "
            "í máli nr. 274/1999 var …")
    out = extract_citations(text, doc_date=D)
    assert [(c.target_court, c.target_case_number, c.target_date) for c in out] == [
        ("Hrd.", "116/1999", dt.date(1999, 10, 21)), ("Hrd.", "274/1999", dt.date(1999, 12, 9))]


def test_sama_ar_uses_previous_year_in_sentence_not_doc_year():
    c = one("Áfrýjað var dómi Landsréttar 18. nóvember sama ár í máli nr. 308/2021, en Hæstiréttur 2. mars 2021 …"
            .split(", en")[0] + ".")
    # no earlier year in the sentence → date must be None, never doc year
    assert c.target_case_number == "308/2021" and c.target_date is None
    c2 = one("Með áfrýjunarstefnu 25. janúar 2021 var áfrýjað dómi Landsréttar 18. nóvember sama ár í máli nr. 308/2021.")
    assert c2.target_date == dt.date(2021, 11, 18)


def test_s_a_abbreviation():
    c = one("Með áfrýjunarstefnu 25. janúar 2021 var áfrýjað dómi Landsréttar 18. nóvember s.á. í máli nr. 308/2021.")
    assert c.target_date == dt.date(2021, 11, 18)


def test_relative_sl_same_year_and_previous_year():
    c = one("til að kæra úrskurð Landsréttar 26. febrúar sl. í máli nr. 93/2025.", doc_date=dt.date(2025, 4, 1))
    assert c.target_date == dt.date(2025, 2, 26)
    c2 = one("til að kæra úrskurð Landsréttar 26. nóvember sl. í máli nr. 93/2024.", doc_date=dt.date(2025, 4, 1))
    assert c2.target_date == dt.date(2024, 11, 26)
    c3 = one("til að kæra úrskurð Landsréttar 26. nóvember sl. í máli nr. 93/2024.", doc_date=None)
    assert c3.target_date is None


def test_law_numbers_are_not_citations():
    assert extract_citations("Í dómi Hæstaréttar kom fram að af lögum nr. 7/1998 leiddi …", doc_date=D) == []
    assert extract_citations("sbr. 2. mgr. 218. gr. laga nr. 19/1940 og reglugerð nr. 12/2001.", doc_date=D) == []


def test_other_adjudicator_nearer_than_court_is_dropped():
    text = ("Hæstiréttur féllst á kröfuna. Þeir kröfðust þess að fellt yrði úr gildi ákvæði í úrskurði "
            "óbyggðanefndar 29. maí 2007 í máli nr. 4/2005 um að …")
    assert extract_citations(text, doc_date=D) == []
    assert extract_citations("Vísað er til úrskurðar nefndarinnar í máli nr. 2/2010.", doc_date=D) == []


def test_sentence_boundary_blocks_court_from_previous_sentence():
    text = "Hæstiréttur staðfesti niðurstöðuna. Í málinu nr. 12/2019 var deilt um …"
    assert extract_citations(text, doc_date=D) == []


@pytest.mark.parametrize("word,abbr", [
    ("Reykjavíkur", "Hérd. Rvk."), ("Reykjaness", "Hérd. Reykn."), ("Suðurlands", "Hérd. Suðl."),
    ("Norðurlands eystra", "Hérd. Norðeyst."), ("Norðurlands vestra", "Hérd. Norðvest."),
    ("Vesturlands", "Hérd. Vestl."), ("Austurlands", "Hérd. Austl."), ("Vestfjarða", "Hérd. Vestfj."),
])
def test_district_courts(word, abbr):
    c = one(f"Kærandi vísar til dóms Héraðsdóms {word} í máli nr. E-351/2022 frá 13. október 2022.")
    assert c.target_court == abbr and c.target_case_number == "E-351/2022"
    assert c.target_date == dt.date(2022, 10, 13)      # 'frá' introduces a date after the number


def test_district_without_place_and_lowercase():
    c = one("sem staðfest var með úrskurði héraðsdóms í máli nr. R-362/2009.")
    assert c.target_court == "Hérd." and c.target_verdict == "Úrskurður"


def test_letter_prefix_overrides_court_word():
    c = one("sbr. dóm Hæstaréttar í máli nr. E-110/2015.")
    assert c.target_court == "Hérd."
    f = one("sbr. dóm Héraðsdóms Reykjavíkur í máli nr. F-3/2015.")
    assert f.target_court == "Féld."
    f2 = one("sbr. dóm Félagsdóms í máli nr. 9/1999.")
    assert f2.target_court == "Féld." and f2.target_case_number == "9/1999"


def test_malskotsbeidni_forms():
    c = one("sbr. ákvörðun Hæstaréttar 21. maí 2019 í máli nr. 2019-155.")
    assert (c.target_court, c.target_case_number, c.target_verdict) == ("Hrd. málsk.", "2019-155", "Ákvörðun")
    c2 = one("með ákvörðun réttarins nr. 2023-68 var beiðninni hafnað.")
    assert c2.target_court == "Hrd. málsk." and c2.target_case_number == "2023-68"


def test_abbreviations_and_reporter():
    a = one("sjá Hrd. 700/2017 og")
    assert (a.target_court, a.target_case_number, a.form) == ("Hrd.", "700/2017", "abbrev")
    b = one("sjá Hérd. Reykn. E-12/2020.")
    assert (b.target_court, b.target_case_number) == ("Hérd. Reykn.", "E-12/2020")
    r = one("en með dómi Hæstaréttar frá árinu 1983, Hrd.1983/1538.")
    assert (r.target_court, r.target_case_number, r.form) == ("Hrd.", None, "reporter")
    h = one("sbr. H 1999:123.")
    assert (h.form, h.target_case_number) == ("reporter", None)


def test_date_after_number_without_introducer_is_ignored():
    c = one("í máli Landsréttar nr. 121/2020, kæra á ákvörðun sýslumanns frá 20. október 2020, var …")
    assert c.target_case_number == "121/2020" and c.target_date is None


def test_two_courts_in_one_sentence():
    text = ("Með dómi Landsréttar 4. júní 2020 í máli nr. 455/2019 var staðfestur dómur Héraðsdóms Reykjavíkur "
            "12. desember 2019 í máli nr. E-1/2019.")
    out = extract_citations(text, doc_date=D)
    assert [(c.target_court, c.target_case_number) for c in out] == [("Lrd.", "455/2019"), ("Hérd. Rvk.", "E-1/2019")]


def test_empty_and_garbage():
    assert extract_citations("", doc_date=D) == []
    assert extract_citations("nr. nr. máli nr. /2020 Hæstaréttar", doc_date=D) == []


def test_raw_text_capped():
    filler = "sem " * 100
    c = one(f"dómi Hæstaréttar {filler}í máli nr. 1/2020.")
    assert len(c.raw_text) <= 240 and c.raw_text.endswith("1/2020")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest -q tests/test_citations_extract.py`
Expected: FAIL, `ModuleNotFoundError: No module named 'engine.processors.citations'`.

- [ ] **Step 3: Implement**

```python
# engine/processors/citations.py
"""Extract references to other rulings from Icelandic court text (spec §5).

Pure: no DB. Everything here is regex over one layer's text. The design
decisions (why backwards-only, why the sentence window, why laws are masked
first) are in docs/superpowers/specs/2026-09-29-citations-design.md §3/§5.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from engine.processors.citation_resolver import norm_case_number

MAX_RAW = 240
WINDOW = 120          # max chars to look back for a court word
DATE_AFTER = 30       # chars after the number in which 'frá …' may introduce a date

MONTHS = {m: i + 1 for i, m in enumerate(
    "janúar febrúar mars apríl maí júní júlí ágúst september október nóvember desember".split())}

# Court words → documents.court values (spec §5.3). Order matters: specific district names first.
COURT_WORDS: dict[str, str] = {
    r"[Hh]éraðsdóm\w*\s+Reykjavíkur": "Hérd. Rvk.",
    r"[Hh]éraðsdóm\w*\s+Reykjaness": "Hérd. Reykn.",
    r"[Hh]éraðsdóm\w*\s+Suðurlands": "Hérd. Suðl.",
    r"[Hh]éraðsdóm\w*\s+Norðurlands\s+eystra": "Hérd. Norðeyst.",
    r"[Hh]éraðsdóm\w*\s+Norðurlands\s+vestra": "Hérd. Norðvest.",
    r"[Hh]éraðsdóm\w*\s+Vesturlands": "Hérd. Vestl.",
    r"[Hh]éraðsdóm\w*\s+Austurlands": "Hérd. Austl.",
    r"[Hh]éraðsdóm\w*\s+Vestfjarða": "Hérd. Vestfj.",
    r"[Hh]éraðsdóm\w*": "Hérd.",
    r"Hæstaréttar|Hæstarétti|Hæstiréttur|Hæstarétt": "Hrd.",
    r"Landsréttar|Landsrétti|Landsréttur|Landsrétt": "Lrd.",
    r"Félagsdóm\w*": "Féld.",
    r"Landsdóm\w*": "Ld.",
    r"Endurupptökudóm\w*": "Eud.",
}
_COURT_RX = re.compile("|".join(f"(?P<c{i}>{pat})" for i, pat in enumerate(COURT_WORDS)))
_COURT_BY_GROUP = {f"c{i}": abbr for i, abbr in enumerate(COURT_WORDS.values())}
_INHERIT_RX = re.compile(r"\b(réttarins|dómstólsins|sama\s+dómstóls|sama\s+réttar)\b")
_OTHER_BODY_RX = re.compile(r"\b(nefnd\w*|sýslumann\w*|ráðuneyt\w*|stofn\w*|Persónuvernd\w*|stjórn\w*|dómstól\w*)\b")
_LAW_RX = re.compile(r"\b(?:l(?:ög|aga|ögum|ögunum)|reglugerð\w*|auglýsing\w*|samþykkt\w*)\s+nr\.\s*\d{1,4}/\d{4}", re.I)
_NUM = r"(?:[A-ZÞÆÖ]{1,2}-\d{1,5}/\d{4}|\d{1,4}/\d{4}|\d{4}-\d{1,3})"
_TRIGGER_RX = re.compile(
    r"\bmál(?:i|inu|s|um|unum)?\s+nr\.\s*(?P<first>" + _NUM + r")"
    r"(?P<rest>(?:\s*,\s*" + _NUM + r")*(?:\s+og\s+" + _NUM + r")?)")
_NUM_RX = re.compile(_NUM)
_AKVORDUN_RX = re.compile(r"\bákvörðun\w*\s+(?:réttarins\s+)?nr\.\s*(?P<first>\d{4}-\d{1,3})")
_ABBREV_RX = re.compile(
    r"\b(?:(?P<hrd>Hrd)\.\s*(?P<hn>\d{1,4}/\d{4})|(?P<lrd>Lrd)\.\s*(?P<ln>\d{1,4}/\d{4})"
    r"|(?P<herd>Hérd)\.\s*(?P<place>Rvk|Reykn|Suðl|Norðeyst|Norðvest|Vestl|Austl|Vestfj)\.\s*(?P<dn>[A-ZÞÆÖ]{1,2}-\d{1,5}/\d{4}))")
_REPORTER_RX = re.compile(r"\bHrd\.?\s*(?P<y>\d{4})[,:/]\s*(?:bls\.\s*)?(?P<p>\d{1,5})\b|\bH\s?(?P<y2>\d{4}):(?P<p2>\d{1,5})\b")
_DATE_RX = re.compile(
    r"(?P<d>\d{1,2})\.\s+(?P<m>janúar|febrúar|mars|apríl|maí|júní|júlí|ágúst|september|október|nóvember|desember)"
    r"\s+(?P<y>\d{4}|sama\s+ár|s\.á\.|þess\s+árs|sl\.|síðastliðin\w*)")
_YEAR_RX = re.compile(r"\b(1[89]\d\d|20\d\d)\b")
_VERB_RX = re.compile(r"\b(dóm\w*|úrskurð\w*|ákvörð\w*)\b")
_SENT_BREAK_RX = re.compile(r"\.\s+(?=[A-ZÁÐÉÍÓÚÝÞÆÖ])|;|\n\s*\n")


@dataclass(frozen=True)
class RawCitation:
    char_start: int
    char_end: int
    raw_text: str
    target_court: str
    target_case_number: str | None
    target_date: date | None
    target_verdict: str | None
    form: str


def _sentence_start(text: str, pos: int) -> int:
    lo = max(0, pos - 600)
    last = lo
    for m in _SENT_BREAK_RX.finditer(text, lo, pos):
        last = m.end()
    return last


def _verdict(text: str, court_pos: int) -> str | None:
    seg = text[max(0, court_pos - 40):court_pos]
    last = None
    for m in _VERB_RX.finditer(seg):
        last = m.group(1).lower()
    if last is None:
        return None
    if last.startswith("dóm"):
        return "Dómur"
    if last.startswith("úrskurð"):
        return "Úrskurður"
    return "Ákvörðun"


def _resolve_year(token: str, *, sentence_before: str, month: int, doc_date: date | None) -> int | None:
    t = token.replace(" ", "")
    if t.isdigit():
        return int(t)
    if t in ("samaár", "s.á.", "þessárs"):
        years = _YEAR_RX.findall(sentence_before)
        return int(years[-1]) if years else None
    # sl. / síðastliðinn
    if doc_date is None:
        return None
    return doc_date.year if month <= doc_date.month else doc_date.year - 1


def _date_between(text: str, a: int, b: int, *, doc_date: date | None, sent_start: int) -> date | None:
    """A date between court word (a) and number (b), else one introduced by frá/dags./uppkveðn* right after b."""
    m = None
    for m in _DATE_RX.finditer(text, a, b):
        pass
    if m is None:
        after = text[b:b + DATE_AFTER + 40]
        m2 = re.match(r"\s*(?:frá|dags\.|uppkveðn\w*)\s+", after)
        if m2:
            m3 = _DATE_RX.match(after, m2.end())
            if m3:
                m = m3
                m_abs_start = b + m3.start()
                return _mk_date(m, text[sent_start:m_abs_start], doc_date)
        return None
    return _mk_date(m, text[sent_start:m.start()], doc_date)


def _mk_date(m, sentence_before: str, doc_date: date | None) -> date | None:
    month = MONTHS[m.group("m")]
    year = _resolve_year(m.group("y"), sentence_before=sentence_before, month=month, doc_date=doc_date)
    if year is None:
        return None
    try:
        return date(year, month, int(m.group("d")))
    except ValueError:
        return None


def _court_for(text: str, num_pos: int, *, sent_start: int, last_court: tuple[int, str] | None):
    """Find the court that owns the number at num_pos, looking back within the sentence.

    Returns (court_pos, abbr) or None. last_court is the previous court found in
    this sentence, inherited through 'réttarins' etc."""
    lo = max(sent_start, num_pos - WINDOW)
    seg = text[lo:num_pos]
    courts = list(_COURT_RX.finditer(seg))
    others = list(_OTHER_BODY_RX.finditer(seg))
    inherits = list(_INHERIT_RX.finditer(seg))
    court = None
    if courts:
        cm = courts[-1]
        abbr = next(v for k, v in _COURT_BY_GROUP.items() if cm.group(k))
        court = (lo + cm.start(), abbr)
    if inherits and last_court is not None and (court is None or inherits[-1].start() > (court[0] - lo)):
        court = (lo + inherits[-1].start(), last_court[1])
    if court is None:
        return None
    if others and others[-1].start() > (court[0] - lo):
        # a non-court adjudicator stands between the court word and the number
        # ('dómstól*' is in _OTHER_BODY_RX; drop it when it is our own court word)
        if not _COURT_RX.fullmatch(others[-1].group(0)):
            return None
    return court


def _apply_prefix_rule(abbr: str, number: str) -> str:
    if re.match(r"\d{4}-\d{1,3}$", number):
        return "Hrd. málsk."
    if number.startswith("F-"):
        return "Féld."
    if "-" in number and not abbr.startswith("Hérd."):
        return "Hérd."
    return abbr


def extract_citations(text: str, *, doc_date: date | None) -> list[RawCitation]:
    if not text:
        return []
    law_spans = [(m.start(), m.end()) for m in _LAW_RX.finditer(text)]

    def in_law(pos: int) -> bool:
        return any(a <= pos < b for a, b in law_spans)

    out: list[RawCitation] = []
    seen: set[int] = set()
    last_court: tuple[int, str] | None = None
    last_sent = -1

    def emit_prose(num_m_start: int, num_text: str, court_pos: int, abbr: str, verdict, tdate):
        if num_m_start in seen or in_law(num_m_start):
            return
        seen.add(num_m_start)
        num_end = num_m_start + len(num_text)
        raw = text[court_pos:num_end]
        if len(raw) > MAX_RAW:
            raw = raw[-MAX_RAW:]
        out.append(RawCitation(num_m_start, num_end, raw, _apply_prefix_rule(abbr, num_text),
                               norm_case_number(num_text), tdate, verdict, "prose"))

    for trig in list(_TRIGGER_RX.finditer(text)) + list(_AKVORDUN_RX.finditer(text)):
        first_start = trig.start("first")
        sent_start = _sentence_start(text, first_start)
        if sent_start != last_sent:
            last_court, last_sent = None, sent_start
        found = _court_for(text, first_start, sent_start=sent_start, last_court=last_court)
        if found is None:
            continue
        court_pos, abbr = found
        last_court = (court_pos, abbr)
        verdict = _verdict(text, court_pos)
        tdate = _date_between(text, court_pos, first_start, doc_date=doc_date, sent_start=sent_start)
        emit_prose(first_start, trig.group("first"), court_pos, abbr, verdict, tdate)
        rest = trig.groupdict().get("rest") or ""
        base = trig.start("rest") if rest else None
        if rest:
            for nm in _NUM_RX.finditer(rest):
                emit_prose(base + nm.start(), nm.group(0), court_pos, abbr, verdict, None)

    for m in _ABBREV_RX.finditer(text):
        if m.group("hrd"):
            abbr, num, ns = "Hrd.", m.group("hn"), m.start("hn")
        elif m.group("lrd"):
            abbr, num, ns = "Lrd.", m.group("ln"), m.start("ln")
        else:
            abbr, num, ns = f"Hérd. {m.group('place')}.", m.group("dn"), m.start("dn")
        if ns in seen or in_law(ns):
            continue
        seen.add(ns)
        out.append(RawCitation(ns, ns + len(num), text[m.start():ns + len(num)], abbr,
                               norm_case_number(num), None, None, "abbrev"))

    for m in _REPORTER_RX.finditer(text):
        ns = m.start()
        if ns in seen:
            continue
        seen.add(ns)
        out.append(RawCitation(ns, m.end(), m.group(0), "Hrd.", None, None, None, "reporter"))

    out.sort(key=lambda c: c.char_start)
    return out
```

- [ ] **Step 4: Run the tests and iterate until green**

Run: `uv run pytest -q tests/test_citations_extract.py -x`. The reference implementation above is a starting point, not gospel: the tests are the contract. Fix the implementation (not the tests) until all pass. Pay attention to: `_court_for`'s `_OTHER_BODY_RX` including `dómstól\w*` (must not cancel a real court word — the `fullmatch` check handles the bare `dómstól` token only; adjust so `Héraðsdómstóll`-like tokens matched by `_COURT_RX` are never treated as "other"); `test_enumeration_yields_distinct_rows_same_court` (first sentence has no explicit court → nothing, second has `Hæstiréttur … í málum nr.`); `test_date_after_number_without_introducer_is_ignored`.

- [ ] **Step 5: Run the full suite and commit**

```bash
set -a; . ./.env; set +a; uv run pytest -q
git add engine/processors/citations.py tests/test_citations_extract.py
git commit -m "feat(citations): pure extractor for references to other rulings"
```

---

### Task 3: Resolver `CitationIndex` / `resolve`

**Files:**
- Modify: `engine/processors/citation_resolver.py` (append)
- Test: `tests/test_citation_resolver.py`

**Interfaces:**
- Consumes: `RawCitation` (Task 2), `norm_case_number` (Task 1).
- Produces:
```python
@dataclass(frozen=True)
class Candidate: id: uuid.UUID; court: str; case_number: str; document_date: date | None; verdict_type: str | None
@dataclass(frozen=True)
class Resolution: status: str; to_doc_id: uuid.UUID | None; method: str | None; confidence: float | None
class CitationIndex:
    def __init__(self, rows: Iterable[tuple]) -> None   # (id, court, case_number, document_date, verdict_type)
    def candidates(self, target_court: str, case_number: str | None) -> list[Candidate]
    @classmethod
    async def load(cls, conn) -> "CitationIndex"   # SELECT over the seven court sources
INDEX_SQL: str
def resolve(raw: RawCitation, *, index: CitationIndex, from_doc_id: uuid.UUID, from_date: date | None) -> Resolution
```

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_citation_resolver.py
import datetime as dt
import uuid

from engine.processors.citation_resolver import CitationIndex, resolve
from engine.processors.citations import RawCitation

U = [uuid.UUID(int=i) for i in range(1, 12)]
ROWS = [
    (U[1], "Hrd.", "055/2001", dt.date(2001, 5, 3), "Dómur"),
    (U[2], "Lrd.", "93/2018", dt.date(2018, 6, 1), "Úrskurður"),
    (U[3], "Lrd.", "93/2018", dt.date(2018, 11, 9), "Dómur"),
    (U[4], "Hérd. Rvk.", "S-953/2008", dt.date(2008, 9, 1), "Dómur"),
    (U[5], "Hérd. Reykn.", "S-953/2008", dt.date(2008, 10, 1), "Dómur"),
    (U[6], "Hrd.", "7/2022", dt.date(2022, 3, 1), "Dómur"),
    (U[7], "Hrd.", "7/2022", dt.date(2022, 9, 1), "Úrskurður"),
    (U[8], "Hrd. málsk.", "2023-65", dt.date(2023, 8, 1), "Ákvörðun"),
    (U[9], "Féld.", "9/1999", dt.date(1999, 2, 2), "Dómur"),
    (U[10], "Hrd.", "700/2017", dt.date(2017, 11, 8), "Dómur"),
]
IDX = CitationIndex(ROWS)
CITING = uuid.UUID(int=99)
D = dt.date(2023, 1, 1)


def rc(court, num, date=None, verdict=None, form="prose"):
    return RawCitation(0, 1, "x", court, num, date, verdict, form)


def test_norm_applied_to_index_and_query():
    assert [c.id for c in IDX.candidates("Hrd.", "55/2001")] == [U[1]]
    assert [c.id for c in IDX.candidates("Hrd.", "055/2001")] == [U[1]]


def test_resolve_leading_zero():
    r = resolve(rc("Hrd.", "55/2001"), index=IDX, from_doc_id=CITING, from_date=D)
    assert (r.status, r.to_doc_id, r.method, r.confidence) == ("resolved", U[1], "casenum_unique", 0.8)


def test_exact_court_match_hrd_does_not_match_malsk():
    assert IDX.candidates("Hrd.", "2023-65") == []
    assert [c.id for c in IDX.candidates("Hrd. málsk.", "2023-65")] == [U[8]]


def test_bare_herd_matches_all_districts_and_is_ambiguous():
    r = resolve(rc("Hérd.", "S-953/2008"), index=IDX, from_doc_id=CITING, from_date=D)
    assert r.status == "ambiguous" and r.to_doc_id is None
    r2 = resolve(rc("Hérd. Rvk.", "S-953/2008"), index=IDX, from_doc_id=CITING, from_date=D)
    assert (r2.status, r2.to_doc_id) == ("resolved", U[4])


def test_reused_lrd_number_resolved_by_verdict_then_by_date():
    r = resolve(rc("Lrd.", "93/2018", verdict="Dómur"), index=IDX, from_doc_id=CITING, from_date=D)
    assert (r.to_doc_id, r.method, r.confidence) == (U[3], "casenum_verdict", 0.9)
    r = resolve(rc("Lrd.", "93/2018", date=dt.date(2018, 6, 1)), index=IDX, from_doc_id=CITING, from_date=D)
    assert (r.to_doc_id, r.method, r.confidence) == (U[2], "casenum_date", 1.0)
    r = resolve(rc("Lrd.", "93/2018"), index=IDX, from_doc_id=CITING, from_date=D)
    assert r.status == "ambiguous"


def test_date_that_empties_set_is_unresolved_not_fallback():
    r = resolve(rc("Lrd.", "93/2018", date=dt.date(2018, 1, 1), verdict="Dómur"), index=IDX, from_doc_id=CITING, from_date=D)
    assert r.status == "unresolved" and r.to_doc_id is None


def test_later_documents_are_excluded():
    r = resolve(rc("Hrd.", "7/2022"), index=IDX, from_doc_id=CITING, from_date=dt.date(2022, 6, 1))
    assert (r.status, r.to_doc_id) == ("resolved", U[6])     # U[7] is after the citing date
    r2 = resolve(rc("Hrd.", "7/2022"), index=IDX, from_doc_id=CITING, from_date=dt.date(2022, 1, 1))
    assert r2.status == "unresolved"
    r3 = resolve(rc("Hrd.", "7/2022"), index=IDX, from_doc_id=CITING, from_date=None)
    assert r3.status == "ambiguous"                              # no date to exclude with


def test_self_and_pre_coverage():
    r = resolve(rc("Hrd.", "700/2017"), index=IDX, from_doc_id=U[10], from_date=dt.date(2017, 11, 8))
    assert r.status == "self" and r.to_doc_id is None
    r = resolve(rc("Hrd.", "190/1996"), index=IDX, from_doc_id=CITING, from_date=D)
    assert r.status == "pre_coverage"
    r = resolve(rc("Hrd.", None, form="reporter"), index=IDX, from_doc_id=CITING, from_date=D)
    assert r.status == "pre_coverage"
    r = resolve(rc("Hrd.", "5/2005"), index=IDX, from_doc_id=CITING, from_date=D)
    assert r.status == "unresolved"
    r = resolve(rc("Lrd.", "1/1996"), index=IDX, from_doc_id=CITING, from_date=D)
    assert r.status == "unresolved"                              # pre-1999 rule is Hrd.-only
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest -q tests/test_citation_resolver.py` → FAIL `ImportError: cannot import name 'CitationIndex'`.

- [ ] **Step 3: Implement (append to `engine/processors/citation_resolver.py`)**

```python
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Iterable

from sqlalchemy import text as _sql_text

COURT_SOURCES = ("haestirettur", "landsrettur", "heradsdomstolar", "felagsdomur",
                 "landsdomar", "endurupptokudomur", "malskotsbeidnir")
INDEX_SQL = """
    SELECT d.id, d.court, d.case_number, d.document_date, d.verdict_type
    FROM documents d JOIN sources s ON s.id = d.source_id
    WHERE s.short_name = ANY(:sources) AND d.case_number IS NOT NULL AND d.court IS NOT NULL
"""
HRD_COVERAGE_FROM_YEAR = 1999


@dataclass(frozen=True)
class Candidate:
    id: uuid.UUID
    court: str
    case_number: str
    document_date: date | None
    verdict_type: str | None


@dataclass(frozen=True)
class Resolution:
    status: str
    to_doc_id: uuid.UUID | None
    method: str | None
    confidence: float | None


class CitationIndex:
    """(court, normalised case number) → candidates. Built once per run."""

    def __init__(self, rows: Iterable[tuple]) -> None:
        self._by_key: dict[tuple[str, str], list[Candidate]] = defaultdict(list)
        self._herd_by_num: dict[str, list[Candidate]] = defaultdict(list)
        for id_, court, case_number, document_date, verdict_type in rows:
            num = norm_case_number(case_number)
            if num is None:
                continue
            c = Candidate(id_, court, num, document_date, verdict_type)
            self._by_key[(court, num)].append(c)
            if court.startswith("Hérd. "):
                self._herd_by_num[num].append(c)

    @classmethod
    async def load(cls, conn) -> "CitationIndex":
        rows = (await conn.execute(_sql_text(INDEX_SQL), {"sources": list(COURT_SOURCES)})).all()
        return cls(rows)

    def candidates(self, target_court: str, case_number: str | None) -> list[Candidate]:
        num = norm_case_number(case_number)
        if num is None:
            return []
        if target_court == "Hérd.":
            return list(self._herd_by_num.get(num, []))
        return list(self._by_key.get((target_court, num), []))


def _year_of(case_number: str | None) -> int | None:
    if not case_number:
        return None
    tail = case_number.rsplit("/", 1)[-1] if "/" in case_number else case_number.split("-", 1)[0]
    return int(tail) if tail.isdigit() and len(tail) == 4 else None


def resolve(raw, *, index: CitationIndex, from_doc_id: uuid.UUID, from_date: date | None) -> Resolution:
    if raw.form == "reporter" or raw.target_case_number is None:
        return Resolution("pre_coverage", None, None, None)
    cands = index.candidates(raw.target_court, raw.target_case_number)
    if from_date is not None:
        cands = [c for c in cands if c.document_date is None or c.document_date <= from_date]
    if not cands:
        yr = _year_of(raw.target_case_number)
        if raw.target_court == "Hrd." and yr is not None and yr < HRD_COVERAGE_FROM_YEAR:
            return Resolution("pre_coverage", None, None, None)
        return Resolution("unresolved", None, None, None)
    method, conf = "casenum_unique", 0.8
    if raw.target_date is not None:
        cands = [c for c in cands if c.document_date == raw.target_date]
        if not cands:
            return Resolution("unresolved", None, None, None)
        method, conf = "casenum_date", 1.0
    elif raw.target_verdict is not None and len(cands) > 1:
        narrowed = [c for c in cands if (c.verdict_type or "") == raw.target_verdict]
        if narrowed:
            cands, method, conf = narrowed, "casenum_verdict", 0.9
    if len(cands) > 1:
        return Resolution("ambiguous", None, None, None)
    c = cands[0]
    if c.id == from_doc_id:
        return Resolution("self", None, None, None)
    return Resolution("resolved", c.id, method, conf)
```

Note on step 3 vs spec: the verdict narrowing applies only when more than one candidate remains (a single candidate with a mismatching verdict word still resolves as `casenum_unique`); if the tests above disagree with that reading, the tests win — they encode the spec.

- [ ] **Step 4: Run tests, full suite, commit**

```bash
uv run pytest -q tests/test_citation_resolver.py tests/test_citations_schema.py
set -a; . ./.env; set +a; uv run pytest -q
git add engine/processors/citation_resolver.py tests/test_citation_resolver.py
git commit -m "feat(citations): CitationIndex and never-guess resolver"
```

---

### Task 4: `rebuild_citations` writer, `build_citations.py`, migration applied (user gate), DB tests

**Files:**
- Create: `engine/processors/citation_build.py`
- Create: `scripts/build_citations.py`
- Modify: `scripts/check_link_orientation.py` (new `cites` check; note that the three existing checks are scoped to appeal relations)
- Test: `tests/test_citation_build_unit.py`, `tests/test_citations_db.py`

**Interfaces:**
- Consumes: `extract_citations`, `CitationIndex`, `resolve`, `norm_case_number`.
- Produces:
```python
def citation_hash(summary: str | None, body: str | None, lower: str | None) -> str
def build_rows(doc_id, *, summary, body, lower, doc_date, index) -> list[dict]      # pure: extract+resolve per layer
async def rebuild_citations(conn, doc_id, *, summary, body, lower, doc_date, index, write_edges=True) -> tuple[int, int]  # (rows, edges)
async def relink_unresolved(conn, index, *, limit=None) -> int
STALE_WHERE = "(d.citation_hash IS NULL OR d.citation_hash <> encode(sha256(convert_to(coalesce(d.summary,'') || chr(31) || coalesce(d.body_text,'') || chr(31) || coalesce(d.lower_body_text,''), 'UTF8')), 'hex'))"
```

- [ ] **Step 1: Write the unit test for the pure part**

```python
# tests/test_citation_build_unit.py
import datetime as dt
import hashlib
import uuid

from engine.processors.citation_build import build_rows, citation_hash
from engine.processors.citation_resolver import CitationIndex

T = uuid.UUID(int=5)
IDX = CitationIndex([(T, "Hrd.", "700/2017", dt.date(2017, 11, 8), "Dómur")])


def test_citation_hash_matches_sql_formula():
    h = citation_hash("a", None, "c")
    assert h == hashlib.sha256(("a" + chr(31) + "" + chr(31) + "c").encode("utf-8")).hexdigest()
    assert citation_hash(None, None, None) == hashlib.sha256((chr(31) + chr(31)).encode()).hexdigest()


def test_build_rows_per_layer_and_resolution():
    body = "Með dómi Hæstaréttar 8. nóvember 2017 í máli nr. 700/2017 var …"
    rows = build_rows(uuid.UUID(int=1), summary="sbr. Hrd. 700/2017.", body=body, lower="sbr. dóm Hæstaréttar í máli nr. 1/1990.",
                      doc_date=dt.date(2020, 1, 1), index=IDX)
    by_layer = {r["layer"]: r for r in rows}
    assert set(by_layer) == {"summary", "body", "lower_body"}
    assert by_layer["body"]["status"] == "resolved" and by_layer["body"]["to_doc_id"] == T
    assert by_layer["body"]["method"] == "casenum_date" and by_layer["body"]["confidence"] == 1.0
    assert by_layer["summary"]["status"] == "resolved"
    assert by_layer["lower_body"]["status"] == "pre_coverage"
    assert body[by_layer["body"]["char_start"]:by_layer["body"]["char_end"]] == "700/2017"
    assert all(set(r) >= {"from_doc_id", "layer", "char_start", "char_end", "raw_text", "target_court",
                          "target_case_number", "target_date", "target_verdict", "to_doc_id", "status",
                          "method", "confidence"} for r in rows)


def test_build_rows_empty_document():
    assert build_rows(uuid.UUID(int=1), summary=None, body=None, lower=None, doc_date=None, index=IDX) == []
```

- [ ] **Step 2: Write the DB tests (skipped until 0004 is applied)**

```python
# tests/test_citations_db.py
"""rebuild_citations against the live DB (read-only role for reads; writes happen
inside a transaction that is rolled back). Skipped until alembic 0004 exists."""
import datetime as dt
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from engine.processors.citation_build import STALE_WHERE, rebuild_citations
from engine.processors.citation_resolver import CitationIndex

_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not _URL, reason="needs DATABASE_URL (writes are rolled back)")


async def _conn():
    eng = create_async_engine(_URL)
    conn = await eng.connect()
    await conn.begin()
    if not (await conn.execute(text("SELECT to_regclass('public.citations')"))).scalar():
        await conn.rollback(); await eng.dispose()
        pytest.skip("alembic 0004 not applied yet")
    return eng, conn


async def _pick_doc(conn):
    return (await conn.execute(text("""
        SELECT d.id, d.summary, d.body_text, d.lower_body_text, d.document_date
        FROM documents d JOIN sources s ON s.id = d.source_id
        WHERE s.short_name = 'haestirettur' AND d.body_text ILIKE '%í máli nr.%'
          AND d.document_date >= '2019-01-01' ORDER BY d.id LIMIT 1"""))).first()


async def test_rebuild_writes_rows_and_edges_and_is_idempotent():
    eng, conn = await _conn()
    try:
        idx = await CitationIndex.load(conn)
        d = await _pick_doc(conn)
        n1, e1 = await rebuild_citations(conn, d.id, summary=d.summary, body=d.body_text, lower=d.lower_body_text,
                                         doc_date=d.document_date, index=idx)
        assert n1 > 0
        n2, e2 = await rebuild_citations(conn, d.id, summary=d.summary, body=d.body_text, lower=d.lower_body_text,
                                         doc_date=d.document_date, index=idx)
        assert (n1, e1) == (n2, e2)
        rows = (await conn.execute(text("SELECT layer, char_start, char_end, status, to_doc_id FROM citations WHERE from_doc_id=:id"), {"id": d.id})).all()
        assert len(rows) == n1
        edges = (await conn.execute(text("SELECT count(*) FROM document_links WHERE from_doc_id=:id AND relation='cites'"), {"id": d.id})).scalar()
        resolved_targets = {r.to_doc_id for r in rows if r.status == "resolved" and r.layer in ("summary", "body")}
        assert edges == e1 == len(resolved_targets)
        # passage lookup at read time works for every row
        for layer, cs, ce, *_ in rows:
            pid = (await conn.execute(text("""SELECT id FROM passages WHERE document_id=:id AND layer=:layer AND char_start <= :pos
                                             ORDER BY char_start DESC LIMIT 1"""), {"id": d.id, "layer": layer, "pos": cs})).scalar()
            assert pid is not None
        # citation_hash written and document no longer stale
        stale = (await conn.execute(text(f"SELECT count(*) FROM documents d WHERE d.id=:id AND {STALE_WHERE}"), {"id": d.id})).scalar()
        assert stale == 0
    finally:
        await conn.rollback(); await eng.dispose()


async def test_rebuild_is_scoped():
    eng, conn = await _conn()
    try:
        idx = await CitationIndex.load(conn)
        d = await _pick_doc(conn)
        before_links = (await conn.execute(text("SELECT count(*) FROM document_links WHERE relation <> 'cites'"))).scalar()
        other = (await conn.execute(text("SELECT count(*) FROM citations WHERE from_doc_id <> :id"), {"id": d.id})).scalar()
        await rebuild_citations(conn, d.id, summary=d.summary, body=d.body_text, lower=d.lower_body_text, doc_date=d.document_date, index=idx)
        assert (await conn.execute(text("SELECT count(*) FROM document_links WHERE relation <> 'cites'"))).scalar() == before_links
        assert (await conn.execute(text("SELECT count(*) FROM citations WHERE from_doc_id <> :id"), {"id": d.id})).scalar() == other
    finally:
        await conn.rollback(); await eng.dispose()


async def test_document_without_text():
    eng, conn = await _conn()
    try:
        idx = CitationIndex([])
        did = (await conn.execute(text("SELECT id FROM documents WHERE body_text IS NULL AND summary IS NULL AND lower_body_text IS NULL LIMIT 1"))).scalar()
        if did is None:
            pytest.skip("no textless document")
        n, e = await rebuild_citations(conn, did, summary=None, body=None, lower=None, doc_date=None, index=idx)
        assert (n, e) == (0, 0)
        assert (await conn.execute(text("SELECT citation_hash FROM documents WHERE id=:id"), {"id": did})).scalar()
    finally:
        await conn.rollback(); await eng.dispose()


async def test_no_edge_points_to_a_later_document():
    eng, conn = await _conn()
    try:
        idx = await CitationIndex.load(conn)
        d = await _pick_doc(conn)
        await rebuild_citations(conn, d.id, summary=d.summary, body=d.body_text, lower=d.lower_body_text, doc_date=d.document_date, index=idx)
        bad = (await conn.execute(text("""SELECT count(*) FROM document_links dl JOIN documents a ON a.id=dl.from_doc_id
                                         JOIN documents b ON b.id=dl.to_doc_id WHERE dl.relation='cites' AND dl.from_doc_id=:id
                                         AND b.document_date > a.document_date"""), {"id": d.id})).scalar()
        assert bad == 0
    finally:
        await conn.rollback(); await eng.dispose()
```

- [ ] **Step 3: Implement `engine/processors/citation_build.py`**

```python
"""Write one document's citations and derived `cites` edges (spec §7). Owns only its own rows."""
from __future__ import annotations

import hashlib
import uuid
from datetime import date

from sqlalchemy import text

from engine.processors.citation_resolver import CitationIndex, resolve
from engine.processors.citations import extract_citations

SEP = chr(31)
STALE_WHERE = ("(d.citation_hash IS NULL OR d.citation_hash <> encode(sha256(convert_to("
               "coalesce(d.summary,'') || chr(31) || coalesce(d.body_text,'') || chr(31) || coalesce(d.lower_body_text,''),"
               " 'UTF8')), 'hex'))")
EDGE_LAYERS = ("summary", "body")
APPEAL_RELATIONS = ("appealed_to", "appealed_from", "leyfisbeidni_um", "leiddi_til_doms")


def citation_hash(summary: str | None, body: str | None, lower: str | None) -> str:
    return hashlib.sha256(((summary or "") + SEP + (body or "") + SEP + (lower or "")).encode("utf-8")).hexdigest()


def build_rows(doc_id: uuid.UUID, *, summary, body, lower, doc_date: date | None, index: CitationIndex) -> list[dict]:
    rows: list[dict] = []
    for layer, src in (("summary", summary), ("body", body), ("lower_body", lower)):
        if not src:
            continue
        for raw in extract_citations(src, doc_date=doc_date):
            r = resolve(raw, index=index, from_doc_id=doc_id, from_date=doc_date)
            rows.append({
                "id": uuid.uuid4(), "from_doc_id": doc_id, "layer": layer,
                "char_start": raw.char_start, "char_end": raw.char_end, "raw_text": raw.raw_text,
                "target_court": raw.target_court, "target_case_number": raw.target_case_number,
                "target_date": raw.target_date, "target_verdict": raw.target_verdict,
                "to_doc_id": r.to_doc_id, "status": r.status, "method": r.method, "confidence": r.confidence,
            })
    return rows


_INSERT = text("""
    INSERT INTO citations (id, from_doc_id, layer, char_start, char_end, raw_text, target_court, target_case_number,
                           target_date, target_verdict, to_doc_id, status, method, confidence)
    VALUES (:id, :from_doc_id, :layer, :char_start, :char_end, :raw_text, :target_court, :target_case_number,
            :target_date, :target_verdict, :to_doc_id, :status, :method, :confidence)
    ON CONFLICT ON CONSTRAINT uq_cit_doc_layer_start DO NOTHING
""")
_EDGE = text("""
    INSERT INTO document_links (id, from_doc_id, to_doc_id, relation, confidence, method)
    VALUES (gen_random_uuid(), :f, :t, 'cites', :c, 'citation')
    ON CONFLICT ON CONSTRAINT uq_link_from_to_rel DO UPDATE
      SET confidence = GREATEST(document_links.confidence, EXCLUDED.confidence), method = 'citation'
""")


async def rebuild_citations(conn, doc_id, *, summary, body, lower, doc_date, index: CitationIndex,
                            write_edges: bool = True) -> tuple[int, int]:
    rows = build_rows(doc_id, summary=summary, body=body, lower=lower, doc_date=doc_date, index=index)
    await conn.execute(text("DELETE FROM citations WHERE from_doc_id = :id"), {"id": doc_id})
    await conn.execute(text("DELETE FROM document_links WHERE from_doc_id = :id AND relation = 'cites'"), {"id": doc_id})
    if rows:
        await conn.execute(_INSERT, rows)
    edges = 0
    if write_edges:
        best: dict[uuid.UUID, float] = {}
        for r in rows:
            if r["status"] == "resolved" and r["layer"] in EDGE_LAYERS and r["to_doc_id"] is not None:
                best[r["to_doc_id"]] = max(best.get(r["to_doc_id"], 0.0), r["confidence"] or 0.0)
        for t, c in best.items():
            await conn.execute(_EDGE, {"f": doc_id, "t": t, "c": c})
        edges = len(best)
    await conn.execute(text("UPDATE documents SET citation_hash = :h WHERE id = :id"),
                       {"h": citation_hash(summary, body, lower), "id": doc_id})
    return len(rows), edges


async def relink_unresolved(conn, index: CitationIndex, *, limit: int | None = None) -> int:
    """Re-run resolve() on unresolved/ambiguous rows without re-extracting (spec §7)."""
    from engine.processors.citations import RawCitation
    q = ("SELECT c.id, c.from_doc_id, c.layer, c.char_start, c.char_end, c.raw_text, c.target_court, c.target_case_number, "
         "c.target_date, c.target_verdict, d.document_date FROM citations c JOIN documents d ON d.id = c.from_doc_id "
         "WHERE c.status IN ('unresolved','ambiguous')" + (f" LIMIT {int(limit)}" if limit else ""))
    rows = (await conn.execute(text(q))).all()
    fixed = 0
    for r in rows:
        raw = RawCitation(r.char_start, r.char_end, r.raw_text, r.target_court, r.target_case_number,
                          r.target_date, r.target_verdict, "prose")
        res = resolve(raw, index=index, from_doc_id=r.from_doc_id, from_date=r.document_date)
        if res.status == "resolved":
            await conn.execute(text("UPDATE citations SET to_doc_id=:t, status='resolved', method=:m, confidence=:c WHERE id=:id"),
                               {"t": res.to_doc_id, "m": res.method, "c": res.confidence, "id": r.id})
            if r.layer in EDGE_LAYERS:
                await conn.execute(_EDGE, {"f": r.from_doc_id, "t": res.to_doc_id, "c": res.confidence})
            fixed += 1
    return fixed
```

- [ ] **Step 4: Implement `scripts/build_citations.py`**

Mirror `scripts/backfill_passages.py` (same `Pool`, `BATCH`, `COMMIT_EVERY`, progress log, rollback-on-write-failure, dead-connection handling). Differences: the worker runs `build_rows` (needs the index → build it once in the parent and pass via `Pool(initializer=…)` with a module-level global, since `CitationIndex` is picklable but large; initializer receives the `rows` list), the parent writes via a small variant of `rebuild_citations` that takes prebuilt rows (`write_citations(conn, doc_id, rows, *, hash_)`; refactor `rebuild_citations` to call it). CLI:

```python
ap.add_argument("--all", action="store_true", help="every stale document (citation_hash missing/changed)")
ap.add_argument("--force", action="store_true", help="with --all/--source: also non-stale documents")
ap.add_argument("--source"); ap.add_argument("--doc"); ap.add_argument("--since")
ap.add_argument("--relink-unresolved", action="store_true")
ap.add_argument("--dry-run", action="store_true", help="extract+resolve, print the summary, write nothing")
ap.add_argument("--limit", type=int); ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
```

Selection SQL: `--all` → `WHERE {STALE_WHERE}` (or `TRUE` with `--force`) restricted to `s.short_name = ANY(COURT_SOURCES)`; `--source` adds `s.short_name = :sn`; `--doc` → that id; `--since` → `d.document_date >= :since`. `--dry-run` runs the pool and prints the summary without opening a write transaction. Summary at the end (always, also for dry-run): counts by `status`, by `layer`, matrix citing-court × cited-court for resolved, edges written, elapsed, and the ten most common `raw_text` shapes among `unresolved` (normalise digits to `#` before counting). With `--relink-unresolved`: load index, call `relink_unresolved`, print fixed count.

- [ ] **Step 5: `check_link_orientation.py` — add the `cites` check**

Append a fourth query, reported the same way as the others and counted in the exit status:

```python
# 4. cites edges must never point at a later-dated document (spec §4.3 / §9)
CITES_LATER = """
SELECT count(*) FROM document_links dl
JOIN documents a ON a.id = dl.from_doc_id JOIN documents b ON b.id = dl.to_doc_id
WHERE dl.relation = 'cites' AND a.document_date IS NOT NULL AND b.document_date IS NOT NULL
  AND b.document_date > a.document_date"""
```

Add a module docstring line: the three original checks are scoped to `appealed_to`/`appealed_from` by design; `leyfisbeidni_um`, `leiddi_til_doms` and `cites` are one-way and only get relation-specific checks.

- [ ] **Step 6: STOP — controller applies migration 0004 after the user's approval**

The implementer stops here and reports DONE with the note "DB tests skipped pending 0004". The controller asks the user:

> Þarf samþykki: keyra `uv run alembic upgrade head` (flutningur 0004: ný tafla `citations`, nýr dálkur `documents.citation_hash`, nýr vísir `ix_link_to_rel` á `document_links`). Engum eldri gögnum er breytt. Segðu „já“.

After "já" the controller runs `set -a; . ./.env; set +a; uv run alembic upgrade head` and verifies `psql … -Atc "\d citations"` and `SELECT count(*) FROM citations` → 0. Then the implementer (resumed) runs the DB tests.

- [ ] **Step 7: Run everything and commit**

```bash
set -a; . ./.env; set +a; uv run pytest -q tests/test_citation_build_unit.py tests/test_citations_db.py -rs
uv run python scripts/build_citations.py --doc <id-from-test> --dry-run   # prints a summary, writes nothing
uv run pytest -q
git add engine/processors/citation_build.py scripts/build_citations.py scripts/check_link_orientation.py tests/test_citation_build_unit.py tests/test_citations_db.py
git commit -m "feat(citations): per-document rebuild writer, build_citations.py, cites orientation check"
```

---

### Task 5: Coverage re-measurement, trial run, full build (user gate)

**Files:**
- Create: `scripts/measure_citations.py` (read-only sampling using the real extractor/resolver; prints the spec §9 coverage table)
- Test: none new (measurement); the gate is the numbers.

**Interfaces:** consumes `extract_citations`, `CitationIndex`, `resolve`.

- [ ] **Step 1: Write `scripts/measure_citations.py`**

Samples `--n` documents per tier (default 800 × tiers 1–3 via `ORDER BY random()`), loads `CitationIndex`, runs `build_rows` in-process (no writes), and prints: counts by `status`; coverage = resolved / (resolved+ambiguous+unresolved) for `layer IN ('summary','body')`; distinct resolved targets per document; top 10 `raw_text` shapes among unresolved and among ambiguous; and 5 random examples per status with ±60 chars of context. Uses `DATABASE_URL_READONLY`.

- [ ] **Step 2: Run it and record**

Run: `set -a; . ./.env; set +a; uv run python scripts/measure_citations.py --n 800 > /tmp/lausnir-dev/citations_measure_v1.txt; head -40 /tmp/lausnir-dev/citations_measure_v1.txt`. Gate: coverage ≥ 80 %. If below, the top unresolved shapes tell you which pattern is missing: fix the extractor in Task 2's file with a new test per shape, re-run, and record both numbers in the report. Do not lower the gate.

- [ ] **Step 3: Trial run on one small source**

`uv run python scripts/build_citations.py --source felagsdomur --workers 4` (308 docs, writes ~hundreds of rows) then `uv run python scripts/check_link_orientation.py` → exit 0, and `SELECT status, count(*) FROM citations GROUP BY 1` printed in the report.

- [ ] **Step 4: STOP — controller runs the full build after the user's approval**

Controller asks: "Þarf samþykki: full keyrsla `build_citations.py --all --workers 8` (≈87.000 dómar, skrifar ≈37.000 `cites`-brúnir í `document_links` og allar raðir í `citations`; 10–20 mín; endurkeyranleg). Segðu „já“." After "já": run in background, log to `/tmp/lausnir-dev/build_citations_full.log`, then `check_link_orientation.py`, then `VACUUM (ANALYZE) citations; VACUUM (ANALYZE) document_links;` (plain VACUUM is allowed — it is maintenance, not data change), then record the summary table in the plan under "## Niðurstöður keyrslu".

- [ ] **Step 5: Commit**

```bash
git add scripts/measure_citations.py docs/superpowers/plans/2026-09-29-citations.md
git commit -m "feat(citations): coverage measurement script and full-run results"
```

---

### Task 6: API — `get_document` citations, `/citations` endpoint, `cited_by_count`

**Files:**
- Modify: `engine/search/queries.py` (`get_document`, `search_documents` result rows, new `get_citations(session, doc_id, *, direction, page, page_size)`)
- Modify: `engine/api/app.py` (new route)
- Test: `tests/test_api_citations.py` (fake session, same style as `tests/test_api_passages.py`), `tests/test_citations_api_db.py` (skipif; real data)

**Interfaces:**
- Produces: `async def get_citations(session, doc_id, *, direction: str, page: int = 1, page_size: int = 50) -> dict` returning `{"direction", "total", "page", "page_size", "items": [CitationRef…]}` where `CitationRef = {document_id, urlausn, source, document_date, layer, passage_id, anchor, raw_text, confidence, also_appeal, same_case}`; `get_document` gains `citations_out`, `citations_out_total`, `cited_by`, `cited_by_total`, `citations_unresolved_total`; search results gain `cited_by_count: int`.

- [ ] **Step 1: SQL for the two directions (put in `queries.py` as module constants)**

```sql
-- OUT: one row per cited document = the citation with the lowest char_start in body, else summary
WITH c AS (
  SELECT DISTINCT ON (c.to_doc_id) c.to_doc_id, c.layer, c.char_start, c.raw_text, c.confidence,
         c.target_case_number, c.target_court
  FROM citations c
  WHERE c.from_doc_id = :id AND c.status = 'resolved' AND c.layer IN ('summary','body')
  ORDER BY c.to_doc_id, (c.layer = 'body') DESC, c.char_start
)
SELECT c.*, o.id AS other_id, o.case_number AS other_case, o.court AS other_court, o.document_date AS other_date,
       o.verdict_type AS other_verdict, os.short_name AS other_source,
       p.id AS passage_id, p.layer AS p_layer, p.para_from, p.para_to, p.section_path, p.ordinal,
       EXISTS (SELECT 1 FROM document_links dl WHERE dl.relation = ANY(:appeal_rels)
               AND ((dl.from_doc_id = :id AND dl.to_doc_id = c.to_doc_id) OR (dl.to_doc_id = :id AND dl.from_doc_id = c.to_doc_id))) AS also_appeal,
       (c.target_court = me.court AND c.target_case_number = :my_norm_case) AS same_case,
       count(*) OVER () AS total
FROM c JOIN documents o ON o.id = c.to_doc_id JOIN sources os ON os.id = o.source_id
JOIN documents me ON me.id = :id
LEFT JOIN LATERAL (SELECT id, layer, para_from, para_to, section_path, ordinal FROM passages p
                   WHERE p.document_id = :id AND p.layer = c.layer AND p.char_start <= c.char_start
                   ORDER BY p.char_start DESC LIMIT 1) p ON TRUE
ORDER BY o.document_date, o.id LIMIT :limit OFFSET :offset
```

```sql
-- IN: documents citing :id, each with its first body/summary citation of :id
WITH c AS (
  SELECT DISTINCT ON (c.from_doc_id) c.from_doc_id, c.layer, c.char_start, c.raw_text, c.confidence,
         c.target_case_number, c.target_court
  FROM citations c
  WHERE c.to_doc_id = :id AND c.status = 'resolved' AND c.layer IN ('summary','body')
  ORDER BY c.from_doc_id, (c.layer = 'body') DESC, c.char_start
)
SELECT c.*, o.id AS other_id, o.case_number AS other_case, o.court AS other_court, o.document_date AS other_date,
       o.verdict_type AS other_verdict, os.short_name AS other_source,
       p.id AS passage_id, p.layer AS p_layer, p.para_from, p.para_to, p.section_path, p.ordinal,
       EXISTS (SELECT 1 FROM document_links dl WHERE dl.relation = ANY(:appeal_rels)
               AND ((dl.from_doc_id = :id AND dl.to_doc_id = c.from_doc_id) OR (dl.to_doc_id = :id AND dl.from_doc_id = c.from_doc_id))) AS also_appeal,
       (o.court = me.court AND :my_norm_case IS NOT NULL AND regexp_replace(upper(replace(o.case_number,' ','')), '^([A-ZÞÆÖ]{1,2}-)?0+(?=\d)', '\1') = :my_norm_case) AS same_case,
       count(*) OVER () AS total
FROM c JOIN documents o ON o.id = c.from_doc_id JOIN sources os ON os.id = o.source_id
JOIN documents me ON me.id = :id
LEFT JOIN LATERAL (SELECT id, layer, para_from, para_to, section_path, ordinal FROM passages p
                   WHERE p.document_id = c.from_doc_id AND p.layer = c.layer AND p.char_start <= c.char_start
                   ORDER BY p.char_start DESC LIMIT 1) p ON TRUE
ORDER BY o.document_date DESC, o.id LIMIT :limit OFFSET :offset
```

`:my_norm_case` = `norm_case_number(me.case_number)` computed in Python (fetch `me.court, me.case_number` first). `anchor` built with `passage_anchor(p_layer, para_from, para_to, section_path, ordinal)` when `passage_id` is not null. `citations_unresolved_total` = `SELECT count(*) FROM citations WHERE from_doc_id=:id AND layer IN ('summary','body') AND status IN ('unresolved','ambiguous','pre_coverage')`.

- [ ] **Step 2: Write the fake-session API test**

Extend the `FakeSession` pattern from `tests/test_api_passages.py`: `GET /api/document/{id}/citations?direction=out` returns the items shaped as above from scripted rows; `direction=sideways` → 400; `page_size=500` clamps to 100. And a `get_document` test that the five new keys exist with list/int types when the scripted queries return empty results.

- [ ] **Step 3: Implement `get_citations`, extend `get_document`, add route and `cited_by_count`**

- `get_citations` validates `direction in ("out","in")` else `SearchError("direction must be 'out' or 'in'")`; clamps page_size 1–100.
- `get_document` calls `get_citations(..., direction="out", page_size=50)` and `("in", 50)`, sets `citations_out=items`, `citations_out_total=total`, `cited_by=items`, `cited_by_total=total`, plus `citations_unresolved_total`.
- `search_documents`: add `cited_by_count` to both projections (passage path in `passage_search.py` result mapping and the regex path) via `(SELECT count(*) FROM document_links l WHERE l.to_doc_id = d.id AND l.relation = 'cites') AS cited_by_count`; measure on the golden set (`scripts/eval_search.py --set core`) before/after: p50 must not move by more than 5 ms; record numbers in the report.
- Route: `@app.get("/api/document/{doc_id}/citations")` with `direction: str = Query("out", pattern="^(out|in)$")`, `page`, `page_size: int = Query(50, ge=1, le=100)`; 404 when the document does not exist (check via `get_document`-style existence query), 400 on `SearchError`.

- [ ] **Step 4: DB test** (`tests/test_citations_api_db.py`, skipif no `DATABASE_URL_READONLY` or no `citations` table): pick a document with ≥1 resolved out-citation (`SELECT from_doc_id FROM citations WHERE status='resolved' AND layer='body' LIMIT 1`), call `get_document`, assert `citations_out_total ≥ 1`, each item has `urlausn`, `raw_text`, and `passage_id`/`anchor` not null; call `get_citations(direction="in")` on `citations_out[0]["document_id"]` and assert the original document appears among `cited_by` (page through if `total > 50`).

- [ ] **Step 5: Run and commit**

```bash
set -a; . ./.env; set +a; uv run pytest -q tests/test_api_citations.py tests/test_citations_api_db.py tests/test_api_passages.py tests/test_search_queries.py && uv run pytest -q
git add engine/search/queries.py engine/api/app.py engine/search/passage_search.py tests/test_api_citations.py tests/test_citations_api_db.py
git commit -m "feat(api): citations in get_document, /citations endpoint, cited_by_count"
```

---

### Task 7: Frontend — Tilvitnanir section, relation labels, cited_by_count

**Files:**
- Modify: `frontend/src/api/types.ts` (`CitationRef`, `DocumentDetail` fields, `SearchResult.cited_by_count`, `AppealLink.relation` union)
- Modify: `frontend/src/api/client.ts` (or wherever fetch helpers live: add `fetchCitations(id, direction, page)`)
- Modify: `frontend/src/components/DocPanel.tsx` (new section; label map)
- Modify: `frontend/src/components/ResultCard.tsx` ("vitnað í N sinnum")
- Test: `frontend/src/components/DocPanel.test.tsx`, `ResultCard.test.tsx`

- [ ] **Step 1: Types**

```ts
export type LinkRelation = "appealed_to" | "appealed_from" | "leyfisbeidni_um" | "leiddi_til_doms" | "cites" | (string & {});
export interface AppealLink { relation: LinkRelation; confidence: number | null; method: string | null; document_id: string; source: string; urlausn: string; }
export interface CitationRef {
  document_id: string; urlausn: string; source: string; document_date: string | null;
  layer: "summary" | "body"; passage_id: string | null; anchor: string | null; raw_text: string;
  confidence: number | null; also_appeal: boolean; same_case: boolean;
}
export interface CitationsResponse { direction: "out" | "in"; total: number; page: number; page_size: number; items: CitationRef[]; }
// DocumentDetail gains:
//   citations_out: CitationRef[]; citations_out_total: number; cited_by: CitationRef[]; cited_by_total: number; citations_unresolved_total: number;
// SearchResult gains: cited_by_count: number;
```

- [ ] **Step 2: Failing tests**

In `DocPanel.test.tsx`: render with `citations_out=[{…also_appeal:false}]`, `cited_by=[{…same_case:true}]`, totals 1 and 3 → expect headings "Tilvitnanir", "Vitnar í (1)", "Vitnað í þennan dóm (3)", the `raw_text` visible, "(sama mál)" badge, a "Sýna fleiri" button only for the list whose total exceeds the items shown, and "2 tilvitnanir fundust ekki í safninu" when `citations_unresolved_total=2`. Render with `appeal_links=[{relation:"leyfisbeidni_um"…}]` → label "Málskotsbeiðni um:". Render with an out-citation that is `also_appeal:true` and the same document in `appeal_links` → the document appears once under "Tengd mál" with badge "(í áfrýjunarkeðju)" in Tilvitnanir? No — decision: it appears in **Tilvitnanir** with the badge and is **omitted from Tengd mál**; assert exactly one link to `/domur/<id>` in the whole panel. In `ResultCard.test.tsx`: `cited_by_count: 12` renders "vitnað í 12 sinnum"; `0` renders nothing.

- [ ] **Step 3: Implement**

`RELATION_LABELS: Record<string,string> = { appealed_to: "Áfrýjað til", appealed_from: "Áfrýjað frá", leyfisbeidni_um: "Málskotsbeiðni um", leiddi_til_doms: "Leiddi til dóms" }`; label = `RELATION_LABELS[l.relation] ?? l.relation`. Tengd mál filters out links whose `document_id` is in `citations_out`/`cited_by` with `also_appeal`. Tilvitnanir section as in spec §8.2; "Sýna fleiri" calls `fetchCitations` and appends. Keep components small: put the two lists in a new `CitationList.tsx` component (props: title, items, total, onMore).

- [ ] **Step 4: Run and commit**

```bash
cd frontend && npx vitest run && npx tsc -b && cd ..
git add frontend/src
git commit -m "feat(frontend): Tilvitnanir section, relation labels, cited_by_count"
```

---

### Task 8: MCP — `citations` tool and counts in `get_document`

**Files:**
- Modify: `engine/mcp/tools.py` (`get_document_tool` adds `citations_out_total`, `cited_by_total`, `citations_out` (≤5), `cited_by` (≤5); new `citations_tool(session, *, doc_id, direction="out", page=1, page_size=10)`)
- Modify: `engine/mcp/server.py` (ninth tool `citations`, `TOOL_NAMES`, description in Icelandic; `INSTRUCTIONS` gains one sentence: "Notaðu `citations` til að sjá í hvaða dóma er vitnað og hverjir vitna í dóm.")
- Modify: `tests/test_mcp_server.py` (nine names, order: append `citations` last), `tests/test_mcp_stdio.py` (count 9), `tests/test_mcp_tools_db.py` (one test calling `citations_tool` both directions on a resolved pair), `tests/test_mcp_tools_unit.py` (page_size clamp ≤25, bad direction → ToolInputError).
- Modify: `docs/wiki/10-mcp.md` (ninth row).

- [ ] **Step 1: Failing tests → Step 2: implement → Step 3: run `tests/test_mcp_*.py` + full suite → Step 4: commit** `feat(mcp): citations tool and citation counts in get_document`.

The tool reuses `queries.get_citations`; items are compacted to `document_id, urlausn, date, layer, anchor, raw_text, also_appeal, same_case` (drop `passage_id`, `confidence`, `source` to save tokens? — keep `passage_id`, it is what `passage_context` needs). `page_size` clamp 1–25 default 10.

---

### Task 9: Precision audit (200 stratified) with an independent verifier

**Files:**
- Create: `scripts/audit_citations.py` (samples 200 resolved rows stratified by `method × target_court`, runs the **independent** verifier described in spec §9 (a)–(f) using its own regexes — do not import `engine.processors.citations` — prints a table per stratum and writes `/tmp/lausnir-dev/citations_audit.jsonl` with `raw_text`, ±250-char context, target urlausn, mechanical verdict)
- Test: `tests/test_audit_citations_verifier.py` (the verifier's own regexes on 6 fixed examples: 4 correct, 2 deliberately wrong)

- [ ] **Step 1** write the verifier + tests; **Step 2** run the audit against the live DB (read-only role); **Step 3** a reviewer subagent reads the 200 contexts from the JSONL and marks each `correct / wrong / unclear` with a one-line reason (dispatched by the controller, not the implementer); **Step 4** the implementer folds the human verdicts into the table and writes `## Nákvæmniúttekt` into the plan with: precision overall and per stratum, every wrong example with raw_text and target, and the fix (if any) it implies. Gate: ≥ 98 % overall. If missed: fix the extractor/resolver with a test per error class, re-run Task 5 Step 4 for the affected documents (`--force --source …` or `--all --force`), re-audit a fresh 200; record both rounds. **Step 5** commit `test(citations): independent precision audit script and results`.

---

### Task 10: Documentation

**Files:**
- Modify: `docs/wiki/02-gagnagrunnur.md` (new `citations` table; `citation_hash`; **all** `document_links` relations: `appealed_to`, `appealed_from`, `leyfisbeidni_um`, `leiddi_til_doms`, `cites` with orientation and one-way/mirrored note; `ix_link_to_rel`), `04-innflutningur.md` (post-import order: `backfill_passages.py` → `build_citations.py --all` → `build_citations.py --relink-unresolved`), `05-leit.md`/`06-api.md`/`07-framendi.md` (new fields, endpoint, UI section), `10-mcp.md` (already touched in Task 8 — verify), `09-gildrur.md` (seven gotchas from spec §11), `README.md` (index), `docs/superpowers/specs/2026-09-29-citations-design.md` (status line → implemented; link to results and audit sections in the plan).
- Verify: `grep -n "cites\|citations" docs/wiki/02-gagnagrunnur.md docs/wiki/06-api.md docs/wiki/07-framendi.md docs/wiki/04-innflutningur.md | wc -l` ≥ 8; no placeholders.
- Commit `docs: citations — schema, API, frontend, import order, gotchas`.

---

## Self-review notes

- Spec §4.1/4.2/4.3 → Task 1 + Task 4; §5 → Task 2; §6 → Task 3; §7 → Task 4 (+ Task 5 run); §8.1 → Task 6; §8.2 → Task 7; §8.3 → Task 8; §9 → Tasks 5 and 9; §10 → tests in each task; §11 → Task 10; §12 gates → Task 4 Step 6 and Task 5 Step 4.
- Names consistent: `extract_citations`, `RawCitation`, `norm_case_number`, `CitationIndex`, `Candidate`, `Resolution`, `resolve`, `build_rows`, `rebuild_citations`, `relink_unresolved`, `citation_hash`, `STALE_WHERE`, `get_citations`, `citations_tool`.
- Review Focus 1 → Task 3 tests; 2 → Task 2 `test_enumeration…`; 3 → Task 2 `test_relative_sl…`; 4 → Task 4 `test_rebuild_is_scoped`; 5 → Task 4 `test_document_without_text`.
- Known soft spot: Task 2's reference implementation is deliberately marked as a starting point; the tests are the contract. The implementer for Task 2 should be a capable model.

---

## Niðurstöður keyrslu (2026-09-29)

Full run #2, after the extractor and resolver fixes landed (Félagsdómur `F-` fallback, standalone-year rule, effective-court rule in the audit verifier).

**Run summary:**

| Mæling | Gildi |
|---|---|
| Skjöl unnin | 44.513 (allar sjö dómstólaheimildir með texta) |
| `citations`-raðir | 60.669 |
| `cites`-brýr | 22.868 |
| Villur | 0 |
| Keyrslutími | 409 s, 8 vinnsluferli |

**Staða (`citations.status`), allar 60.669 raðir:**

| Staða | Fjöldi |
|---|---|
| `resolved` | 51.794 |
| `unresolved` | 4.793 |
| `pre_coverage` | 3.268 |
| `self` | 677 |
| `ambiguous` | 137 |

**Lag (`citations.layer`):**

| Lag | Fjöldi |
|---|---|
| `body` | 36.906 |
| `lower_body` | 23.433 |
| `summary` | 330 |

**Þekja** (2.400 skjala úrtak, lokareglur): **88,2 %** = `resolved / (resolved + ambiguous + unresolved)`, talið yfir `summary`+`body` lögin — yfir 80 % viðmiðinu í spec §9. Félagsdómur `F-`-fallleiðin ein og sér bætti þekjuna um **+0,87 hlutfallsstig** í deterministic A/B-samanburði (fallleiðin á/af, sama úrtak, sama reglur að öðru leyti).

**Stærstu leystu tilvitnanaflæðin** (vitnandi dómstóll → vitnaður dómstóll; frá keyrslu #1 — keyrsla #2 er sögð víkja innan við 2 % frá þessum tölum, því „≈" hér að neðan):

| Flæði | Fjöldi (≈) |
|---|---|
| Hrd. → Hrd. | 14.248 |
| Hérd. Rvk. → Hrd. | 10.510 |
| Lrd. → Hrd. | 9.236 |
| Hérd. Reykn. → Hrd. | 1.794 |

**Algengustu `unresolved`-formin eftir keyrslu #2** (öll eru raunveruleg eyður í safninu — óbirtir héraðsdómar, Landsréttarnúmer sem eru ekki í safninu, Félagsdómur fyrir 2000 — **ekki** göt í mynstrunum sjálfum):

| Form (tölur → `#`) | Fjöldi |
|---|---|
| „Landsréttar í máli nr. ###/####" | 89 |
| „Félagsdóms í máli nr. #/####" | 60 |
| „Héraðsdóms Reykjavíkur í máli nr. E-####/####" | 55 |
| „Hæstaréttar í máli nr. ###/####" | 52 |
| „Héraðsdóms Reykjavíkur í máli nr. R-###/####" | 50 |

**`check_link_orientation.py` eftir keyrsluna:** `cites`-athugunin (engin brún á síðar-dagsett skjal) skilar **0**. Skriptan finnur að auki **4** eldri, **ótengd** rangstefnu-tilvik í `appealed_to`/`appealed_from`-pörum sem voru til staðar fyrir þessa vinnu — skráð sem opið atriði í [09-gildrur](../../wiki/09-gildrur.md), ekki lagfært hér.

## Nákvæmniúttekt

Sjálfstæð úttekt (`scripts/audit_citations.py`), 200 `status='resolved'` raðir úr `summary`/`body`, lagskiptar eftir `method` × `target_court`, sannprófaðar með reglum skrifuðum **frá spec-inu, ekki frá útdráttarkóðanum** (sjá kafla 15 í hönnunarskjalinu fyrir tvær lagfæringar sem sannprófarinn sjálfur fékk).

**Vélrænar tölur úr keyrslu #1** (áður en úttektarreglurnar tvær í kafla 15 voru lagfærðar — talan er birt hér óbreytt sem grunnlína; sjá athugasemd hér að neðan um keyrslu #2):

- Talna-, stefnu- og bils-athuganirnar (checks „number", „order", „span") stóðust í öllum 200/200 tilvikum.
- **199/200** raðir voru vélrænt samræmar (`mech_ok`) eftir að tvær falskar villuviðvaranir í sannprófaranum sjálfum voru leiðréttar (sjá kafla 15: Félagsdómur `F-`-frávik og virki dómstóllinn skv. §5.3).
- **30/200** báru misræmi milli dómsorðsins næst á undan dómstólsorðinu og hins geymda `verdict_type` (`verdict_mismatch`) — þetta er merkingarvenja Hæstaréttar við kærumál (dómurinn er orðaður „dómi …" þótt `verdict_type` sé skráð `Úrskurður`), **ekki** rangur hlekkur, og telst því ekki með í `mech_ok`/`mech_fail`-heildinni (sjá kafla 15, fjórða atriði).
- **1** dagsetningarfrávik reyndist innsláttarvilla (rangt ár) í frumtexta heimildarinnar sjálfrar, ekki í útdrættinum.

**Keyrsla #2 (2026-09-29):** sama sjálfstæða úttektarskripta keyrð aftur á 200 fersk lagskipt sýni eftir að öll fimm atriðin í kafla 15 höfðu verið löguð. Manneskjulegi lesturinn á ±250 stafa samhengi þeirra 200 sýna (er þetta yfirhöfuð tilvísun í úrlausn; á dómstólsorðið við þetta númer) er skráður hér af stjórnandanum eftir yfirferð, ekki af innleiðingaraðilanum.

### Lokaúttekt á keyrslu #2 (200 leystar tilvitnanir, nýtt lagskipt úrtak, 2026-09-29)

| Þrep | Niðurstaða |
|---|---|
| Vélrænar athuganir (númer, dómstóll, dagsetning, tímaröð, textabil) | 200/200 í lagi |
| Dómsorð í texta vs. skráð `verdict_type` | 29/200 misræmi — allt raðir þar sem grunnurinn skráir „Úrskurður“ en textinn segir „dómur“; 8 þeirra eru „Áfrýjað er dómi Héraðsdóms … E-…“ (áfrýjuð E-mál eru dómar), svo skráningin er röng, ekki tengingin. Sérstakt lagfæringarefni: `verdict_type` kærumála Hæstaréttar fyrir 2018 og nokkurra héraðsdóma. |
| Mannlegur lestur í samhengi | **196 réttar, 0 rangar, 4 óljósar** |
| Nákvæmni | 196/196 = **100 %** (réttar af réttum+röngum); íhaldssamt 196/200 = **98,0 %** → viðmið ≥ 98 % **stenst** |

Óljósu fjórar vísa allar á rétt mál en textinn vísar til málsmeðferðar frekar en úrlausnarinnar: `4191eb2f` („héraðsdómara í máli nr. Z-9/2013“, ekkert dómstólsorð, leyst milli héraða með einkvæmni), `3e515abf` (greinargerð í E-151/2020), `42ebfe82` (ákæra vísar til fyrri meðferðar í S-114/2018), `b1c85081` (Landsdómur kom saman í máli 1/2011). Sérstaklega athugað og ekki fundið: rangt erft dómstólsorð í upptalningum, lögnúmer lesin sem málsnúmer, sjálfstilvísanir, villur í afstæðum dagsetningum. Eina eftirstandandi áhættan sem lesarinn nefndi: bert „héraðsdóm(ara)“ án staðar leyst milli héraða með einkvæmni í safninu (1 af 200).
