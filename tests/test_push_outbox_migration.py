import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text

from push_outbox import PushAttempt, PushEvent  # noqa: F401


def test_empty_roundtrip_preserves_users_and_nonempty_downgrade_blocked(test_engine, monkeypatch):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0019_push_outbox.py"
    spec = importlib.util.spec_from_file_location("outbox_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with test_engine.begin() as conn:
        monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(conn)))
        before = conn.execute(text("SELECT count(*) FROM users")).scalar_one()
        module.downgrade()
        module.upgrade()
        assert {"push_events", "push_attempts"} <= set(inspect(conn).get_table_names())
        assert conn.execute(text("SELECT count(*) FROM users")).scalar_one() == before
        conn.execute(
            text(
                "INSERT INTO users (id,username,email,hashed_password,is_verified,is_admin) "
                "VALUES (900,'migration','migration@example.test','unused',0,0)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO push_events (event_key,user_id,kind,created_at,expires_at) "
                "VALUES ('test',900,'order_status',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
        with pytest.raises(RuntimeError, match="preservation"):
            module.downgrade()
