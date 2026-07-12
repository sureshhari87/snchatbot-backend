"""Add chat response feedback and analytics tables.

Revision ID: 0005_chat_feedback_analytics
Revises: 0004_catalogue_customer_flows
Create Date: 2026-07-12
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0005_chat_feedback_analytics"
down_revision: Union[str, None] = "0004_catalogue_customer_flows"
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
    if not _has_table("chat_response_analytics"):
        op.create_table(
            "chat_response_analytics",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("response_id", sa.String(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("session_id", sa.String(), nullable=False),
            sa.Column("user_message", sa.Text(), nullable=False),
            sa.Column("assistant_reply", sa.Text(), nullable=False),
            sa.Column("intent", sa.String(), nullable=False),
            sa.Column("applied_filters", sa.Text(), nullable=True),
            sa.Column("product_ids", sa.Text(), nullable=True),
            sa.Column("product_names", sa.Text(), nullable=True),
            sa.Column("result_count", sa.Integer(), nullable=False),
            sa.Column("unmatched", sa.Boolean(), nullable=False),
            sa.Column("low_conversion", sa.Boolean(), nullable=False),
            sa.Column("lead_captured", sa.Boolean(), nullable=False),
            sa.Column("reviewed_at", sa.DateTime(), nullable=True),
            sa.Column("review_notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing(
        "ix_chat_response_analytics_response_id",
        "chat_response_analytics",
        ["response_id"],
        unique=True,
    )
    _create_index_if_missing("ix_chat_response_analytics_id", "chat_response_analytics", ["id"])
    _create_index_if_missing(
        "ix_chat_response_analytics_user_id", "chat_response_analytics", ["user_id"]
    )
    _create_index_if_missing(
        "ix_chat_response_analytics_session_id", "chat_response_analytics", ["session_id"]
    )
    _create_index_if_missing(
        "ix_chat_response_analytics_intent", "chat_response_analytics", ["intent"]
    )
    _create_index_if_missing(
        "ix_chat_response_analytics_unmatched", "chat_response_analytics", ["unmatched"]
    )
    _create_index_if_missing(
        "ix_chat_response_analytics_low_conversion",
        "chat_response_analytics",
        ["low_conversion"],
    )

    if not _has_table("response_feedback"):
        op.create_table(
            "response_feedback",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("response_id", sa.String(), nullable=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("session_id", sa.String(), nullable=True),
            sa.Column("feedback_type", sa.String(), nullable=False),
            sa.Column("helpful", sa.Boolean(), nullable=True),
            sa.Column("rating", sa.Integer(), nullable=True),
            sa.Column("context", sa.String(), nullable=True),
            sa.Column("comment", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("ix_response_feedback_id", "response_feedback", ["id"])
    _create_index_if_missing("ix_response_feedback_response_id", "response_feedback", ["response_id"])
    _create_index_if_missing("ix_response_feedback_user_id", "response_feedback", ["user_id"])
    _create_index_if_missing("ix_response_feedback_session_id", "response_feedback", ["session_id"])
    _create_index_if_missing(
        "ix_response_feedback_feedback_type", "response_feedback", ["feedback_type"]
    )


def downgrade() -> None:
    for table_name in ["response_feedback", "chat_response_analytics"]:
        if _has_table(table_name):
            op.drop_table(table_name)
