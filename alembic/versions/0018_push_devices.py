"""Session-owned push registrations; sending remains disabled."""

import sqlalchemy as sa

from alembic import op

revision = "0018_push_devices"
down_revision = "0017_system_notifications"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "push_devices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("family_id", sa.String(100), nullable=False, unique=True),
        sa.Column("token_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("push_token", sa.String(4096), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_push_devices_user_id", "push_devices", ["user_id"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT count(*) FROM push_devices")).scalar_one():
        raise RuntimeError("Device registrations exist; reviewed preservation plan required")
    op.drop_index("ix_push_devices_user_id", table_name="push_devices")
    op.drop_table("push_devices")
