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
    ForeignKey,
    Index,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
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

    # ── Search ────────────────────────────────────────────────────────────────
    embedding: Mapped[Any | None] = mapped_column(Vector(3072))
    # tsvector computed on insert/update via trigger (see migration)

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
    )

    def __repr__(self) -> str:
        return f"<Document {self.case_number or self.external_id!r} ({self.court})>"
