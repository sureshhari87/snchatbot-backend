"""Authenticated API/payment acceptance on real migrated, isolated SQLite tables."""

import json
from datetime import timedelta

import pytest
from cryptography.fernet import Fernet
from financial_test_provider import Provider
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from test_commerce_migration import configure_delivery
from test_rewards_vouchers_reservation_migration import migrations

import financial_checkout
import main
from database import Base
from models import (
    CommerceCartItem,
    CustomerNotification,
    OrderSnapshot,
    Product,
    User,
)
from rewards_vouchers_models import (
    GiftVoucher,
    GiftVoucherEntry,
    RewardAccount,
    RewardEntry,
    RewardVoucherHold,
)
from rewards_vouchers_reservation_models import FinancialReservation
from voucher_funding_models import FinancialCheckoutAttempt, VoucherFunding, VoucherFundingEvent


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    financial = {
        "reward_accounts",
        "reward_entries",
        "gift_vouchers",
        "gift_voucher_entries",
        "reward_voucher_holds",
        "financial_reservations",
        "financial_reservation_events",
        "voucher_funding",
        "voucher_funding_events",
        "financial_checkout_attempts",
    }
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        Base.metadata.create_all(
            connection,
            tables=[table for table in Base.metadata.sorted_tables if table.name not in financial],
        )
        migrations(connection, include_funding=True)
    with Session(engine, autoflush=False) as session:
        main.seed_products(session)
        yield session
    engine.dispose()


