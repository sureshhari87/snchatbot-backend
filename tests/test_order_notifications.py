import pytest

from models import CustomerNotification, OrderSnapshot
from order_notifications import enqueue_admin_order_status, enqueue_verified_payment


def seed_order(db, user):
    order = OrderSnapshot(user_id=user.id, order_reference="ORDER-INBOX-TEST", status="placed")
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def test_admin_status_message_is_owned_and_deduplicated(
    client, db, verified_user, admin_user, admin_headers, auth_headers
):
    order = seed_order(db, verified_user)
    path = f"/admin/orders/{order.id}"
    for _ in range(2):
        assert (
            client.patch(path, headers=admin_headers, json={"status": "shipped"}).status_code == 200
        )
    row = db.query(CustomerNotification).one()
    assert row.user_id == verified_user.id
    assert row.created_by == admin_user.id
    assert row.read_at is None
    assert "shipped" in row.body
    own = client.get("/notifications/my", headers=auth_headers).json()
    assert [item["id"] for item in own] == [row.id]
    assert client.get("/notifications/my", headers=admin_headers).json() == []
    assert (
        client.patch(path, headers=admin_headers, json={"status": "delivered"}).status_code == 200
    )
    assert db.query(CustomerNotification).count() == 2


def test_untrusted_and_nonstatus_updates_do_not_notify(
    client, db, verified_user, admin_headers, auth_headers
):
    order = seed_order(db, verified_user)
    path = f"/admin/orders/{order.id}"
    assert client.patch(path, json={"status": "shipped"}).status_code == 401
    assert client.patch(path, headers=auth_headers, json={"status": "shipped"}).status_code == 403
    assert (
        client.patch(path, headers=admin_headers, json={"tracking_number": "TEST"}).status_code
        == 200
    )
    assert client.patch(path, headers=admin_headers, json={"status": "placed"}).status_code == 200
    assert db.query(CustomerNotification).count() == 0


def test_order_message_rolls_back_with_transaction(db, verified_user, admin_user):
    order = seed_order(db, verified_user)
    order.status = "shipped"
    enqueue_admin_order_status(db, order, "placed", admin_user.id)
    db.flush()
    assert db.query(CustomerNotification).count() == 1
    db.rollback()
    assert db.query(CustomerNotification).count() == 0
    db.refresh(order)
    assert order.status == "placed"


def test_payment_message_is_deduplicated_and_rolls_back(db, verified_user):
    order = seed_order(db, verified_user)
    order.payment_status = "verified"
    order.payment_reference = "pay_test"
    enqueue_verified_payment(db, order)
    enqueue_verified_payment(db, order)
    assert db.query(CustomerNotification).count() == 1
    assert db.query(CustomerNotification).one().created_by is None
    db.rollback()
    assert db.query(CustomerNotification).count() == 0
    db.refresh(order)
    assert order.payment_status is None


@pytest.mark.parametrize("status", ["pending", "authorized", "failed"])
def test_unverified_payment_cannot_enqueue(db, verified_user, status):
    order = seed_order(db, verified_user)
    order.payment_status = status
    order.payment_reference = "pay_test"
    with pytest.raises(ValueError):
        enqueue_verified_payment(db, order)
    assert db.query(CustomerNotification).count() == 0
