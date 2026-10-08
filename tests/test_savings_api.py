import json
from datetime import timedelta

import pytest
from sqlalchemy import event

import main
import savings_api
from models import utc_now
from savings_models import (
    SavingsAudit,
    SavingsCheckoutAttempt,
    SavingsEntry,
    SavingsPayment,
    SavingsScheme,
)


@pytest.fixture(autouse=True)
def savings_enabled(monkeypatch):
    monkeypatch.setattr(savings_api, "ENABLED", True)


@pytest.fixture(autouse=True)
def provider(monkeypatch):
    orders, calls = {}, []
    monkeypatch.setattr(main, "RAZORPAY_KEY_ID", "rzp_test_synthetic_savings")
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

    def call(method, path, payload=None):
        calls.append((method, path, payload))
        if method == "POST":
            result = dict(
                payload, entity="order", status="created", id="order_savings" + str(len(orders) + 1)
            )
            orders[result["id"]] = result
            return 200, result
        return (
            (200, orders[path.removeprefix("orders/")])
            if path.removeprefix("orders/") in orders
            else (404, {})
        )

    def fetch(payment_id):
        order = next(iter(orders.values()))
        return 200, dict(
            id=payment_id,
            order_id=order["id"],
            amount=order["amount"],
            currency="INR",
            status="captured",
            amount_refunded=0,
            refunded=False,
        )

    monkeypatch.setattr(main, "call_razorpay", call)
    monkeypatch.setattr(main, "fetch_razorpay_payment", fetch)
    return orders, calls


