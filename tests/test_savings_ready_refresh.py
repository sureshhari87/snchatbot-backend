"""Recover a captured bound order without a new charge or client balance."""

import pytest
from test_savings_api import (
    open_checkout,
    payment,
    scheme,
)
from test_savings_api import (
    provider as provider,
)
from test_savings_api import (
    savings_enabled as savings_enabled,
)

import main
from savings_models import SavingsAudit, SavingsEntry, SavingsPayment


def ready_case(client, auth_headers, provider, monkeypatch, paid=True):
    row = scheme(client, auth_headers)
    request = payment(client, auth_headers, row, mode="razorpay")
    checkout = open_checkout(client, auth_headers, request)
    orders, _ = provider
    order = orders[checkout["order_id"]]
    if paid:
        order["status"] = "paid"
    evidence = {
        "id": "pay_refresh",
        "order_id": order["id"],
        "amount": 50000,
        "currency": "INR",
        "status": "captured",
        "amount_refunded": 0,
        "refunded": False,
    }
    original = main.call_razorpay
    reads = []

    def fetch(method, path, payload=None):
        reads.append((method, path))
        assert method == "GET"
        if path == f"orders/{order['id']}/payments":
            return 200, {"entity": "collection", "count": 1, "items": [evidence]}
        if path == "payments/pay_refresh":
            return 200, evidence
        return original(method, path, payload)

    monkeypatch.setattr(main, "call_razorpay", fetch)
    return row, request, order, evidence, reads


@pytest.mark.parametrize("admin", [False, True])
def test_bound_capture_recovery_and_repeat_credit_once(
    client, auth_headers, admin_headers, provider, monkeypatch, db, admin
):
    row, p, order, evidence, reads = ready_case(client, auth_headers, provider, monkeypatch)
    path = (
        f"/admin/savings/payments/{p['id']}/reconcile-checkout"
        if admin
        else f"/savings/payments/{p['id']}/refresh"
    )
    headers = admin_headers if admin else auth_headers
    body = {"provider_order_id": order["id"]} if admin else {}
    for _ in range(2):
        response = client.post(path, headers=headers, json=body)
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "posted"
    db.expire_all()
    assert db.get(SavingsPayment, p["id"]).verified_reference == evidence["id"]
    assert db.query(SavingsEntry).one().principal_paise == 50000
    for action in ("payment_posted", "checkout_bound", "checkout_recovered"):
        assert db.query(SavingsAudit).filter_by(action=action).count() == 1
    assert reads and all(method == "GET" for method, _ in reads)


def test_customer_refresh_cannot_read_or_credit_another_owner(
    client, auth_headers, admin_headers, provider, monkeypatch, db
):
    _, p, _, _, reads = ready_case(client, auth_headers, provider, monkeypatch)
    assert (
        client.post(
            f"/savings/payments/{p['id']}/refresh", headers=admin_headers, json={}
        ).status_code
        == 409
    )
    assert reads == [] and db.query(SavingsEntry).count() == 0


@pytest.mark.parametrize(
    "extra", [{"provider_order_id": "order_other"}, {"amount_paise": 999999}, {"balance": 999999}]
)
def test_refresh_rejects_client_financial_fields(
    client, auth_headers, provider, monkeypatch, db, extra
):
    _, p, _, _, reads = ready_case(client, auth_headers, provider, monkeypatch)
    assert (
        client.post(
            f"/savings/payments/{p['id']}/refresh", headers=auth_headers, json=extra
        ).status_code
        == 422
    )
    assert reads == [] and db.query(SavingsEntry).count() == 0


@pytest.mark.parametrize(
    "field,value", [("amount", 49999), ("currency", "USD"), ("amount_refunded", 1)]
)
def test_mismatched_or_refunded_capture_stays_unposted(
    client, auth_headers, provider, monkeypatch, db, field, value
):
    _, p, _, evidence, _ = ready_case(client, auth_headers, provider, monkeypatch)
    evidence[field] = value
    assert (
        client.post(
            f"/savings/payments/{p['id']}/refresh", headers=auth_headers, json={}
        ).status_code
        == 409
    )
    db.expire_all()
    assert db.get(SavingsPayment, p["id"]).state == "pending"
    assert db.query(SavingsEntry).count() == 0
    assert db.query(SavingsAudit).filter_by(action="checkout_recovered").count() == 0


def test_unpaid_bound_order_check_does_not_create_or_credit_payment(
    client, auth_headers, provider, monkeypatch, db
):
    _, p, _, _, reads = ready_case(client, auth_headers, provider, monkeypatch, paid=False)
    response = client.post(f"/savings/payments/{p['id']}/refresh", headers=auth_headers, json={})
    assert response.status_code == 200 and response.json()["state"] == "pending"
    assert db.query(SavingsEntry).count() == 0
    assert len(reads) == 1 and reads[0][0] == "GET"


def test_held_plan_stops_recovery_before_provider_read(
    client, auth_headers, admin_headers, provider, monkeypatch, db
):
    row, p, _, _, reads = ready_case(client, auth_headers, provider, monkeypatch)
    response = client.post(
        f"/admin/savings/schemes/{row['id']}/hold",
        headers=admin_headers,
        json={"reference": "synthetic-review", "reason": "Test review"},
    )
    assert response.status_code == 200, response.text
    assert (
        client.post(
            f"/savings/payments/{p['id']}/refresh", headers=auth_headers, json={}
        ).status_code
        == 409
    )
    assert reads == [] and db.query(SavingsEntry).count() == 0


def test_provider_outage_keeps_pending_and_withholds_details(
    client, auth_headers, provider, monkeypatch, db
):
    _, p, _, _, _ = ready_case(client, auth_headers, provider, monkeypatch)

    def unavailable(*args):
        raise RuntimeError("synthetic-private-provider-detail")

    monkeypatch.setattr(main, "call_razorpay", unavailable)
    response = client.post(f"/savings/payments/{p['id']}/refresh", headers=auth_headers, json={})
    assert response.status_code == 503
    assert "synthetic-private-provider-detail" not in response.text
    assert db.query(SavingsEntry).count() == 0
