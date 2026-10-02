"""Opt-in disposable loopback PostgreSQL; no Neon or provider access."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from uuid import uuid4

import pytest

from models import NotificationSettings, RefreshToken, User, utc_now
from push_devices import detach_all, register_device
from push_outbox import PushAttempt, deliver_one, enqueue
from tests.test_payment_concurrency_postgres import local_postgres  # noqa: F401


def seed(factory):
    key = uuid4().hex
    with factory() as db:
        user = User(
            username=key, email=key + "@example.test", hashed_password="unused", is_verified=True
        )
        db.add(user)
        db.flush()
        uid = user.id
        db.add(
            RefreshToken(
                user_id=uid,
                token=key,
                family_id=key,
                is_revoked=False,
                expires_at=utc_now() + timedelta(days=1),
            )
        )
        db.add(NotificationSettings(user_id=uid, push_enabled=True, order_updates_enabled=True))
        db.commit()
        register_device(db, uid, key, "synthetic-token-" + key)
        eid = enqueue(db, user_id=uid, event_key=key, kind="order_status", enabled=True)
        db.commit()
        aid = db.query(PushAttempt.id).filter_by(event_id=eid).scalar()
    return uid, key, aid


def test_second_worker_skips_locked_owner_and_cannot_send_twice(request):
    factory = request.getfixturevalue("local_postgres")
    _, _, aid = seed(factory)
    entered, release = Event(), Event()
    calls = []

    def sender(*args):
        calls.append(1)
        entered.set()
        assert release.wait(10)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(deliver_one, factory, aid, sender, enabled=True)
        try:
            assert entered.wait(10)
            second = pool.submit(deliver_one, factory, aid, sender, enabled=True)
            assert second.result(timeout=5) == "busy"
        finally:
            release.set()
        assert first.result(timeout=10) == "sent"
    assert calls == [1]
    assert deliver_one(factory, aid, sender, enabled=True) == "not_due"


@pytest.mark.parametrize("rotate", [True, False])
def test_logout_or_rotation_wins_before_worker(request, rotate):
    factory = request.getfixturevalue("local_postgres")
    uid, key, aid = seed(factory)

    def forbidden(*args):
        pytest.fail("Stale registration reached sender")

    with factory() as db:
        if rotate:
            register_device(db, uid, key, "replacement-token-" + key)
        else:
            detach_all(db, uid)
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(deliver_one, factory, aid, forbidden, enabled=True)
            assert result.result(timeout=5) == "busy"
        db.commit()
    assert deliver_one(factory, aid, forbidden, enabled=True) == "cancelled"
