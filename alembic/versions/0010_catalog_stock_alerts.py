"""Add catalogue metadata and stock alert subscriptions.

Revision ID: 0010_catalog_stock_alerts
Revises: 0009_ai_generated_concepts
Create Date: 2026-08-13
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0010_catalog_stock_alerts"
down_revision: Union[str, None] = "0009_ai_generated_concepts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    return _inspector().has_table(table_name)


def _columns(table_name: str) -> set[str]:
    if not _has_table(table_name):
        return set()
    return {column["name"] for column in _inspector().get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    if not _has_table(table_name):
        return set()
    return {index["name"] for index in _inspector().get_indexes(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if _has_table(table_name) and column.name not in _columns(table_name):
        op.add_column(table_name, column)


def _create_index_if_missing(
    index_name: str, table_name: str, columns: list[str], unique: bool = False
) -> None:
    if _has_table(table_name) and index_name not in _indexes(table_name):
        op.create_index(index_name, table_name, columns, unique=unique)


def upgrade() -> None:
    _add_column_if_missing("products", sa.Column("product_type", sa.String(), nullable=True))
    _add_column_if_missing("products", sa.Column("audience", sa.String(), nullable=True))
    _add_column_if_missing("products", sa.Column("purity", sa.String(), nullable=True))
    _add_column_if_missing("products", sa.Column("weight", sa.Float(), nullable=True))
    _add_column_if_missing("products", sa.Column("tags", sa.JSON(), nullable=True))
    _add_column_if_missing("products", sa.Column("occasion", sa.JSON(), nullable=True))
    _add_column_if_missing("products", sa.Column("style", sa.JSON(), nullable=True))
    _create_index_if_missing("ix_products_product_type", "products", ["product_type"])
    _create_index_if_missing("ix_products_audience", "products", ["audience"])
    _create_index_if_missing("ix_products_purity", "products", ["purity"])

    if not _has_table("back_in_stock_subscriptions"):
        op.create_table(
            "back_in_stock_subscriptions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("product_id", sa.Integer(), nullable=False),
            sa.Column("email", sa.String(), nullable=True),
            sa.Column("phone", sa.String(), nullable=True),
            sa.Column("size", sa.String(), nullable=True),
            sa.Column("variant", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("notified_at", sa.DateTime(), nullable=True),
            sa.Column("cancelled_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing(
        "ix_back_in_stock_subscriptions_id",
        "back_in_stock_subscriptions",
        ["id"],
    )
    _create_index_if_missing(
        "ix_back_in_stock_subscriptions_user_id",
        "back_in_stock_subscriptions",
        ["user_id"],
    )
    _create_index_if_missing(
        "ix_back_in_stock_subscriptions_product_id",
        "back_in_stock_subscriptions",
        ["product_id"],
    )
    _create_index_if_missing(
        "ix_back_in_stock_subscriptions_status",
        "back_in_stock_subscriptions",
        ["status"],
    )


def downgrade() -> None:
    if _has_table("back_in_stock_subscriptions"):
        op.drop_table("back_in_stock_subscriptions")

    if _has_table("products"):
        existing_columns = _columns("products")
        for column_name in [
            "style",
            "occasion",
            "tags",
            "weight",
            "purity",
            "audience",
            "product_type",
        ]:
            if column_name in existing_columns:
                op.drop_column("products", column_name)
