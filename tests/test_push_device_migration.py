import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text


def test_push_migration_preserves_users_and_guards_nonempty_downgrade(
    test_engine, verified_user, monkeypatch
):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0018_push_devices.py"
    spec = importlib.util.spec_from_file_location("push_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with test_engine.begin() as conn:
        monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(conn)))
        before = conn.execute(text("SELECT count(*) FROM users")).scalar_one()
        module.downgrade()
        module.upgrade()
        assert "push_devices" in inspect(conn).get_table_names()
        assert conn.execute(text("SELECT count(*) FROM users")).scalar_one() == before
        conn.execute(
            text(
                "INSERT INTO push_devices (user_id,family_id,token_digest,push_token,updated_at) "
                "VALUES (:uid,'test-family','test-digest','test-token',CURRENT_TIMESTAMP)"
            ),
            {"uid": verified_user.id},
        )
        with pytest.raises(RuntimeError, match="preservation"):
            module.downgrade()