@pytest.fixture(autouse=True)
def provider(monkeypatch):
    fake = Provider()
    monkeypatch.setenv("VOUCHER_CODE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(financial_checkout, "ENABLED", True)
    monkeypatch.setattr(main, "RAZORPAY_KEY_ID", "rzp_test_synthetic_financial")
    monkeypatch.setattr(main, "razorpay_checkout_is_configured", lambda: True)
    monkeypatch.setattr(main, "razorpay_webhook_is_configured", lambda: True)
    monkeypatch.setattr(
        main,
        "razorpay_checkout_signature_is_valid",
        lambda order, payment, signature: signature == "0" * 64,
    )
    monkeypatch.setattr(
        main, "razorpay_signature_is_valid", lambda raw, signature: signature == "synthetic"
    )
    monkeypatch.setattr(main, "call_razorpay", fake.call)
    return fake


def purchase(client, headers, mode="manual", key="purchase", **extra):
    response = client.post(
        "/vouchers",
        headers=headers,
        json=dict(request_key=key, amount_paise=50000, payment_mode=mode, **extra),
    )
    assert response.status_code == 200, response.text
    return response.json()


def confirm(client, headers, voucher_id, **changes):
    body = dict(
        confirmed_paise=50000, reference="synthetic-receipt", reason="Synthetic receipt verified"
    )
    body.update(changes)
    return client.post(f"/admin/vouchers/{voucher_id}/confirm-manual", headers=headers, json=body)


def online(client, headers, provider):
    row = purchase(client, headers, "razorpay")
    checkout = client.post(f"/vouchers/{row['id']}/checkout", headers=headers)
    assert checkout.status_code == 200, checkout.text
    payment_id = provider.capture(checkout.json()["order_id"])
    proof = dict(
        razorpay_order_id=checkout.json()["order_id"],
        razorpay_payment_id=payment_id,
        razorpay_signature="0" * 64,
    )
    return row, proof


def verify(client, headers, row, proof):
    return client.post(f"/vouchers/{row['id']}/verify", headers=headers, json=proof)


def webhook(client, order_id, payment_id, event_name="payment.captured", **extra):
    payload = dict(
        event=event_name,
        payload=dict(payment=dict(entity=dict(id=payment_id, order_id=order_id)), **extra),
    )
    return client.post(
        "/financial/payments/razorpay/webhook",
        headers={"x-razorpay-signature": "synthetic"},
        content=json.dumps(payload),
    )


def jewellery(client, headers, db, key="checkout", **changes):
    if db.query(main.AppConfigEntry).filter_by(key="commerce.delivery_routes").first() is None:
        configure_delivery(db)
    product = db.query(Product).first()
    product.price = 1000
    product.stock_quantity = 10
    product.in_stock = True
    db.commit()
    body = dict(
        request_key=key,
        commerce_source="fastapi",
        delivery_address=dict(postal_code="624401"),
        items=[dict(product_id=str(product.id), backend_product_id=product.id, qty=1)],
    )
    body.update(changes)
    response = client.post("/payments/razorpay/orders", headers=headers, json=body)
    assert response.status_code == 200, response.text
    return response.json(), body, product.id


def jewellery_verify(client, headers, provider, checkout):
    payment_id = provider.capture(checkout["order_id"])
    body = dict(
        razorpay_order_id=checkout["order_id"],
        razorpay_payment_id=payment_id,
        razorpay_signature="0" * 64,
    )
    response = client.post("/payments/razorpay/verify", headers=headers, json=body)
    return response, body


def test_default_disabled_does_not_query_financial_tables(client, db, monkeypatch):
    monkeypatch.setattr(financial_checkout, "ENABLED", False)

    def deny(connection, cursor, statement, parameters, context, executemany):
        assert all(
            name not in statement.lower()
            for name in (
                "gift_vouchers",
                "reward_accounts",
                "financial_reservations",
                "voucher_funding",
            )
        )

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", deny)
    try:
        assert client.get("/vouchers/my").status_code == 503
        assert client.get("/rewards").status_code == 503
        assert client.post("/vouchers", json={}).status_code == 503
        assert client.post("/financial/payments/razorpay/webhook", content="{}").status_code == 503
    finally:
        event.remove(engine, "before_cursor_execute", deny)


def test_customer_admin_and_account_isolation(client, auth_headers, admin_headers, db):
    assert client.get("/vouchers/my").status_code == 401
    assert client.get("/admin/vouchers", headers=auth_headers).status_code == 403
    row = purchase(client, auth_headers)
    other = User(
        username="financial_other",
        email="financial_other@example.invalid",
        hashed_password="unused",
        is_verified=True,
    )
    db.add(other)
    db.commit()
    headers = {"Authorization": "Bearer " + main.create_access_token({"sub": other.email})}
    for path in (
        f"/vouchers/{row['id']}",
        f"/vouchers/{row['id']}/code",
        f"/vouchers/{row['id']}/checkout",
    ):
        response = (
            client.get(path, headers=headers)
            if path.endswith(str(row["id"]))
            else client.post(path, headers=headers)
        )
        assert response.status_code == 404
    assert client.get("/vouchers/my", headers=headers).json() == []
    assert client.get("/rewards/history", headers=headers).json() == []


@pytest.mark.parametrize(
    "extra",
    [
        {"balance_paise": 50000},
        {"state": "active"},
        {"code": "CLIENT-CODE"},
        {"purchaser_id": 999},
        {"expires_at": "2030-01-01"},
    ],
)
def test_customer_cannot_supply_authoritative_voucher_fields(client, auth_headers, extra):
    response = client.post(
        "/vouchers",
        headers=auth_headers,
        json=dict(request_key="key", amount_paise=50000, payment_mode="manual", **extra),
    )
    assert response.status_code == 422


@pytest.mark.parametrize("amount", [True, "50000", 50000.0, 0, -1, 49999])
def test_voucher_denomination_and_integer_validation(client, auth_headers, amount):
    response = client.post(
        "/vouchers",
        headers=auth_headers,
        json=dict(request_key="key", amount_paise=amount, payment_mode="manual"),
    )
    assert response.status_code == 422


def test_manual_confirmation_is_audited_idempotent_and_starts_expiry(
    client, auth_headers, admin_headers, db
):
    row = purchase(client, auth_headers)
    assert row["balance_paise"] == 0 and row["expires_at"] is None
    assert purchase(client, auth_headers)["id"] == row["id"]
    assert client.post(f"/vouchers/{row['id']}/code", headers=auth_headers).status_code == 409
    assert confirm(client, auth_headers, row["id"]).status_code == 403
    assert confirm(client, admin_headers, row["id"], confirmed_paise=100).status_code == 409
    first = confirm(client, admin_headers, row["id"])
    assert first.status_code == 200, first.text
    second = confirm(client, admin_headers, row["id"])
    assert second.json()["activated_at"] == first.json()["activated_at"]
    assert second.json()["expires_at"] == first.json()["expires_at"]
    assert confirm(client, admin_headers, row["id"], reference="different").status_code == 409
    assert db.query(GiftVoucherEntry).count() == 1
    entry = db.query(GiftVoucherEntry).one()
    assert entry.actor_id is not None and entry.reason
    item = db.get(GiftVoucher, row["id"])
    assert item.expires_at - item.activated_at == timedelta(days=365)
    assert db.query(RewardEntry).count() == 0
    code = client.post(f"/vouchers/{row['id']}/code", headers=auth_headers)
    assert code.status_code == 200 and code.headers["cache-control"] == "no-store"
    funding = db.get(VoucherFunding, row["id"])
    assert code.json()["code"] not in funding.code_ciphertext
    assert "code" not in client.get(f"/vouchers/{row['id']}", headers=auth_headers).json()
    assert client.get(f"/admin/vouchers/{row['id']}/reconciliation", headers=admin_headers).json()[
        "consistent"
    ]


def test_complimentary_is_admin_only_and_does_not_award_points(
    client, auth_headers, admin_headers, verified_user, db
):
    body = dict(
        request_key="gift",
        purchaser_id=verified_user.id,
        amount_paise=12345,
        reason="Synthetic customer goodwill",
    )
    assert (
        client.post("/admin/vouchers/complimentary", headers=auth_headers, json=body).status_code
        == 403
    )
    result = client.post("/admin/vouchers/complimentary", headers=admin_headers, json=body)
    assert result.status_code == 200, result.text
    again = client.post("/admin/vouchers/complimentary", headers=admin_headers, json=body)
    assert again.json()["id"] == result.json()["id"]
    assert result.json()["balance_paise"] == 12345
    assert db.query(GiftVoucherEntry).one().kind == "complimentary"
    assert db.query(RewardEntry).count() == 0


def test_online_capture_and_duplicate_callbacks_fund_once(
    client, auth_headers, admin_headers, provider, db
):
    row, proof = online(client, auth_headers, provider)
    assert confirm(client, admin_headers, row["id"]).status_code == 409
    bad = proof | dict(razorpay_signature="1" * 64)
    assert verify(client, auth_headers, row, bad).status_code == 400
    for _ in range(2):
        response = verify(client, auth_headers, row, proof)
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "active"
    for event_name in ("payment.captured", "order.paid"):
        response = webhook(
            client, proof["razorpay_order_id"], proof["razorpay_payment_id"], event_name
        )
        assert response.status_code == 200, response.text
    assert db.query(GiftVoucherEntry).count() == 1
    assert db.query(VoucherFundingEvent).filter_by(action="funding_posted").count() == 1
    assert db.query(RewardEntry).count() == 0
    assert client.get(f"/admin/vouchers/{row['id']}/reconciliation", headers=admin_headers).json()[
        "consistent"
    ]


@pytest.mark.parametrize(
    "field,value",
    [("currency", "USD"), ("amount", 1), ("captured", False), ("order_id", "order_Other")],
)
def test_wrong_online_evidence_never_activates(client, auth_headers, provider, db, field, value):
    row, proof = online(client, auth_headers, provider)
    provider.payments[proof["razorpay_payment_id"]][field] = value
    assert verify(client, auth_headers, row, proof).status_code == 409
    assert db.get(GiftVoucher, row["id"]).balance_paise == 0
    assert db.query(GiftVoucherEntry).count() == 0


def test_unknown_order_creation_requires_existing_order_recovery(
    client, auth_headers, admin_headers, provider, db
):
    row = purchase(client, auth_headers, "razorpay")
    provider.timeout_create = True
    response = client.post(f"/vouchers/{row['id']}/checkout", headers=auth_headers)
    assert response.status_code == 503
    assert client.post(f"/vouchers/{row['id']}/checkout", headers=auth_headers).status_code == 409
    assert len(provider.orders) == 1
    provider.timeout_create = False
    order_id = next(iter(provider.orders))
    provider.capture(order_id)
    response = client.post(
        f"/admin/vouchers/{row['id']}/reconcile-checkout",
        headers=admin_headers,
        json=dict(provider_order_id=order_id),
    )
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "active"
    assert db.query(GiftVoucherEntry).count() == 1


def test_provider_collection_failure_keeps_voucher_unfunded_then_recovers(
    client, auth_headers, provider, db
):
    row, proof = online(client, auth_headers, provider)
    provider.malformed_collections.add("disputes")
    assert verify(client, auth_headers, row, proof).status_code == 503
    assert db.get(GiftVoucher, row["id"]).balance_paise == 0
    provider.malformed_collections.clear()
    assert verify(client, auth_headers, row, proof).status_code == 200
    assert db.query(GiftVoucherEntry).count() == 1


def test_refund_before_capture_event_persists_hold_without_credit(
    client, auth_headers, provider, db
):
    row, proof = online(client, auth_headers, provider)
    payment_id = proof["razorpay_payment_id"]
    provider.refunds["rfnd_Test"] = dict(
        id="rfnd_Test",
        entity="refund",
        payment_id=payment_id,
        currency="INR",
        amount=50000,
        status="processed",
    )
    response = webhook(
        client,
        proof["razorpay_order_id"],
        payment_id,
        "refund.processed",
        refund=dict(entity=dict(id="rfnd_Test")),
    )
    assert response.status_code == 200 and response.json()["status"] == "held"
    for _ in range(2):
        response = verify(client, auth_headers, row, proof)
        assert response.status_code == 200 and response.json()["review_required"]
    assert db.query(RewardVoucherHold).count() == 1
    assert db.query(GiftVoucherEntry).count() == 0
    assert db.get(GiftVoucher, row["id"]).balance_paise == 0


def test_invalid_webhook_signature_and_malformed_body_do_not_touch_provider(client, provider):
    start = len(provider.calls)
    response = client.post(
        "/financial/payments/razorpay/webhook",
        headers={"x-razorpay-signature": "invalid"},
        content="{}",
    )
    assert response.status_code == 400
    response = client.post(
        "/financial/payments/razorpay/webhook",
        headers={"x-razorpay-signature": "synthetic"},
        content="[]",
    )
    assert response.status_code == 400
    assert len(provider.calls) == start


def test_jewellery_earning_and_voucher_then_points_are_atomic(
    client, auth_headers, admin_headers, provider, db
):
    checkout, _, product_id = jewellery(client, auth_headers, db, "first")
    response, proof = jewellery_verify(client, auth_headers, provider, checkout)
    assert response.status_code == 200, response.text
    account = db.query(RewardAccount).one()
    assert account.balance_points == 10
    row = purchase(client, auth_headers)
    assert confirm(client, admin_headers, row["id"]).status_code == 200
    code = client.post(f"/vouchers/{row['id']}/code", headers=auth_headers).json()["code"]
    checkout, body, _ = jewellery(
        client, auth_headers, db, "second", gift_voucher_code=code, reward_points_requested=10
    )
    assert checkout["amount"] == 49000
    assert checkout["voucher_redemption_paise"] == 50000
    quote = client.post("/financial/checkout/quote", headers=auth_headers, json=body)
    assert quote.status_code == 200
    assert quote.json()["payable_paise"] == checkout["amount"]
    response, proof = jewellery_verify(client, auth_headers, provider, checkout)
    assert response.status_code == 200, response.text
    assert (
        client.post("/payments/razorpay/verify", headers=auth_headers, json=proof).status_code
        == 200
    )
    assert (
        webhook(client, proof["razorpay_order_id"], proof["razorpay_payment_id"]).status_code == 200
    )
    db.expire_all()
    assert db.get(RewardAccount, account.user_id).balance_points == 4
    assert db.get(GiftVoucher, row["id"]).balance_paise == 0
    assert db.get(Product, product_id).stock_quantity == 9
    assert db.query(GiftVoucherEntry).filter_by(kind="redemption").count() == 1
    assert db.query(CustomerNotification).count() == 2


def test_financial_unknown_checkout_never_reorders_and_can_reconcile(
    client, auth_headers, admin_headers, provider, db
):
    configure_delivery(db)
    product = db.query(Product).first()
    product.price = 1000
    db.commit()
    body = dict(
        request_key="unknown",
        commerce_source="fastapi",
        delivery_address=dict(postal_code="624401"),
        items=[dict(product_id=str(product.id), backend_product_id=product.id, qty=1)],
    )
    provider.timeout_create = True
    assert (
        client.post("/payments/razorpay/orders", headers=auth_headers, json=body).status_code == 503
    )
    assert (
        client.post("/payments/razorpay/orders", headers=auth_headers, json=body).status_code == 409
    )
    assert len(provider.orders) == 1
    row = db.query(FinancialReservation).one()
    provider.timeout_create = False
    order_id = next(iter(provider.orders))
    provider.capture(order_id)
    response = client.post(
        f"/admin/financial/checkouts/{row.id}/reconcile",
        headers=admin_headers,
        json=dict(provider_order_id=order_id),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "finalized"
    assert db.query(RewardEntry).count() == 1


def test_financial_items_cannot_change_under_same_key(client, auth_headers, provider, db):
    checkout, body, _ = jewellery(client, auth_headers, db)
    assert (
        client.post("/payments/razorpay/orders", headers=auth_headers, json=body).json()["order_id"]
        == checkout["order_id"]
    )
    body["items"][0]["qty"] = 2
    assert (
        client.post("/payments/razorpay/orders", headers=auth_headers, json=body).status_code == 409
    )
    assert len(provider.orders) == 1


def test_stock_failure_rolls_back_reward_posting_and_order_verification(
    client, auth_headers, provider, db
):
    checkout, _, product_id = jewellery(client, auth_headers, db)
    product = db.get(Product, product_id)
    product.stock_quantity = 0
    db.commit()
    response, _ = jewellery_verify(client, auth_headers, provider, checkout)
    assert response.status_code == 409
    db.expire_all()
    assert db.query(RewardEntry).count() == 0
    assert db.query(FinancialReservation).one().state == "ready"
    assert db.get(OrderSnapshot, checkout["local_order_id"]).payment_status != "verified"
    assert db.get(Product, product_id).stock_quantity == 0
    assert db.query(CustomerNotification).count() == 0


def test_paid_voucher_retry_recovers_without_opening_another_checkout(
    client, auth_headers, provider, db
):
    row, proof = online(client, auth_headers, provider)
    response = client.post(f"/vouchers/{row['id']}/checkout", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json()["checkout_available"] is False
    assert response.json()["voucher"]["state"] == "active"
    assert len(provider.orders) == 1
    assert db.query(GiftVoucherEntry).count() == 1


def test_attempted_voucher_checkout_cannot_reuse_order_for_another_attempt(
    client, auth_headers, provider
):
    row = purchase(client, auth_headers, "razorpay")
    result = client.post(f"/vouchers/{row['id']}/checkout", headers=auth_headers)
    assert result.status_code == 200
    order = provider.orders[result.json()["order_id"]]
    order.update(status="attempted", attempts=1)
    response = client.post(f"/vouchers/{row['id']}/checkout", headers=auth_headers)
    assert response.status_code == 409
    assert len(provider.orders) == 1


def test_paid_jewellery_retry_recovers_callback_loss_once(client, auth_headers, provider, db):
    checkout, body, product_id = jewellery(client, auth_headers, db)
    provider.capture(checkout["order_id"])
    for _ in range(2):
        result = client.post("/payments/razorpay/orders", headers=auth_headers, json=body)
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "paid"
    assert len(provider.orders) == 1
    assert db.query(RewardEntry).count() == 1
    db.expire_all()
    assert db.get(Product, product_id).stock_quantity == 9


def test_attempted_jewellery_checkout_requires_reconciliation(client, auth_headers, provider, db):
    checkout, body, _ = jewellery(client, auth_headers, db)
    provider.orders[checkout["order_id"]].update(status="attempted", attempts=1)
    assert (
        client.post("/payments/razorpay/orders", headers=auth_headers, json=body).status_code == 409
    )
    assert len(provider.orders) == 1


def test_known_hold_is_not_cleared_by_later_clean_provider_evidence(
    client, auth_headers, admin_headers, provider, db
):
    checkout, _, _ = jewellery(client, auth_headers, db)
    response, proof = jewellery_verify(client, auth_headers, provider, checkout)
    assert response.status_code == 200
    user_id = db.query(RewardAccount).one().user_id
    response = client.post(
        f"/admin/rewards/{user_id}/hold",
        headers=admin_headers,
        json=dict(reference="synthetic-review", reason="Review required"),
    )
    assert response.status_code == 200
    assert (
        client.post("/payments/razorpay/verify", headers=auth_headers, json=proof).status_code
        == 409
    )
    account = client.get("/rewards", headers=auth_headers).json()
    assert account["balance_points"] == 10 and account["available_points"] == 0
    assert db.query(RewardEntry).count() == 1
    assert db.query(RewardVoucherHold).count() == 1


def test_verified_refund_after_posting_holds_without_reversing_rewards(
    client, auth_headers, provider, db
):
    checkout, _, _ = jewellery(client, auth_headers, db)
    response, proof = jewellery_verify(client, auth_headers, provider, checkout)
    assert response.status_code == 200
    provider.refunds["rfnd_Late"] = dict(
        id="rfnd_Late",
        entity="refund",
        payment_id=proof["razorpay_payment_id"],
        amount=checkout["amount"],
        currency="INR",
        status="processed",
    )
    for _ in range(2):
        response = webhook(
            client,
            checkout["order_id"],
            proof["razorpay_payment_id"],
            "refund.processed",
            refund=dict(entity=dict(id="rfnd_Late")),
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "held"
    account = client.get("/rewards", headers=auth_headers).json()
    assert account["balance_points"] == 10 and account["available_points"] == 0
    assert db.query(RewardEntry).count() == 1
    assert db.query(RewardVoucherHold).count() == 1


def test_admin_review_hold_blocks_manual_activation_without_balance_changes(
    client, auth_headers, admin_headers, db
):
    row = purchase(client, auth_headers)
    response = client.post(
        f"/admin/vouchers/{row['id']}/hold",
        headers=admin_headers,
        json=dict(reference="manual-review", reason="Receipt needs review"),
    )
    assert response.status_code == 200
    assert confirm(client, admin_headers, row["id"]).status_code == 409
    assert db.get(GiftVoucher, row["id"]).balance_paise == 0
    assert db.query(GiftVoucherEntry).count() == 0
    assert (
        client.post(
            f"/admin/vouchers/{row['id']}/release", headers=admin_headers, json={}
        ).status_code
        == 404
    )


def test_assigned_voucher_is_private_and_cannot_be_redeemed_by_buyer(
    client, auth_headers, admin_headers, db
):
    other = User(
        username="voucher_recipient",
        email="voucher_recipient@example.invalid",
        hashed_password="unused",
        is_verified=True,
    )
    db.add(other)
    db.commit()
    row = purchase(client, auth_headers, assigned_user_id=other.id)
    assert confirm(client, admin_headers, row["id"]).status_code == 200
    code = client.post(f"/vouchers/{row['id']}/code", headers=auth_headers).json()["code"]
    assert (
        client.post("/vouchers/validate", headers=auth_headers, json=dict(code=code)).status_code
        == 404
    )
    recipient = {"Authorization": "Bearer " + main.create_access_token({"sub": other.email})}
    response = client.post("/vouchers/validate", headers=recipient, json=dict(code=code))
    assert response.status_code == 200 and response.json()["available_paise"] == 50000
    history = client.get(f"/vouchers/{row['id']}/history", headers=recipient)
    assert history.status_code == 200
    assert "order_id" not in history.json()[0]


def test_code_key_absence_fails_before_creating_a_voucher(client, auth_headers, monkeypatch, db):
    monkeypatch.delenv("VOUCHER_CODE_ENCRYPTION_KEY", raising=False)
    response = client.post(
        "/vouchers",
        headers=auth_headers,
        json=dict(request_key="missing-key", amount_paise=50000, payment_mode="manual"),
    )
    assert response.status_code == 503
    assert db.query(GiftVoucher).count() == 0
    assert db.query(VoucherFundingEvent).count() == 0


def test_untrusted_checkout_metadata_and_firebase_token_are_not_saved(
    client, auth_headers, provider, db
):
    checkout, _, _ = jewellery(
        client,
        auth_headers,
        db,
        notes=dict(
            reward_points_requested=99999, coupon_discount=99999, gift_voucher_code="CLIENT-FAKE"
        ),
        firebase_id_token="synthetic-token-not-a-real-credential",
    )
    assert checkout["amount"] == 100000
    raw = db.get(OrderSnapshot, checkout["local_order_id"]).raw_payload
    saved = db.get(FinancialCheckoutAttempt, checkout["reservation_id"]).context_json
    for value in (raw, saved):
        assert "synthetic-token-not-a-real-credential" not in value
        assert "CLIENT-FAKE" not in value
        assert "99999" not in value


def test_downstream_notification_failure_rolls_back_entire_financial_order(
    client, auth_headers, provider, db, monkeypatch
):
    import order_notifications

    checkout, _, product_id = jewellery(client, auth_headers, db)

    def fail(*args):
        raise RuntimeError("Synthetic notification failure")

    monkeypatch.setattr(order_notifications, "enqueue_verified_payment", fail)
    response, _ = jewellery_verify(client, auth_headers, provider, checkout)
    assert response.status_code == 500
    db.expire_all()
    assert db.query(RewardEntry).count() == 0
    assert db.get(Product, product_id).stock_quantity == 10
    assert db.query(FinancialReservation).one().state == "ready"
    assert db.get(OrderSnapshot, checkout["local_order_id"]).payment_status == "pending"
    assert db.query(CustomerNotification).count() == 0


def test_financial_checkout_disabled_after_creation_cannot_bypass_ledger(
    client, auth_headers, provider, db, monkeypatch
):
    checkout, _, product_id = jewellery(client, auth_headers, db)
    monkeypatch.setattr(financial_checkout, "ENABLED", False)
    response, _ = jewellery_verify(client, auth_headers, provider, checkout)
    assert response.status_code == 503
    db.expire_all()
    assert db.get(Product, product_id).stock_quantity == 10
    assert db.query(RewardEntry).count() == 0


def test_stock_and_cart_cleanup_roll_back_together_on_later_failure(
    client, auth_headers, provider, db, monkeypatch
):
    import order_notifications

    product = db.query(Product).first()
    product.price = 1000
    product.stock_quantity = 10
    db.commit()
    cart = client.post(
        "/cart", headers=auth_headers, json=dict(product_id=product.id, quantity=1)
    ).json()
    checkout, _, product_id = jewellery(
        client,
        auth_headers,
        db,
        items=[
            dict(
                product_id=str(product.id),
                backend_product_id=product.id,
                qty=1,
                cart_item_id=str(cart["id"]),
            )
        ],
    )

    def fail(*args):
        raise RuntimeError("Synthetic late failure")

    monkeypatch.setattr(order_notifications, "enqueue_verified_payment", fail)
    response, _ = jewellery_verify(client, auth_headers, provider, checkout)
    assert response.status_code == 500
    db.expire_all()
    assert db.get(CommerceCartItem, cart["id"]) is not None
    assert db.get(Product, product_id).stock_quantity == 10
    assert db.query(RewardEntry).count() == 0


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE voucher_funding SET code_ciphertext='tampered' WHERE voucher_id=:id",
        "UPDATE voucher_funding SET payment_mode='manual' WHERE voucher_id=:id",
        "UPDATE voucher_funding SET provider_order_id='order_Other' WHERE voucher_id=:id",
        "UPDATE voucher_funding SET provider_payment_id='pay_Other' WHERE voucher_id=:id",
        "DELETE FROM voucher_funding WHERE voucher_id=:id",
        "DELETE FROM voucher_funding_events WHERE voucher_id=:id",
    ],
)
def test_funding_identity_and_audit_are_immutable(client, auth_headers, provider, db, sql):
    from sqlalchemy.exc import DatabaseError

    row, proof = online(client, auth_headers, provider)
    assert verify(client, auth_headers, row, proof).status_code == 200
    with pytest.raises(DatabaseError):
        db.execute(text(sql), dict(id=row["id"]))
    db.rollback()
    assert db.get(GiftVoucher, row["id"]).balance_paise == 50000
    assert db.query(GiftVoucherEntry).count() == 1
