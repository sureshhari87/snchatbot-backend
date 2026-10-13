"""Frozen balance intents, bound attempts and immutable posting. Local/unapplied."""

import sqlalchemy as sa

from alembic import op

revision = "0032_custom_design_balances"
down_revision = "0031_inbox_broadcast"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "custom_design_balance_requests",
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
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("request_key", sa.String(100), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("amount_paise > 0", name="ck_custom_balance_request_amount"),
        sa.CheckConstraint("mode IN ('manual','razorpay')", name="ck_custom_balance_request_mode"),
        sa.CheckConstraint("length(payload_sha256) = 64", name="ck_custom_balance_request_digest"),
    )
    op.create_table(
        "custom_design_balance_checkouts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "request_id",
            sa.Integer(),
            sa.ForeignKey("custom_design_balance_requests.id"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("receipt", sa.String(40), nullable=False, unique=True),
        sa.Column("provider_order_id", sa.String(100), nullable=True, unique=True),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("request_id", "sequence", name="uq_custom_balance_checkout_sequence"),
        sa.CheckConstraint("sequence > 0", name="ck_custom_balance_checkout_sequence"),
        sa.CheckConstraint(
            "state IN ('creating','unknown','ready','paid_pending','closed_failed')",
            name="ck_custom_balance_checkout_state",
        ),
        sa.CheckConstraint(
            "state IN ('creating','unknown') OR provider_order_id IS NOT NULL",
            name="ck_custom_balance_checkout_binding",
        ),
    )
    op.create_index(
        "ix_custom_design_balance_checkouts_request_id",
        "custom_design_balance_checkouts",
        ["request_id"],
    )
    op.create_index(
        "uq_custom_balance_active_checkout",
        "custom_design_balance_checkouts",
        ["request_id"],
        unique=True,
        postgresql_where=sa.text("state != 'closed_failed'"),
        sqlite_where=sa.text("state != 'closed_failed'"),
    )
    op.create_table(
        "custom_design_balance_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "design_id",
            sa.Integer(),
            sa.ForeignKey("custom_designs.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "request_id",
            sa.Integer(),
            sa.ForeignKey("custom_design_balance_requests.id"),
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
        sa.Column(
            "checkout_id",
            sa.Integer(),
            sa.ForeignKey("custom_design_balance_checkouts.id"),
            nullable=True,
            unique=True,
        ),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("reference", sa.String(100), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("provider_order_id", sa.String(100), nullable=True, unique=True),
        sa.Column("provider_payment_id", sa.String(100), nullable=True, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("amount_paise > 0", name="ck_custom_balance_entry_amount"),
        sa.CheckConstraint("mode IN ('manual','razorpay')", name="ck_custom_balance_entry_mode"),
        sa.CheckConstraint(
            "(mode = 'manual' AND actor_id IS NOT NULL AND checkout_id IS NULL AND provider_order_id IS NULL AND provider_payment_id IS NULL) OR (mode = 'razorpay' AND actor_id IS NULL AND checkout_id IS NOT NULL AND provider_order_id IS NOT NULL AND provider_payment_id IS NOT NULL)",
            name="ck_custom_balance_entry_evidence",
        ),
        sa.UniqueConstraint("mode", "reference", name="uq_custom_balance_receipt"),
    )
    op.create_index(
        "uq_custom_design_completed",
        "custom_design_audit",
        ["design_id"],
        unique=True,
        postgresql_where=sa.text("action = 'completed'"),
        sqlite_where=sa.text("action = 'completed'"),
    )
    immutable_guards()


def immutable_guards():
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            "CREATE FUNCTION sona_custom_balance_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Custom balance intent/posting is immutable'; END; $$"
        )
        for table in ["custom_design_balance_requests", "custom_design_balance_entries"]:
            op.execute(
                f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION sona_custom_balance_immutable()"
            )
        op.execute("""CREATE FUNCTION sona_custom_balance_checkout_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Custom balance checkout history is immutable'; END IF;
          IF ROW(NEW.id,NEW.request_id,NEW.sequence,NEW.receipt,NEW.created_at) IS DISTINCT FROM ROW(OLD.id,OLD.request_id,OLD.sequence,OLD.receipt,OLD.created_at)
             OR (OLD.provider_order_id IS NOT NULL AND NEW.provider_order_id IS DISTINCT FROM OLD.provider_order_id)
             OR NOT (NEW.state = OLD.state OR OLD.state IN ('creating','unknown') AND NEW.state IN ('unknown','ready','paid_pending')
                     OR OLD.state = 'ready' AND NEW.state IN ('paid_pending','closed_failed'))
          THEN RAISE EXCEPTION 'Invalid custom balance checkout transition'; END IF;
          RETURN NEW;
        END; $$""")
        op.execute(
            "CREATE TRIGGER custom_balance_checkout_guard BEFORE UPDATE OR DELETE ON custom_design_balance_checkouts FOR EACH ROW EXECUTE FUNCTION sona_custom_balance_checkout_guard()"
        )
    elif dialect == "sqlite":
        for table in ["custom_design_balance_requests", "custom_design_balance_entries"]:
            for action in ["UPDATE", "DELETE"]:
                op.execute(
                    f"CREATE TRIGGER {table}_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Custom balance intent/posting is immutable'); END"
                )
        op.execute(
            "CREATE TRIGGER custom_balance_checkout_delete BEFORE DELETE ON custom_design_balance_checkouts BEGIN SELECT RAISE(ABORT,'Custom balance checkout history is immutable'); END"
        )
        op.execute("""CREATE TRIGGER custom_balance_checkout_update BEFORE UPDATE ON custom_design_balance_checkouts
        WHEN NEW.id IS NOT OLD.id OR NEW.request_id IS NOT OLD.request_id OR NEW.sequence IS NOT OLD.sequence OR NEW.receipt IS NOT OLD.receipt OR NEW.created_at IS NOT OLD.created_at
          OR (OLD.provider_order_id IS NOT NULL AND NEW.provider_order_id IS NOT OLD.provider_order_id)
          OR NOT (NEW.state = OLD.state OR OLD.state IN ('creating','unknown') AND NEW.state IN ('unknown','ready','paid_pending')
                  OR OLD.state = 'ready' AND NEW.state IN ('paid_pending','closed_failed'))
        BEGIN SELECT RAISE(ABORT,'Invalid custom balance checkout transition'); END""")


def downgrade():
    if (
        op.get_bind()
        .execute(sa.text("SELECT COUNT(*) FROM custom_design_audit WHERE action = 'completed'"))
        .scalar()
    ):
        raise RuntimeError("Completed designs exist; rollback must preserve completion history")
    for table in [
        "custom_design_balance_requests",
        "custom_design_balance_checkouts",
        "custom_design_balance_entries",
    ]:
        if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM " + table)).scalar():
            raise RuntimeError(
                "Custom balance tables contain data; rollback must preserve payment history"
            )
    op.drop_index("uq_custom_design_completed", table_name="custom_design_audit")
    for table in [
        "custom_design_balance_entries",
        "custom_design_balance_checkouts",
        "custom_design_balance_requests",
    ]:
        op.drop_table(table)
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION sona_custom_balance_checkout_guard()")
        op.execute("DROP FUNCTION sona_custom_balance_immutable()")
