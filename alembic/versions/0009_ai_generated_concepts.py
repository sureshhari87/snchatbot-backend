"""Add AI generated concept records.

Revision ID: 0009_ai_generated_concepts
Revises: 0008_local_order_backend
Create Date: 2026-08-07
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0009_ai_generated_concepts"
down_revision: Union[str, None] = "0008_local_order_backend"
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
    if not _has_table("ai_generated_concepts"):
        op.create_table(
            "ai_generated_concepts",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("concept_id", sa.String(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("source_prompt", sa.Text(), nullable=False),
            sa.Column("design_brief", sa.Text(), nullable=False),
            sa.Column("materials", sa.Text(), nullable=True),
            sa.Column("craft_notes", sa.Text(), nullable=True),
            sa.Column("image_base64", sa.Text(), nullable=True),
            sa.Column("image_mime_type", sa.String(), nullable=True),
            sa.Column("answer_source", sa.String(), nullable=False),
            sa.Column("category", sa.String(), nullable=True),
            sa.Column("metal", sa.String(), nullable=True),
            sa.Column("gemstones", sa.Text(), nullable=True),
            sa.Column("budget", sa.Float(), nullable=True),
            sa.Column("related_product_ids", sa.Text(), nullable=True),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("ix_ai_generated_concepts_id", "ai_generated_concepts", ["id"])
    _create_index_if_missing(
        "ix_ai_generated_concepts_concept_id",
        "ai_generated_concepts",
        ["concept_id"],
        unique=True,
    )
    _create_index_if_missing(
        "ix_ai_generated_concepts_user_id", "ai_generated_concepts", ["user_id"]
    )


def downgrade() -> None:
    if _has_table("ai_generated_concepts"):
        op.drop_table("ai_generated_concepts")
