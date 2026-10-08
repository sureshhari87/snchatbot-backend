import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text
from sqlalchemy.exc import DatabaseError


def load_migration():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0020_savings_ledger.py"
    spec = importlib.util.spec_from_file_location("savings_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_schema_roundtrip_and_data_preservation(test_engine, monkeypatch):
    migration = load_migration()
    with test_engine.begin() as conn:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(conn)))
        # create_all tables have no migration triggers; remove just empty savings tables.
        for table in (
            "savings_audit",
            "savings_entries",
            "savings_gold_rates",
            "savings_payments",
            "savings_schemes",
        ):
            conn.execute(text("DROP TABLE " + table))
        migration.upgrade()
        assert "savings_entries" in inspect(conn).get_table_names()
        migration.downgrade()
        migration.upgrade()
        conn.execute(
            text(
                "INSERT INTO users (id,username,email,hashed_password,is_verified,is_admin) "
                "VALUES (901,'savings_migration','savings_migration@example.test','unused',1,1)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO savings_schemes "
                "(id,user_id,request_key,kind,monthly_paise,state,started_at,matures_at) "
                "VALUES (1,901,'key','monthly',50000,'active','2024-01-01','2024-12-01')"
            )
        )
        with pytest.raises(RuntimeError, match="preservation"):
            migration.downgrade()
        assert conn.execute(text("SELECT count(*) FROM users WHERE id=901")).scalar_one() == 1
        assert conn.execute(text("SELECT count(*) FROM savings_schemes")).scalar_one() == 1


@pytest.mark.parametrize(
    "table,insert",
    [
        (
            "savings_gold_rates",
            "INSERT INTO savings_gold_rates "
            "(id,paise_per_gram,purity,source,effective_at,valid_until,created_by,created_at) "
            "VALUES (1,700000,'24K 995','synthetic','2024-01-01','2024-12-01',901,'2024-01-01')",
        ),
        (
            "savings_audit",
            "INSERT INTO savings_audit "
            "(id,scheme_id,event_key,action,actor_id,evidence_reference,created_at) "
            "VALUES (1,1,'audit','enrolled',901,'synthetic','2024-01-01')",
        ),
        (
            "savings_entries",
            "INSERT INTO savings_entries "
            "(id,scheme_id,event_key,payment_id,kind,principal_paise,gold_micrograms,bonus_micrograms,"
            "store_bonus_paise,bonus_bps,evidence_reference,created_at) "
            "VALUES (1,1,'redeem',NULL,'redemption',-50000,0,0,50000,0,'synthetic','2024-12-01')",
        ),
    ],
)
def test_database_blocks_ledger_audit_and_rate_mutation(test_engine, monkeypatch, table, insert):
    migration = load_migration()
    with test_engine.begin() as conn:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(conn)))
        migration.install_immutable_tables()
        conn.execute(
            text(
                "INSERT INTO users (id,username,email,hashed_password,is_verified,is_admin) "
                "VALUES (901,'immutable','immutable@example.test','unused',1,1)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO savings_schemes "
                "(id,user_id,request_key,kind,monthly_paise,state,started_at,matures_at) "
                "VALUES (1,901,'key','monthly',50000,'active','2024-01-01','2024-12-01')"
            )
        )
        conn.execute(text(insert))
        for sql in ("DELETE FROM " + table, "UPDATE " + table + " SET id = 2"):
            with pytest.raises(DatabaseError, match="immutable"):
                conn.execute(text(sql))
        assert conn.execute(text("SELECT count(*) FROM " + table)).scalar_one() == 1
