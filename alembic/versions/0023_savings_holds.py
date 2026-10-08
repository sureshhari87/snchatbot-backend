"""Immutable review holds; no ledger reversal and no automatic release."""

import sqlalchemy as sa

from alembic import op

revision = "0023_savings_holds"
down_revision = "0022_savings_resolution"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "savings_holds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scheme_id", sa.Integer(), sa.ForeignKey("savings_schemes.id"), nullable=False),
        sa.Column("payment_id", sa.Integer(), sa.ForeignKey("savings_payments.id"), nullable=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("reference", sa.String(100), nullable=False),
        sa.Column("reason", sa.String(100), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("scheme_id", "kind", "reference", name="uq_savings_hold_reference"),
        sa.CheckConstraint(
            "kind IN ('refund', 'dispute', 'admin_review')", name="ck_savings_hold_kind"
        ),
    )
    op.create_index("ix_savings_holds_scheme_id", "savings_holds", ["scheme_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""CREATE FUNCTION sona_savings_hold_immutable() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'Savings hold history is immutable'; END;
        $$ LANGUAGE plpgsql""")
        op.execute(
            "CREATE TRIGGER savings_hold_immutable BEFORE UPDATE OR DELETE ON savings_holds FOR EACH ROW EXECUTE FUNCTION sona_savings_hold_immutable()"
        )
    elif op.get_bind().dialect.name == "sqlite":
        for action in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER savings_hold_{action.lower()} BEFORE {action} ON savings_holds BEGIN SELECT RAISE(ABORT, 'Savings hold history is immutable'); END"
            )
    else:
        raise RuntimeError("Savings holds require PostgreSQL or SQLite")


def downgrade():
    conn = op.get_bind()
    if conn.execute(sa.text("SELECT count(*) FROM savings_holds")).scalar_one():
        raise RuntimeError("Review history exists; reviewed preservation plan required")
    if conn.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS savings_hold_immutable ON savings_holds")
        op.execute("DROP FUNCTION IF EXISTS sona_savings_hold_immutable()")
    elif conn.dialect.name == "sqlite":
        for action in ("update", "delete"):
            op.execute(f"DROP TRIGGER IF EXISTS savings_hold_{action}")
    op.drop_index("ix_savings_holds_scheme_id", table_name="savings_holds")
    op.drop_table("savings_holds")