def scheme(client, headers, key="scheme", kind="monthly"):
    payload = dict(request_key=key, kind=kind)
    if kind == "monthly":
        payload["monthly_paise"] = 50000
    response = client.post("/savings/schemes", headers=headers, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def payment(client, headers, row, key="payment", mode="manual", amount=None):
    payload = dict(request_key=key, mode=mode)
    if amount is not None:
        payload["amount_paise"] = amount
    response = client.post(f"/savings/schemes/{row['id']}/payments", headers=headers, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def open_checkout(client, headers, payment):
    response = client.post(f"/savings/payments/{payment['id']}/checkout", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def verification(checkout):
    return dict(
        razorpay_order_id=checkout["order_id"],
        razorpay_payment_id="pay_savings",
        razorpay_signature="0" * 64,
    )


def webhook_payload(order_id, payment_id="pay_savings", event_name="payment.captured"):
    return dict(
        event=event_name, payload={"payment": {"entity": {"id": payment_id, "order_id": order_id}}}
    )


def send_webhook(client, payload, signature="synthetic"):
    return client.post(
        "/savings/payments/razorpay/webhook",
        headers={"x-razorpay-signature": signature},
        content=json.dumps(payload),
    )


def test_default_disabled_never_queries_savings_tables(client, test_engine, monkeypatch):
    monkeypatch.setattr(savings_api, "ENABLED", False)

    def deny(conn, cursor, statement, parameters, context, executemany):
        assert "savings_" not in statement.lower()

    event.listen(test_engine, "before_cursor_execute", deny)
    try:
        assert client.get("/savings/schemes").status_code == 503
        assert client.post("/admin/savings/rates", json={}).status_code == 503
        assert client.post("/savings/payments/razorpay/webhook", content="{}").status_code == 503
    finally:
        event.remove(test_engine, "before_cursor_execute", deny)


def test_authentication_and_customer_admin_isolation(client, auth_headers):
    assert client.get("/savings/schemes").status_code == 401
    assert client.get("/admin/savings/schemes", headers=auth_headers).status_code == 403
    assert client.get("/admin/savings/payments", headers=auth_headers).status_code == 403
    assert (
        client.post(
            "/admin/savings/rates",
            headers=auth_headers,
            json=dict(
                paise_per_gram=700000,
                source="synthetic",
                valid_until=(utc_now() + timedelta(days=1)).isoformat() + "Z",
            ),
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"user_id": 999},
        {"state": "redeemed"},
        {"balance": 100000},
        {"gold_micrograms": 90000},
        {"started_at": "2020-01-01"},
        {"matures_at": "2020-01-01"},
        {"bonus_paise": 50000},
    ],
)
def test_enrollment_rejects_authoritative_fields(client, auth_headers, extra):
    body = dict(request_key="key", kind="monthly", monthly_paise=50000, **extra)
    assert client.post("/savings/schemes", headers=auth_headers, json=body).status_code == 422


@pytest.mark.parametrize("amount", [True, 50000.0, "50000", 49999, 0, -1])
def test_monthly_amount_validation(client, auth_headers, amount):
    assert (
        client.post(
            "/savings/schemes",
            headers=auth_headers,
            json=dict(request_key="key", kind="monthly", monthly_paise=amount),
        ).status_code
        == 422
    )


def test_enrollment_retry_and_changed_input(client, auth_headers, db):
    row = scheme(client, auth_headers)
    assert scheme(client, auth_headers)["id"] == row["id"]
    assert db.query(SavingsScheme).count() == 1
    assert (
        client.post(
            "/savings/schemes",
            headers=auth_headers,
            json=dict(request_key="scheme", kind="monthly", monthly_paise=60000),
        ).status_code
        == 409
    )


def test_customer_cannot_read_or_request_another_accounts_scheme(
    client, auth_headers, admin_headers
):
    row = scheme(client, auth_headers)
    for suffix in ("", "/payments", "/ledger"):
        assert (
            client.get(f"/savings/schemes/{row['id']}{suffix}", headers=admin_headers).status_code
            == 404
        )
    assert (
        client.post(
            f"/savings/schemes/{row['id']}/payments",
            headers=admin_headers,
            json=dict(request_key="key", mode="manual"),
        ).status_code
        == 404
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"user_id": 999},
        {"state": "posted"},
        {"gold_micrograms": 90000},
        {"gold_rate": 1},
        {"bonus_micrograms": 100000},
        {"installment": 11},
        {"provider_order_id": "order_other"},
    ],
)
def test_payment_rejects_authoritative_fields(client, auth_headers, extra):
    row = scheme(client, auth_headers)
    assert (
        client.post(
            f"/savings/schemes/{row['id']}/payments",
            headers=auth_headers,
            json=dict(request_key="key", mode="manual", **extra),
        ).status_code
        == 422
    )


def test_manual_confirmation_is_audited_and_idempotent(client, auth_headers, admin_headers, db):
    row = scheme(client, auth_headers)
    p = payment(client, auth_headers, row)
    path = f"/admin/savings/payments/{p['id']}/confirm"
    assert client.post(path, headers=auth_headers, json={"reference": "receipt"}).status_code == 403
    first = client.post(path, headers=admin_headers, json={"reference": "receipt"})
    assert first.status_code == 200
    assert (
        client.post(path, headers=admin_headers, json={"reference": "receipt"}).json()["id"]
        == first.json()["id"]
    )
    assert (
        client.post(path, headers=admin_headers, json={"reference": "changed"}).status_code == 409
    )
    assert db.query(SavingsEntry).count() == 1
    current = client.get(f"/savings/schemes/{row['id']}", headers=auth_headers).json()
    assert current["principal_paise"] == 50000 and current["confirmed_payments"] == 1
    assert (
        client.get(f"/savings/schemes/{row['id']}/ledger", headers=auth_headers).json()[0][
            "principal_paise"
        ]
        == 50000
    )
    assert (
        client.get(f"/admin/savings/schemes/{row['id']}/audit", headers=admin_headers).status_code
        == 200
    )
    assert (
        client.get(f"/admin/savings/schemes/{row['id']}/audit", headers=auth_headers).status_code
        == 403
    )


