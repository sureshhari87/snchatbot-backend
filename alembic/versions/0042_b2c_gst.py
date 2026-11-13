"""Append-only B2C approvals and invoice adjustments; no historic tax updates."""

import sqlalchemy as sa

from alembic import op

revision = "0042_b2c_gst"
down_revision = "0041_customer_profiles"
branch_labels = None
depends_on = None
TABLES = ("billing_tax_approvals", "billing_invoice_adjustments", "billing_supply_events")


def upgrade():
    op.create_table(
        "billing_supply_events",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "billing_tax_approvals",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("scope", sa.String(120), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("scope", "version", name="uq_billing_tax_version"),
        sa.CheckConstraint(
            "status IN ('pending','approved','revoked')", name="ck_billing_tax_status"
        ),
    )
    op.create_index("ix_billing_tax_approvals_scope", "billing_tax_approvals", ["scope"])
    op.create_table(
        "billing_invoice_adjustments",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "invoice_id", sa.String(32), sa.ForeignKey("billing_documents.id"), nullable=False
        ),
        sa.Column("request_key", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("number", sa.String(16)),
        sa.Column("provider_reference", sa.String(100)),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("invoice_id", "request_key", name="uq_billing_adjustment_request"),
        sa.UniqueConstraint("number", name="uq_billing_adjustment_number"),
        sa.UniqueConstraint("provider_reference", name="uq_billing_adjustment_refund"),
        sa.CheckConstraint(
            "kind IN ('credit_note','cancellation','refund')", name="ck_billing_adjustment_kind"
        ),
    )
    op.create_index(
        "ix_billing_invoice_adjustments_invoice_id", "billing_invoice_adjustments", ["invoice_id"]
    )
    for table in TABLES:
        dialect = op.get_bind().dialect.name
        if dialect == "postgresql":
            op.execute(
                f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION sona_billing_immutable()"
            )
        elif dialect == "sqlite":
            for action in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER {table}_immutable_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'Billing records are immutable'); END"
                )
        else:
            raise RuntimeError("Billing requires PostgreSQL or SQLite")


def downgrade():
    for table in TABLES:
        if op.get_bind().execute(sa.text("SELECT count(*) FROM " + table)).scalar_one():
            raise RuntimeError("Billing history exists; reviewed preservation plan required")
    for table in reversed(TABLES):
        op.drop_table(table)
