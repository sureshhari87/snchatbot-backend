"""Allow system-generated notifications without inventing a human sender."""

import sqlalchemy as sa

from alembic import op

revision = "0017_system_notifications"
down_revision = "0016_notification_inbox"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("customer_notifications") as batch:
        batch.alter_column("created_by", existing_type=sa.Integer(), nullable=True)


def downgrade():
    count = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM customer_notifications WHERE created_by IS NULL"))
        .scalar_one()
    )
    if count:
        raise RuntimeError(
            "System notifications exist; downgrade requires a reviewed preservation plan"
        )
    with op.batch_alter_table("customer_notifications") as batch:
        batch.alter_column("created_by", existing_type=sa.Integer(), nullable=False)
