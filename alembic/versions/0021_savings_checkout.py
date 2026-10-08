"""Durable savings checkout attempts; no provider calls or financial backfill."""

import sqlalchemy as sa

from alembic import op

revision = "0021_savings_checkout"
down_revision = "0020_savings_ledger"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "savings_checkout_attempts",
        sa.Column(
            "payment_id", sa.Integer(), sa.ForeignKey("savings_payments.id"), primary_key=True
        ),
        sa.Column("receipt", sa.String(40), nullable=False, unique=True),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "state IN ('creating', 'unknown', 'ready')", name="ck_savings_checkout_state"
        ),
    )


def downgrade():
    if (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM savings_checkout_attempts"))
        .scalar_one()
    ):
        raise RuntimeError("Checkout attempts exist; reviewed preservation plan required")
    op.drop_table("savings_checkout_attempts")
