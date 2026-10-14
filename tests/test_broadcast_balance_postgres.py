"""Opt-in synthetic records on disposable loopback PostgreSQL only."""

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import jwt
from custom_balance_fakes import Provider
from test_payment_concurrency_postgres import local_postgres as _pg

import config
import custom_design_api as designs
import custom_design_balance as balance
import inbox_broadcast_api as broadcasts
from custom_design_balance_api import BalanceInput, CompleteInput, ManualInput
from custom_design_balance_models import CustomDesignBalanceEntry as Entry
from custom_design_models import CustomDesignAudit
from inbox_broadcast_models import InboxBroadcast
from models import CustomerNotification, User
from push_outbox import PushEvent

local_postgres = _pg


def people(factory, label):
    with factory() as db:
        users = [
            User(
                username=label + str(i),
                email=label + str(i) + "@example.test",
                hashed_password="synthetic-only",
                is_verified=True,
                is_admin=i == 0,
            )
            for i in range(3)
        ]
        db.add_all(users)
        db.commit()
        return [u.id for u in users]


def parallel(operation):
    barrier = Barrier(2)

    def worker(i):
        barrier.wait(timeout=30)
        return operation(i)

    with ThreadPoolExecutor(max_workers=2) as pool:
        return list(pool.map(worker, range(2)))


def plan(factory, label, mode):
    ids = people(factory, label)
    provider = Provider()
    with factory() as db:
        owner, admin = db.get(User, ids[1]), db.get(User, ids[0])
        created = designs.create(
            db,
            owner,
            designs.CreateInput(
                request_key=label,
                description="Synthetic ring",
                metal="Gold",
                purity="22K",
                budget_range="Synthetic",
            ),
            provider,
        )
        db.commit()
        did = created["id"]
        quote = designs.publish(
            db,
            did,
            admin,
            designs.QuoteInput(request_key=label, total_paise=50000, advance_paise=0),
        )
        db.commit()
        designs.decide(
            db,
            did,
            owner,
            designs.DecisionInput(request_key=label, quote_id=quote["id"], action="approved"),
        )
        db.commit()
        req = balance.request_balance(
            db, did, owner.id, BalanceInput(request_key=label, quote_id=quote["id"], mode=mode)
        )
        db.commit()
        return did, req.id, ids, provider


def test_parallel_broadcast_has_one_snapshot_and_no_push(local_postgres, monkeypatch):
    factory = local_postgres
    monkeypatch.setattr(config, "APP_ENV", "staging")
    ids = people(factory, "pg-broadcast")
    value = broadcasts.BroadcastInput(
        request_key="pg-broadcast-key",
        recipient_ids=ids[1:],
        title="Synthetic",
        body="Synthetic inbox only",
    )
    sha = broadcasts.digest(broadcasts.canonical(value))
    token = jwt.encode(
        {
            "aud": broadcasts.AUDIENCE,
            "exp": int(time.time()) + 600,
            "actor_id": ids[0],
            "payload_sha256": sha,
            "environment": "staging",
        },
        config.SECRET_KEY,
        algorithm=config.ALGORITHM,
    )
    payload = broadcasts.ConfirmInput(**value.model_dump(), preview_token=token)

    def operation(i):
        with factory() as db:
            return broadcasts.confirm(db, db.get(User, ids[0]), payload)

    results = parallel(operation)
    assert sum(created for result, created in results) == 1
    assert results[0][0]["id"] == results[1][0]["id"]
    with factory() as db:
        assert db.query(InboxBroadcast).filter_by(request_key=value.request_key).count() == 1
        assert (
            db.query(CustomerNotification).filter(CustomerNotification.user_id.in_(ids[1:])).count()
            == 2
        )
        assert db.query(PushEvent).count() == 0


def test_parallel_manual_balance_and_completion_post_once(local_postgres, monkeypatch):
    factory = local_postgres
    monkeypatch.setattr(balance, "ENABLED", True)
    did, rid, ids, provider = plan(factory, "pg-manual", "manual")
    payload = ManualInput(
        request_id=rid, reference="PG-SYNTHETIC-CASH", reason="Synthetic verified receipt"
    )

    def operation(i):
        with factory() as db:
            result = balance.manual_post(db, did, db.get(User, ids[0]), payload)
            db.commit()
            return result.id

    assert len(set(parallel(operation))) == 1
    with factory() as db:
        row = designs.locked(db, did)
        designs.audit(db, row, "pg-manufacturing", "manufacturing_started", ids[0], "0" * 64)
        db.commit()
    complete = CompleteInput(request_key="pg-complete", reason="Synthetic completion")

    def finish(i):
        with factory() as db:
            balance.complete(db, did, db.get(User, ids[0]), complete, provider)
            db.commit()

    parallel(finish)
    with factory() as db:
        assert db.query(Entry).filter_by(design_id=did).count() == 1
        assert (
            db.query(CustomDesignAudit).filter_by(design_id=did, action="balance_posted").count()
            == 1
        )
        assert db.query(CustomDesignAudit).filter_by(design_id=did, action="completed").count() == 1


def test_parallel_online_capture_posts_one_balance(local_postgres, monkeypatch):
    factory = local_postgres
    monkeypatch.setattr(balance, "ENABLED", True)
    monkeypatch.setattr(balance, "ONLINE", True)
    did, rid, ids, provider = plan(factory, "pg-online", "razorpay")
    with factory() as db:
        attempt, request = balance.checkout(db, did, ids[1], provider)
        db.commit()
        aid, oid = attempt.id, attempt.provider_order_id
    pid = provider.payment(oid)

    def operation(i):
        with factory() as db:
            entry = balance.capture_post(db, did, aid, pid, provider)
            db.commit()
            return entry.id

    assert len(set(parallel(operation))) == 1
    with factory() as db:
        assert db.query(Entry).filter_by(design_id=did).count() == 1
        assert (
            db.query(CustomDesignAudit).filter_by(design_id=did, action="balance_posted").count()
            == 1
        )
    assert provider.posts == 1


def test_postgres_financial_and_broadcast_records_are_immutable(local_postgres):
    import importlib.util
    from pathlib import Path

    import pytest
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text
    from sqlalchemy.exc import DatabaseError

    factory = local_postgres
    with factory() as db:
        for filename in ["0031_inbox_broadcast.py", "0032_custom_design_balances.py"]:
            path = Path("alembic/versions") / filename
            spec = importlib.util.spec_from_file_location("guard_" + filename, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.op = Operations(MigrationContext.configure(db.connection()))
            module.immutable_guards()
        db.commit()
    for statement in [
        "UPDATE inbox_broadcasts SET title=title",
        "DELETE FROM inbox_broadcasts",
        "UPDATE custom_design_balance_requests SET amount_paise=amount_paise",
        "DELETE FROM custom_design_balance_requests",
        "UPDATE custom_design_balance_entries SET amount_paise=amount_paise",
        "DELETE FROM custom_design_balance_entries",
        "UPDATE custom_design_balance_checkouts SET receipt='changed'",
        "DELETE FROM custom_design_balance_checkouts",
    ]:
        with factory() as db:
            with pytest.raises(DatabaseError, match="immutable|lifecycle|guard"):
                db.execute(text(statement))
            db.rollback()
