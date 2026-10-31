"""Separate customer contact details from authentication identifiers."""

import sqlalchemy as sa

from alembic import op

revision = "0041_customer_profiles"
down_revision = "0040_purchase_identity"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "customer_profiles",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("contact_email", sa.String(254), nullable=True, unique=True),
        sa.Column("email_verified_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "customer_contact_challenges",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("request_key", sa.String(80), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "request_key", name="uq_contact_challenge_request"),
    )
    op.create_index(
        "ix_customer_contact_challenges_user_id", "customer_contact_challenges", ["user_id"]
    )
    op.create_index(
        "ix_customer_contact_challenges_email", "customer_contact_challenges", ["email"]
    )


def downgrade():
    raise RuntimeError("Customer profile removal needs an approved retention/backup plan")
