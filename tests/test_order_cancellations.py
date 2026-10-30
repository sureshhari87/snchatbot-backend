"""Synthetic isolated cancellation tests; no live financial calls."""

from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_financial_api import db as db
from test_financial_api import jewellery, jewellery_verify
from test_financial_api import provider as provider

import main
import order_cancellations as service
from database import Base
from models import OrderSnapshot, Product, User
from order_cancellation_models import OrderCancellation, OrderCancellationEvent
from rewards_vouchers_models import RewardEntry, RewardVoucherHold


@pytest.fixture(autouse=True)
def feature(monkeypatch):
    monkeypatch.setenv("ORDER_CANCELLATIONS_ENABLED", "1")
    monkeypatch.setenv("ORDER_REFUND_EXECUTION_ENABLED", "1")


@pytest.fixture
def paid(client, auth_headers, provider, db):
    checkout, _, product_id = jewellery(client, auth_headers, db)
    result, proof = jewellery_verify(client, auth_headers, provider, checkout)
    assert result.status_code == 200, result.text
    order = db.query(OrderSnapshot).filter_by(order_reference=checkout["order_id"]).one()
    return order.id, checkout["amount"], product_id, proof


def path(identity, admin=False):
    return ("/admin" if admin else "") + f"/orders/{identity}/cancellation-review"


def requested(client, headers, identity):
    response = client.post(
        path(identity), headers=headers, json={"reason": "Synthetic cancellation"}
    )
    assert response.status_code == 200, response.text
    return response.json()


def proposal(client, headers, identity, row, amount, deductions=None):
    return client.post(
        path(identity, True) + "/proposal",
        headers=headers,
        json=dict(
            version=row["version"],
            refund_paise=amount,
            reason="Synthetic itemized settlement",
            deductions=deductions or [],
        ),
    )


def decision(client, headers, identity, row, action="accept"):
    return client.post(
        path(identity) + "/decision",
        headers=headers,
        json=dict(
            version=row["version"], action=action, reason="Explicit synthetic customer decision"
        ),
    )


def accepted(client, customer, admin, identity, cash):
    row = requested(client, customer, identity)
    response = proposal(client, admin, identity, row, cash)
    assert response.status_code == 200, response.text
    response = decision(client, customer, identity, response.json())
    assert response.status_code == 200, response.text
    return response.json()


def fake_refunds(monkeypatch, provider, *, timeout=False, wrong=False):
    calls = []

    def create(payment, amount, receipt):
        calls.append((payment, amount, receipt))
        row = dict(
            id="rfnd_Cancel" + uuid4().hex,
            entity="refund",
            payment_id=payment,
            amount=amount + int(wrong),
            currency="INR",
            receipt=receipt,
            status="processed",
        )
        provider.refunds[row["id"]] = deepcopy(row)
        if timeout:
            raise TimeoutError("Synthetic ambiguous result")
        return row

    monkeypatch.setattr(main, "create_cancellation_refund", create)
    return calls


def execute(client, headers, identity, row):
    return client.post(
        path(identity, True) + "/refund",
        headers=headers,
        json=dict(version=row["version"], reason="Verify original payment and accepted terms"),
    )


def test_request_is_idempotent_without_order_or_financial_changes(client, auth_headers, db, paid):
    identity, _, product_id, _ = paid
    stock = db.get(Product, product_id).stock_quantity
    row = requested(client, auth_headers, identity)
    assert requested(client, auth_headers, identity)["id"] == row["id"]
    assert row["state"] == "requested"
    assert db.get(OrderSnapshot, identity).status == "placed"
    assert db.get(Product, product_id).stock_quantity == stock
    assert db.query(OrderCancellationEvent).count() == 1


def test_agreed_refund_posts_once_and_preserves_financial_ledgers(
    client, auth_headers, admin_headers, provider, db, paid, monkeypatch
):
    identity, cash, product_id, _ = paid
    stock = db.get(Product, product_id).stock_quantity
    points = [(r.id, r.points_delta) for r in db.query(RewardEntry)]
    row = accepted(client, auth_headers, admin_headers, identity, cash)
    calls = fake_refunds(monkeypatch, provider)
    for _ in range(2):
        response = execute(client, admin_headers, identity, row)
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "refunded"
    for _ in range(2):
        assert (
            client.post(path(identity, True) + "/reconcile", headers=admin_headers).status_code
            == 200
        )
    assert len(calls) == 1
    assert db.get(OrderSnapshot, identity).status == "cancelled"
    assert db.query(OrderCancellationEvent).filter_by(action="refund_processed").count() == 1
    assert db.query(RewardVoucherHold).count() == 1
    assert [(r.id, r.points_delta) for r in db.query(RewardEntry)] == points
    assert db.get(Product, product_id).stock_quantity == stock
    for status in ("placed", "shipped", "delivered"):
        assert (
            client.patch(
                f"/admin/orders/{identity}", headers=admin_headers, json={"status": status}
            ).status_code
            == 409
        )


