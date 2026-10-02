"""Disabled push event queue; no backfill and no delivery activation."""

import sqlalchemy as sa

from alembic import op

revision = "0019_push_outbox"
down_revision = "0018_push_devices"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "push_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_key", sa.String(160), nullable=False, unique=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "push_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("push_events.id"), nullable=False),
        sa.Column("family_id", sa.String(100), nullable=False),
        sa.Column("token_digest", sa.String(64), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("due_at", sa.DateTime(), nullable=False),
        sa.Column("reason", sa.String(40), nullable=True),
        sa.UniqueConstraint("event_id", "family_id", name="uq_push_event_family"),
    )
    op.create_index("ix_push_attempts_due_at", "push_attempts", ["due_at"])


def downgrade():
    for table in ("push_attempts", "push_events"):
        if op.get_bind().execute(sa.text("SELECT count(*) FROM " + table)).scalar_one():
            raise RuntimeError("Push records exist; reviewed preservation plan required")
    op.drop_index("ix_push_attempts_due_at", table_name="push_attempts")
    op.drop_table("push_attempts")
    op.drop_table("push_events")
