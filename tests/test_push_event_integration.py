import pytest

from models import CustomerNotification
from order_notifications import enqueue_admin_order_status, enqueue_verified_payment
from push_outbox import PushEvent
from tests.test_order_notifications import seed_order


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("PUSH_DEVICE_REGISTRATION_ENABLED", "1")
    monkeypatch.setenv("PUSH_OUTBOX_ENABLED", "1")
    monkeypatch.setenv("PUSH_DELIVERY_ENABLED", "0")


def test_new_verified_event_atomic_and_idempotent(db, verified_user, enabled):
    order = seed_order(db, verified_user)
    order.payment_status, order.payment_reference = "verified", "synthetic-payment"
    enqueue_verified_payment(db, order)
    enqueue_verified_payment(db, order)
    assert db.query(PushEvent).count() == db.query(CustomerNotification).count() == 1
    assert db.query(PushEvent).one().kind == "verified_payment"
    db.rollback()
    assert db.query(PushEvent).count() == db.query(CustomerNotification).count() == 0


def test_enabling_does_not_replay_old_inbox(db, verified_user, monkeypatch, enabled):
    monkeypatch.setenv("PUSH_OUTBOX_ENABLED", "0")
    order = seed_order(db, verified_user)
    order.payment_status, order.payment_reference = "verified", "synthetic-payment"
    enqueue_verified_payment(db, order)
    db.commit()
    monkeypatch.setenv("PUSH_OUTBOX_ENABLED", "1")
    enqueue_verified_payment(db, order)
    db.commit()
    assert db.query(PushEvent).count() == 0
    assert db.query(CustomerNotification).count() == 1


def test_status_producer_notifies_once_and_rolls_back(db, verified_user, admin_user, enabled):
    order = seed_order(db, verified_user)
    order.status = "processing"
    enqueue_admin_order_status(db, order, "placed", admin_user.id)
    enqueue_admin_order_status(db, order, "placed", admin_user.id)
    assert db.query(PushEvent).count() == db.query(CustomerNotification).count() == 1
    db.rollback()
    assert db.query(PushEvent).count() == db.query(CustomerNotification).count() == 0


def test_admin_inbox_key_cannot_invent_trusted_push(
    client, db, verified_user, admin_headers, enabled
):
    response = client.post(
        "/admin/notifications",
        headers=admin_headers,
        json={
            "user_id": verified_user.id,
            "deduplication_key": "payment-verified:1234",
            "title": "Payment confirmed",
            "body": "Synthetic test",
        },
    )
    assert response.status_code == 201
    assert db.query(CustomerNotification).count() == 1
    assert db.query(PushEvent).count() == 0


def test_admin_status_endpoint_produces_queue(client, db, verified_user, admin_headers, enabled):
    order = seed_order(db, verified_user)
    for _ in range(2):
        response = client.patch(
            f"/admin/orders/{order.id}", headers=admin_headers, json={"status": "processing"}
        )
        assert response.status_code == 200
    assert db.query(PushEvent).count() == 1


def test_new_preferences_patch_has_no_intermediate_commit(db, verified_user, monkeypatch):
    import main
    from schemas import NotificationSettingsUpdate

    real_commit = db.commit
    commits = []

    def commit():
        commits.append(1)
        real_commit()

    monkeypatch.setattr(db, "commit", commit)
    result = main.update_my_notification_settings(
        NotificationSettingsUpdate(push_enabled=False), verified_user, db
    )
    assert result.push_enabled is False
    assert commits == [1]


def test_reconciliation_queue_failure_rolls_back_financial_changes(
    db, verified_user, monkeypatch, enabled
):
    import main
    import order_notifications
    from models import Product
    from schemas import OrderSyncRequest

    product = db.query(Product).first()
    before = product.stock_quantity
    order = main.upsert_local_order_snapshot(
        db,
        verified_user,
        OrderSyncRequest(
            order_reference="order_queue_failure",
            status="payment_pending",
            total=100,
            currency="INR",
            payment_status="pending",
            source="razorpay_checkout",
            items=[
                {
                    "product_id": f"snchatbot_{product.id}",
                    "backend_product_id": product.id,
                    "name": "Synthetic",
                    "qty": 1,
                    "price": 100,
                }
            ],
        ),
    )
    db.commit()
    payment = {"id": "pay_queue_failure", "status": "captured", "amount": 10000, "currency": "INR"}
    monkeypatch.setattr(main, "call_razorpay", lambda *a, **kw: (200, {"items": [payment]}))
    monkeypatch.setattr(main, "fetch_razorpay_payment", lambda *a, **kw: (200, payment))

    def fail(*args, **kwargs):
        raise ValueError("Synthetic queue failure")

    monkeypatch.setattr(order_notifications, "enqueue", fail)
    result = main.reconcile_razorpay_orders(db)
    db.commit()  # The admin endpoint commits the failure audit too.
    db.expire_all()
    assert result["failed"] == 1
    assert result["finalized"] == 0
    assert product.stock_quantity == before
    assert order.payment_status == "pending"
    assert db.query(CustomerNotification).count() == db.query(PushEvent).count() == 0
