"""Add catalogue admin and customer intent flow tables.

Revision ID: 0004_catalogue_customer_flows
Revises: 0003_chat_session_state
Create Date: 2026-07-12
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0004_catalogue_customer_flows"
down_revision: Union[str, None] = "0003_chat_session_state"
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
    if column.name not in _columns(table_name):
        op.add_column(table_name, column)


def _create_index_if_missing(
    index_name: str, table_name: str, columns: list[str], unique: bool = False
) -> None:
    if _has_table(table_name) and index_name not in _indexes(table_name):
        op.create_index(index_name, table_name, columns, unique=unique)


def upgrade() -> None:
    _add_column_if_missing(
        "users", sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    _add_column_if_missing("products", sa.Column("description", sa.Text(), nullable=True))
    _add_column_if_missing("products", sa.Column("sku", sa.String(), nullable=True))
    _add_column_if_missing(
        "products", sa.Column("stock_quantity", sa.Integer(), nullable=False, server_default="0")
    )
    _add_column_if_missing(
        "products",
        sa.Column("is_featured", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    _create_index_if_missing("ix_products_sku", "products", ["sku"], unique=True)

    if not _has_table("product_categories"):
        op.create_table(
            "product_categories",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("slug", sa.String(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("ix_product_categories_id", "product_categories", ["id"])
    _create_index_if_missing(
        "ix_product_categories_name", "product_categories", ["name"], unique=True
    )
    _create_index_if_missing(
        "ix_product_categories_slug", "product_categories", ["slug"], unique=True
    )

    if not _has_table("seasonal_collections"):
        op.create_table(
            "seasonal_collections",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("slug", sa.String(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("season", sa.String(), nullable=True),
            sa.Column("starts_at", sa.DateTime(), nullable=True),
            sa.Column("ends_at", sa.DateTime(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("ix_seasonal_collections_id", "seasonal_collections", ["id"])
    _create_index_if_missing(
        "ix_seasonal_collections_name", "seasonal_collections", ["name"], unique=True
    )
    _create_index_if_missing(
        "ix_seasonal_collections_slug", "seasonal_collections", ["slug"], unique=True
    )

    if not _has_table("featured_items"):
        op.create_table(
            "featured_items",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("product_id", sa.Integer(), nullable=False),
            sa.Column("title", sa.String(), nullable=True),
            sa.Column("subtitle", sa.String(), nullable=True),
            sa.Column("display_order", sa.Integer(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("ix_featured_items_id", "featured_items", ["id"])
    _create_index_if_missing("ix_featured_items_product_id", "featured_items", ["product_id"])

    if not _has_table("wishlist_items"):
        op.create_table(
            "wishlist_items",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("product_id", sa.Integer(), nullable=False),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("ix_wishlist_items_id", "wishlist_items", ["id"])
    _create_index_if_missing("ix_wishlist_items_user_id", "wishlist_items", ["user_id"])
    _create_index_if_missing("ix_wishlist_items_product_id", "wishlist_items", ["product_id"])

    if not _has_table("save_for_later_items"):
        op.create_table(
            "save_for_later_items",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("product_id", sa.Integer(), nullable=False),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("ix_save_for_later_items_id", "save_for_later_items", ["id"])
    _create_index_if_missing("ix_save_for_later_items_user_id", "save_for_later_items", ["user_id"])
    _create_index_if_missing(
        "ix_save_for_later_items_product_id", "save_for_later_items", ["product_id"]
    )

    if not _has_table("callback_requests"):
        op.create_table(
            "callback_requests",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(), nullable=True),
            sa.Column("phone", sa.String(), nullable=True),
            sa.Column("email", sa.String(), nullable=True),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("preferred_time", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("ix_callback_requests_id", "callback_requests", ["id"])
    _create_index_if_missing("ix_callback_requests_user_id", "callback_requests", ["user_id"])

    if not _has_table("appointment_bookings"):
        op.create_table(
            "appointment_bookings",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(), nullable=True),
            sa.Column("phone", sa.String(), nullable=True),
            sa.Column("email", sa.String(), nullable=True),
            sa.Column("store_location", sa.String(), nullable=False),
            sa.Column("appointment_time", sa.DateTime(), nullable=False),
            sa.Column("purpose", sa.Text(), nullable=True),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("ix_appointment_bookings_id", "appointment_bookings", ["id"])
    _create_index_if_missing("ix_appointment_bookings_user_id", "appointment_bookings", ["user_id"])

    if not _has_table("lead_captures"):
        op.create_table(
            "lead_captures",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("session_id", sa.String(), nullable=True),
            sa.Column("source", sa.String(), nullable=False),
            sa.Column("intent", sa.String(), nullable=False),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("contact_name", sa.String(), nullable=True),
            sa.Column("contact_phone", sa.String(), nullable=True),
            sa.Column("contact_email", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("ix_lead_captures_id", "lead_captures", ["id"])
    _create_index_if_missing("ix_lead_captures_user_id", "lead_captures", ["user_id"])
    _create_index_if_missing("ix_lead_captures_session_id", "lead_captures", ["session_id"])


def downgrade() -> None:
    for table_name in [
        "lead_captures",
        "appointment_bookings",
        "callback_requests",
        "save_for_later_items",
        "wishlist_items",
        "featured_items",
        "seasonal_collections",
        "product_categories",
    ]:
        if _has_table(table_name):
            op.drop_table(table_name)

    if "ix_products_sku" in _indexes("products"):
        op.drop_index("ix_products_sku", table_name="products")

    for table_name, column_names in {
        "products": ["is_featured", "stock_quantity", "sku", "description"],
        "users": ["is_admin"],
    }.items():
        existing_columns = _columns(table_name)
        for column_name in column_names:
            if column_name in existing_columns:
                op.drop_column(table_name, column_name)
