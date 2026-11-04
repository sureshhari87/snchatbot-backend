"""Owned immutable payment acknowledgements and issuance audit."""

import sqlalchemy as sa

from alembic import op

revision = "0037_billing_receipts"
down_revision = "0036_review_photos"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "billing_receipts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("savings_payments.id"), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("source", "source_id", name="uq_billing_receipt_source"),
        sa.CheckConstraint(
            "source = 'savings' AND source_id > 0", name="ck_billing_receipt_source"
        ),
        sa.CheckConstraint("length(snapshot_sha256) = 64", name="ck_billing_receipt_digest"),
    )
    op.create_index("ix_billing_receipts_user_id", "billing_receipts", ["user_id"])
    op.create_table(
        "billing_receipt_audit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "receipt_id",
            sa.String(32),
            sa.ForeignKey("billing_receipts.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("action = 'issued'", name="ck_billing_receipt_audit_action"),
    )

    install_immutable_tables()


def downgrade():
    for table in ("billing_receipt_audit", "billing_receipts"):
        if op.get_bind().execute(sa.text("SELECT count(*) FROM " + table)).scalar_one():
            raise RuntimeError("Billing documents exist; reviewed preservation plan required")
    remove_immutable_tables()
    op.drop_table("billing_receipt_audit")
    op.drop_table("billing_receipts")


IMMUTABLE_TABLES = ("billing_receipts", "billing_receipt_audit")


def install_immutable_tables():
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("""CREATE FUNCTION sona_billing_immutable() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'Billing documents and issue audit are immutable'; END;
            $$ LANGUAGE plpgsql""")
        for table in IMMUTABLE_TABLES:
            op.execute(
                f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION sona_billing_immutable()"
            )
    elif dialect == "sqlite":
        for table in IMMUTABLE_TABLES:
            for action in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER {table}_immutable_{action.lower()} BEFORE {action} ON {table} "
                    "BEGIN SELECT RAISE(ABORT, 'Billing documents are immutable'); END"
                )
    else:
        raise RuntimeError("Billing immutability requires PostgreSQL or SQLite")


def remove_immutable_tables():
    dialect = op.get_bind().dialect.name
    for table in IMMUTABLE_TABLES:
        if dialect == "postgresql":
            op.execute(f"DROP TRIGGER {table}_immutable ON {table}")
        elif dialect == "sqlite":
            for action in ("update", "delete"):
                op.execute(f"DROP TRIGGER {table}_immutable_{action}")
    if dialect == "postgresql":
        op.execute("DROP FUNCTION sona_billing_immutable()")
