"""Isolated migration integrity; no connection to staging or production."""

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from database import Base
from models import OrderSnapshot, User


def test_cancellation_migration_preserves_orders_and_locks_audit():
    path = Path("alembic/versions/0039_order_cancellations.py")
    spec = importlib.util.spec_from_file_location("cancellation_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "0038_billing_documents"
    assert len(migration.revision) <= 32
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        Base.metadata.create_all(conn, tables=[User.__table__, OrderSnapshot.__table__])
        conn.execute(
            User.__table__.insert().values(
                id=1,
                username="Synthetic",
                email="synthetic@example.com",
                hashed_password="synthetic",
            )
        )
        conn.execute(
            OrderSnapshot.__table__.insert().values(
                id=1, user_id=1, order_reference="order_Synthetic", total=10, status="placed"
            )
        )
        before = conn.execute(text("SELECT * FROM order_snapshots")).all()
        migration.op = Operations(MigrationContext.configure(conn))
        migration.upgrade()
        assert conn.execute(text("SELECT * FROM order_snapshots")).all() == before
        assert {"order_cancellations", "order_cancellation_events"} <= set(
            inspect(conn).get_table_names()
        )
        conn.execute(
            text(
                "INSERT INTO order_cancellations (id,order_id,user_id,state,version,reason,cash_paise,refund_paise,created_at) VALUES ('case',1,1,'requested',1,'Synthetic',0,0,CURRENT_TIMESTAMP)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO order_cancellation_events (case_id,version,actor_id,action,detail_json,created_at) VALUES ('case',1,1,'requested','{}',CURRENT_TIMESTAMP)"
            )
        )
        for sql in (
            "UPDATE order_cancellation_events SET action='changed'",
            "DELETE FROM order_cancellation_events",
        ):
            with pytest.raises(IntegrityError, match="immutable"):
                conn.exec_driver_sql(sql)
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO order_cancellations (id,order_id,user_id,state,version,reason,cash_paise,refund_paise,created_at) VALUES ('duplicate',1,1,'requested',1,'Synthetic',0,0,CURRENT_TIMESTAMP)"
                )
            )
        with pytest.raises(RuntimeError, match="preserved"):
            migration.downgrade()
        assert (
            conn.execute(text("SELECT count(*) FROM order_cancellation_events")).scalar_one() == 1
        )
    engine.dispose()