def test_unknown_result_is_reconciled_without_second_post(
    client, auth_headers, admin_headers, provider, db, paid, monkeypatch
):
    identity, cash, _, _ = paid
    row = accepted(client, auth_headers, admin_headers, identity, cash)
    calls = fake_refunds(monkeypatch, provider, timeout=True)
    assert execute(client, admin_headers, identity, row).status_code == 503
    assert db.query(OrderCancellation).one().state == "refund_pending"
    assert execute(client, admin_headers, identity, row).status_code == 200
    response = client.post(path(identity, True) + "/reconcile", headers=admin_headers)
    assert response.status_code == 200 and response.json()["state"] == "refunded"
    assert len(calls) == 1


def test_proposal_math_stale_acceptance_and_dispute_history(
    client, auth_headers, admin_headers, db, paid
):
    identity, cash, _, _ = paid
    row = requested(client, auth_headers, identity)
    assert proposal(client, admin_headers, identity, row, cash - 1).status_code == 422
    row = proposal(client, admin_headers, identity, row, cash).json()
    stale = row.copy()
    row = proposal(client, admin_headers, identity, row, cash).json()
    assert decision(client, auth_headers, identity, stale).status_code == 409
    response = decision(client, auth_headers, identity, row, "dispute")
    assert response.status_code == 200 and response.json()["state"] == "disputed"
    assert db.query(OrderCancellationEvent).filter_by(action="proposal").count() == 2


@pytest.mark.parametrize(
    "status,tracking", [("shipped", ""), ("delivered", ""), ("processing", "TRACK")]
)
def test_dispatched_orders_require_separate_return_review(
    client, auth_headers, admin_headers, db, paid, status, tracking
):
    identity, cash, _, _ = paid
    order = db.get(OrderSnapshot, identity)
    order.status, order.tracking_number = status, tracking
    db.commit()
    row = requested(client, auth_headers, identity)
    assert row["state"] == "return_review"
    assert proposal(client, admin_headers, identity, row, cash).status_code == 409
    assert db.get(OrderSnapshot, identity).status == status


def test_owner_and_admin_boundaries(client, auth_headers, db, paid):
    identity, cash, _, _ = paid
    row = requested(client, auth_headers, identity)
    assert proposal(client, auth_headers, identity, row, cash).status_code == 403
    outsider = User(
        username="outsider",
        email="outsider@example.com",
        hashed_password="synthetic",
        is_verified=True,
    )
    db.add(outsider)
    db.commit()
    main.app.dependency_overrides[main.get_current_user] = lambda: outsider
    try:
        assert client.get(path(identity), headers=auth_headers).status_code == 404
        assert decision(client, auth_headers, identity, row).status_code == 404
    finally:
        main.app.dependency_overrides.pop(main.get_current_user, None)


def test_no_refund_without_agreement_and_no_direct_admin_bypass(
    client, auth_headers, admin_headers, paid
):
    identity, _, _, _ = paid
    row = requested(client, auth_headers, identity)
    assert execute(client, admin_headers, identity, row).status_code == 409
    for body in (
        dict(status="cancelled"),
        dict(status="refunded"),
        dict(payment_status="refunded"),
        dict(status="shipped"),
        dict(tracking_number="TRACK"),
    ):
        assert (
            client.patch(f"/admin/orders/{identity}", headers=admin_headers, json=body).status_code
            == 409
        )


def test_execution_default_off(client, auth_headers, admin_headers, paid, monkeypatch):
    identity, cash, _, _ = paid
    row = accepted(client, auth_headers, admin_headers, identity, cash)
    monkeypatch.delenv("ORDER_REFUND_EXECUTION_ENABLED")
    assert execute(client, admin_headers, identity, row).status_code == 503


def test_adverse_or_malformed_provider_never_dispatches(
    client, auth_headers, admin_headers, provider, paid, monkeypatch
):
    identity, cash, _, proof = paid
    row = accepted(client, auth_headers, admin_headers, identity, cash)
    calls = fake_refunds(monkeypatch, provider)
    provider.payments[proof["razorpay_payment_id"]]["amount_refunded"] = 1
    assert execute(client, admin_headers, identity, row).status_code == 409
    assert calls == []


def test_mismatched_refund_remains_pending(
    client, auth_headers, admin_headers, provider, db, paid, monkeypatch
):
    identity, cash, _, _ = paid
    row = accepted(client, auth_headers, admin_headers, identity, cash)
    calls = fake_refunds(monkeypatch, provider, wrong=True)
    assert execute(client, admin_headers, identity, row).status_code == 503
    assert len(calls) == 1 and db.query(OrderCancellation).one().state == "refund_pending"
    assert (
        client.post(path(identity, True) + "/reconcile", headers=admin_headers).status_code == 409
    )


