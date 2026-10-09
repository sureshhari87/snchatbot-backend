"""Durable custom advance checkout, immutable credits and review holds."""

import sqlalchemy as sa

from alembic import op

revision = "0026_custom_design_advances"
down_revision = "0025_custom_design_quotes"
branch_labels = None
depends_on = None
HISTORY = ("custom_design_advances", "custom_design_holds")


def upgrade():
    dialect = op.get_bind().dialect.name
    if dialect not in ("postgresql", "sqlite"):
        raise RuntimeError("Custom advance history requires PostgreSQL or SQLite")
    op.create_table(
        "custom_design_checkouts",
        sa.Column(
            "quote_id", sa.Integer(), sa.ForeignKey("custom_design_quotes.id"), primary_key=True
        ),
        sa.Column(
            "design_id",
            sa.Integer(),
            sa.ForeignKey("custom_designs.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("receipt", sa.String(40), nullable=False, unique=True),
        sa.Column("provider_order_id", sa.String(100), nullable=True, unique=True),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "state IN ('creating','unknown','ready')", name="ck_custom_design_checkout_state"
        ),
    )
    op.create_table(
        "custom_design_advances",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "design_id",
            sa.Integer(),
            sa.ForeignKey("custom_designs.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "quote_id",
            sa.Integer(),
            sa.ForeignKey("custom_design_quotes.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("provider_order_id", sa.String(100), nullable=False, unique=True),
        sa.Column("provider_payment_id", sa.String(100), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("amount_paise >= 100", name="ck_custom_design_advance_amount"),
    )
    op.create_table(
        "custom_design_holds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("design_id", sa.Integer(), sa.ForeignKey("custom_designs.id"), nullable=False),
        sa.Column("reference", sa.String(100), nullable=False),
        sa.Column("reason", sa.String(200), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("design_id", "reference", name="uq_custom_design_hold_reference"),
    )
    op.create_index("ix_custom_design_holds_design_id", "custom_design_holds", ["design_id"])
    if dialect == "postgresql":
        op.execute("""CREATE FUNCTION sona_custom_design_advance_immutable() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'Custom advance history is immutable'; END;
            $$ LANGUAGE plpgsql""")
        for table in HISTORY:
            op.execute(
                f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION sona_custom_design_advance_immutable()"
            )
    else:
        for table in HISTORY:
            for action in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER {table}_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'Custom advance history is immutable'); END"
                )


def downgrade():
    conn = op.get_bind()
    for table in ("custom_design_checkouts",) + HISTORY:
        if conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one():
            raise RuntimeError("Custom advance history exists; preservation plan required")
    for table in HISTORY:
        if conn.dialect.name == "postgresql":
            op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
        else:
            for action in ("update", "delete"):
                op.execute(f"DROP TRIGGER IF EXISTS {table}_{action}")
    for table in ("custom_design_holds", "custom_design_advances", "custom_design_checkouts"):
        op.drop_table(table)
    if conn.dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS sona_custom_design_advance_immutable()")
