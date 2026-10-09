"""Structured custom-design requests and immutable financial quote history."""

import sqlalchemy as sa

from alembic import op

revision = "0025_custom_design_quotes"
down_revision = "0024_savings_hold_review"
branch_labels = None
depends_on = None
HISTORY = ("custom_design_quotes", "custom_design_decisions", "custom_design_audit")


def upgrade():
    dialect = op.get_bind().dialect.name
    if dialect not in ("postgresql", "sqlite"):
        raise RuntimeError("Custom design history requires PostgreSQL or SQLite")
    op.create_table(
        "custom_designs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "custom_order_id",
            sa.Integer(),
            sa.ForeignKey("custom_order_requests.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("request_key", sa.String(100), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("customer_notes", sa.Text(), nullable=False),
        sa.Column("reference_image_url", sa.String(2048), nullable=True),
        sa.Column("purity", sa.String(40), nullable=False),
        sa.Column("budget_range", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "request_key", name="uq_custom_design_request_key"),
    )
    op.create_index("ix_custom_designs_user_id", "custom_designs", ["user_id"])
    op.create_table(
        "custom_design_quotes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("design_id", sa.Integer(), sa.ForeignKey("custom_designs.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("request_key", sa.String(100), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("total_paise", sa.BigInteger(), nullable=False),
        sa.Column("advance_paise", sa.BigInteger(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("design_id", "version", name="uq_custom_design_quote_version"),
        sa.UniqueConstraint("design_id", "request_key", name="uq_custom_design_quote_key"),
        sa.CheckConstraint("version > 0", name="ck_custom_design_quote_version"),
        sa.CheckConstraint(
            "total_paise > 0 AND advance_paise >= 0 AND advance_paise <= total_paise",
            name="ck_custom_design_quote_money",
        ),
        sa.CheckConstraint(
            "advance_paise = 0 OR advance_paise >= 100", name="ck_custom_design_advance_minimum"
        ),
    )
    op.create_index("ix_custom_design_quotes_design_id", "custom_design_quotes", ["design_id"])
    op.create_table(
        "custom_design_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("design_id", sa.Integer(), sa.ForeignKey("custom_designs.id"), nullable=False),
        sa.Column(
            "quote_id", sa.Integer(), sa.ForeignKey("custom_design_quotes.id"), nullable=False
        ),
        sa.Column("request_key", sa.String(100), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("design_id", "request_key", name="uq_custom_design_decision_key"),
        sa.CheckConstraint("action IN ('approved', 'rejected')", name="ck_custom_design_decision"),
    )
    op.create_index(
        "ix_custom_design_decisions_design_id", "custom_design_decisions", ["design_id"]
    )
    op.create_table(
        "custom_design_audit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("design_id", sa.Integer(), sa.ForeignKey("custom_designs.id"), nullable=False),
        sa.Column("event_key", sa.String(140), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column(
            "quote_id", sa.Integer(), sa.ForeignKey("custom_design_quotes.id"), nullable=True
        ),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("design_id", "event_key", name="uq_custom_design_audit_event"),
    )
    op.create_index("ix_custom_design_audit_design_id", "custom_design_audit", ["design_id"])
    if dialect == "postgresql":
        op.execute("""CREATE FUNCTION sona_custom_design_history_immutable() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'Custom design history is immutable'; END;
            $$ LANGUAGE plpgsql""")
        for table in HISTORY:
            op.execute(
                f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION sona_custom_design_history_immutable()"
            )
    else:
        for table in HISTORY:
            for action in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER {table}_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'Custom design history is immutable'); END"
                )


def downgrade():
    conn = op.get_bind()
    for table in ("custom_designs",) + HISTORY:
        if conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one():
            raise RuntimeError("Custom design history exists; preservation plan required")
    for table in HISTORY:
        if conn.dialect.name == "postgresql":
            op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
        else:
            for action in ("update", "delete"):
                op.execute(f"DROP TRIGGER IF EXISTS {table}_{action}")
    for table in (
        "custom_design_audit",
        "custom_design_decisions",
        "custom_design_quotes",
        "custom_designs",
    ):
        op.drop_table(table)
    if conn.dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS sona_custom_design_history_immutable()")
