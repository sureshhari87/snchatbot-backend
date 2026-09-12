"""Add authenticated FastAPI shopping carts."""

import sqlalchemy as sa

from alembic import op

revision = "0013_commerce_cart"
down_revision = "0012_firebase_commerce_identity"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "commerce_cart_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_commerce_cart_items_user_id", "commerce_cart_items", ["user_id"])


def downgrade():
    op.drop_table("commerce_cart_items")