def test_admin_maturity_enforced(client, auth_headers, admin_headers, db):
    row = scheme(client, auth_headers)
    p = payment(client, auth_headers, row)
    client.post(
        f"/admin/savings/payments/{p['id']}/confirm",
        headers=admin_headers,
        json={"reference": "receipt"},
    )
    response = client.post(
        f"/admin/savings/schemes/{row['id']}/redeem",
        headers=admin_headers,
        json={"reference": "redeem"},
    )
    assert response.status_code == 409
    assert db.query(SavingsScheme).one().state == "active"


def test_rate_publication_and_confirmation_snapshot(client, auth_headers, admin_headers, db):
    assert client.get("/savings/rate", headers=auth_headers).status_code == 503
    until = (utc_now() + timedelta(days=1)).isoformat() + "Z"
    first = client.post(
        "/admin/savings/rates",
        headers=admin_headers,
        json=dict(paise_per_gram=700000, source="synthetic store", valid_until=until),
    )
    assert first.status_code == 200
    row = scheme(client, auth_headers, kind="digigold")
    p = payment(client, auth_headers, row, amount=10000)
    second = client.post(
        "/admin/savings/rates",
        headers=admin_headers,
        json=dict(paise_per_gram=800000, source="updated store", valid_until=until),
    )
    assert second.status_code == 200
    assert client.get("/savings/rate", headers=auth_headers).json()["id"] == second.json()["id"]
    posted = client.post(
        f"/admin/savings/payments/{p['id']}/confirm",
        headers=admin_headers,
        json={"reference": "digi"},
    )
    assert posted.status_code == 200
    assert posted.json()["rate_id"] == second.json()["id"]
    assert posted.json()["gold_micrograms"] == 12500
    assert posted.json()["bonus_micrograms"] == 625
    assert "source" not in client.get("/savings/rate", headers=auth_headers).json()


@pytest.mark.parametrize(
    "changes",
    [
        {"paise_per_gram": 0},
        {"paise_per_gram": True},
        {"valid_until": "2020-01-01T00:00:00Z"},
        {"valid_until": "2030-01-01T00:00:00"},
        {"purity": "22K"},
        {"created_by": 999},
    ],
)
def test_rate_input_rejected(client, admin_headers, changes):
    body = dict(
        paise_per_gram=700000,
        source="synthetic",
        valid_until=(utc_now() + timedelta(days=1)).isoformat() + "Z",
    )
    body.update(changes)
    assert client.post("/admin/savings/rates", headers=admin_headers, json=body).status_code == 422


def test_checkout_created_once_for_server_amount(client, auth_headers, provider, db):
    row = scheme(client, auth_headers)
    p = payment(client, auth_headers, row, mode="razorpay")
    first = open_checkout(client, auth_headers, p)
    assert open_checkout(client, auth_headers, p) == first
    orders, calls = provider
    assert len(calls) == 1
    assert calls[0][2]["amount"] == 50000
    assert calls[0][2]["partial_payment"] is False
    assert len(calls[0][2]["receipt"]) <= 40
    assert db.query(SavingsCheckoutAttempt).one().state == "ready"
    assert db.query(SavingsEntry).count() == 0
    assert (
        client.post(
            f"/savings/payments/{p['id']}/checkout", headers=auth_headers, json={"amount_paise": 1}
        ).status_code
        == 422
    )


def test_timeout_persists_unknown_and_never_recreates(
    client, auth_headers, provider, monkeypatch, db
):
    row = scheme(client, auth_headers)
    p = payment(client, auth_headers, row, mode="razorpay")
    calls = []

    def fail(*args, **kwargs):
        calls.append(args)
        raise RuntimeError("PRIVATE_PROVIDER_DETAIL")

    monkeypatch.setattr(main, "call_razorpay", fail)
    first = client.post(f"/savings/payments/{p['id']}/checkout", headers=auth_headers)
    assert first.status_code == 503
    assert "PRIVATE_PROVIDER_DETAIL" not in first.text
    assert db.query(SavingsCheckoutAttempt).one().state == "unknown"
    assert (
        client.post(f"/savings/payments/{p['id']}/checkout", headers=auth_headers).status_code
        == 409
    )
    assert len(calls) == 1


