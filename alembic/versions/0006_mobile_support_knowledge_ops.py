"""Add mobile support, knowledge base, and order support tables.

Revision ID: 0006_mobile_ops
Revises: 0005_chat_feedback_analytics
Create Date: 2026-07-14
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0006_mobile_ops"
down_revision: Union[str, None] = "0005_chat_feedback_analytics"
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
    if not _has_table("knowledge_base_items"):
        op.create_table(
            "knowledge_base_items",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("kind", sa.String(), nullable=False),
            sa.Column("slug", sa.String(), nullable=False),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("tags", sa.Text(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("ix_knowledge_base_items_id", "knowledge_base_items", ["id"])
    _create_index_if_missing("ix_knowledge_base_items_kind", "knowledge_base_items", ["kind"])
    _create_index_if_missing(
        "ix_knowledge_base_items_slug", "knowledge_base_items", ["slug"], unique=True
    )

    if not _has_table("app_config_entries"):
        op.create_table(
            "app_config_entries",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("key", sa.String(), nullable=False),
            sa.Column("value", sa.Text(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("is_public", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("ix_app_config_entries_id", "app_config_entries", ["id"])
    _create_index_if_missing(
        "ix_app_config_entries_key", "app_config_entries", ["key"], unique=True
    )

    if not _has_table("custom_order_requests"):
        op.create_table(
            "custom_order_requests",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("product_id", sa.Integer(), nullable=True),
            sa.Column("session_id", sa.String(), nullable=True),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("budget", sa.Float(), nullable=True),
            sa.Column("metal", sa.String(), nullable=True),
            sa.Column("category", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("ix_custom_order_requests_id", "custom_order_requests", ["id"])
    _create_index_if_missing(
        "ix_custom_order_requests_user_id", "custom_order_requests", ["user_id"]
    )
    _create_index_if_missing(
        "ix_custom_order_requests_product_id", "custom_order_requests", ["product_id"]
    )
    _create_index_if_missing(
        "ix_custom_order_requests_session_id", "custom_order_requests", ["session_id"]
    )

    if not _has_table("complaint_tickets"):
        op.create_table(
            "complaint_tickets",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("order_reference", sa.String(), nullable=True),
            sa.Column("category", sa.String(), nullable=False),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("priority", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("ix_complaint_tickets_id", "complaint_tickets", ["id"])
    _create_index_if_missing("ix_complaint_tickets_user_id", "complaint_tickets", ["user_id"])
    _create_index_if_missing(
        "ix_complaint_tickets_order_reference", "complaint_tickets", ["order_reference"]
    )

    if not _has_table("order_support_requests"):
        op.create_table(
            "order_support_requests",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("order_reference", sa.String(), nullable=True),
            sa.Column("request_type", sa.String(), nullable=False),
            sa.Column("message", sa.Text(), nullable=True),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("ix_order_support_requests_id", "order_support_requests", ["id"])
    _create_index_if_missing(
        "ix_order_support_requests_user_id", "order_support_requests", ["user_id"]
    )
    _create_index_if_missing(
        "ix_order_support_requests_order_reference",
        "order_support_requests",
        ["order_reference"],
    )
    _create_index_if_missing(
        "ix_order_support_requests_request_type", "order_support_requests", ["request_type"]
    )


def downgrade() -> None:
    for table_name in [
        "order_support_requests",
        "complaint_tickets",
        "custom_order_requests",
        "app_config_entries",
        "knowledge_base_items",
    ]:
        if _has_table(table_name):
            op.drop_table(table_name)
