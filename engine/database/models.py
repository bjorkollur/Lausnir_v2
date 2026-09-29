"""
Database models for Lausnir v2.

Design principles:
- raw_api_data is immutable — never overwrite after first write
- body_text / lower_body_text are extracted at import, never manually edited
- .md files are derived from DB columns — always re-generatable
- validation_errors stores detected issues without blocking import
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    short_name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    base_url: Mapped[str | None] = mapped_column(Text)
    collector_config: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    documents: Mapped[list["Document"]] = relationship(back_populates="source")

    def __repr__(self) -> str:
        return f"<Source {self.short_name!r}>"


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), nullable=False
    )
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(Text)

    # ── Layer 1: RAW ─────────────────────────────────────────────────────────
    raw_api_data: Mapped[dict | None] = mapped_column(JSONB)

    # ── Layer 2: NORM ─────────────────────────────────────────────────────────
    case_number: Mapped[str | None] = mapped_column(Text)
    document_date: Mapped[date | None] = mapped_column(Date)
    court: Mapped[str | None] = mapped_column(Text)        # abbreviation
    verdict_type: Mapped[str | None] = mapped_column(Text)
    instance_tier: Mapped[int | None] = mapped_column(SmallInteger)

    plaintiffs: Mapped[list[Any] | None] = mapped_column(JSONB)   # [{name, lawyer}]
    defendants: Mapped[list[Any] | None] = mapped_column(JSONB)   # [{name, lawyer}]
    keywords: Mapped[list[str] | None] = mapped_column(JSONB)
    summary: Mapped[str | None] = mapped_column(Text)

    case_type: Mapped[str | None] = mapped_column(Text)         # Einkamál / Sakamál / Stjórnsýslumál / o.fl.

    body_text: Mapped[str | None] = mapped_column(Text)        # current court body
    lower_body_text: Mapped[str | None] = mapped_column(Text)  # embedded lower court
    provisions: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # [{num, text}] for lagasafn

    isbn: Mapped[str | None] = mapped_column(Text)        # logfraedibaekur only
    publisher: Mapped[str | None] = mapped_column(Text)   # logfraedibaekur only

    # malskotsbeidnir only — did Hæstiréttur grant leave to appeal?
    # 'veitt' | 'hafnað' | NULL (no outcome stated, or not this source)
    appeal_outcome: Mapped[str | None] = mapped_column(Text)

    # ── Search ────────────────────────────────────────────────────────────────
    embedding: Mapped[Any | None] = mapped_column(Vector(3072))
    # fts: GENERATED ALWAYS AS (to_tsvector('simple', ...)) — exact/keyword
    # fts_is: BÍN-lemmatized tsvector — Icelandic morphology-aware search
    fts_is: Mapped[Any | None] = mapped_column(TSVECTOR)
    cited_provisions: Mapped[list[Any] | None] = mapped_column(JSONB)
    # GIN index ix_doc_cited_provisions created by scripts/setup_provision_index.py
    # md5(summary ‖ \x1f ‖ body_text ‖ \x1f ‖ lower_body_text) as of the last
    # passages build. NULL or mismatch = passages are stale (see passage_index.py).
    passage_hash: Mapped[str | None] = mapped_column(Text)
    citation_hash: Mapped[str | None] = mapped_column(Text)   # sha256 of summary|body|lower; see build_citations.py

    # ── Paths ─────────────────────────────────────────────────────────────────
    verdict_filename: Mapped[str | None] = mapped_column(Text)

    # ── Metadata ──────────────────────────────────────────────────────────────
    validation_errors: Mapped[list[Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )

    source: Mapped["Source"] = relationship(back_populates="documents")

    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_doc_source_external"),
        Index("ix_doc_case_number", "case_number"),
        Index("ix_doc_court", "court"),
        Index("ix_doc_date", "document_date"),
        Index("ix_doc_source_id", "source_id"),
        Index("ix_doc_fts_is", "fts_is", postgresql_using="gin"),
        # Faceting / scoped-date filters for the search API.
        Index("ix_doc_source_date", "source_id", "document_date"),
        Index("ix_doc_verdict_type", "verdict_type"),
        Index("ix_doc_instance_tier", "instance_tier"),
        Index("ix_doc_case_type", "case_type"),
        # NB: the pg_trgm GIN indexes used by regex search — ix_doc_body_trgm,
        # ix_doc_summary_trgm, ix_doc_case_number_trgm — are created by
        # scripts/setup_search_indexes.py, NOT here. They need the pg_trgm
        # extension at DDL time, which create_all() would not install.
    )

    def __repr__(self) -> str:
        return f"<Document {self.case_number or self.external_id!r} ({self.court})>"


class DocumentLink(Base):
    """Appeals-chain edge: a lower-court doc was appealed to a higher-court doc.

    Direction is always lower → higher (from_doc_id → to_doc_id) with
    relation 'appealed_to'. The chain Hérd → Lrd → Hrd is reconstructed by
    following edges; instance_tier on each document disambiguates the rungs.
    Built by matching a higher court's embedded lower_body_text against the
    lower court's own body_text (see scripts/link_appeals.py).
    """
    __tablename__ = "document_links"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    from_doc_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    to_doc_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    relation: Mapped[str] = mapped_column(Text, nullable=False)  # 'appealed_to'
    confidence: Mapped[float | None] = mapped_column(Float)       # combined-sim score
    method: Mapped[str | None] = mapped_column(Text)             # casenum | court_date | court_window
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        UniqueConstraint("from_doc_id", "to_doc_id", "relation", name="uq_link_from_to_rel"),
        Index("ix_link_from", "from_doc_id"),
        Index("ix_link_to", "to_doc_id"),
        Index("ix_link_to_rel", "to_doc_id", "relation"),
    )

    def __repr__(self) -> str:
        return f"<DocumentLink {self.from_doc_id} -{self.relation}-> {self.to_doc_id}>"


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

    def __repr__(self) -> str:
        return f"<Citation {self.from_doc_id} {self.layer}@{self.char_start} -> {self.target_case_number!r}>"


class Passage(Base):
    """One citable passage of a document (spec: 2026-09-27-passages-design.md).

    layer: 'summary' | 'body' | 'lower_body' — which column char_start/char_end
    index into. Passages are contiguous, non-overlapping, ordered by ordinal
    across all three layers. fts_is is BÍN-lemmatised like documents.fts_is.
    """
    __tablename__ = "passages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    layer: Mapped[str] = mapped_column(Text, nullable=False)
    section_path: Mapped[str | None] = mapped_column(Text)
    section_kind: Mapped[str] = mapped_column(Text, nullable=False)
    para_from: Mapped[int | None] = mapped_column(SmallInteger)
    para_to: Mapped[int | None] = mapped_column(SmallInteger)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    word_count: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    fts_is: Mapped[Any] = mapped_column(TSVECTOR, nullable=False)

    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="uq_passage_doc_ordinal"),
        Index("ix_passage_doc", "document_id", "ordinal"),
        Index("ix_passage_fts_is", "fts_is", postgresql_using="gin"),
        Index("ix_passage_section_kind", "section_kind"),
    )

    def __repr__(self) -> str:
        return f"<Passage doc={self.document_id} #{self.ordinal} {self.layer}/{self.section_kind}>"
