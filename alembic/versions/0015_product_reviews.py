"""Add moderated reviews without altering legacy data or financial balances."""

import sqlalchemy as sa

from alembic import op

revision = "0015_product_reviews"
down_revision = "0014_catalogue_source"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "product_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("display_name", sa.String(40), nullable=False),
        sa.Column("fit", sa.String(30), nullable=False),
        sa.Column("size_feedback", sa.String(30), nullable=False),
        sa.Column("seller_response", sa.String(1000)),
        sa.Column("moderation_reason", sa.String(1000)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("moderated_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("moderated_at", sa.DateTime()),
        sa.UniqueConstraint("user_id", "product_id", name="uq_review_user_product"),
        sa.CheckConstraint("rating >= 1 AND rating <= 5", name="ck_review_rating"),
    )
    for name in ("product_id", "user_id", "status"):
        op.create_index("ix_product_reviews_" + name, "product_reviews", [name])
    op.create_table(
        "review_helpful_votes",
        sa.Column(
            "review_id",
            sa.Integer(),
            sa.ForeignKey("product_reviews.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table("review_helpful_votes")
    op.drop_table("product_reviews")
