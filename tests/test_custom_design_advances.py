import importlib.util
import json
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError
from test_custom_design_quotes import create, decide, quote

import custom_design_api
import main
from custom_design_models import CustomDesignAudit
from custom_design_payments import CustomDesignAdvance, CustomDesignCheckout, CustomDesignHold


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(custom_design_api, "ENABLED", True)


@pytest.fixture
def provider(monkeypatch):
    data = {
        "orders": {},
        "posts": 0,
        "unknown": False,
        "refunded": False,
        "foreign": False,
        "outage": False,
    }
    monkeypatch.setattr(main, "RAZORPAY_KEY_ID", "rzp_test_synthetic_custom")
    monkeypatch.setattr(main, "razorpay_checkout_is_configured", lambda: True)
    monkeypatch.setattr(main, "razorpay_webhook_is_configured", lambda: True)
    monkeypatch.setattr(main, "razorpay_checkout_signature_is_valid", lambda o, p, s: s == "0" * 64)
    monkeypatch.setattr(main, "razorpay_signature_is_valid", lambda raw, s: s == "synthetic")

    def call(method, path, payload=None):
        if data["outage"]:
            raise OSError("PRIVATE_PROVIDER_ERROR")
        if method == "POST":
            assert path == "orders"
            data["posts"] += 1
            order = dict(payload, id="order_custom", entity="order", status="created")
            data["orders"][order["id"]] = order
            if data["unknown"]:
                raise OSError("PRIVATE_UNCERTAIN_CREATE")
            return 200, dict(order)
        if path == "orders/order_custom":
            return 200, dict(data["orders"]["order_custom"])
        if path == "orders/order_custom/payments":
            return 200, {
                "entity": "collection",
                "count": 1,
                "items": [{"id": "pay_custom", "status": "captured"}],
            }
        if path == "payments/pay_custom":
            return 200, dict(
                id="pay_custom",
                entity="payment",
                order_id="order_foreign" if data["foreign"] else "order_custom",
                amount=data["orders"]["order_custom"]["amount"],
                currency="INR",
                status="captured",
                captured=True,
                amount_refunded=100 if data["refunded"] else 0,
                refund_status="partial" if data["refunded"] else None,
            )
        if path == "refunds/rfnd_custom":
            return 200, dict(
                id="rfnd_custom",
                entity="refund",
                payment_id="pay_custom",
                amount=100,
                currency="INR",
                status="processed",
            )
        if path == "disputes/disp_custom":
            return 200, dict(
                id="disp_custom",
                entity="dispute",
                payment_id="pay_custom",
                amount=100,
                currency="INR",
                status="open",
            )
        return 404, {}

    monkeypatch.setattr(main, "call_razorpay", call)
    return data


def accepted(client, auth_headers, admin_headers, advance=500000):
    row = create(client, auth_headers).json()
    q = quote(client, admin_headers, row, advance_paise=advance).json()
    response = decide(client, auth_headers, row, q)
    assert response.status_code == 200
    return row, q


def open_checkout(client, headers, row):
    return client.post(f"/custom-designs/{row['id']}/checkout", headers=headers)


def verify(client, headers, row, **extra):
    body = dict(
        razorpay_order_id="order_custom",
        razorpay_payment_id="pay_custom",
        razorpay_signature="0" * 64,
    )
    body.update(extra)
    return client.post(f"/custom-designs/{row['id']}/verify", headers=headers, json=body)


def webhook(client, event="payment.captured", entity=None):
    kind = (
        "payment"
        if event in ("payment.captured", "order.paid")
        else "refund"
        if event.startswith("refund.")
        else "dispute"
    )
    if entity is None:
        entity = (
            dict(id="pay_custom", order_id="order_custom")
            if kind == "payment"
            else dict(id="rfnd_custom" if kind == "refund" else "disp_custom")
        )
    body = {"event": event, "payload": {kind: {"entity": entity}}}
    return client.post(
        "/custom-designs/payments/razorpay/webhook",
        content=json.dumps(body),
        headers={"x-razorpay-signature": "synthetic"},
    )


def start(client, headers, row, **extra):
    body = dict(request_key="start-one", reason="Accepted design and verified advance")
    body.update(extra)
    return client.post(
        f"/admin/custom-designs/{row['id']}/manufacturing/start", headers=headers, json=body
    )


def test_checkout_uses_accepted_quote_once_and_freezes_terms(
    client, auth_headers, admin_headers, provider, db
):
    row = create(client, auth_headers).json()
    q = quote(client, admin_headers, row).json()
    assert open_checkout(client, auth_headers, row).status_code == 409
    assert decide(client, auth_headers, row, q).status_code == 200
    first = open_checkout(client, auth_headers, row)
    assert first.status_code == 200 and first.json()["amount_paise"] == 500000
    assert open_checkout(client, auth_headers, row).json() == first.json()
    assert provider["posts"] == 1 and db.query(CustomDesignCheckout).count() == 1
    assert quote(client, admin_headers, row, request_key="quote-new").status_code == 409
    assert (
        decide(client, auth_headers, row, q, request_key="changed", action="rejected").status_code
        == 409
    )


