"""Real cancellation migration, row-lock races and immutable audit; no provider calls."""

import importlib.util
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

import order_cancellations as service
from models import OrderSnapshot, User
from order_cancellation_models import OrderCancellation, OrderCancellationEvent
from tests.test_payment_concurrency_postgres import local_postgres  # noqa: F401
from tests.test_purchase_identity_postgres import pg  # noqa: F401


@pytest.fixture
def cancellations(request, monkeypatch):
    if os.getenv("PURCHASE_IDENTITY_TEST_POSTGRES_URL"):
        parent = request.getfixturevalue("pg")
    elif os.getenv("RUN_LOCAL_POSTGRES_TESTS") == "1":
        parent = request.getfixturevalue("local_postgres").kw["bind"]
    else:
        pytest.skip("Dedicated disposable PostgreSQL not supplied")
    assert parent.url.host == "127.0.0.1"
    assert (parent.url.username, parent.url.database) in {
        ("identity_ci", "sona_identity_ci"),
        ("postgres", "postgres"),
    }
    schema = "cancellation_test_" + uuid4().hex
    with parent.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = create_engine(parent.url, connect_args={"options": "-c search_path=" + schema})
    monkeypatch.setenv("ORDER_CANCELLATIONS_ENABLED", "1")
    monkeypatch.setenv("ORDER_REFUND_EXECUTION_ENABLED", "0")
    try:
        with engine.begin() as conn:
            User.__table__.create(conn)
            OrderSnapshot.__table__.create(conn)
            conn.exec_driver_sql(
                "CREATE FUNCTION sona_billing_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Immutable cancellation evidence'; END $$"
            )
            path = Path(__file__).parents[1] / "alembic/versions/0039_order_cancellations.py"
            spec = importlib.util.spec_from_file_location("cancellation_pg_migration", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            with Operations.context(MigrationContext.configure(conn)):
                module.upgrade()
        with Session(engine) as db:
            for uid in (1, 2, 3):
                db.add(
                    User(
                        id=uid,
                        username=f"synthetic-cancel-{uid}",
                        email=f"synthetic-cancel-{uid}@example.test",
                        hashed_password="unused",
                        is_admin=uid != 1,
                    )
                )
            db.flush()
            db.add(
                OrderSnapshot(
                    id=1,
                    user_id=1,
                    order_reference="order_synthetic_cancel",
                    status="placed",
                    payment_status="captured",
                    payment_reference="pay_synthetic_cancel",
                    total=500,
                    currency="INR",
                )
            )
            db.commit()
        # Stub only the already-tested funding boundary: these tests exercise
        # PostgreSQL locking/audit, not provider capture or financial posting.
        monkeypatch.setattr(
            service.billing_sources, "evidence", lambda *_: {"tender": {"cash_paise": 50000}}
        )
        yield engine
    finally:
        engine.dispose()
        with parent.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')


def race(engine, operation, inputs):
    barrier = Barrier(2)

    def worker(value):
        with Session(engine) as db:
            barrier.wait(timeout=30)
            order = service.order_for(db, 1, 1)
            try:
                row = operation(db, order, value)
                result = (row.id, row.state)
                db.commit()
                return result
            except HTTPException as error:
                db.rollback()
                assert error.status_code == 409
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        return list(pool.map(worker, inputs))


def propose(db, order, amount):
    deductions = (
        []
        if amount == 50000
        else [
            SimpleNamespace(
                amount_paise=100,
                model_dump=lambda: {
                    "amount_paise": 100,
                    "reason": "Synthetic negotiated deduction",
                },
            )
        ]
    )
    return service.propose(
        db,
        order,
        service.current(db, order),
        2,
        SimpleNamespace(
            version=1, refund_paise=amount, deductions=deductions, reason="Synthetic proposal"
        ),
    )


def seed_proposal(engine):
    with Session(engine) as db:
        order = service.order_for(db, 1, 1)
        service.request(db, order, 1, "Synthetic request")
        propose(db, order, 50000)
        db.commit()


def test_concurrent_requests_keep_one_case_and_audit(cancellations):
    result = race(
        cancellations,
        lambda db, order, _: service.request(db, order, 1, "Synthetic request"),
        [1, 2],
    )
    assert result[0] == result[1]
    with Session(cancellations) as db:
        assert db.query(OrderCancellation).count() == 1
        assert db.query(OrderCancellationEvent).count() == 1
        assert db.get(OrderSnapshot, 1).status == "placed"
        assert db.get(OrderSnapshot, 1).payment_status == "captured"


def test_competing_money_proposals_require_fresh_version(cancellations):
    with Session(cancellations) as db:
        service.request(db, service.order_for(db, 1, 1), 1, "Synthetic request")
        db.commit()
    result = race(cancellations, propose, [50000, 49900])
    assert result.count("conflict") == 1
    with Session(cancellations) as db:
        row = db.query(OrderCancellation).one()
        assert row.version == 2 and row.state == "proposed"
        assert row.cash_paise == 50000 and row.refund_paise in {49900, 50000}
        assert db.query(OrderCancellationEvent).count() == 2
        assert db.get(OrderSnapshot, 1).status == "placed"


def test_accept_dispute_race_records_one_current_decision(cancellations):
    seed_proposal(cancellations)

    def decide(db, order, action):
        return service.decide(
            db,
            order,
            service.current(db, order),
            1,
            SimpleNamespace(version=2, action=action, reason="Synthetic explicit decision"),
        )

    result = race(cancellations, decide, ["accept", "dispute"])
    assert result.count("conflict") == 1
    with Session(cancellations) as db:
        row = db.query(OrderCancellation).one()
        assert row.version == 3 and row.state in {"accepted", "disputed"}
        events = db.query(OrderCancellationEvent).order_by(OrderCancellationEvent.version).all()
        assert [event.version for event in events] == [1, 2, 3]
        assert events[-1].action == ("accept" if row.state == "accepted" else "dispute")
        assert db.get(OrderSnapshot, 1).status == "placed"
        assert db.get(OrderSnapshot, 1).payment_status == "captured"


def test_actual_migration_prevents_audit_update_and_delete(cancellations):
    seed_proposal(cancellations)
    for statement in (
        "UPDATE order_cancellation_events SET detail_json='{}'",
        "DELETE FROM order_cancellation_events",
    ):
        with pytest.raises(DBAPIError) as error:
            with cancellations.begin() as conn:
                conn.execute(text(statement))
        assert "Immutable cancellation evidence" in str(error.value.orig)
    with Session(cancellations) as db:
        assert db.query(OrderCancellationEvent).count() == 2
