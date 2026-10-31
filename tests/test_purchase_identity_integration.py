"""High-value payment gates use synthetic provider records, never real payments."""

import pytest
from cryptography.fernet import Fernet
from test_commerce_migration import configure_delivery
from test_custom_design_quotes import create, decide, quote
from test_financial_api import db as db
from test_financial_api import jewellery_verify
from test_financial_api import provider as provider

import custom_design_api
import custom_design_balance
from models import OrderSnapshot, Product
from purchase_identity_models import PurchaseIdentityUse
from rewards_vouchers_reservation_models import FinancialReservation

NUMBER = "ABCDE1234F"
NAME = "Synthetic Buyer"


@pytest.fixture(autouse=True)
def identity_feature(monkeypatch):
    monkeypatch.setenv("PURCHASE_IDENTITY_ENABLED", "1")
    monkeypatch.setattr(custom_design_api, "ENABLED", True)
    monkeypatch.setattr(custom_design_balance, "ENABLED", True)
    monkeypatch.setenv("PURCHASE_IDENTITY_ENCRYPTION_KEY", Fernet.generate_key().decode())


def body(db, **extra):
    configure_delivery(db)
    product = db.query(Product).first()
    product.price = 250000
    product.stock_quantity = 10
    product.in_stock = True
    db.commit()
    value = dict(
        request_key="synthetic-high-value",
        commerce_source="fastapi",
        customer_name=NAME,
        delivery_address=dict(postal_code="624401"),
        items=[dict(product_id=str(product.id), backend_product_id=product.id, qty=1)],
    )
    value.update(extra)
    return value


def verified(client, customer, admin):
    result = client.post(
        "/purchase-identity",
        headers=customer,
        json=dict(
            request_key="synthetic-pan",
            pan=NUMBER,
            legal_name=NAME,
            buyer_kind="retail",
            declaration_confirmed=True,
        ),
    )
    assert result.status_code == 200, result.text
    identity = result.json()["id"]
    response = client.post(
        f"/admin/purchase-identities/{identity}/review",
        headers=admin,
        json=dict(
            decision="approved",
            verified_pan=NUMBER,
            verified_legal_name=NAME,
            evidence_reference="synthetic-original-checked",
            reason="Synthetic manual verification",
        ),
    )
    assert response.status_code == 200, response.text
    return identity


def test_missing_identity_blocks_provider_and_reservation(client, auth_headers, db, provider):
    response = client.post("/payments/razorpay/orders", headers=auth_headers, json=body(db))
    assert response.status_code == 409
    assert not provider.calls
    assert db.query(FinancialReservation).count() == 0


def test_client_payable_and_redemption_do_not_bypass_full_value(client, auth_headers, db, provider):
    request = body(db, amount=100, reward_points_requested=1000, gift_voucher_code="SYNTHETIC-CODE")
    response = client.post("/payments/razorpay/orders", headers=auth_headers, json=request)
    assert response.status_code == 409
    assert "PAN" in response.json()["detail"]
    assert not provider.calls


def test_verified_name_is_frozen_without_pan_in_provider_notes(
    client, auth_headers, admin_headers, db, provider
):
    identity = verified(client, auth_headers, admin_headers)
    response = client.post("/payments/razorpay/orders", headers=auth_headers, json=body(db))
    assert response.status_code == 200, response.text
    reservation = db.query(FinancialReservation).one()
    usage = db.query(PurchaseIdentityUse).one()
    assert usage.identity_id == identity
    assert usage.resource_key == "reservation:" + str(reservation.id)
    assert usage.amount_paise > 20_000_000
    assert NUMBER not in str(provider.calls)
    assert NUMBER not in db.query(OrderSnapshot).one().raw_payload


def test_wrong_billing_name_blocks_provider(client, auth_headers, admin_headers, db, provider):
    verified(client, auth_headers, admin_headers)
    response = client.post(
        "/payments/razorpay/orders", headers=auth_headers, json=body(db, customer_name="Nickname")
    )
    assert response.status_code == 409
    assert not provider.calls


