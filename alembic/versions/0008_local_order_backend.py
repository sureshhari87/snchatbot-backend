"""Add local order backend tables.

Revision ID: 0008_local_order_backend
Revises: 0007_user_oms_llm_monitoring
Create Date: 2026-07-26
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0008_local_order_backend"
down_revision: Union[str, None] = "0007_user_oms_llm_monitoring"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    return _inspector().has_table(table_name)


def _indexes(table_name: str) -> set[str]:
    if not _has_table(table_name):
        return set()
    return {index["name"] for index in _inspector().get_indexes(table_name)}


def _create_index_if_missing(
    index_name: str, table_name: str, columns: list[str], unique: bool = False
) -> None:
    if _has_table(table_name) and index_name not in _indexes(table_name):
        op.create_index(index_name, table_name, columns, unique=unique)


def upgrade() -> None:
    if not _has_table("order_snapshots"):
        op.create_table(
            "order_snapshots",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("order_reference", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("total", sa.Float(), nullable=False),
            sa.Column("currency", sa.String(), nullable=False),
            sa.Column("customer_name", sa.String(), nullable=True),
            sa.Column("customer_email", sa.String(), nullable=True),
            sa.Column("customer_phone", sa.String(), nullable=True),
            sa.Column("delivery_address", sa.Text(), nullable=True),
            sa.Column("payment_status", sa.String(), nullable=True),
            sa.Column("payment_reference", sa.String(), nullable=True),
            sa.Column("tracking_number", sa.String(), nullable=True),
            sa.Column("tracking_url", sa.String(), nullable=True),
            sa.Column("expected_delivery", sa.String(), nullable=True),
            sa.Column("source", sa.String(), nullable=False),
            sa.Column("raw_payload", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("ix_order_snapshots_id", "order_snapshots", ["id"])
    _create_index_if_missing("ix_order_snapshots_user_id", "order_snapshots", ["user_id"])
    _create_index_if_missing(
        "ix_order_snapshots_order_reference",
        "order_snapshots",
        ["order_reference"],
    )
    _create_index_if_missing("ix_order_snapshots_status", "order_snapshots", ["status"])
    _create_index_if_missing(
        "ix_order_snapshots_payment_status",
        "order_snapshots",
        ["payment_status"],
    )
    _create_index_if_missing(
        "ix_order_snapshots_payment_reference",
        "order_snapshots",
        ["payment_reference"],
    )
    _create_index_if_missing("ix_order_snapshots_source", "order_snapshots", ["source"])

    if not _has_table("order_snapshot_items"):
        op.create_table(
            "order_snapshot_items",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("order_id", sa.Integer(), nullable=False),
            sa.Column("product_reference", sa.String(), nullable=True),
            sa.Column("backend_product_id", sa.Integer(), nullable=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("qty", sa.Integer(), nullable=False),
            sa.Column("unit_price", sa.Float(), nullable=False),
            sa.Column("image", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["order_id"], ["order_snapshots.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("ix_order_snapshot_items_id", "order_snapshot_items", ["id"])
    _create_index_if_missing(
        "ix_order_snapshot_items_order_id",
        "order_snapshot_items",
        ["order_id"],
    )
    _create_index_if_missing(
        "ix_order_snapshot_items_product_reference",
        "order_snapshot_items",
        ["product_reference"],
    )
    _create_index_if_missing(
        "ix_order_snapshot_items_backend_product_id",
        "order_snapshot_items",
        ["backend_product_id"],
    )


def downgrade() -> None:
    for table_name in ["order_snapshot_items", "order_snapshots"]:
        if _has_table(table_name):
            op.drop_table(table_name)
