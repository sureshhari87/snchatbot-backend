"""Disposable migration preserves live settings and makes draft audits immutable."""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def migration(conn):
    spec = importlib.util.spec_from_file_location(
        "draft_migration", Path("alembic/versions/0033_delivery_drafts.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.op = Operations(MigrationContext.configure(conn))
    return module


def predecessor(conn):
    conn.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY)")
    conn.exec_driver_sql("CREATE TABLE app_config_entries (id INTEGER PRIMARY KEY, value TEXT)")
    conn.exec_driver_sql("INSERT INTO users VALUES (3)")
    conn.exec_driver_sql("INSERT INTO app_config_entries VALUES (1,'SYNTHETIC-LIVE-PRESERVED')")


def test_empty_downgrade_preserves_predecessor():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        predecessor(conn)
        module = migration(conn)
        module.upgrade()
        module.downgrade()
        assert set(sa.inspect(conn).get_table_names()) == {"users", "app_config_entries"}
        assert (
            conn.exec_driver_sql("SELECT value FROM app_config_entries").scalar_one()
            == "SYNTHETIC-LIVE-PRESERVED"
        )
    engine.dispose()


def test_draft_audit_immutable_and_nonempty_downgrade_blocked():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        predecessor(conn)
        module = migration(conn)
        module.upgrade()
        conn.exec_driver_sql(
            "INSERT INTO delivery_drafts (id,name,route,version,created_at,updated_at) VALUES (1,'Synthetic','{}',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
        conn.exec_driver_sql(
            "INSERT INTO delivery_draft_audits (id,draft_id,version,actor_id,request_key,payload_sha256,reason,\"after\",created_at) VALUES (1,1,1,3,'synthetic-key','digest','Synthetic reason','{}',CURRENT_TIMESTAMP)"
        )
        for statement in [
            "UPDATE delivery_draft_audits SET reason='Changed'",
            "DELETE FROM delivery_draft_audits",
        ]:
            with pytest.raises(sa.exc.IntegrityError):
                conn.exec_driver_sql(statement)
        with pytest.raises(RuntimeError, match="history"):
            module.downgrade()
        assert (
            conn.exec_driver_sql("SELECT value FROM app_config_entries").scalar_one()
            == "SYNTHETIC-LIVE-PRESERVED"
        )
    engine.dispose()