def test_verify_refresh_and_duplicate_webhooks_keep_one_credit(
    client, auth_headers, admin_headers, provider, db
):
    row, _ = accepted(client, auth_headers, admin_headers)
    assert open_checkout(client, auth_headers, row).status_code == 200
    provider["orders"]["order_custom"]["status"] = "paid"
    first = client.post(f"/custom-designs/{row['id']}/refresh", headers=auth_headers)
    assert first.status_code == 200 and first.json()["advance"]["state"] == "posted"
    assert verify(client, auth_headers, row).status_code == 200
    one = webhook(client)
    two = webhook(client, "order.paid")
    assert one.status_code == two.status_code == 200 and one.json() == two.json()
    assert db.query(CustomDesignAdvance).count() == 1
    assert db.query(CustomDesignAudit).filter_by(action="advance_posted").count() == 1
    assert open_checkout(client, auth_headers, row).status_code == 409
    assert provider["posts"] == 1


def test_unknown_create_requires_audited_binding_without_second_order(
    client, auth_headers, admin_headers, provider, db
):
    row, _ = accepted(client, auth_headers, admin_headers)
    provider["unknown"] = True
    response = open_checkout(client, auth_headers, row)
    assert response.status_code == 503 and "PRIVATE" not in response.text
    assert db.query(CustomDesignCheckout).one().state == "unknown"
    assert open_checkout(client, auth_headers, row).status_code == 409 and provider["posts"] == 1
    assert (
        client.post(f"/custom-designs/{row['id']}/refresh", headers=auth_headers).status_code == 409
    )
    response = client.post(
        f"/admin/custom-designs/{row['id']}/reconcile-checkout",
        headers=admin_headers,
        json={"provider_order_id": "order_custom"},
    )
    assert response.status_code == 200
    assert open_checkout(client, auth_headers, row).json()["order_id"] == "order_custom"
    assert provider["posts"] == 1


@pytest.mark.parametrize("alter", ["signature", "foreign", "outage"])
def test_invalid_or_unavailable_capture_does_not_post(
    client, auth_headers, admin_headers, provider, db, alter
):
    row, _ = accepted(client, auth_headers, admin_headers)
    assert open_checkout(client, auth_headers, row).status_code == 200
    if alter == "signature":
        response = verify(client, auth_headers, row, razorpay_signature="1" * 64)
        assert response.status_code == 400
    else:
        provider[alter] = True
        response = verify(client, auth_headers, row)
        assert response.status_code == (503 if alter == "outage" else 409)
    assert "PRIVATE" not in response.text and db.query(CustomDesignAdvance).count() == 0


def test_manufacturing_requires_acceptance_and_advance_and_keeps_reason(
    client, auth_headers, admin_headers, provider, db
):
    row = create(client, auth_headers).json()
    q = quote(client, admin_headers, row).json()
    assert start(client, admin_headers, row).status_code == 409
    assert decide(client, auth_headers, row, q).status_code == 200
    assert start(client, admin_headers, row).status_code == 409
    assert open_checkout(client, auth_headers, row).status_code == 200
    assert verify(client, auth_headers, row).status_code == 200
    first = start(client, admin_headers, row)
    assert first.status_code == 200 and first.json()["manufacturing_started"]
    assert first.json()["completion_available"] is False
    assert start(client, admin_headers, row).status_code == 200
    assert start(client, admin_headers, row, reason="Changed reason").status_code == 409
    assert (
        db.query(CustomDesignAudit).filter_by(action="manufacturing_started").one().detail
        == "Accepted design and verified advance"
    )
    assert start(client, auth_headers, row).status_code == 403


def test_zero_advance_needs_no_checkout_but_quote_is_frozen_after_start(
    client, auth_headers, admin_headers, provider
):
    row, _ = accepted(client, auth_headers, admin_headers, advance=0)
    assert open_checkout(client, auth_headers, row).status_code == 409 and provider["posts"] == 0
    assert start(client, admin_headers, row).status_code == 200
    assert quote(client, admin_headers, row, request_key="new").status_code == 409


@pytest.mark.parametrize(
    "event",
    ["refund.created", "refund.processed", "payment.dispute.created", "payment.dispute.won"],
)
def test_provider_review_preserves_paid_advance_and_blocks_manufacturing(
    client, auth_headers, admin_headers, provider, db, event
):
    row, _ = accepted(client, auth_headers, admin_headers)
    assert open_checkout(client, auth_headers, row).status_code == 200
    assert verify(client, auth_headers, row).status_code == 200
    first = webhook(client, event)
    assert first.status_code == 200 and first.json()["status"] == "held"
    assert webhook(client, event).json() == first.json()
    assert db.query(CustomDesignAdvance).one().amount_paise == 500000
    assert db.query(CustomDesignHold).count() == 1
    assert start(client, admin_headers, row).status_code == 409
    assert quote(client, admin_headers, row, request_key="new").status_code == 409
    assert client.get(f"/custom-designs/{row['id']}", headers=auth_headers).json()[
        "review_required"
    ]


