"""Add user profile integrations, OMS audit, and monitoring tables.

Revision ID: 0007_user_oms_llm_monitoring
Revises: 0006_mobile_ops
Create Date: 2026-07-14
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0007_user_oms_llm_monitoring"
down_revision: Union[str, None] = "0006_mobile_ops"
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
    if not _has_table("user_addresses"):
        op.create_table(
            "user_addresses",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("label", sa.String(), nullable=False),
            sa.Column("full_name", sa.String(), nullable=False),
            sa.Column("phone", sa.String(), nullable=True),
            sa.Column("line1", sa.String(), nullable=False),
            sa.Column("line2", sa.String(), nullable=True),
            sa.Column("city", sa.String(), nullable=False),
            sa.Column("state", sa.String(), nullable=False),
            sa.Column("postal_code", sa.String(), nullable=False),
            sa.Column("country", sa.String(), nullable=False),
            sa.Column("is_default", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("ix_user_addresses_id", "user_addresses", ["id"])
    _create_index_if_missing("ix_user_addresses_user_id", "user_addresses", ["user_id"])

    if not _has_table("notification_settings"):
        op.create_table(
            "notification_settings",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("email_enabled", sa.Boolean(), nullable=False),
            sa.Column("sms_enabled", sa.Boolean(), nullable=False),
            sa.Column("push_enabled", sa.Boolean(), nullable=False),
            sa.Column("marketing_enabled", sa.Boolean(), nullable=False),
            sa.Column("order_updates_enabled", sa.Boolean(), nullable=False),
            sa.Column("chat_updates_enabled", sa.Boolean(), nullable=False),
            sa.Column("appointment_reminders_enabled", sa.Boolean(), nullable=False),
            sa.Column("quiet_hours_start", sa.String(), nullable=True),
            sa.Column("quiet_hours_end", sa.String(), nullable=True),
            sa.Column("push_token", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("ix_notification_settings_id", "notification_settings", ["id"])
    _create_index_if_missing(
        "ix_notification_settings_user_id",
        "notification_settings",
        ["user_id"],
        unique=True,
    )

    if not _has_table("external_integration_events"):
        op.create_table(
            "external_integration_events",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("service", sa.String(), nullable=False),
            sa.Column("action", sa.String(), nullable=False),
            sa.Column("reference", sa.String(), nullable=True),
            sa.Column("request_payload", sa.Text(), nullable=True),
            sa.Column("response_payload", sa.Text(), nullable=True),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("status_code", sa.Integer(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing(
        "ix_external_integration_events_id",
        "external_integration_events",
        ["id"],
    )
    _create_index_if_missing(
        "ix_external_integration_events_user_id",
        "external_integration_events",
        ["user_id"],
    )
    _create_index_if_missing(
        "ix_external_integration_events_service",
        "external_integration_events",
        ["service"],
    )
    _create_index_if_missing(
        "ix_external_integration_events_action",
        "external_integration_events",
        ["action"],
    )
    _create_index_if_missing(
        "ix_external_integration_events_reference",
        "external_integration_events",
        ["reference"],
    )
    _create_index_if_missing(
        "ix_external_integration_events_status",
        "external_integration_events",
        ["status"],
    )


def downgrade() -> None:
    for table_name in [
        "external_integration_events",
        "notification_settings",
        "user_addresses",
    ]:
        if _has_table(table_name):
            op.drop_table(table_name)
