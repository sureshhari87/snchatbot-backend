"""Initial schema.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-07-09
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    return _inspector().has_table(table_name)


def _has_column(table_name: str, column_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(column["name"] == column_name for column in _inspector().get_columns(table_name))


def _has_index(table_name: str, index_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(index["name"] == index_name for index in _inspector().get_indexes(table_name))


def _create_index(
    index_name: str, table_name: str, columns: list[str], unique: bool = False
) -> None:
    if _has_table(table_name) and not _has_index(table_name, index_name):
        op.create_index(index_name, table_name, columns, unique=unique)


def upgrade() -> None:
    if not _has_table("users"):
        op.create_table(
            "users",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("username", sa.String(), nullable=False),
            sa.Column("email", sa.String(), nullable=False),
            sa.Column("hashed_password", sa.String(), nullable=False),
            sa.Column("is_verified", sa.Boolean(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    elif not _has_column("users", "is_verified"):
        op.add_column(
            "users",
            sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        )

    _create_index("ix_users_id", "users", ["id"])
    _create_index("ix_users_username", "users", ["username"], unique=True)
    _create_index("ix_users_email", "users", ["email"], unique=True)

    if not _has_table("products"):
        op.create_table(
            "products",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("category", sa.String(), nullable=True),
            sa.Column("metal", sa.String(), nullable=True),
            sa.Column("price", sa.Float(), nullable=False),
            sa.Column("image", sa.String(), nullable=True),
            sa.Column("in_stock", sa.Boolean(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index("ix_products_id", "products", ["id"])
    _create_index("ix_products_name", "products", ["name"])
    _create_index("ix_products_category", "products", ["category"])
    _create_index("ix_products_metal", "products", ["metal"])

    if not _has_table("chat_sessions"):
        op.create_table(
            "chat_sessions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("session_id", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=True,
            ),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index("ix_chat_sessions_id", "chat_sessions", ["id"])
    _create_index("ix_chat_sessions_session_id", "chat_sessions", ["session_id"], unique=True)
    _create_index("ix_chat_sessions_user_id", "chat_sessions", ["user_id"])

    if not _has_table("chat_messages"):
        op.create_table(
            "chat_messages",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("session_id", sa.String(), nullable=True),
            sa.Column("user_id", sa.String(), nullable=True),
            sa.Column("role", sa.String(), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=True,
            ),
            sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.session_id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index("ix_chat_messages_id", "chat_messages", ["id"])
    _create_index("ix_chat_messages_session_id", "chat_messages", ["session_id"])
    _create_index("ix_chat_messages_user_id", "chat_messages", ["user_id"])

    if not _has_table("refresh_tokens"):
        op.create_table(
            "refresh_tokens",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("token", sa.String(), nullable=False),
            sa.Column("is_revoked", sa.Boolean(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=True,
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index("ix_refresh_tokens_id", "refresh_tokens", ["id"])
    _create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    _create_index("ix_refresh_tokens_token", "refresh_tokens", ["token"], unique=True)

    if not _has_table("password_reset_tokens"):
        op.create_table(
            "password_reset_tokens",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("token_hash", sa.String(), nullable=False),
            sa.Column("is_used", sa.Boolean(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index("ix_password_reset_tokens_id", "password_reset_tokens", ["id"])
    _create_index("ix_password_reset_tokens_user_id", "password_reset_tokens", ["user_id"])
    _create_index(
        "ix_password_reset_tokens_token_hash", "password_reset_tokens", ["token_hash"], unique=True
    )

    if not _has_table("email_verification_tokens"):
        op.create_table(
            "email_verification_tokens",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("token_hash", sa.String(), nullable=False),
            sa.Column("is_used", sa.Boolean(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index("ix_email_verification_tokens_id", "email_verification_tokens", ["id"])
    _create_index("ix_email_verification_tokens_user_id", "email_verification_tokens", ["user_id"])
    _create_index(
        "ix_email_verification_tokens_token_hash",
        "email_verification_tokens",
        ["token_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("email_verification_tokens")
    op.drop_table("password_reset_tokens")
    op.drop_table("refresh_tokens")
    op.drop_table("chat_messages")
    op.drop_table("chat_sessions")
    op.drop_table("products")
    op.drop_table("users")