def test_unknown_checkout_reconciliation_verifies_provider_receipt_and_amount(
    client, auth_headers, admin_headers, provider, db, monkeypatch
):
    row = scheme(client, auth_headers)
    p = payment(client, auth_headers, row, mode="razorpay")
    original = main.call_razorpay

    def create_then_timeout(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("synthetic timeout after provider creation")

    monkeypatch.setattr(main, "call_razorpay", create_then_timeout)
    assert (
        client.post(f"/savings/payments/{p['id']}/checkout", headers=auth_headers).status_code
        == 503
    )
    monkeypatch.setattr(main, "call_razorpay", original)
    orders, _ = provider
    order_id = next(iter(orders))
    path = f"/admin/savings/payments/{p['id']}/reconcile-checkout"
    assert (
        client.post(path, headers=auth_headers, json={"provider_order_id": order_id}).status_code
        == 403
    )
    orders["order_wrong"] = dict(orders[order_id], id="order_wrong", receipt="wrong")
    assert (
        client.post(
            path, headers=admin_headers, json={"provider_order_id": "order_wrong"}
        ).status_code
        == 409
    )
    assert (
        client.post(path, headers=admin_headers, json={"provider_order_id": order_id}).status_code
        == 200
    )
    assert open_checkout(client, auth_headers, p)["order_id"] == order_id


def test_checkout_signature_ownership_and_verified_capture(client, auth_headers, admin_headers, db):
    row = scheme(client, auth_headers)
    p = payment(client, auth_headers, row, mode="razorpay")
    checkout = open_checkout(client, auth_headers, p)
    path = f"/savings/payments/{p['id']}/verify"
    body = verification(checkout)
    assert client.post(path, headers=admin_headers, json=body).status_code == 409
    assert (
        client.post(
            path, headers=auth_headers, json=dict(body, razorpay_signature="1" * 64)
        ).status_code
        == 400
    )
    first = client.post(path, headers=auth_headers, json=body)
    assert first.status_code == 200
    assert client.post(path, headers=auth_headers, json=body).json()["id"] == first.json()["id"]
    assert db.query(SavingsEntry).count() == 1
    assert db.query(SavingsPayment).one().state == "posted"


def test_webhook_signature_and_duplicate_verify_credit_once(client, auth_headers, db):
    row = scheme(client, auth_headers)
    p = payment(client, auth_headers, row, mode="razorpay")
    checkout = open_checkout(client, auth_headers, p)
    payload = webhook_payload(checkout["order_id"])
    assert send_webhook(client, payload, "wrong").status_code == 400
    first = send_webhook(client, payload)
    assert first.status_code == 200
    assert send_webhook(client, payload).json()["entry_id"] == first.json()["entry_id"]
    assert (
        client.post(
            f"/savings/payments/{p['id']}/verify", headers=auth_headers, json=verification(checkout)
        ).json()["id"]
        == first.json()["entry_id"]
    )
    assert db.query(SavingsEntry).count() == 1
    assert db.query(SavingsAudit).filter_by(action="payment_posted").count() == 1


def test_webhook_fetches_evidence_instead_of_trusting_body(client, auth_headers, monkeypatch, db):
    row = scheme(client, auth_headers)
    p = payment(client, auth_headers, row, mode="razorpay")
    checkout = open_checkout(client, auth_headers, p)
    monkeypatch.setattr(
        main,
        "fetch_razorpay_payment",
        lambda _: (
            200,
            dict(
                id="pay_savings",
                order_id=checkout["order_id"],
                status="authorized",
                currency="INR",
                amount=50000,
            ),
        ),
    )
    assert send_webhook(client, webhook_payload(checkout["order_id"])).status_code == 409
    assert db.query(SavingsEntry).count() == 0


def test_webhook_size_unknown_order_and_invalid_body(client):
    assert send_webhook(client, webhook_payload("order_unknown")).json() == {"status": "ignored"}
    assert (
        client.post(
            "/savings/payments/razorpay/webhook",
            headers={"x-razorpay-signature": "synthetic"},
            content="[",
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/savings/payments/razorpay/webhook",
            headers={"x-razorpay-signature": "synthetic"},
            content="x" * 1_048_577,
        ).status_code
        == 413
    )


def test_bounded_pagination(client, auth_headers, admin_headers):
    assert client.get("/savings/schemes?limit=51", headers=auth_headers).status_code == 422
    assert client.get("/savings/schemes?offset=-1", headers=auth_headers).status_code == 422
    assert client.get("/admin/savings/payments?limit=999", headers=admin_headers).status_code == 422


def test_paid_order_recovery_posts_verified_capture(
    client, auth_headers, admin_headers, provider, monkeypatch, db
):
    row = scheme(client, auth_headers)
    p = payment(client, auth_headers, row, mode="razorpay")
    original = main.call_razorpay

    def timeout(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("synthetic creation timeout")

    monkeypatch.setattr(main, "call_razorpay", timeout)
    assert (
        client.post(f"/savings/payments/{p['id']}/checkout", headers=auth_headers).status_code
        == 503
    )
    orders, _ = provider
    order_id = next(iter(orders))
    orders[order_id]["status"] = "paid"
    captured = dict(
        id="pay_recovered",
        order_id=order_id,
        amount=50000,
        currency="INR",
        status="captured",
        amount_refunded=0,
        refunded=False,
    )

    def recover(method, path, payload=None):
        if path == f"orders/{order_id}/payments":
            return 200, {"items": [captured]}
        if path == "payments/pay_recovered":
            return 200, captured
        return original(method, path, payload)

    monkeypatch.setattr(main, "call_razorpay", recover)
    assert send_webhook(client, webhook_payload(order_id, "pay_recovered")).json() == {
        "status": "ignored"
    }
    result = client.post(
        f"/admin/savings/payments/{p['id']}/reconcile-checkout",
        headers=admin_headers,
        json={"provider_order_id": order_id},
    )
    assert result.status_code == 200
    assert result.json()["state"] == "posted"
    assert db.query(SavingsEntry).count() == 1
    assert (
        client.post(
            f"/admin/savings/payments/{p['id']}/reconcile-checkout",
            headers=admin_headers,
            json={"provider_order_id": order_id},
        ).status_code
        == 200
    )
    assert db.query(SavingsEntry).count() == 1


def test_provider_outage_never_returns_provider_details(client, auth_headers, monkeypatch, db):
    row = scheme(client, auth_headers)
    p = payment(client, auth_headers, row, mode="razorpay")
    order = open_checkout(client, auth_headers, p)

    def unavailable(*args):
        raise OSError("PRIVATE_PROVIDER_DETAIL")

    monkeypatch.setattr(main, "fetch_razorpay_payment", unavailable)
    result = client.post(
        f"/savings/payments/{p['id']}/verify", headers=auth_headers, json=verification(order)
    )
    assert result.status_code == 503
    assert "PRIVATE_PROVIDER_DETAIL" not in result.text
    assert db.query(SavingsEntry).count() == 0


def test_malformed_provider_order_leaves_unknown_outcome(client, auth_headers, monkeypatch, db):
    row = scheme(client, auth_headers)
    p = payment(client, auth_headers, row, mode="razorpay")

    def wrong(method, path, payload):
        return 200, dict(
            payload, id="order_wrong_currency", entity="order", status="created", currency="USD"
        )

    monkeypatch.setattr(main, "call_razorpay", wrong)
    assert (
        client.post(f"/savings/payments/{p['id']}/checkout", headers=auth_headers).status_code
        == 503
    )
    assert db.query(SavingsCheckoutAttempt).one().state == "unknown"
    assert db.query(SavingsPayment).one().provider_order_id is None
