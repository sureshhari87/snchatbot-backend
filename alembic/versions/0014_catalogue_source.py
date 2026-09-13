"""Preserve imported catalogue identities and source attributes."""

import sqlalchemy as sa

from alembic import op

revision = "0014_catalogue_source"
down_revision = "0013_commerce_cart"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("products", sa.Column("source_id", sa.String(), nullable=True))
    op.add_column("products", sa.Column("source_data", sa.JSON(), nullable=True))
    op.create_index("ix_products_source_id", "products", ["source_id"], unique=True)


def downgrade():
    op.drop_index("ix_products_source_id", table_name="products")
    op.drop_column("products", "source_data")
    op.drop_column("products", "source_id")
