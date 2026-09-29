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