def test_custom_quote_uses_full_total_not_small_advance(
    client, auth_headers, admin_headers, db, provider
):
    row = create(client, auth_headers).json()
    q = quote(client, admin_headers, row, total_paise=25_000_000, advance_paise=10000).json()
    response = decide(client, auth_headers, row, q)
    assert response.status_code == 409
    assert not provider.calls


def test_custom_manual_balance_keeps_full_quote_binding(
    client, auth_headers, admin_headers, db, provider
):
    identity = verified(client, auth_headers, admin_headers)
    row = create(client, auth_headers).json()
    q = quote(client, admin_headers, row, total_paise=25_000_000, advance_paise=0).json()
    response = decide(client, auth_headers, row, q)
    assert response.status_code == 200, response.text
    response = client.post(
        f"/custom-designs/{row['id']}/balance/request",
        headers=auth_headers,
        json=dict(request_key="synthetic-balance", quote_id=q["id"], mode="manual"),
    )
    assert response.status_code == 200, response.text
    usage = db.query(PurchaseIdentityUse).one()
    assert usage.identity_id == identity and usage.amount_paise == 25_000_000
    assert usage.resource_key == str(q["id"])
    assert not provider.calls


def test_existing_capture_reconciles_but_unverified_fulfilment_is_blocked(
    client, auth_headers, admin_headers, db, provider, monkeypatch
):
    monkeypatch.setenv("PURCHASE_IDENTITY_ENABLED", "0")
    response = client.post("/payments/razorpay/orders", headers=auth_headers, json=body(db))
    assert response.status_code == 200, response.text
    checkout = response.json()
    monkeypatch.setenv("PURCHASE_IDENTITY_ENABLED", "1")
    result, _ = jewellery_verify(client, auth_headers, provider, checkout)
    assert result.status_code == 200, result.text
    order = db.query(OrderSnapshot).one()
    assert order.payment_status == "verified"
    old = order.status
    response = client.patch(
        f"/admin/orders/{order.id}", headers=admin_headers, json=dict(status="shipped")
    )
    assert response.status_code == 409
    db.refresh(order)
    assert order.status == old
    assert order.payment_status == "verified"


def test_existing_uncaptured_intent_cannot_gain_a_later_identity(
    client, auth_headers, admin_headers, db, provider, monkeypatch
):
    monkeypatch.setenv("PURCHASE_IDENTITY_ENABLED", "0")
    request = body(db)
    response = client.post("/payments/razorpay/orders", headers=auth_headers, json=request)
    assert response.status_code == 200, response.text
    monkeypatch.setenv("PURCHASE_IDENTITY_ENABLED", "1")
    verified(client, auth_headers, admin_headers)
    response = client.post("/payments/razorpay/orders", headers=auth_headers, json=request)
    assert response.status_code == 409
    assert db.query(PurchaseIdentityUse).count() == 0
    assert sum(method == "POST" and path == "orders" for method, path, _ in provider.calls) == 1


def test_existing_manual_balance_needs_original_identity_review(
    client, auth_headers, admin_headers, db, provider, monkeypatch
):
    monkeypatch.setenv("PURCHASE_IDENTITY_ENABLED", "0")
    row = create(client, auth_headers).json()
    q = quote(client, admin_headers, row, total_paise=25_000_000, advance_paise=0).json()
    assert decide(client, auth_headers, row, q).status_code == 200
    request = dict(request_key="synthetic-old-balance", quote_id=q["id"], mode="manual")
    path = f"/custom-designs/{row['id']}/balance/request"
    assert client.post(path, headers=auth_headers, json=request).status_code == 200
    monkeypatch.setenv("PURCHASE_IDENTITY_ENABLED", "1")
    verified(client, auth_headers, admin_headers)
    response = client.post(path, headers=auth_headers, json=request)
    assert response.status_code == 409
    assert db.query(PurchaseIdentityUse).count() == 0
    assert not provider.calls
