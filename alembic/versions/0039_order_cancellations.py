"""Negotiated order cancellations; no automatic refunds or data backfill."""

import sqlalchemy as sa

from alembic import op

revision = "0039_order_cancellations"
down_revision = "0038_billing_documents"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "order_cancellations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "order_id",
            sa.Integer(),
            sa.ForeignKey("order_snapshots.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(1000), nullable=False),
        sa.Column("cash_paise", sa.BigInteger(), nullable=False),
        sa.Column("refund_paise", sa.BigInteger(), nullable=False),
        sa.Column("proposal_json", sa.Text()),
        sa.Column("payment_id", sa.String(100)),
        sa.Column("refund_id", sa.String(100), unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_cancellation_version"),
        sa.CheckConstraint(
            "cash_paise >= 0 AND refund_paise >= 0 AND refund_paise <= cash_paise",
            name="ck_cancellation_money",
        ),
        sa.CheckConstraint(
            "state IN ('requested','return_review','proposed','accepted','disputed','refund_pending','refund_review','refunded','cancelled')",
            name="ck_cancellation_state",
        ),
    )
    op.create_index("ix_order_cancellations_user_id", "order_cancellations", ["user_id"])
    op.create_table(
        "order_cancellation_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "case_id", sa.String(32), sa.ForeignKey("order_cancellations.id"), nullable=False
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("detail_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("case_id", "version", name="uq_cancellation_event_version"),
    )
    op.create_index(
        "ix_order_cancellation_events_case_id", "order_cancellation_events", ["case_id"]
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE TRIGGER order_cancellation_events_immutable BEFORE UPDATE OR DELETE ON order_cancellation_events FOR EACH ROW EXECUTE FUNCTION sona_billing_immutable()"
        )
    elif op.get_bind().dialect.name == "sqlite":
        for action in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER order_cancellation_events_no_{action.lower()} BEFORE {action} ON order_cancellation_events BEGIN SELECT RAISE(ABORT, 'Cancellation audit is immutable'); END"
            )


def downgrade():
    raise RuntimeError("Cancellation audit must be preserved; disable feature flags instead")
