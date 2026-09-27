"""passages table + documents.passage_hash

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    doc_cols = {c["name"] for c in insp.get_columns("documents")}
    if "passage_hash" not in doc_cols:
        op.add_column("documents", sa.Column("passage_hash", sa.Text(), nullable=True))
    if not insp.has_table("passages"):
        op.create_table(
            "passages",
            sa.Column("id", sa.UUID(), nullable=False),
            sa.Column("document_id", sa.UUID(), nullable=False),
            sa.Column("ordinal", sa.Integer(), nullable=False),
            sa.Column("layer", sa.Text(), nullable=False),
            sa.Column("section_path", sa.Text(), nullable=True),
            sa.Column("section_kind", sa.Text(), nullable=False),
            sa.Column("para_from", sa.SmallInteger(), nullable=True),
            sa.Column("para_to", sa.SmallInteger(), nullable=True),
            sa.Column("char_start", sa.Integer(), nullable=False),
            sa.Column("char_end", sa.Integer(), nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("word_count", sa.SmallInteger(), nullable=False),
            sa.Column("fts_is", postgresql.TSVECTOR(), nullable=False),
            sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("document_id", "ordinal", name="uq_passage_doc_ordinal"),
        )
        op.create_index("ix_passage_doc", "passages", ["document_id", "ordinal"])
        op.create_index("ix_passage_fts_is", "passages", ["fts_is"], postgresql_using="gin")
        op.create_index("ix_passage_section_kind", "passages", ["section_kind"])


def downgrade() -> None:
    op.drop_index("ix_passage_section_kind", table_name="passages")
    op.drop_index("ix_passage_fts_is", table_name="passages")
    op.drop_index("ix_passage_doc", table_name="passages")
    op.drop_table("passages")
    op.drop_column("documents", "passage_hash")
