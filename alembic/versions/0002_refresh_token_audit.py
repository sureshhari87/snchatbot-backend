"""Add refresh token rotation audit fields.

Revision ID: 0002_refresh_token_audit
Revises: 0001_initial_schema
Create Date: 2026-07-12
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0002_refresh_token_audit"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector():
    return sa.inspect(op.get_bind())


def _columns(table_name: str) -> set[str]:
    return {column["name"] for column in _inspector().get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    return {index["name"] for index in _inspector().get_indexes(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if column.name not in _columns(table_name):
        op.add_column(table_name, column)


def _create_index_if_missing(
    index_name: str, table_name: str, columns: list[str], unique: bool = False
) -> None:
    if index_name not in _indexes(table_name):
        op.create_index(index_name, table_name, columns, unique=unique)


def upgrade() -> None:
    _add_column_if_missing("refresh_tokens", sa.Column("token_jti", sa.String(), nullable=True))
    _add_column_if_missing("refresh_tokens", sa.Column("family_id", sa.String(), nullable=True))
    _add_column_if_missing(
        "refresh_tokens", sa.Column("parent_token_id", sa.Integer(), nullable=True)
    )
    _add_column_if_missing(
        "refresh_tokens", sa.Column("replaced_by_token_id", sa.Integer(), nullable=True)
    )
    _add_column_if_missing("refresh_tokens", sa.Column("revoked_at", sa.DateTime(), nullable=True))
    _add_column_if_missing(
        "refresh_tokens", sa.Column("revoked_reason", sa.String(), nullable=True)
    )
    _add_column_if_missing("refresh_tokens", sa.Column("created_ip", sa.String(), nullable=True))
    _add_column_if_missing(
        "refresh_tokens", sa.Column("created_user_agent", sa.String(), nullable=True)
    )
    _add_column_if_missing(
        "refresh_tokens", sa.Column("last_used_at", sa.DateTime(), nullable=True)
    )
    _add_column_if_missing("refresh_tokens", sa.Column("last_used_ip", sa.String(), nullable=True))
    _add_column_if_missing(
        "refresh_tokens", sa.Column("last_used_user_agent", sa.String(), nullable=True)
    )

    _create_index_if_missing(
        "ix_refresh_tokens_token_jti", "refresh_tokens", ["token_jti"], unique=True
    )
    _create_index_if_missing("ix_refresh_tokens_family_id", "refresh_tokens", ["family_id"])


def downgrade() -> None:
    for index_name in [
        "ix_refresh_tokens_family_id",
        "ix_refresh_tokens_token_jti",
    ]:
        if index_name in _indexes("refresh_tokens"):
            op.drop_index(index_name, table_name="refresh_tokens")

    for column_name in [
        "last_used_user_agent",
        "last_used_ip",
        "last_used_at",
        "created_user_agent",
        "created_ip",
        "revoked_reason",
        "revoked_at",
        "replaced_by_token_id",
        "parent_token_id",
        "family_id",
        "token_jti",
    ]:
        if column_name in _columns("refresh_tokens"):
            op.drop_column("refresh_tokens", column_name)
