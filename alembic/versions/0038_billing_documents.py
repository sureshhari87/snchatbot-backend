"""Cross-service immutable acknowledgements and sales documents."""

import sqlalchemy as sa

from alembic import op

revision = "0038_billing_documents"
down_revision = "0037_billing_receipts"
branch_labels = None
depends_on = None
TABLES = ("billing_documents", "billing_document_audit")


def upgrade():
    # Explicit column copies keep this revision independent of future model changes.
    op.create_table(
        "billing_series",
        sa.Column("key", sa.String(20), primary_key=True),
        sa.Column("next_sequence", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("next_sequence > 0", name="ck_billing_series_sequence"),
    )
    op.create_table(
        "billing_documents",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("number", sa.String(16)),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("source", "source_id", "kind", name="uq_billing_document_source_kind"),
        sa.UniqueConstraint("number", name="uq_billing_document_number"),
        sa.CheckConstraint("source_id > 0", name="ck_billing_document_source_id"),
        sa.CheckConstraint("kind IN ('receipt','sales_invoice')", name="ck_billing_document_kind"),
        sa.CheckConstraint(
            "source IN ('voucher','custom_advance','custom_balance','merchandise','custom_design')",
            name="ck_billing_document_source",
        ),
        sa.CheckConstraint("length(snapshot_sha256) = 64", name="ck_billing_document_digest"),
    )
    op.create_index("ix_billing_documents_user_id", "billing_documents", ["user_id"])
    op.create_table(
        "billing_document_audit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "document_id",
            sa.String(32),
            sa.ForeignKey("billing_documents.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reason", sa.String(200), nullable=False),
        sa.Column("snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    for table in TABLES:
        if op.get_bind().dialect.name == "postgresql":
            op.execute(
                f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION sona_billing_immutable()"
            )
        elif op.get_bind().dialect.name == "sqlite":
            for action in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER {table}_immutable_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'Billing documents are immutable'); END"
                )
        else:
            raise RuntimeError("Billing immutability requires PostgreSQL or SQLite")


def downgrade():
    for table in TABLES + ("billing_series",):
        if op.get_bind().execute(sa.text("SELECT count(*) FROM " + table)).scalar_one():
            raise RuntimeError("Billing history exists; reviewed preservation plan required")
    for table in reversed(TABLES):
        op.drop_table(table)
    op.drop_table("billing_series")
