"""Private review photo bytes and owned, idempotent upload references."""

import sqlalchemy as sa

from alembic import op

revision = "0036_review_photos"
down_revision = "0035_review_purchase"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "review_photos",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("review_id", sa.Integer(), sa.ForeignKey("product_reviews.id"), nullable=True),
        sa.Column("upload_key", sa.String(100), nullable=False),
        sa.Column("input_sha256", sa.String(64), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=True),
        sa.Column("stored_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("removed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("owner_id", "upload_key", name="uq_review_photo_upload"),
        sa.CheckConstraint(
            "stored_bytes >= 0 AND stored_bytes <= 393216", name="ck_review_photo_size"
        ),
        sa.CheckConstraint(
            "(removed_at IS NULL AND content IS NOT NULL AND stored_bytes > 0 AND length(content) = stored_bytes) OR (removed_at IS NOT NULL AND content IS NULL AND stored_bytes = 0 AND review_id IS NULL)",
            name="ck_review_photo_content",
        ),
    )
    op.create_index("ix_review_photos_owner_id", "review_photos", ["owner_id"])
    op.create_index("ix_review_photos_review_id", "review_photos", ["review_id"])


def downgrade():
    op.drop_table("review_photos")
