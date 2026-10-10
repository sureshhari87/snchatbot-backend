"""Reward/voucher ledger foundation, unapplied until reviewed rollout."""

import sqlalchemy as sa

from alembic import op

revision = "0027_rewards_vouchers_ledger"
down_revision = "0026_custom_design_advances"
branch_labels = None
depends_on = None
HISTORY = ("reward_entries", "gift_voucher_entries", "reward_voucher_holds")
TABLES = (
    "reward_accounts",
    "gift_vouchers",
    "reward_entries",
    "gift_voucher_entries",
    "reward_voucher_holds",
)


def upgrade():
    # Freeze table definitions in this revision rather than importing live models.
    dialect = op.get_bind().dialect.name
    if dialect not in ("postgresql", "sqlite"):
        raise RuntimeError("Financial history requires PostgreSQL or SQLite")
    op.create_table(
        "reward_accounts",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("balance_points", sa.BigInteger(), nullable=False),
        sa.Column("reserved_points", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("balance_points >= 0", name="ck_reward_balance"),
        sa.CheckConstraint(
            "reserved_points >= 0 AND reserved_points <= balance_points", name="ck_reward_reserved"
        ),
    )
    op.create_table(
        "gift_vouchers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("purchaser_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("assigned_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("request_key", sa.String(100), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("balance_paise", sa.BigInteger(), nullable=False),
        sa.Column("reserved_paise", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("amount_paise >= 100", name="ck_gift_voucher_amount"),
        sa.CheckConstraint("state IN ('pending','active')", name="ck_gift_voucher_state"),
        sa.CheckConstraint(
            "source IN ('purchased','complimentary')", name="ck_gift_voucher_source"
        ),
        sa.CheckConstraint(
            "balance_paise >= 0 AND balance_paise <= amount_paise AND reserved_paise >= 0 AND reserved_paise <= balance_paise",
            name="ck_gift_voucher_balance",
        ),
        sa.CheckConstraint(
            "(state = 'pending' AND balance_paise = 0 AND activated_at IS NULL AND expires_at IS NULL) OR (state = 'active' AND activated_at IS NOT NULL AND expires_at IS NOT NULL AND expires_at > activated_at)",
            name="ck_gift_voucher_activation",
        ),
        sa.UniqueConstraint("purchaser_id", "request_key", name="uq_gift_voucher_request"),
    )
    op.create_table(
        "reward_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("reward_accounts.user_id"), nullable=False
        ),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("order_snapshots.id"), nullable=False),
        sa.Column("event_key", sa.String(100), nullable=False, unique=True),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("points_delta", sa.BigInteger(), nullable=False),
        sa.Column("evidence_reference", sa.String(100), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("kind IN ('purchase_credit','redemption')", name="ck_reward_entry_kind"),
        sa.CheckConstraint(
            "(kind = 'purchase_credit' AND points_delta > 0) OR (kind = 'redemption' AND points_delta < 0)",
            name="ck_reward_entry_sign",
        ),
        sa.UniqueConstraint("user_id", "order_id", "kind", name="uq_reward_order_posting"),
    )
    op.create_index("ix_reward_entries_user_id", "reward_entries", ["user_id"])
    op.create_table(
        "gift_voucher_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("voucher_id", sa.Integer(), sa.ForeignKey("gift_vouchers.id"), nullable=False),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("order_snapshots.id"), nullable=True),
        sa.Column("event_key", sa.String(100), nullable=False, unique=True),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("amount_delta_paise", sa.BigInteger(), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reason", sa.String(200), nullable=True),
        sa.Column("evidence_reference", sa.String(100), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('manual_funding','razorpay_funding','complimentary','redemption')",
            name="ck_gift_voucher_entry_kind",
        ),
        sa.CheckConstraint(
            "(kind = 'redemption' AND amount_delta_paise < 0 AND order_id IS NOT NULL) OR (kind != 'redemption' AND amount_delta_paise > 0 AND order_id IS NULL)",
            name="ck_gift_voucher_entry_sign",
        ),
        sa.CheckConstraint(
            "kind NOT IN ('manual_funding','complimentary') OR (actor_id IS NOT NULL AND reason IS NOT NULL AND length(trim(reason)) > 0)",
            name="ck_gift_voucher_manual_audit",
        ),
        sa.UniqueConstraint(
            "voucher_id", "kind", "evidence_reference", name="uq_gift_voucher_evidence"
        ),
    )
    op.create_index("ix_gift_voucher_entries_voucher_id", "gift_voucher_entries", ["voucher_id"])
    op.create_index(
        "uq_gift_voucher_one_funding",
        "gift_voucher_entries",
        ["voucher_id"],
        unique=True,
        postgresql_where=sa.text("kind != 'redemption'"),
        sqlite_where=sa.text("kind != 'redemption'"),
    )

    op.create_index(
        "uq_gift_voucher_order_redemption",
        "gift_voucher_entries",
        ["voucher_id", "order_id"],
        unique=True,
    )
    op.create_table(
        "reward_voucher_holds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "reward_user_id", sa.Integer(), sa.ForeignKey("reward_accounts.user_id"), nullable=True
        ),
        sa.Column("voucher_id", sa.Integer(), sa.ForeignKey("gift_vouchers.id"), nullable=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("reference", sa.String(100), nullable=False),
        sa.Column("reason", sa.String(200), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "(reward_user_id IS NOT NULL AND voucher_id IS NULL) OR (reward_user_id IS NULL AND voucher_id IS NOT NULL)",
            name="ck_reward_voucher_hold_target",
        ),
        sa.CheckConstraint(
            "kind IN ('refund','dispute','admin_review')", name="ck_reward_voucher_hold_kind"
        ),
        sa.CheckConstraint("length(trim(reason)) > 0", name="ck_reward_voucher_hold_reason"),
        sa.UniqueConstraint("reward_user_id", "kind", "reference", name="uq_reward_hold_reference"),
        sa.UniqueConstraint("voucher_id", "kind", "reference", name="uq_voucher_hold_reference"),
    )
    op.create_index(
        "ix_reward_voucher_holds_reward_user_id", "reward_voucher_holds", ["reward_user_id"]
    )
    op.create_index("ix_reward_voucher_holds_voucher_id", "reward_voucher_holds", ["voucher_id"])
    if dialect == "postgresql":
        op.execute("""CREATE FUNCTION sona_reward_voucher_immutable() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'Reward/voucher history is immutable'; END;
            $$ LANGUAGE plpgsql""")
        for table in HISTORY:
            op.execute(
                f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION sona_reward_voucher_immutable()"
            )
    else:
        for table in HISTORY:
            for action in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER {table}_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'Reward/voucher history is immutable'); END"
                )


def downgrade():
    conn = op.get_bind()
    if any(conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one() for table in TABLES):
        raise RuntimeError("Reward/voucher records exist; preservation plan required")
    for table in HISTORY:
        if conn.dialect.name == "postgresql":
            op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
        else:
            for action in ("update", "delete"):
                op.execute(f"DROP TRIGGER IF EXISTS {table}_{action}")
    for table in reversed(TABLES):
        op.drop_table(table)
    if conn.dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS sona_reward_voucher_immutable()")
