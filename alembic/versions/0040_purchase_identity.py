"""Encrypted high-value buyer facts; no backfill or collection enablement."""

import sqlalchemy as sa

from alembic import op

revision = "0040_purchase_identity"
down_revision = "0039_order_cancellations"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "purchase_identities",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("request_key", sa.String(80), nullable=False),
        sa.Column("encrypted_facts", sa.Text(), nullable=False),
        sa.Column("pan_suffix", sa.String(4), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "version", name="uq_purchase_identity_version"),
        sa.UniqueConstraint("user_id", "request_key", name="uq_purchase_identity_request"),
        sa.CheckConstraint("version > 0", name="ck_purchase_identity_version"),
    )
    op.create_index("ix_purchase_identities_user_id", "purchase_identities", ["user_id"])
    op.create_table(
        "purchase_identity_reviews",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "identity_id",
            sa.String(32),
            sa.ForeignKey("purchase_identities.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("decision", sa.String(12), nullable=False),
        sa.Column("method", sa.String(40), nullable=False),
        sa.Column("evidence_reference", sa.String(160), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "decision IN ('approved','rejected')", name="ck_purchase_identity_decision"
        ),
    )
    op.create_table(
        "purchase_identity_uses",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "identity_id", sa.String(32), sa.ForeignKey("purchase_identities.id"), nullable=False
        ),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("resource_kind", sa.String(30), nullable=False),
        sa.Column("resource_key", sa.String(100), nullable=False),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("resource_kind", "resource_key", name="uq_purchase_identity_use"),
        sa.CheckConstraint("amount_paise > 20000000", name="ck_purchase_identity_amount"),
    )
    for table in ("purchase_identities", "purchase_identity_reviews", "purchase_identity_uses"):
        if op.get_bind().dialect.name == "postgresql":
            op.execute(
                f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION sona_billing_immutable()"
            )
        elif op.get_bind().dialect.name == "sqlite":
            for action in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'Buyer verification evidence is immutable'); END"
                )


def downgrade():
    raise RuntimeError("Preserve buyer verification evidence; disable the feature instead")
