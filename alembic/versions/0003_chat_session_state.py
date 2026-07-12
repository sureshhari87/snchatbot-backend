"""Add chat session filter and preference state.

Revision ID: 0003_chat_session_state
Revises: 0002_refresh_token_audit
Create Date: 2026-07-12
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0003_chat_session_state"
down_revision: Union[str, None] = "0002_refresh_token_audit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector():
    return sa.inspect(op.get_bind())


def _columns(table_name: str) -> set[str]:
    return {column["name"] for column in _inspector().get_columns(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if column.name not in _columns(table_name):
        op.add_column(table_name, column)


def upgrade() -> None:
    _add_column_if_missing("chat_sessions", sa.Column("last_filters", sa.Text(), nullable=True))
    _add_column_if_missing("chat_sessions", sa.Column("preferences", sa.Text(), nullable=True))


def downgrade() -> None:
    existing_columns = _columns("chat_sessions")
    for column_name in ["preferences", "last_filters"]:
        if column_name in existing_columns:
            op.drop_column("chat_sessions", column_name)