def test_feature_default_off_does_not_query_cases(client, auth_headers, paid, monkeypatch):
    monkeypatch.delenv("ORDER_CANCELLATIONS_ENABLED")
    assert client.get(path(paid[0]), headers=auth_headers).status_code == 503


def test_stale_independent_writer_cannot_overwrite_decision(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "concurrency.sqlite"))
    Base.metadata.create_all(engine)
    with Session(engine) as first, Session(engine) as second:
        row = OrderCancellation(
            id="a" * 32,
            order_id=1,
            user_id=1,
            state="proposed",
            version=2,
            reason="Synthetic race",
            cash_paise=100,
            refund_paise=100,
        )
        first.add(row)
        first.commit()
        stale = second.get(OrderCancellation, row.id)
        service.change(first, row, 2, "accepted")
        first.commit()
        with pytest.raises(HTTPException) as error:
            service.change(second, stale, 2, "disputed")
        assert error.value.status_code == 409
        second.rollback()
        assert second.get(OrderCancellation, row.id).state == "accepted"
    engine.dispose()


def test_refund_header_and_original_payment_adapter(monkeypatch):
    received = []
    monkeypatch.setattr(main, "razorpay_checkout_is_configured", lambda: True)
    monkeypatch.setattr(main, "razorpay_api_headers", lambda: {"Authorization": "synthetic"})

    def send(method, url, payload, headers, timeout):
        received.append((method, url, deepcopy(payload), deepcopy(headers)))
        return 200, {"entity": "refund"}

    monkeypatch.setattr(main, "json_http_request", send)
    main.create_cancellation_refund("pay_Synthetic", 100, "sc_" + "a" * 32)
    assert received[0][3]["X-Refund-Idempotency"] == received[0][2]["receipt"]
    assert received[0][2]["amount"] == 100
    assert received[0][1].endswith("/payments/pay_Synthetic/refund")


def test_legacy_action_is_review_only_and_never_calls_oms(
    client, auth_headers, db, paid, monkeypatch
):
    identity, _, _, _ = paid
    order = db.get(OrderSnapshot, identity)

    def forbidden(*args, **kwargs):
        raise AssertionError("OMS must not receive cancellation/refund mutations")

    monkeypatch.setattr(main, "submit_oms_order_action", forbidden)
    for action in ("cancel", "refund"):
        response = client.post(
            f"/orders/{order.order_reference}/{action}",
            headers=auth_headers,
            json={"reason": "Synthetic request"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["cancellation"]["state"] == "requested"
    assert db.get(OrderSnapshot, identity).status == "placed"
    assert db.query(OrderCancellationEvent).count() == 1


def test_partial_refund_has_itemized_deduction_and_distinct_payment_status(
    client, auth_headers, admin_headers, provider, db, paid, monkeypatch
):
    identity, cash, _, _ = paid
    row = requested(client, auth_headers, identity)
    response = proposal(
        client,
        admin_headers,
        identity,
        row,
        cash - 500,
        [dict(amount_paise=500, reason="Synthetic agreed work cost")],
    )
    assert response.status_code == 200, response.text
    response = decision(client, auth_headers, identity, response.json())
    assert response.status_code == 200
    calls = fake_refunds(monkeypatch, provider)
    response = execute(client, admin_headers, identity, response.json())
    assert response.status_code == 200, response.text
    assert calls[0][1] == cash - 500
    assert db.get(OrderSnapshot, identity).payment_status == "partially_refunded"


def test_zero_refund_never_calls_provider_write(
    client, auth_headers, admin_headers, provider, paid, monkeypatch
):
    identity, cash, _, _ = paid
    row = requested(client, auth_headers, identity)
    response = proposal(
        client,
        admin_headers,
        identity,
        row,
        0,
        [dict(amount_paise=cash, reason="Synthetic agreed full deduction")],
    )
    assert response.status_code == 200
    response = decision(client, auth_headers, identity, response.json())
    assert response.status_code == 200
    calls = fake_refunds(monkeypatch, provider)
    response = execute(client, admin_headers, identity, response.json())
    assert response.status_code == 200 and response.json()["state"] == "cancelled"
    assert calls == []


def test_changed_accepted_amount_is_not_dispatched(
    client, auth_headers, admin_headers, provider, db, paid, monkeypatch
):
    identity, cash, _, _ = paid
    row = accepted(client, auth_headers, admin_headers, identity, cash)
    case = db.query(OrderCancellation).one()
    case.refund_paise = cash - 1
    db.commit()
    calls = fake_refunds(monkeypatch, provider)
    assert execute(client, admin_headers, identity, row).status_code == 409
    assert calls == []


@pytest.mark.parametrize("refund", [True, -1, 1.5])
def test_non_integer_or_negative_money_rejected(client, auth_headers, admin_headers, paid, refund):
    identity, _, _, _ = paid
    row = requested(client, auth_headers, identity)
    assert proposal(client, admin_headers, identity, row, refund).status_code == 422
