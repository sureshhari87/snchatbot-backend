"""Add phone OTP authentication tables.

Revision ID: 0011_phone_otp_auth
Revises: 0010_catalog_stock_alerts
Create Date: 2026-08-13
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0011_phone_otp_auth"
down_revision: Union[str, None] = "0010_catalog_stock_alerts"
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
    if not _has_table("phone_auth_identities"):
        op.create_table(
            "phone_auth_identities",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("phone_hash", sa.String(), nullable=False),
            sa.Column("phone_masked", sa.String(), nullable=False),
            sa.Column("provider", sa.String(), nullable=False),
            sa.Column("verified_at", sa.DateTime(), nullable=False),
            sa.Column("last_login_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("ix_phone_auth_identities_id", "phone_auth_identities", ["id"])
    _create_index_if_missing(
        "ix_phone_auth_identities_user_id",
        "phone_auth_identities",
        ["user_id"],
        unique=True,
    )
    _create_index_if_missing(
        "ix_phone_auth_identities_phone_hash",
        "phone_auth_identities",
        ["phone_hash"],
        unique=True,
    )

    if not _has_table("phone_otp_challenges"):
        op.create_table(
            "phone_otp_challenges",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("phone_hash", sa.String(), nullable=False),
            sa.Column("phone_masked", sa.String(), nullable=False),
            sa.Column("otp_hash", sa.String(), nullable=False),
            sa.Column("provider", sa.String(), nullable=False),
            sa.Column("purpose", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column("max_attempts", sa.Integer(), nullable=False),
            sa.Column("created_ip", sa.String(), nullable=True),
            sa.Column("created_user_agent", sa.String(), nullable=True),
            sa.Column("verified_ip", sa.String(), nullable=True),
            sa.Column("verified_user_agent", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("sent_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("verified_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("ix_phone_otp_challenges_id", "phone_otp_challenges", ["id"])
    _create_index_if_missing(
        "ix_phone_otp_challenges_phone_hash",
        "phone_otp_challenges",
        ["phone_hash"],
    )
    _create_index_if_missing(
        "ix_phone_otp_challenges_provider",
        "phone_otp_challenges",
        ["provider"],
    )
    _create_index_if_missing(
        "ix_phone_otp_challenges_purpose",
        "phone_otp_challenges",
        ["purpose"],
    )
    _create_index_if_missing(
        "ix_phone_otp_challenges_status",
        "phone_otp_challenges",
        ["status"],
    )


def downgrade() -> None:
    for table_name in ["phone_otp_challenges", "phone_auth_identities"]:
        if _has_table(table_name):
            op.drop_table(table_name)
