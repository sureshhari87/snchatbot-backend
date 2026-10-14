"""Inactive delivery drafts and immutable audit; local, unapplied."""

import sqlalchemy as sa

from alembic import op

revision = "0033_delivery_drafts"
down_revision = "0032_custom_design_balances"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "delivery_drafts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("route", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "delivery_draft_audits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("draft_id", sa.Integer(), sa.ForeignKey("delivery_drafts.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("request_key", sa.String(100), nullable=False, unique=True),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("draft_id", "version", name="uq_delivery_draft_version"),
    )
    immutable_guards()


def immutable_guards():
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE FUNCTION sona_delivery_draft_audit_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Delivery draft audit is immutable'; END; $$"
        )
        op.execute(
            "CREATE TRIGGER delivery_draft_audit_immutable BEFORE UPDATE OR DELETE ON delivery_draft_audits FOR EACH ROW EXECUTE FUNCTION sona_delivery_draft_audit_immutable()"
        )
    elif op.get_bind().dialect.name == "sqlite":
        for action in ["UPDATE", "DELETE"]:
            op.execute(
                f"CREATE TRIGGER delivery_draft_audit_{action.lower()} BEFORE {action} ON delivery_draft_audits BEGIN SELECT RAISE(ABORT,'Delivery draft audit is immutable'); END"
            )


def downgrade():
    for table in ["delivery_draft_audits", "delivery_drafts"]:
        if op.get_bind().execute(sa.select(sa.func.count()).select_from(sa.table(table))).scalar():
            raise RuntimeError("Draft history exists; downgrade would discard it")
    op.drop_table("delivery_draft_audits")
    op.drop_table("delivery_drafts")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION sona_delivery_draft_audit_immutable()")
