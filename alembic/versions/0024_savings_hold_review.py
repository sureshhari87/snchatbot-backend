"""Audited hold decisions; preserve original holds and financial entries."""

import sqlalchemy as sa

from alembic import op

revision = "0024_savings_hold_review"
down_revision = "0023_savings_holds"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "savings_hold_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("hold_id", sa.Integer(), sa.ForeignKey("savings_holds.id"), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("event_key", sa.String(100), nullable=False, unique=True),
        sa.Column("reason", sa.String(100), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "action IN ('released', 'reopened')", name="ck_savings_hold_decision_action"
        ),
        sa.CheckConstraint(
            "action != 'released' OR actor_id IS NOT NULL", name="ck_savings_release_actor"
        ),
    )
    op.create_index("ix_savings_hold_decisions_hold_id", "savings_hold_decisions", ["hold_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""CREATE FUNCTION sona_savings_hold_decision_immutable() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'Savings hold decision history is immutable'; END;
        $$ LANGUAGE plpgsql""")
        op.execute(
            "CREATE TRIGGER savings_hold_decision_immutable BEFORE UPDATE OR DELETE ON savings_hold_decisions FOR EACH ROW EXECUTE FUNCTION sona_savings_hold_decision_immutable()"
        )
    elif op.get_bind().dialect.name == "sqlite":
        for action in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER savings_hold_decision_{action.lower()} BEFORE {action} ON savings_hold_decisions BEGIN SELECT RAISE(ABORT, 'Savings hold decision history is immutable'); END"
            )
    else:
        raise RuntimeError("Savings reviews require PostgreSQL or SQLite")


def downgrade():
    conn = op.get_bind()
    if conn.execute(sa.text("SELECT count(*) FROM savings_hold_decisions")).scalar_one():
        raise RuntimeError("Review decision history exists; preservation plan required")
    if conn.dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS savings_hold_decision_immutable ON savings_hold_decisions"
        )
        op.execute("DROP FUNCTION IF EXISTS sona_savings_hold_decision_immutable()")
    elif conn.dialect.name == "sqlite":
        for action in ("update", "delete"):
            op.execute(f"DROP TRIGGER IF EXISTS savings_hold_decision_{action}")
    op.drop_index("ix_savings_hold_decisions_hold_id", table_name="savings_hold_decisions")
    op.drop_table("savings_hold_decisions")
