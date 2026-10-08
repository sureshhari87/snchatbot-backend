"""Audited closure of unconfirmed requests; no backfill or financial reversal."""

import sqlalchemy as sa

from alembic import op

revision = "0022_savings_resolution"
down_revision = "0021_savings_checkout"
branch_labels = None
depends_on = None


def payment_states(value):
    with op.batch_alter_table("savings_payments") as batch:
        batch.drop_constraint("ck_savings_payment_state", type_="check")
        batch.create_check_constraint("ck_savings_payment_state", value)


def upgrade():
    payment_states("state IN ('pending', 'posted', 'cancelled', 'rejected')")
    op.create_table(
        "savings_payment_resolutions",
        sa.Column(
            "payment_id", sa.Integer(), sa.ForeignKey("savings_payments.id"), primary_key=True
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(100), nullable=False),
        sa.Column("original_installment", sa.Integer(), nullable=True),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("kind IN ('cancelled', 'rejected')", name="ck_savings_resolution_kind"),
        sa.CheckConstraint(
            "original_installment IS NULL OR (original_installment >= 1 AND original_installment <= 11)",
            name="ck_savings_resolution_installment",
        ),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""CREATE FUNCTION sona_savings_resolution_immutable() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'Savings resolution history is immutable'; END;
        $$ LANGUAGE plpgsql""")
        op.execute(
            "CREATE TRIGGER savings_resolution_immutable BEFORE UPDATE OR DELETE ON savings_payment_resolutions FOR EACH ROW EXECUTE FUNCTION sona_savings_resolution_immutable()"
        )
    elif op.get_bind().dialect.name == "sqlite":
        for action in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER savings_resolution_{action.lower()} BEFORE {action} ON savings_payment_resolutions BEGIN SELECT RAISE(ABORT, 'Savings resolution history is immutable'); END"
            )
    else:
        raise RuntimeError("Savings resolution requires PostgreSQL or SQLite")


def downgrade():
    conn = op.get_bind()
    if (
        conn.execute(sa.text("SELECT count(*) FROM savings_payment_resolutions")).scalar_one()
        or conn.execute(
            sa.text(
                "SELECT count(*) FROM savings_payments WHERE state IN ('cancelled', 'rejected')"
            )
        ).scalar_one()
    ):
        raise RuntimeError("Resolution history exists; reviewed preservation plan required")
    if conn.dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS savings_resolution_immutable ON savings_payment_resolutions"
        )
        op.execute("DROP FUNCTION IF EXISTS sona_savings_resolution_immutable()")
    elif conn.dialect.name == "sqlite":
        for action in ("update", "delete"):
            op.execute(f"DROP TRIGGER IF EXISTS savings_resolution_{action}")
    op.drop_table("savings_payment_resolutions")
    payment_states("state IN ('pending', 'posted')")
