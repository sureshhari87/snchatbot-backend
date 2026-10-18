from types import SimpleNamespace

import pytest

from models import OrderSnapshot, OrderSnapshotItem, Product, User, utc_now
from review_purchase import eligible_order, verified
from rewards_vouchers_reservation_models import FinancialReservation, FinancialReservationEvent


@pytest.fixture
def purchase(db):
    user = User(username="review-buyer", email="buyer@example.test", hashed_password="unused")
    db.add(user)
    db.flush()
    product = db.query(Product).first()
    order = OrderSnapshot(
        user_id=user.id,
        order_reference="review-test",
        status="delivered",
        payment_status="verified",
        payment_reference="test_capture",
        source="razorpay_checkout",
    )
    db.add(order)
    db.flush()
    item = OrderSnapshotItem(order_id=order.id, backend_product_id=product.id, name="Test", qty=1)
    reservation = FinancialReservation(
        user_id=user.id,
        request_key="review",
        request_sha256="a" * 64,
        receipt="review",
        quote_json="{}",
        payable_paise=100,
        reward_points=0,
        voucher_paise=0,
        state="posted",
        order_id=order.id,
        provider_order_id="test_order",
        provider_payment_id="test_capture",
        created_at=utc_now(),
    )
    db.add_all([item, reservation])
    db.flush()
    event = FinancialReservationEvent(
        reservation_id=reservation.id,
        event_key="review-posted",
        action="payment_posted",
        evidence_sha256="b" * 64,
        created_at=utc_now(),
    )
    db.add(event)
    db.commit()
    return user, product, order, item, reservation, event


def test_explicit_binding_and_isolation(db, purchase):
    user, product, order, *_ = purchase
    review = SimpleNamespace(user_id=user.id, product_id=product.id, verified_order_id=None)
    assert eligible_order(db, user.id, product.id) == order.id
    assert not verified(db, review)
    review.verified_order_id = order.id
    assert verified(db, review)
    review.user_id += 1000
    assert not verified(db, review)


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "refunded"),
        ("status", "returned"),
        ("status", "placed"),
        ("payment_status", "pending"),
        ("source", "legacy"),
        ("payment_reference", "other_capture"),
    ],
)
def test_ineligible_states(db, purchase, field, value):
    user, product, order, *_ = purchase
    setattr(order, field, value)
    db.commit()
    assert eligible_order(db, user.id, product.id) is None


@pytest.mark.parametrize("missing", ["event", "owner", "product", "quantity"])
def test_incomplete_evidence(db, purchase, missing):
    user, product, order, item, reservation, event = purchase
    if missing == "event":
        db.delete(event)
    elif missing == "owner":
        reservation.user_id += 1000
    elif missing == "product":
        item.backend_product_id = None
        item.product_reference = str(product.id)
    else:
        item.qty = 0
    db.commit()
    assert eligible_order(db, user.id, product.id) is None


@pytest.mark.parametrize("kind", ["refund", "dispute", "admin_review"])
def test_hold_revokes_badge(db, purchase, kind):
    from rewards_vouchers_models import RewardAccount, RewardVoucherHold

    user, product, order, *_ = purchase
    review = SimpleNamespace(user_id=user.id, product_id=product.id, verified_order_id=order.id)
    assert verified(db, review)
    db.add(RewardAccount(user_id=user.id, created_at=utc_now()))
    db.flush()
    db.add(
        RewardVoucherHold(
            reward_user_id=user.id,
            kind=kind,
            reference="test-hold",
            reason="Test evidence",
            created_at=utc_now(),
        )
    )
    db.commit()
    assert not verified(db, review)
    assert review.verified_order_id == order.id


