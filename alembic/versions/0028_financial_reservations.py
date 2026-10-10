"""Durable financial reservations and immutable audit; rollout not yet applied."""

import sqlalchemy as sa

from alembic import op

revision = "0028_financial_reservations"
down_revision = "0027_rewards_vouchers_ledger"
branch_labels = None
depends_on = None
TERMS = (
    "id",
    "user_id",
    "request_key",
    "request_sha256",
    "receipt",
    "quote_json",
    "payable_paise",
    "reward_points",
    "voucher_id",
    "voucher_paise",
    "created_at",
)


def upgrade():
    dialect = op.get_bind().dialect.name
    if dialect not in ("postgresql", "sqlite"):
        raise RuntimeError("Financial reservations require PostgreSQL or SQLite")
    op.create_table(
        "financial_reservations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("request_key", sa.String(100), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("receipt", sa.String(40), nullable=False, unique=True),
        sa.Column("quote_json", sa.Text(), nullable=False),
        sa.Column("payable_paise", sa.BigInteger(), nullable=False),
        sa.Column("reward_points", sa.BigInteger(), nullable=False),
        sa.Column("voucher_id", sa.Integer(), sa.ForeignKey("gift_vouchers.id"), nullable=True),
        sa.Column("voucher_paise", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column(
            "order_id",
            sa.Integer(),
            sa.ForeignKey("order_snapshots.id"),
            nullable=True,
            unique=True,
        ),
        sa.Column("provider_order_id", sa.String(100), nullable=True, unique=True),
        sa.Column("provider_payment_id", sa.String(100), nullable=True, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "request_key", name="uq_financial_reservation_request"),
        sa.CheckConstraint(
            "state IN ('reserved','ready','posted')", name="ck_financial_reservation_state"
        ),
        sa.CheckConstraint(
            "payable_paise >= 100 AND reward_points >= 0 AND voucher_paise >= 0",
            name="ck_financial_reservation_amounts",
        ),
        sa.CheckConstraint(
            "(voucher_id IS NULL AND voucher_paise = 0) OR (voucher_id IS NOT NULL AND voucher_paise >= 0)",
            name="ck_financial_reservation_voucher",
        ),
        sa.CheckConstraint(
            "(state = 'reserved' AND provider_order_id IS NULL AND order_id IS NULL AND provider_payment_id IS NULL) OR (state = 'ready' AND provider_order_id IS NOT NULL AND order_id IS NOT NULL AND provider_payment_id IS NULL) OR (state = 'posted' AND provider_order_id IS NOT NULL AND order_id IS NOT NULL AND provider_payment_id IS NOT NULL)",
            name="ck_financial_reservation_binding",
        ),
    )
    op.create_index("ix_financial_reservations_user_id", "financial_reservations", ["user_id"])
    op.create_table(
        "financial_reservation_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "reservation_id",
            sa.Integer(),
            sa.ForeignKey("financial_reservations.id"),
            nullable=False,
        ),
        sa.Column("event_key", sa.String(100), nullable=False, unique=True),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("reservation_id", "action", name="uq_financial_reservation_action"),
        sa.CheckConstraint(
            "action IN ('reserved','checkout_bound','payment_posted')",
            name="ck_financial_reservation_event_action",
        ),
    )
    op.create_index(
        "ix_financial_reservation_events_reservation_id",
        "financial_reservation_events",
        ["reservation_id"],
    )
    comparison = "IS DISTINCT FROM" if dialect == "postgresql" else "IS NOT"
    terms_changed = " OR ".join(f"NEW.{column} {comparison} OLD.{column}" for column in TERMS)
    invalid_transition = "NOT (NEW.state = OLD.state OR (OLD.state = 'reserved' AND NEW.state = 'ready') OR (OLD.state = 'ready' AND NEW.state = 'posted'))"
    rebound = f"(OLD.state IN ('ready','posted') AND (NEW.order_id {comparison} OLD.order_id OR NEW.provider_order_id {comparison} OLD.provider_order_id))"
    reposted = (
        f"(OLD.state = 'posted' AND NEW.provider_payment_id {comparison} OLD.provider_payment_id)"
    )
    guard = f"({terms_changed}) OR ({invalid_transition}) OR {rebound} OR {reposted}"
    if dialect == "postgresql":
        op.execute(f"""CREATE FUNCTION sona_financial_reservation_guard() RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Financial reservations cannot be deleted'; END IF;
                IF {guard} THEN RAISE EXCEPTION 'Financial reservation terms or binding are immutable'; END IF;
                RETURN NEW;
            END; $$ LANGUAGE plpgsql""")
        op.execute(
            "CREATE TRIGGER financial_reservations_guard BEFORE UPDATE OR DELETE ON financial_reservations FOR EACH ROW EXECUTE FUNCTION sona_financial_reservation_guard()"
        )
        op.execute("""CREATE FUNCTION sona_financial_event_immutable() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'Financial reservation audit is immutable'; END;
            $$ LANGUAGE plpgsql""")
        op.execute(
            "CREATE TRIGGER financial_reservation_events_immutable BEFORE UPDATE OR DELETE ON financial_reservation_events FOR EACH ROW EXECUTE FUNCTION sona_financial_event_immutable()"
        )
    else:
        op.execute(
            f"CREATE TRIGGER financial_reservations_update BEFORE UPDATE ON financial_reservations WHEN {guard} BEGIN SELECT RAISE(ABORT, 'Financial reservation terms or binding are immutable'); END"
        )
        op.execute(
            "CREATE TRIGGER financial_reservations_delete BEFORE DELETE ON financial_reservations BEGIN SELECT RAISE(ABORT, 'Financial reservations cannot be deleted'); END"
        )
        for action in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER financial_reservation_events_{action.lower()} BEFORE {action} ON financial_reservation_events BEGIN SELECT RAISE(ABORT, 'Financial reservation audit is immutable'); END"
            )

    # Voucher identity and approved activation validity cannot drift after funding.
    voucher_terms = (
        "id",
        "purchaser_id",
        "assigned_user_id",
        "request_key",
        "code_hash",
        "source",
        "amount_paise",
        "created_at",
    )
    voucher_changed = " OR ".join(
        f"NEW.{column} {comparison} OLD.{column}" for column in voucher_terms
    )
    activated_changed = " OR ".join(
        f"NEW.{column} {comparison} OLD.{column}"
        for column in ("state", "activated_at", "expires_at")
    )
    voucher_guard = f"({voucher_changed}) OR (OLD.state = 'active' AND ({activated_changed}))"
    if dialect == "postgresql":
        op.execute(f"""CREATE FUNCTION sona_gift_voucher_terms_guard() RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Gift vouchers cannot be deleted'; END IF;
                IF {voucher_guard} THEN RAISE EXCEPTION 'Gift voucher terms are immutable'; END IF;
                RETURN NEW;
            END; $$ LANGUAGE plpgsql""")
        op.execute(
            "CREATE TRIGGER gift_vouchers_terms_guard BEFORE UPDATE OR DELETE ON gift_vouchers FOR EACH ROW EXECUTE FUNCTION sona_gift_voucher_terms_guard()"
        )
    else:
        op.execute(
            f"CREATE TRIGGER gift_vouchers_terms_update BEFORE UPDATE ON gift_vouchers WHEN {voucher_guard} BEGIN SELECT RAISE(ABORT, 'Gift voucher terms are immutable'); END"
        )
        op.execute(
            "CREATE TRIGGER gift_vouchers_terms_delete BEFORE DELETE ON gift_vouchers BEGIN SELECT RAISE(ABORT, 'Gift vouchers cannot be deleted'); END"
        )


def downgrade():
    conn = op.get_bind()
    for table in ("financial_reservations", "financial_reservation_events"):
        if conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one():
            raise RuntimeError("Financial reservation records exist; preservation plan required")
    if conn.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS gift_vouchers_terms_guard ON gift_vouchers")
        op.execute("DROP FUNCTION IF EXISTS sona_gift_voucher_terms_guard()")
    else:
        op.execute("DROP TRIGGER IF EXISTS gift_vouchers_terms_update")
        op.execute("DROP TRIGGER IF EXISTS gift_vouchers_terms_delete")
    op.drop_table("financial_reservation_events")
    op.drop_table("financial_reservations")
    if conn.dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS sona_financial_event_immutable()")
        op.execute("DROP FUNCTION IF EXISTS sona_financial_reservation_guard()")
