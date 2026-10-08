"""Savings ledger foundation; no backfill and no API cutover."""

import sqlalchemy as sa

from alembic import op

revision = "0020_savings_ledger"
down_revision = "0019_push_outbox"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "savings_schemes",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("request_key", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("monthly_paise", sa.BigInteger(), nullable=True),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("matures_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("kind IN ('monthly', 'digigold')", name="ck_savings_kind"),
        sa.CheckConstraint(
            "(kind = 'monthly' AND monthly_paise IS NOT NULL AND monthly_paise >= 50000) OR (kind = 'digigold' AND monthly_paise IS NULL)",
            name="ck_savings_monthly_amount",
        ),
        sa.CheckConstraint("state IN ('active', 'redeemed')", name="ck_savings_state"),
        sa.UniqueConstraint("user_id", "request_key", name="uq_savings_enrollment_key"),
    )
    op.create_index("ix_savings_schemes_user_id", "savings_schemes", ["user_id"])
    op.create_table(
        "savings_payments",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("scheme_id", sa.Integer(), sa.ForeignKey("savings_schemes.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("request_key", sa.String(length=100), nullable=False),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("installment", sa.Integer(), nullable=True),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("provider_order_id", sa.String(length=100), nullable=True),
        sa.Column("verified_reference", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("posted_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("provider_order_id"),
        sa.CheckConstraint(
            "installment IS NULL OR (installment >= 1 AND installment <= 11)",
            name="ck_savings_installment",
        ),
        sa.CheckConstraint("amount_paise > 0", name="ck_savings_payment_amount"),
        sa.CheckConstraint("mode IN ('manual', 'razorpay')", name="ck_savings_payment_mode"),
        sa.CheckConstraint("state IN ('pending', 'posted')", name="ck_savings_payment_state"),
        sa.UniqueConstraint("scheme_id", "installment", name="uq_savings_installment"),
        sa.UniqueConstraint("user_id", "request_key", name="uq_savings_payment_key"),
        sa.UniqueConstraint("mode", "verified_reference", name="uq_savings_verified_reference"),
    )
    op.create_index("ix_savings_payments_scheme_id", "savings_payments", ["scheme_id"])
    op.create_table(
        "savings_gold_rates",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("paise_per_gram", sa.BigInteger(), nullable=False),
        sa.Column("purity", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=200), nullable=False),
        sa.Column("effective_at", sa.DateTime(), nullable=False),
        sa.Column("valid_until", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("paise_per_gram > 0", name="ck_savings_rate_amount"),
        sa.CheckConstraint("valid_until > effective_at", name="ck_savings_rate_window"),
    )
    op.create_table(
        "savings_entries",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("scheme_id", sa.Integer(), sa.ForeignKey("savings_schemes.id"), nullable=False),
        sa.Column("event_key", sa.String(length=100), nullable=False),
        sa.Column("payment_id", sa.Integer(), sa.ForeignKey("savings_payments.id"), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("principal_paise", sa.BigInteger(), nullable=False),
        sa.Column("gold_micrograms", sa.BigInteger(), nullable=False),
        sa.Column("bonus_micrograms", sa.BigInteger(), nullable=False),
        sa.Column("store_bonus_paise", sa.BigInteger(), nullable=False),
        sa.Column("rate_id", sa.Integer(), sa.ForeignKey("savings_gold_rates.id"), nullable=True),
        sa.Column("bonus_bps", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("evidence_reference", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("payment_id"),
        sa.UniqueConstraint("event_key"),
        sa.CheckConstraint("bonus_bps >= 0 AND bonus_bps <= 500", name="ck_savings_bonus_bps"),
        sa.CheckConstraint("kind IN ('credit', 'redemption')", name="ck_savings_entry_kind"),
        sa.CheckConstraint(
            "(kind = 'credit' AND payment_id IS NOT NULL AND principal_paise > 0 AND gold_micrograms >= 0 AND bonus_micrograms >= 0 AND store_bonus_paise = 0) OR (kind = 'redemption' AND payment_id IS NULL AND principal_paise <= 0 AND gold_micrograms <= 0 AND bonus_micrograms <= 0 AND store_bonus_paise >= 0)",
            name="ck_savings_entry_signs",
        ),
    )
    op.create_index("ix_savings_entries_scheme_id", "savings_entries", ["scheme_id"])
    op.create_table(
        "savings_audit",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("scheme_id", sa.Integer(), sa.ForeignKey("savings_schemes.id"), nullable=False),
        sa.Column("event_key", sa.String(length=100), nullable=False),
        sa.Column("action", sa.String(length=30), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("evidence_reference", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("event_key"),
    )
    op.create_index("ix_savings_audit_scheme_id", "savings_audit", ["scheme_id"])

    install_immutable_tables()


def downgrade():
    tables = (
        "savings_audit",
        "savings_entries",
        "savings_gold_rates",
        "savings_payments",
        "savings_schemes",
    )
    for table in tables:
        if op.get_bind().execute(sa.text("SELECT count(*) FROM " + table)).scalar_one():
            raise RuntimeError("Savings records exist; reviewed preservation plan required")
    remove_immutable_tables()
    op.drop_index("ix_savings_audit_scheme_id", table_name="savings_audit")
    op.drop_table("savings_audit")
    op.drop_index("ix_savings_entries_scheme_id", table_name="savings_entries")
    op.drop_table("savings_entries")
    op.drop_table("savings_gold_rates")
    op.drop_index("ix_savings_payments_scheme_id", table_name="savings_payments")
    op.drop_table("savings_payments")
    op.drop_index("ix_savings_schemes_user_id", table_name="savings_schemes")
    op.drop_table("savings_schemes")


IMMUTABLE_TABLES = ("savings_entries", "savings_audit", "savings_gold_rates")


def install_immutable_tables():
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("""
            CREATE FUNCTION sona_savings_immutable() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'Savings ledger, audit and rate snapshots are immutable';
            END;
            $$ LANGUAGE plpgsql
        """)
        for table in IMMUTABLE_TABLES:
            op.execute(
                f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION sona_savings_immutable()"
            )
    elif dialect == "sqlite":
        for table in IMMUTABLE_TABLES:
            for action in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER {table}_immutable_{action.lower()} BEFORE {action} ON {table} "
                    "BEGIN SELECT RAISE(ABORT, 'Savings records are immutable'); END"
                )
    else:
        raise RuntimeError("Savings immutability requires PostgreSQL or SQLite")


def remove_immutable_tables():
    dialect = op.get_bind().dialect.name
    for table in IMMUTABLE_TABLES:
        if dialect == "postgresql":
            op.execute(f"DROP TRIGGER {table}_immutable ON {table}")
        elif dialect == "sqlite":
            for action in ("update", "delete"):
                op.execute(f"DROP TRIGGER {table}_immutable_{action}")
    if dialect == "postgresql":
        op.execute("DROP FUNCTION sona_savings_immutable()")