def test_api_selects_binding_and_never_exposes_order(client, db, auth_headers, purchase):
    from models import ProductReview

    user, product, order, *_ = purchase

    # Use the authenticated identity from the existing fixture, not a client claim.
    def content():
        return {"rating": 5, "body": "Comfortable and well made."}

    path = f"/products/{product.id}/reviews"
    forged = client.post(
        path, headers=auth_headers, json={**content(), "verified_order_id": order.id}
    )
    assert forged.status_code == 422
    response = client.post(path, headers=auth_headers, json=content())
    assert response.status_code == 201
    assert response.json()["verifiedPurchase"] is False
    assert "verified_order_id" not in response.json()
    row = db.get(ProductReview, int(response.json()["id"]))
    order.user_id = row.user_id
    purchase[4].user_id = row.user_id
    db.commit()
    # Existing review must not silently gain a badge from newly matching evidence.
    assert client.get(path + "/me", headers=auth_headers).json()["verifiedPurchase"] is False

    client.delete(f"/reviews/{row.id}", headers=auth_headers)
    created = client.post(path, headers=auth_headers, json=content())
    assert created.status_code == 201
    assert created.json()["verifiedPurchase"] is True
    order.status = "returned"
    db.commit()
    assert client.get(path + "/me", headers=auth_headers).json()["verifiedPurchase"] is False


def test_purchase_migration_preserves_legacy_rows(test_engine, monkeypatch):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text

    path = Path(__file__).resolve().parents[1] / "alembic/versions/0035_review_purchase.py"
    spec = importlib.util.spec_from_file_location("purchase_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with test_engine.begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.downgrade()
        connection.execute(
            text(
                "INSERT INTO product_reviews (id, product_id, user_id, rating, title, body, status, display_name, fit, size_feedback, created_at, updated_at) VALUES (987, 1, 1, 5, 'Legacy', 'Legacy review body', 'approved', 'Sona customer', 'notApplicable', 'notApplicable', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        migration.upgrade()
        row = connection.execute(
            text("SELECT title, verified_order_id FROM product_reviews WHERE id=987")
        ).one()
        assert tuple(row) == ("Legacy", None)
        migration.downgrade()
        assert (
            connection.execute(text("SELECT title FROM product_reviews WHERE id=987")).scalar_one()
            == "Legacy"
        )
        migration.upgrade()


def test_verified_filter_precedes_pagination_and_rechecks_holds(client, db, purchase):
    from models import ProductReview
    from rewards_vouchers_models import RewardAccount, RewardVoucherHold

    user, product, order, *_ = purchase
    review = ProductReview(
        user_id=user.id,
        product_id=product.id,
        rating=5,
        body="Verified delivered purchase",
        status="approved",
        verified_order_id=order.id,
    )
    db.add(review)
    db.flush()
    verified_id = str(review.id)
    for index in range(2):
        other = User(
            username=f"unverified-{index}",
            email=f"unverified-{index}@example.test",
            hashed_password="unused",
        )
        db.add(other)
        db.flush()
        db.add(
            ProductReview(
                user_id=other.id,
                product_id=product.id,
                rating=4,
                body="Unreconciled legacy review",
                status="approved",
                verified_order_id=None,
            )
        )
    db.add(RewardAccount(user_id=other.id, created_at=utc_now()))
    db.flush()
    db.add(
        RewardVoucherHold(
            reward_user_id=other.id,
            kind="dispute",
            reference="other-account-hold",
            reason="Other account must not affect this review",
            created_at=utc_now(),
        )
    )
    db.commit()
    path = f"/products/{product.id}/reviews"
    ordinary = client.get(path + "?limit=1").json()
    assert ordinary["items"][0]["id"] != verified_id
    filtered = client.get(path + "?verified_only=true&limit=1").json()
    assert [row["id"] for row in filtered["items"]] == [verified_id]
    assert filtered["items"][0]["verifiedPurchase"] is True
    assert filtered["next_offset"] is None
    assert client.get(path + "?verified_only=true&stars=4").json()["items"] == []
    review.status = "pending"
    db.commit()
    assert client.get(path + "?verified_only=true").json()["items"] == []
    review.status = "approved"
    db.add(RewardAccount(user_id=user.id, created_at=utc_now()))
    db.flush()
    db.add(
        RewardVoucherHold(
            reward_user_id=user.id,
            kind="dispute",
            reference="filter-hold",
            reason="Test dispute",
            created_at=utc_now(),
        )
    )
    db.commit()
    assert client.get(path + "?verified_only=true").json()["items"] == []
    assert client.get(path).json()["items"][-1]["verifiedPurchase"] is False
