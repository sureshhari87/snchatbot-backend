"""Restock inbox guarantees, using only the isolated test database."""

import pytest

from main import notify_back_in_stock_subscribers
from models import BackInStockSubscription, CustomerNotification, Product
from stock_alerts import notify_subscribers


def subscription(db, user, *, status="active", public=True):
    product = db.query(Product).first()
    product.in_stock = False
    product.stock_quantity = 0
    product.source_data = {"active": public}
    row = BackInStockSubscription(
        user_id=user.id,
        product_id=product.id,
        status=status,
        size="12",
        variant="yellow",
        email="synthetic@example.com",
    )
    db.add(row)
    db.commit()
    return product, row


def messages(db):
    return db.query(CustomerNotification).filter(
        CustomerNotification.deduplication_key.like("stock-alert:%")
    )


def test_restock_posts_once_without_external_delivery(db, verified_user, monkeypatch):
    product, row = subscription(db, verified_user)
    monkeypatch.setattr("main.send_email", lambda *a, **kw: pytest.fail("Unexpected email"))
    product.in_stock, product.stock_quantity = True, 2
    assert notify_back_in_stock_subscribers(db, product) == 1
    db.commit()
    assert notify_back_in_stock_subscribers(db, product) == 0
    db.commit()
    message = messages(db).one()
    assert message.user_id == verified_user.id
    assert message.target == "/stock-alerts"
    assert message.deduplication_key == f"stock-alert:{row.id}"
    assert "12" in message.body and "yellow" in message.body
    assert "does not reserve" in message.body
    assert row.status == "notified" and row.notified_at is not None


@pytest.mark.parametrize(
    "public,in_stock,quantity",
    [
        (False, True, 2),
        (True, False, 2),
        (True, True, 0),
    ],
)
def test_unavailable_product_never_consumes_subscription(
    db, verified_user, public, in_stock, quantity
):
    product, row = subscription(db, verified_user, public=public)
    product.in_stock, product.stock_quantity = in_stock, quantity
    assert notify_subscribers(db, product) == 0
    db.commit()
    assert row.status == "active"
    assert messages(db).count() == 0


def test_cancelled_subscription_receives_no_message(db, verified_user):
    product, row = subscription(db, verified_user, status="cancelled")
    product.in_stock, product.stock_quantity = True, 2
    assert notify_subscribers(db, product) == 0
    db.commit()
    assert row.status == "cancelled" and messages(db).count() == 0


def test_rollback_preserves_waiting_alert_and_old_stock(db, verified_user):
    product, row = subscription(db, verified_user)
    product_id, row_id = product.id, row.id
    product.in_stock, product.stock_quantity = True, 3
    assert notify_subscribers(db, product) == 1
    db.rollback()
    db.expire_all()
    assert db.get(Product, product_id).stock_quantity == 0
    assert db.get(BackInStockSubscription, row_id).status == "active"
    assert messages(db).count() == 0


def test_existing_inbox_key_does_not_duplicate_or_depend_on_email(db, verified_user):
    product, row = subscription(db, verified_user)
    db.add(
        CustomerNotification(
            user_id=verified_user.id,
            deduplication_key=f"stock-alert:{row.id}",
            title="Back in stock",
            body="Existing saved event",
            target="/stock-alerts",
        )
    )
    db.commit()
    product.in_stock, product.stock_quantity = True, 1
    assert notify_subscribers(db, product) == 1
    db.commit()
    assert messages(db).count() == 1
    assert row.status == "notified"


def test_generic_product_editor_triggers_same_inbox(client, auth_headers, admin_headers, db):
    product = db.query(Product).first()
    product.in_stock, product.stock_quantity = False, 0
    db.commit()
    alert = client.post(f"/products/{product.id}/back-in-stock", headers=auth_headers, json={})
    assert alert.status_code == 200
    response = client.patch(
        f"/admin/products/{product.id}", headers=admin_headers, json={"stock_quantity": 2}
    )
    assert response.status_code == 200
    assert messages(db).count() == 1
    retry = client.post(f"/admin/products/{product.id}/back-in-stock/notify", headers=admin_headers)
    assert retry.status_code == 200 and retry.json()["notified_count"] == 0
    assert messages(db).count() == 1


def test_inventory_flow_saves_inbox_for_opted_in_owner_only(
    client, auth_headers, admin_headers, db, admin_user
):
    product = db.query(Product).first()
    product.in_stock, product.stock_quantity = False, 0
    db.commit()
    alert = client.post(f"/products/{product.id}/back-in-stock", headers=auth_headers, json={})
    assert alert.status_code == 200
    repeated = client.post(f"/products/{product.id}/back-in-stock", headers=auth_headers, json={})
    assert repeated.json()["id"] == alert.json()["id"]
    forbidden = client.delete(f"/back-in-stock/{alert.json()['id']}", headers=admin_headers)
    assert forbidden.status_code == 404
    result = client.patch(
        f"/admin/products/{product.id}/inventory", headers=admin_headers, json={"stock_quantity": 2}
    )
    assert result.status_code == 200
    assert messages(db).count() == 1
    assert messages(db).filter_by(user_id=admin_user.id).count() == 0


def test_catalogue_editor_restock_triggers_inbox(client, auth_headers, admin_headers, db):
    product = db.query(Product).first()
    product.in_stock, product.stock_quantity = False, 0
    db.commit()
    result = client.post(f"/products/{product.id}/back-in-stock", headers=auth_headers, json={})
    assert result.status_code == 200
    response = client.patch(
        f"/admin/catalogue/products/{product.id}",
        headers=admin_headers,
        json={"stock": 2, "price": 500, "useDynamicPricing": False, "active": True},
    )
    assert response.status_code == 200
    assert messages(db).count() == 1


def test_new_restock_cycle_requires_a_new_opt_in(db, verified_user):
    product, row = subscription(db, verified_user)
    product.in_stock, product.stock_quantity = True, 1
    assert notify_subscribers(db, product) == 1
    db.commit()
    product.in_stock, product.stock_quantity = False, 0
    db.commit()
    product.in_stock, product.stock_quantity = True, 2
    assert notify_subscribers(db, product) == 0
    db.commit()
    assert messages(db).count() == 1
