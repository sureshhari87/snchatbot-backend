"""Frozen voucher funding and checkout context schema; unapplied rollout."""

import sqlalchemy as sa

from alembic import op

revision = "0029_voucher_funding"
down_revision = "0028_financial_reservations"
branch_labels = None
depends_on = None
DDL = {
    "postgresql": (
        "\nCREATE TABLE voucher_funding (\n\tvoucher_id INTEGER NOT NULL, \n\trequest_sha256 VARCHAR(64) NOT NULL, \n\tpayment_mode VARCHAR(20) NOT NULL, \n\tcode_ciphertext TEXT NOT NULL, \n\treceipt VARCHAR(40) NOT NULL, \n\tstate VARCHAR(20) NOT NULL, \n\tprovider_order_id VARCHAR(100), \n\tprovider_payment_id VARCHAR(100), \n\tcreated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, \n\tPRIMARY KEY (voucher_id), \n\tCONSTRAINT ck_voucher_funding_mode CHECK (payment_mode IN ('manual','razorpay','complimentary')), \n\tCONSTRAINT ck_voucher_funding_state CHECK (state IN ('not_started','creating','unknown','ready','posted')), \n\tCONSTRAINT ck_voucher_funding_binding CHECK ((payment_mode IN ('manual','complimentary') AND state IN ('not_started','posted') AND provider_order_id IS NULL AND provider_payment_id IS NULL) OR (payment_mode = 'razorpay' AND ((state IN ('not_started','creating','unknown') AND provider_order_id IS NULL AND provider_payment_id IS NULL) OR (state = 'ready' AND provider_order_id IS NOT NULL AND provider_payment_id IS NULL) OR (state = 'posted' AND provider_order_id IS NOT NULL AND provider_payment_id IS NOT NULL)))), \n\tFOREIGN KEY(voucher_id) REFERENCES gift_vouchers (id), \n\tUNIQUE (receipt), \n\tUNIQUE (provider_order_id), \n\tUNIQUE (provider_payment_id)\n)\n\n",
        "\nCREATE TABLE voucher_funding_events (\n\tid SERIAL NOT NULL, \n\tvoucher_id INTEGER NOT NULL, \n\taction VARCHAR(30) NOT NULL, \n\tactor_id INTEGER, \n\tevidence_sha256 VARCHAR(64) NOT NULL, \n\tcreated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_voucher_funding_event_action UNIQUE (voucher_id, action), \n\tCONSTRAINT ck_voucher_funding_event_action CHECK (action IN ('request_created','checkout_started','checkout_unknown','checkout_bound','funding_posted')), \n\tFOREIGN KEY(voucher_id) REFERENCES gift_vouchers (id), \n\tFOREIGN KEY(actor_id) REFERENCES users (id)\n)\n\n",
        "CREATE INDEX ix_voucher_funding_events_voucher_id ON voucher_funding_events (voucher_id)",
        "\nCREATE TABLE financial_checkout_attempts (\n\treservation_id INTEGER NOT NULL, \n\tclient_request_sha256 VARCHAR(64) NOT NULL, \n\tcontext_json TEXT NOT NULL, \n\tstate VARCHAR(20) NOT NULL, \n\tcreated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, \n\tPRIMARY KEY (reservation_id), \n\tCONSTRAINT ck_financial_checkout_attempt_state CHECK (state IN ('creating','unknown','ready')), \n\tFOREIGN KEY(reservation_id) REFERENCES financial_reservations (id)\n)\n\n",
    ),
    "sqlite": (
        "\nCREATE TABLE voucher_funding (\n\tvoucher_id INTEGER NOT NULL, \n\trequest_sha256 VARCHAR(64) NOT NULL, \n\tpayment_mode VARCHAR(20) NOT NULL, \n\tcode_ciphertext TEXT NOT NULL, \n\treceipt VARCHAR(40) NOT NULL, \n\tstate VARCHAR(20) NOT NULL, \n\tprovider_order_id VARCHAR(100), \n\tprovider_payment_id VARCHAR(100), \n\tcreated_at DATETIME NOT NULL, \n\tPRIMARY KEY (voucher_id), \n\tCONSTRAINT ck_voucher_funding_mode CHECK (payment_mode IN ('manual','razorpay','complimentary')), \n\tCONSTRAINT ck_voucher_funding_state CHECK (state IN ('not_started','creating','unknown','ready','posted')), \n\tCONSTRAINT ck_voucher_funding_binding CHECK ((payment_mode IN ('manual','complimentary') AND state IN ('not_started','posted') AND provider_order_id IS NULL AND provider_payment_id IS NULL) OR (payment_mode = 'razorpay' AND ((state IN ('not_started','creating','unknown') AND provider_order_id IS NULL AND provider_payment_id IS NULL) OR (state = 'ready' AND provider_order_id IS NOT NULL AND provider_payment_id IS NULL) OR (state = 'posted' AND provider_order_id IS NOT NULL AND provider_payment_id IS NOT NULL)))), \n\tFOREIGN KEY(voucher_id) REFERENCES gift_vouchers (id), \n\tUNIQUE (receipt), \n\tUNIQUE (provider_order_id), \n\tUNIQUE (provider_payment_id)\n)\n\n",
        "\nCREATE TABLE voucher_funding_events (\n\tid INTEGER NOT NULL, \n\tvoucher_id INTEGER NOT NULL, \n\taction VARCHAR(30) NOT NULL, \n\tactor_id INTEGER, \n\tevidence_sha256 VARCHAR(64) NOT NULL, \n\tcreated_at DATETIME NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_voucher_funding_event_action UNIQUE (voucher_id, action), \n\tCONSTRAINT ck_voucher_funding_event_action CHECK (action IN ('request_created','checkout_started','checkout_unknown','checkout_bound','funding_posted')), \n\tFOREIGN KEY(voucher_id) REFERENCES gift_vouchers (id), \n\tFOREIGN KEY(actor_id) REFERENCES users (id)\n)\n\n",
        "CREATE INDEX ix_voucher_funding_events_voucher_id ON voucher_funding_events (voucher_id)",
        "\nCREATE TABLE financial_checkout_attempts (\n\treservation_id INTEGER NOT NULL, \n\tclient_request_sha256 VARCHAR(64) NOT NULL, \n\tcontext_json TEXT NOT NULL, \n\tstate VARCHAR(20) NOT NULL, \n\tcreated_at DATETIME NOT NULL, \n\tPRIMARY KEY (reservation_id), \n\tCONSTRAINT ck_financial_checkout_attempt_state CHECK (state IN ('creating','unknown','ready')), \n\tFOREIGN KEY(reservation_id) REFERENCES financial_reservations (id)\n)\n\n",
    ),
}


