"""Adverse webhook events must outrank captured payment entities."""

import hashlib
import hmac
import json

import pytest

import main
from models import ExternalIntegrationEvent, OrderSnapshot, Product

ADVERSE_EVENTS = [
    ("refund.created", "refund_pending"),
    ("refund.processed", "refunded"),
    ("refund.failed", "refund_failed"),
    ("payment.dispute.created", "dispute_open"),
    ("payment.dispute.under_review", "dispute_open"),
    ("payment.dispute.action_required", "dispute_open"),
    ("payment.dispute.won", "dispute_won"),
    ("payment.dispute.lost", "dispute_lost"),
    ("payment.dispute.closed", "dispute_closed"),
]


@pytest.mark.parametrize(("event", "expected"), ADVERSE_EVENTS)
def test_adverse_webhook_updates_saved_order_once(
    client, db, verified_user, monkeypatch, event, expected
):
    monkeypatch.setattr(main.custom_design_api, "ENABLED", False)
    monkeypatch.setattr(main.financial_checkout, "ENABLED", False)
    monkeypatch.setattr(main, "RAZORPAY_WEBHOOK_SECRET", "synthetic-event-secret")
    monkeypatch.setattr(main, "send_monitoring_alert", lambda *args, **kwargs: False)

    def reject_provider_call(*args, **kwargs):
        raise AssertionError("Adverse notifications must not make a payment call")

    monkeypatch.setattr(main, "fetch_razorpay_payment", reject_provider_call)
    monkeypatch.setattr(main, "finalize_razorpay_payment", reject_provider_call)
    order = OrderSnapshot(
        user_id=verified_user.id,
        order_reference="order_adverse",
        status="placed",
        total=200,
        currency="INR",
        payment_status="verified",
        payment_reference="pay_adverse",
        source="razorpay_checkout",
    )
    db.add(order)
    db.commit()
    stocks = {p.id: p.stock_quantity for p in db.query(Product)}
    payment = {
        "id": "pay_adverse",
        "order_id": "order_adverse",
        "status": "captured",
        "amount": 20000,
        "currency": "INR",
    }
    payload = {
        "id": "evt_" + event.replace(".", "_"),
        "event": event,
        "payload": {"payment": {"entity": payment}},
    }
    kind = "refund" if event.startswith("refund.") else "dispute"
    payload["payload"][kind] = {
        "entity": {
            "id": "synthetic_adverse_reference",
            "payment_id": "pay_adverse",
            "amount": 20000,
            "currency": "INR",
        }
    }
    raw = json.dumps(payload).encode()
    headers = {
        "Content-Type": "application/json",
        "x-razorpay-signature": hmac.new(
            b"synthetic-event-secret", raw, hashlib.sha256
        ).hexdigest(),
    }
    response = client.post("/payments/razorpay/webhook", content=raw, headers=headers)
    assert response.status_code == 200
    db.refresh(order)
    assert order.payment_status == expected
    assert order.status == "placed"
    events_after_first = db.query(ExternalIntegrationEvent).count()

    duplicate = client.post("/payments/razorpay/webhook", content=raw, headers=headers)
    assert duplicate.status_code == 200
    db.refresh(order)
    assert order.payment_status == expected
    assert db.query(ExternalIntegrationEvent).count() == events_after_first
    assert {p.id: p.stock_quantity for p in db.query(Product)} == stocks


@pytest.mark.parametrize("payment_state", ["captured", "authorized", "failed"])
@pytest.mark.parametrize(("event", "expected"), ADVERSE_EVENTS)
def test_adverse_event_normalization_outranks_payment_entity(payment_state, event, expected):
    payment = main.razorpay_payment_status(event, {"status": payment_state})
    dispute = main.razorpay_dispute_status(event)
    assert payment or dispute
    assert (payment or dispute) == expected


def test_unhandled_refund_event_preserves_saved_status():
    assert main.razorpay_payment_status("refund.speed_changed", {"status": "captured"}) is None


@pytest.mark.parametrize(
    ("event", "payment_state", "expected"),
    [
        ("payment.captured", "captured", "verified"),
        ("payment.authorized", "authorized", "authorized"),
        ("payment.failed", "failed", "failed"),
        ("order.paid", "captured", "verified"),
        ("unknown", "unknown", None),
    ],
)
def test_existing_payment_normalization(event, payment_state, expected):
    assert main.razorpay_payment_status(event, {"status": payment_state}) == expected
