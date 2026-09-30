"""Real PostgreSQL races using only the disposable loopback test cluster."""

import inspect
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from uuid import uuid4

import pytest
from starlette.requests import Request

import main
from models import RefreshToken, User, utc_now
from push_devices import PushDevice, RegistrationRejected, lock_owner, register_device
from schemas import RefreshTokenRequest
from tests.test_payment_concurrency_postgres import local_postgres  # noqa: F401


@pytest.mark.parametrize("register_first", [True, False])
@pytest.mark.parametrize("all_devices", [True, False])
def test_registration_logout_serialized(request, monkeypatch, register_first, all_devices):
    monkeypatch.setenv("PUSH_DEVICE_REGISTRATION_ENABLED", "1")
    unique = uuid4().hex
    factory = request.getfixturevalue("local_postgres")
    with factory() as db:
        user = User(
            username=unique,
            email=unique + "@example.test",
            hashed_password="unused",
            is_verified=True,
        )
        db.add(user)
        db.flush()
        user_id = user.id
        token = "synthetic-refresh-" + unique
        db.add(
            RefreshToken(
                user_id=user_id,
                token=token,
                family_id=unique,
                is_revoked=False,
                expires_at=utc_now() + timedelta(days=1),
            )
        )
        db.commit()
    locked, attempted = Event(), Event()
    waiting_pid = []

    def worker(first):
        with factory() as db:
            db.execute(main.text("SET lock_timeout = '10s'"))
            if first:
                lock_owner(db, user_id)
                locked.set()
                assert attempted.wait(10)
                deadline = time.monotonic() + 5
                while not db.execute(
                    main.text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"),
                    {"pid": waiting_pid[0]},
                ).scalar_one():
                    assert time.monotonic() < deadline, "Second operation never waited on row lock"
                    time.sleep(0.01)
            else:
                assert locked.wait(10)
                waiting_pid.append(db.execute(main.text("SELECT pg_backend_pid()")).scalar_one())
                attempted.set()
            if first == register_first:
                try:
                    register_device(db, user_id, token, "synthetic-fcm-" + unique)
                    db.commit()
                    return "registered"
                except RegistrationRejected:
                    db.rollback()
                    return "rejected"
            request = Request(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/logout",
                    "headers": [],
                    "client": ("127.0.0.1", 1234),
                }
            )
            user = db.get(User, user_id)
            if all_devices:
                inspect.unwrap(main.logout_all_devices)(request=request, current_user=user, db=db)
            else:
                inspect.unwrap(main.logout)(
                    request=request,
                    req=RefreshTokenRequest(refresh_token=token),
                    current_user=user,
                    db=db,
                )
            return "logged-out"

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(worker, True)
        second = executor.submit(worker, False)
        results = [first.result(timeout=30), second.result(timeout=30)]
    assert "logged-out" in results
    assert ("registered" if register_first else "rejected") in results
    with factory() as db:
        assert db.query(PushDevice).filter_by(user_id=user_id).count() == 0
        assert db.query(RefreshToken).filter_by(user_id=user_id, is_revoked=False).count() == 0
