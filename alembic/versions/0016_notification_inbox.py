"""Add recipient-owned notification inbox. No legacy records are deleted."""
import sqlalchemy as sa

from alembic import op

revision = "0016_notification_inbox"
down_revision = "0015_product_reviews"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "customer_notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("deduplication_key", sa.String(100), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("body", sa.String(2000), nullable=False),
        sa.Column("target", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("read_at", sa.DateTime()),
        sa.UniqueConstraint("user_id", "deduplication_key", name="uq_notification_recipient_key"),
    )
    op.create_index("ix_customer_notifications_user_id", "customer_notifications", ["user_id"])


def downgrade():
    op.drop_table("customer_notifications")
