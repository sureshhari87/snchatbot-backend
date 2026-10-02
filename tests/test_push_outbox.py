from datetime import timedelta

import pytest
from sqlalchemy.orm import sessionmaker

from models import NotificationSettings, RefreshToken, User, utc_now
from push_devices import PushDevice, detach_all, register_device
from push_outbox import (
    InvalidDeviceError,
    PermanentDeliveryError,
    PushAttempt,
    PushEvent,
    RetryableDeliveryError,
    deliver_one,
    enqueue,
    run_batch,
)


@pytest.fixture
def queued(test_engine):
    factory = sessionmaker(bind=test_engine, autoflush=False)
    with factory() as db:
        user = User(
            username="push-test",
            email="push@example.test",
            hashed_password="unused",
            is_verified=True,
        )
        db.add(user)
        db.flush()
        uid = user.id
        db.add(
            RefreshToken(
                user_id=uid,
                token="synthetic-refresh",
                family_id="push-family",
                is_revoked=False,
                expires_at=utc_now() + timedelta(days=1),
            )
        )
        db.add(NotificationSettings(user_id=uid, push_enabled=True, order_updates_enabled=True))
        db.commit()
        register_device(db, uid, "synthetic-refresh", "synthetic-push-token-0001")
        enqueue(db, user_id=uid, event_key="payment:1", kind="verified_payment", enabled=True)
        db.commit()
        aid = db.query(PushAttempt.id).scalar()
    return factory, uid, aid


def test_default_off_and_success_deduplicated(queued):
    factory, uid, aid = queued
    sent = []

    def sender(token, data):
        sent.append((token, data))

    assert run_batch(factory, sender) == []
    assert deliver_one(factory, aid, sender) == "disabled"
    with factory() as db:
        assert enqueue(db, user_id=uid, event_key="off", kind="order_status") is None
        enqueue(db, user_id=uid, event_key="payment:1", kind="verified_payment", enabled=True)
        db.commit()
        assert db.query(PushEvent).count() == db.query(PushAttempt).count() == 1
    assert run_batch(factory, sender, enabled=True) == ["sent"]
    assert deliver_one(factory, aid, sender, enabled=True) == "not_due"
    assert len(sent) == 1
    assert sent[0][1] == {"user_id": str(uid), "event_id": "1", "type": "verified_payment"}


def test_queue_rolls_back_and_kind_conflict(queued):
    factory, uid, _ = queued
    with factory() as db:
        enqueue(db, user_id=uid, event_key="order:2", kind="order_status", enabled=True)
        db.rollback()
        assert db.query(PushEvent).count() == 1
        with pytest.raises(ValueError, match="conflict"):
            enqueue(db, user_id=uid, event_key="payment:1", kind="order_status", enabled=True)


@pytest.mark.parametrize(
    "change", ["logout", "rotation", "expired_session", "opt_out", "event_expired", "unverified"]
)
def test_revalidates_before_send(queued, change):
    factory, uid, aid = queued
    with factory() as db:
        if change == "logout":
            detach_all(db, uid)
        elif change == "rotation":
            register_device(db, uid, "synthetic-refresh", "synthetic-new-token-0002")
        elif change == "expired_session":
            db.query(RefreshToken).one().expires_at = utc_now() - timedelta(seconds=1)
        elif change == "opt_out":
            db.query(NotificationSettings).one().push_enabled = False
        elif change == "event_expired":
            db.query(PushEvent).one().expires_at = utc_now() - timedelta(seconds=1)
        else:
            db.get(User, uid).is_verified = False
        db.commit()

    def forbidden(*args):
        pytest.fail("Ineligible recipient reached sender")

    assert deliver_one(factory, aid, forbidden, enabled=True) == "cancelled"
    if change == "rotation":
        with factory() as db:
            assert db.query(PushDevice).one().push_token == "synthetic-new-token-0002"


