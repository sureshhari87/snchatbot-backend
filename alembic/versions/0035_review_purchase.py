"""Server-selected purchase attribution; existing reviews remain unverified."""

import sqlalchemy as sa

from alembic import op

revision = "0035_review_purchase"
down_revision = "0034_referrals"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("product_reviews") as batch:
        batch.add_column(sa.Column("verified_order_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_review_verified_order", "order_snapshots", ["verified_order_id"], ["id"]
        )


def downgrade():
    with op.batch_alter_table("product_reviews") as batch:
        batch.drop_constraint("fk_review_verified_order", type_="foreignkey")
        batch.drop_column("verified_order_id")
