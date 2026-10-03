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
        # The manifest file is the issue's identity: importers upsert on manifest_path
        # because analyze may change volume/year after discovery.
        op.create_index("uq_journal_issue_manifest", "journal_issues", ["manifest_path"], unique=True)
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
    op.drop_index("uq_journal_issue_manifest", table_name="journal_issues")
    op.execute("DROP INDEX IF EXISTS uq_journal_issue")
    op.drop_index("ix_journal_issue_status", table_name="journal_issues")
    op.drop_index("ix_journal_issue_source", table_name="journal_issues")
    op.drop_table("journal_issues")