@pytest.mark.parametrize(
    "error,state", [(InvalidDeviceError, "cancelled"), (PermanentDeliveryError, "failed")]
)
def test_terminal_provider_errors(queued, error, state):
    factory, _, aid = queued

    def reject(*args):
        raise error("must not be persisted")

    assert deliver_one(factory, aid, reject, enabled=True) == state
    with factory() as db:
        assert db.query(PushDevice).count() == (0 if error is InvalidDeviceError else 1)
        assert "must not" not in db.get(PushAttempt, aid).reason


def test_bounded_retry_and_no_early_retry(queued):
    factory, _, aid = queued
    calls = []

    def retry(*args):
        calls.append(1)
        raise RetryableDeliveryError("private provider message")

    for n in range(1, 6):
        assert deliver_one(factory, aid, retry, enabled=True) == ("failed" if n == 5 else "pending")
        assert deliver_one(factory, aid, retry, enabled=True) == "not_due"
        with factory() as db:
            row = db.get(PushAttempt, aid)
            assert row.attempts == n
            assert row.reason == "provider_retry"
            row.due_at = utc_now() - timedelta(seconds=1)
            db.commit()
    assert len(calls) == 5


def test_quiet_hours_defer_without_attempt_then_recheck_optout(queued, monkeypatch):
    from datetime import datetime

    import push_outbox

    factory, _, aid = queued
    now = datetime(2030, 1, 1, 17, 0)  # 22:30 India time
    monkeypatch.setattr(push_outbox, "utc_now", lambda: now)
    with factory() as db:
        pref = db.query(NotificationSettings).one()
        pref.quiet_hours_start, pref.quiet_hours_end = "22:00", "07:00"
        db.query(RefreshToken).one().expires_at = now + timedelta(days=1)
        db.query(PushEvent).one().expires_at = now + timedelta(days=1)
        db.commit()

    def forbidden(*args):
        pytest.fail("Quiet/opted-out message reached sender")

    assert deliver_one(factory, aid, forbidden, enabled=True) == "deferred"
    with factory() as db:
        row = db.get(PushAttempt, aid)
        assert row.due_at == datetime(2030, 1, 2, 1, 30)
        assert row.attempts == 0
        db.query(NotificationSettings).one().push_enabled = False
        db.commit()
    monkeypatch.setattr(push_outbox, "utc_now", lambda: datetime(2030, 1, 2, 1, 30))
    assert deliver_one(factory, aid, forbidden, enabled=True) == "cancelled"


def test_provider_configuration_failure_stops_batch_without_spending_attempts(queued):
    from push_outbox import DeliveryUnavailableError

    factory, uid, aid = queued
    with factory() as db:
        enqueue(db, user_id=uid, event_key="status:2", kind="order_status", enabled=True)
        db.commit()
    calls = []

    def blocked(*args):
        calls.append(1)
        raise DeliveryUnavailableError("Provider configuration unavailable")

    assert run_batch(factory, blocked, enabled=True) == ["provider_unavailable"]
    assert calls == [1]
    with factory() as db:
        assert all(row.attempts == 0 and row.state == "pending" for row in db.query(PushAttempt))


def test_provider_retry_after_is_honoured(queued):
    factory, _, aid = queued
    started = utc_now()

    def limited(*args):
        raise RetryableDeliveryError("Quota", retry_after=120)

    assert deliver_one(factory, aid, limited, enabled=True) == "pending"
    with factory() as db:
        assert db.get(PushAttempt, aid).due_at >= started + timedelta(seconds=120)


def test_unallowlisted_recipient_is_not_sent_or_mutated(queued):
    factory, uid, aid = queued
    def forbidden(*args):
        pytest.fail("Unapproved recipient reached sender")
    assert run_batch(factory, forbidden, enabled=True, allowed_user_ids={uid + 1}) == []
    assert deliver_one(factory, aid, forbidden, enabled=True,
                       allowed_user_ids=set()) == "recipient_not_allowed"
    with factory() as db:
        row = db.get(PushAttempt, aid)
        assert row.state == "pending" and row.attempts == 0 and row.reason is None
    assert run_batch(factory, lambda *args: None, enabled=True,
                     allowed_user_ids={uid}) == ["sent"]