def upgrade():
    dialect = op.get_bind().dialect.name
    if dialect not in DDL:
        raise RuntimeError("Voucher funding requires PostgreSQL or SQLite")
    for statement in DDL[dialect]:
        op.execute(statement)
    comparison = "IS DISTINCT FROM" if dialect == "postgresql" else "IS NOT"
    funding_terms = (
        "voucher_id",
        "request_sha256",
        "payment_mode",
        "code_ciphertext",
        "receipt",
        "created_at",
    )
    attempt_terms = ("reservation_id", "client_request_sha256", "context_json", "created_at")
    rules = {
        "voucher_funding": (
            funding_terms,
            "NOT (NEW.state = OLD.state OR (OLD.state = 'not_started' AND NEW.state = 'creating') OR (OLD.state = 'not_started' AND NEW.state = 'posted' AND OLD.payment_mode IN ('manual','complimentary')) OR (OLD.state = 'creating' AND NEW.state IN ('unknown','ready')) OR (OLD.state = 'unknown' AND NEW.state = 'ready') OR (OLD.state = 'ready' AND NEW.state = 'posted'))",
            f"(OLD.state IN ('ready','posted') AND NEW.provider_order_id {comparison} OLD.provider_order_id) OR (OLD.state = 'posted' AND NEW.provider_payment_id {comparison} OLD.provider_payment_id)",
        ),
        "financial_checkout_attempts": (
            attempt_terms,
            "NOT (NEW.state = OLD.state OR (OLD.state = 'creating' AND NEW.state IN ('unknown','ready')) OR (OLD.state = 'unknown' AND NEW.state = 'ready'))",
            "FALSE",
        ),
    }
    for table, (terms, transition, binding) in rules.items():
        changed = " OR ".join(f"NEW.{column} {comparison} OLD.{column}" for column in terms)
        guard = f"({changed}) OR ({transition}) OR ({binding})"
        if dialect == "postgresql":
            op.execute(f"""CREATE FUNCTION sona_{table}_guard() RETURNS trigger AS $$
                BEGIN
                    IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Financial payment attempts cannot be deleted'; END IF;
                    IF {guard} THEN RAISE EXCEPTION 'Financial payment terms or binding are immutable'; END IF;
                    RETURN NEW;
                END; $$ LANGUAGE plpgsql""")
            op.execute(
                f"CREATE TRIGGER {table}_guard BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION sona_{table}_guard()"
            )
        else:
            op.execute(
                f"CREATE TRIGGER {table}_update BEFORE UPDATE ON {table} WHEN {guard} BEGIN SELECT RAISE(ABORT, 'Financial payment terms or binding are immutable'); END"
            )
            op.execute(
                f"CREATE TRIGGER {table}_delete BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT, 'Financial payment attempts cannot be deleted'); END"
            )
    if dialect == "postgresql":
        op.execute("""CREATE FUNCTION sona_voucher_funding_event_immutable() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'Voucher funding audit is immutable'; END;
            $$ LANGUAGE plpgsql""")
        op.execute(
            "CREATE TRIGGER voucher_funding_events_immutable BEFORE UPDATE OR DELETE ON voucher_funding_events FOR EACH ROW EXECUTE FUNCTION sona_voucher_funding_event_immutable()"
        )
    else:
        for action in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER voucher_funding_events_{action.lower()} BEFORE {action} ON voucher_funding_events BEGIN SELECT RAISE(ABORT, 'Voucher funding audit is immutable'); END"
            )


def downgrade():
    connection = op.get_bind()
    tables = ("voucher_funding", "voucher_funding_events", "financial_checkout_attempts")
    if any(
        connection.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
        for table in tables
    ):
        raise RuntimeError("Voucher funding records exist; preservation plan required")
    for table in reversed(tables):
        op.drop_table(table)
    if connection.dialect.name == "postgresql":
        for name in (
            "sona_voucher_funding_guard",
            "sona_financial_checkout_attempts_guard",
            "sona_voucher_funding_event_immutable",
        ):
            op.execute(f"DROP FUNCTION IF EXISTS {name}()")
