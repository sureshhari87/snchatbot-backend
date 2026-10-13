"""Balance migration preserves predecessor history and immutable financial records."""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def migration(conn):
    spec = importlib.util.spec_from_file_location(
        "balance_migration", Path("alembic/versions/0032_custom_design_balances.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.op = Operations(MigrationContext.configure(conn))
    return module


def predecessor(conn):
    conn.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY)")
    conn.exec_driver_sql("CREATE TABLE custom_designs (id INTEGER PRIMARY KEY)")
    conn.exec_driver_sql("CREATE TABLE custom_design_quotes (id INTEGER PRIMARY KEY)")
    conn.exec_driver_sql(
        "CREATE TABLE custom_design_audit (id INTEGER PRIMARY KEY,design_id INTEGER,action TEXT)"
    )
    conn.exec_driver_sql("INSERT INTO users VALUES (3)")
    conn.exec_driver_sql("INSERT INTO custom_designs VALUES (1),(2)")
    conn.exec_driver_sql("INSERT INTO custom_design_quotes VALUES (1),(2)")
    conn.exec_driver_sql("INSERT INTO custom_design_audit VALUES (9,1,'manufacturing_started')")


def test_upgrade_preserves_old_audit_and_freezes_intent_and_posting():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        predecessor(conn)
        module = migration(conn)
        module.upgrade()
        assert (
            conn.exec_driver_sql("SELECT action FROM custom_design_audit WHERE id=9").scalar_one()
            == "manufacturing_started"
        )
        for rid, mode in [(1, "manual"), (2, "razorpay")]:
            conn.exec_driver_sql(
                "INSERT INTO custom_design_balance_requests (id,design_id,quote_id,actor_id,request_key,payload_sha256,amount_paise,mode,created_at) VALUES (?,?,?,3,?,?,900,?,CURRENT_TIMESTAMP)",
                (rid, rid, rid, "synthetic-request-" + str(rid), "a" * 64, mode),
            )
        conn.exec_driver_sql(
            "INSERT INTO custom_design_balance_entries (id,design_id,request_id,quote_id,amount_paise,mode,reference,actor_id,created_at) VALUES (1,1,1,1,900,'manual','synthetic-receipt',3,CURRENT_TIMESTAMP)"
        )
        for statement in [
            "UPDATE custom_design_balance_requests SET amount_paise=1",
            "DELETE FROM custom_design_balance_requests",
            "UPDATE custom_design_balance_entries SET amount_paise=1",
            "DELETE FROM custom_design_balance_entries",
        ]:
            with pytest.raises(sa.exc.IntegrityError):
                conn.exec_driver_sql(statement)
        with pytest.raises(RuntimeError, match="preserve payment history"):
            module.downgrade()
    engine.dispose()


def test_checkout_allows_only_audited_lifecycle_fields_and_one_active_attempt():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        predecessor(conn)
        module = migration(conn)
        module.upgrade()
        conn.exec_driver_sql(
            "INSERT INTO custom_design_balance_requests (id,design_id,quote_id,actor_id,request_key,payload_sha256,amount_paise,mode,created_at) VALUES (1,1,1,3,'synthetic',?,900,'razorpay',CURRENT_TIMESTAMP)",
            ("a" * 64,),
        )
        conn.exec_driver_sql(
            "INSERT INTO custom_design_balance_checkouts (id,request_id,sequence,receipt,state,created_at) VALUES (1,1,1,'receipt-one','creating',CURRENT_TIMESTAMP)"
        )
        with pytest.raises(sa.exc.IntegrityError):
            conn.exec_driver_sql(
                "INSERT INTO custom_design_balance_checkouts (id,request_id,sequence,receipt,state,created_at) VALUES (2,1,2,'receipt-two','creating',CURRENT_TIMESTAMP)"
            )
        conn.exec_driver_sql(
            "UPDATE custom_design_balance_checkouts SET state='unknown' WHERE id=1"
        )
        conn.exec_driver_sql(
            "UPDATE custom_design_balance_checkouts SET state='ready',provider_order_id='order_synthetic' WHERE id=1"
        )
        for statement in [
            "UPDATE custom_design_balance_checkouts SET receipt='changed'",
            "UPDATE custom_design_balance_checkouts SET provider_order_id='order_changed'",
            "UPDATE custom_design_balance_checkouts SET state='creating'",
            "DELETE FROM custom_design_balance_checkouts",
        ]:
            with pytest.raises(sa.exc.IntegrityError):
                conn.exec_driver_sql(statement)
        conn.exec_driver_sql(
            "UPDATE custom_design_balance_checkouts SET state='closed_failed' WHERE id=1"
        )
        conn.exec_driver_sql(
            "INSERT INTO custom_design_balance_checkouts (id,request_id,sequence,receipt,state,created_at) VALUES (2,1,2,'receipt-two','creating',CURRENT_TIMESTAMP)"
        )
        conn.exec_driver_sql("INSERT INTO custom_design_audit VALUES (10,1,'completed')")
        with pytest.raises(sa.exc.IntegrityError):
            conn.exec_driver_sql("INSERT INTO custom_design_audit VALUES (11,1,'completed')")
    engine.dispose()


def test_empty_downgrade_preserves_predecessor_records():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        predecessor(conn)
        module = migration(conn)
        module.upgrade()
        module.downgrade()
        assert conn.exec_driver_sql("SELECT COUNT(*) FROM custom_design_audit").scalar_one() == 1
        assert set(sa.inspect(conn).get_table_names()) == {
            "users",
            "custom_designs",
            "custom_design_quotes",
            "custom_design_audit",
        }
    engine.dispose()


def test_completion_only_history_also_blocks_downgrade():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        predecessor(conn)
        module = migration(conn)
        module.upgrade()
        conn.exec_driver_sql("INSERT INTO custom_design_audit VALUES (10,1,'completed')")
        with pytest.raises(RuntimeError, match="preserve completion history"):
            module.downgrade()
        assert conn.exec_driver_sql("SELECT COUNT(*) FROM custom_design_audit").scalar_one() == 2
        assert "custom_design_balance_entries" in sa.inspect(conn).get_table_names()
    engine.dispose()