def test_refunded_capture_is_held_without_creating_spendable_credit(
    client, auth_headers, admin_headers, provider, db
):
    row, _ = accepted(client, auth_headers, admin_headers)
    assert open_checkout(client, auth_headers, row).status_code == 200
    provider["refunded"] = True
    assert verify(client, auth_headers, row).status_code == 409
    assert db.query(CustomDesignHold).count() == 1 and db.query(CustomDesignAdvance).count() == 0
    assert start(client, admin_headers, row).status_code == 409


def test_customer_and_webhook_security_boundaries(
    client, auth_headers, admin_headers, provider, db
):
    row, _ = accepted(client, auth_headers, admin_headers)
    assert open_checkout(client, admin_headers, row).status_code == 404
    assert open_checkout(client, auth_headers, row).status_code == 200
    assert verify(client, admin_headers, row).status_code == 404
    assert (
        client.post(
            "/custom-designs/payments/razorpay/webhook",
            content="{}",
            headers={"x-razorpay-signature": "wrong"},
        ).status_code
        == 400
    )
    assert db.query(CustomDesignAdvance).count() == 0


def test_advance_migration_guards_history_and_downgrade(
    client, auth_headers, admin_headers, provider, db, monkeypatch
):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0026_custom_design_advances.py"
    spec = importlib.util.spec_from_file_location("custom_advance_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(db.connection())))
    module.downgrade()
    module.upgrade()
    db.commit()
    row, _ = accepted(client, auth_headers, admin_headers)
    assert open_checkout(client, auth_headers, row).status_code == 200
    assert verify(client, auth_headers, row).status_code == 200
    assert webhook(client, "refund.processed").status_code == 200
    for table in module.HISTORY:
        for command in (f"UPDATE {table} SET id=id", f"DELETE FROM {table}"):
            with pytest.raises(DatabaseError, match="immutable"):
                db.execute(text(command))
            db.rollback()
    monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(db.connection())))
    with pytest.raises(RuntimeError, match="preservation"):
        module.downgrade()


def test_paid_cancellation_review_is_owned_idempotent_and_keeps_credit(
    client, auth_headers, admin_headers, provider, db
):
    row, _ = accepted(client, auth_headers, admin_headers)
    path = f"/custom-designs/{row['id']}/cancellation-review"
    body = {"request_key": "cancel-review", "reason": "Customer changed the design"}
    assert client.post(path, headers=auth_headers, json=body).status_code == 409
    assert open_checkout(client, auth_headers, row).status_code == 200
    assert verify(client, auth_headers, row).status_code == 200
    assert client.post(path, headers=admin_headers, json=body).status_code == 404
    first = client.post(path, headers=auth_headers, json=body)
    assert first.status_code == 200 and first.json()["review_required"]
    assert client.post(path, headers=auth_headers, json=body).json() == first.json()
    assert (
        client.post(path, headers=auth_headers, json={**body, "reason": "Changed"}).status_code
        == 409
    )
    assert db.query(CustomDesignAdvance).one().amount_paise == 500000
    assert db.query(CustomDesignHold).count() == 1
    assert start(client, admin_headers, row).status_code == 409


def test_reconciliation_is_read_only_and_never_authorizes_release(
    client, auth_headers, admin_headers, provider, db
):
    row, _ = accepted(client, auth_headers, admin_headers)
    assert open_checkout(client, auth_headers, row).status_code == 200
    assert verify(client, auth_headers, row).status_code == 200
    path = f"/admin/custom-designs/{row['id']}/reconciliation"
    assert client.get(path, headers=auth_headers).status_code == 403
    before = db.query(CustomDesignAudit).count()
    report = client.get(path, headers=admin_headers).json()
    assert report["consistent"] and report["issues"] == []
    assert report["advance_paise"] == 500000 and report["advance_entry_count"] == 1
    assert report["provider_verified"] is report["release_authorized"] is False
    assert report["completion_available"] is False
    assert db.query(CustomDesignAudit).count() == before
    assert webhook(client, "refund.processed").status_code == 200
    report = client.get(path, headers=admin_headers).json()
    assert report["consistent"] and report["review_required"] and report["hold_count"] == 1
    history = client.get(f"/admin/custom-designs/{row['id']}/holds?limit=20", headers=admin_headers)
    assert history.status_code == 200 and len(history.json()) == 1


def test_disabled_custom_payments_and_webhook_make_no_financial_queries(
    client, auth_headers, db, monkeypatch
):
    from sqlalchemy import event

    monkeypatch.setattr(custom_design_api, "ENABLED", False)
    statements = []

    def capture(conn, cursor, statement, *args):
        statements.append(statement.lower())

    event.listen(db.get_bind(), "before_cursor_execute", capture)
    try:
        for path in ("/custom-designs/1/checkout", "/custom-designs/1/refresh"):
            assert client.post(path, headers=auth_headers).status_code == 503
        assert (
            client.post("/custom-designs/payments/razorpay/webhook", content="{}").status_code
            == 503
        )
        assert not any("custom_design" in statement for statement in statements)
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", capture)
