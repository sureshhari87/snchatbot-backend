"""Add Firebase commerce identity fields.

Revision ID: 0012_firebase_commerce_identity
Revises: 0011_phone_otp_auth
Create Date: 2026-09-07
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0012_firebase_commerce_identity"
down_revision: Union[str, None] = "0011_phone_otp_auth"
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
    _add_column_if_missing("users", sa.Column("firebase_uid", sa.String(), nullable=True))
    _add_column_if_missing("users", sa.Column("auth_provider", sa.String(), nullable=True))
    _create_index_if_missing("ix_users_firebase_uid", "users", ["firebase_uid"], unique=True)


def downgrade() -> None:
    if _has_table("users"):
        indexes = _indexes("users")
        if "ix_users_firebase_uid" in indexes:
            op.drop_index("ix_users_firebase_uid", table_name="users")

        existing_columns = _columns("users")
        if "auth_provider" in existing_columns:
            op.drop_column("users", "auth_provider")
        if "firebase_uid" in existing_columns:
            op.drop_column("users", "firebase_uid")
