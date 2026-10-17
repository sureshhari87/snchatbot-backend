"""Approved referral terms and immutable dual-party credit evidence; unapplied."""

import sqlalchemy as sa

from alembic import op

revision = "0034_referrals"
down_revision = "0033_delivery_drafts"
branch_labels = None
depends_on = None
TABLES = ("referral_codes", "referral_applications", "referral_outcomes", "referral_credits")


def upgrade():
    op.create_table(
        "referral_codes",
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("code", sa.String(20), nullable=False, unique=True),
        sa.UniqueConstraint("owner_id", "code", name="uq_referral_code_owner"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "referral_applications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "referred_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, unique=True
        ),
        sa.Column("referrer_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("code", sa.String(20), sa.ForeignKey("referral_codes.code"), nullable=False),
        sa.Column("terms_version", sa.Integer(), nullable=False),
        sa.Column("referrer_points", sa.BigInteger(), nullable=False),
        sa.Column("customer_points", sa.BigInteger(), nullable=False),
        sa.Column("minimum_merchandise_paise", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["referrer_id", "code"],
            ["referral_codes.owner_id", "referral_codes.code"],
            name="fk_referral_code_owner",
        ),
        sa.CheckConstraint("referred_id != referrer_id", name="ck_referral_not_self"),
        sa.CheckConstraint(
            "terms_version = 1 AND referrer_points = 100 AND customer_points = 50 AND minimum_merchandise_paise = 100000",
            name="ck_referral_approved_terms",
        ),
    )
    op.create_index(
        "ix_referral_applications_referrer_id", "referral_applications", ["referrer_id"]
    )
    op.create_table(
        "referral_outcomes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "application_id",
            sa.Integer(),
            sa.ForeignKey("referral_applications.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "order_id",
            sa.Integer(),
            sa.ForeignKey("order_snapshots.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(100), nullable=False),
        sa.Column("merchandise_after_coupon_paise", sa.BigInteger(), nullable=False),
        sa.Column("evidence_reference", sa.String(100), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "state IN ('rewarded','not_qualified','held')", name="ck_referral_outcome_state"
        ),
        sa.CheckConstraint(
            "merchandise_after_coupon_paise >= 0", name="ck_referral_outcome_amount"
        ),
    )
    op.create_table(
        "referral_credits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "outcome_id", sa.Integer(), sa.ForeignKey("referral_outcomes.id"), nullable=False
        ),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("reward_accounts.user_id"), nullable=False
        ),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("order_snapshots.id"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("points_delta", sa.BigInteger(), nullable=False),
        sa.Column("event_key", sa.String(100), nullable=False, unique=True),
        sa.Column("evidence_reference", sa.String(100), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "role IN ('referrer','new_customer') AND points_delta > 0", name="ck_referral_credit"
        ),
        sa.UniqueConstraint("outcome_id", "role", name="uq_referral_credit_role"),
        sa.UniqueConstraint("outcome_id", "user_id", name="uq_referral_credit_user"),
    )
    op.create_index("ix_referral_credits_user_id", "referral_credits", ["user_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE FUNCTION sona_referral_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Referral history is immutable'; END; $$"
        )
        for table in TABLES:
            op.execute(
                f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION sona_referral_immutable()"
            )
    else:
        for table in TABLES:
            for action in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER {table}_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'Referral history is immutable'); END"
                )


def downgrade():
    if any(
        op.get_bind().execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
        for table in TABLES
    ):
        raise RuntimeError("Referral history exists; reviewed preservation plan required")
    for table in reversed(TABLES):
        op.drop_table(table)
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION sona_referral_immutable()")
