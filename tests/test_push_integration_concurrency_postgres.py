"""Real loopback PostgreSQL, mocked send/payment providers only."""

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import text

import main
from models import NotificationSettings, User
from push_outbox import PushEvent, deliver_one
from schemas import NotificationSettingsUpdate
from tests.test_payment_concurrency_postgres import local_postgres  # noqa: F401
from tests.test_payment_concurrency_postgres import (
    test_parallel_verify_and_webhook_inventory_once as run_payment_race,
)
from tests.test_push_outbox_concurrency_postgres import seed


@pytest.mark.parametrize("same_order", [True, False])
def test_enabled_payment_outbox_under_parallel_callbacks(request, monkeypatch, same_order):
    factory = request.getfixturevalue("local_postgres")
    monkeypatch.setenv("PUSH_OUTBOX_ENABLED", "1")
    monkeypatch.setenv("PUSH_DEVICE_REGISTRATION_ENABLED", "1")
    with factory() as db:
        before = db.query(PushEvent).count()
    run_payment_race(factory, monkeypatch, same_order)
    with factory() as db:
        assert db.query(PushEvent).count() - before == (1 if same_order else 2)


def test_preference_patch_waits_for_send_and_applies_before_future_send(request):
    factory = request.getfixturevalue("local_postgres")
    uid, _, aid = seed(factory)
    sending, release, attempted = Event(), Event(), Event()
    pids = []

    def sender(*args):
        sending.set()
        assert release.wait(20)

    def opt_out():
        assert sending.wait(15)
        with factory() as db:
            pids.append(db.execute(text("SELECT pg_backend_pid()")).scalar_one())
            attempted.set()
            user = db.get(User, uid)
            main.update_my_notification_settings(
                NotificationSettingsUpdate(push_enabled=False), user, db
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        send = pool.submit(deliver_one, factory, aid, sender, enabled=True)
        update = pool.submit(opt_out)
        try:
            assert attempted.wait(15)
            deadline = time.monotonic() + 10
            with factory() as observer:
                while not observer.execute(
                    text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": pids[0]}
                ).scalar_one():
                    assert time.monotonic() < deadline, (
                        "Preference update did not acquire owner lock"
                    )
                    time.sleep(0.02)
        finally:
            release.set()
        assert send.result(timeout=15) == "sent"
        update.result(timeout=15)
    with factory() as db:
        assert db.query(NotificationSettings).filter_by(user_id=uid).one().push_enabled is False
