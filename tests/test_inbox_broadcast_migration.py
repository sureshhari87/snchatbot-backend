"""New audit migration preserves predecessor records and rejects history deletion."""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def migration(conn):
    path = Path("alembic/versions/0031_inbox_broadcast.py")
    spec = importlib.util.spec_from_file_location("broadcast_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.op = Operations(MigrationContext.configure(conn))
    return module


def test_audit_upgrade_preserves_existing_records_and_history_is_immutable():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY)")
        conn.exec_driver_sql(
            "CREATE TABLE customer_notifications (id INTEGER PRIMARY KEY,title TEXT)"
        )
        conn.exec_driver_sql("INSERT INTO users VALUES (3)")
        conn.exec_driver_sql("INSERT INTO customer_notifications VALUES (9,'Preserved inbox')")
        module = migration(conn)
        module.upgrade()
        assert (
            conn.exec_driver_sql("SELECT title FROM customer_notifications WHERE id=9").scalar_one()
            == "Preserved inbox"
        )
        assert conn.exec_driver_sql("SELECT COUNT(*) FROM inbox_broadcasts").scalar_one() == 0
        conn.exec_driver_sql(
            "INSERT INTO inbox_broadcasts (id,request_key,actor_id,payload_sha256,channel,recipient_count,recipient_ids,notification_ids,title,body,target,created_at) VALUES (1,'synthetic-broadcast',3,?, 'inbox',1,'[3]','[9]','Test','Test','',CURRENT_TIMESTAMP)",
            ("a" * 64,),
        )
        for statement in [
            "UPDATE inbox_broadcasts SET title='changed'",
            "DELETE FROM inbox_broadcasts",
        ]:
            with pytest.raises(sa.exc.IntegrityError):
                conn.exec_driver_sql(statement)
        with pytest.raises(RuntimeError, match="discard history"):
            module.downgrade()
    engine.dispose()


def test_empty_audit_downgrade_preserves_predecessor():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY)")
        module = migration(conn)
        module.upgrade()
        module.downgrade()
        assert set(sa.inspect(conn).get_table_names()) == {"users"}
    engine.dispose()
