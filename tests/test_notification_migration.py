import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect

from models import CustomerNotification


def test_notification_schema_round_trip(test_engine, monkeypatch):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0016_notification_inbox.py"
    spec = importlib.util.spec_from_file_location("notification_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with test_engine.begin() as connection:
        CustomerNotification.__table__.drop(connection)
        tables = set(inspect(connection).get_table_names())
        monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(connection)))
        module.upgrade()
        assert set(inspect(connection).get_table_names()) == tables | {"customer_notifications"}
        module.downgrade()
        assert set(inspect(connection).get_table_names()) == tables
        module.upgrade()
