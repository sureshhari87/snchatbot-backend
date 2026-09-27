import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text


def test_system_sender_migration_preserves_rows_and_guards_downgrade(
    test_engine, verified_user, monkeypatch
):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0017_system_notifications.py"
    spec = importlib.util.spec_from_file_location("system_notification_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with test_engine.begin() as connection:
        monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(connection)))
        module.downgrade()
        column = next(
            c
            for c in inspect(connection).get_columns("customer_notifications")
            if c["name"] == "created_by"
        )
        assert column["nullable"] is False
        connection.execute(
            text(
                "INSERT INTO customer_notifications "
                "(user_id,created_by,deduplication_key,title,body,target,created_at) "
                "VALUES (:uid,:uid,'migration-human','Human','Preserve me','',CURRENT_TIMESTAMP)"
            ),
            {"uid": verified_user.id},
        )
        module.upgrade()
        column = next(
            c
            for c in inspect(connection).get_columns("customer_notifications")
            if c["name"] == "created_by"
        )
        assert column["nullable"] is True
        assert (
            connection.execute(text("SELECT created_by FROM customer_notifications")).scalar_one()
            == verified_user.id
        )
        module.downgrade()
        module.upgrade()
        connection.execute(
            text(
                "INSERT INTO customer_notifications "
                "(user_id,created_by,deduplication_key,title,body,target,created_at) "
                "VALUES (:uid,NULL,'migration-system','System','Preserve me','',CURRENT_TIMESTAMP)"
            ),
            {"uid": verified_user.id},
        )
        with pytest.raises(RuntimeError, match="preservation plan"):
            module.downgrade()
        assert (
            connection.execute(text("SELECT count(*) FROM customer_notifications")).scalar_one()
            == 2
        )
