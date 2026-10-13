"""Server-owned balance posting and full-payment/no-hold completion gates."""

import json

import pytest
from custom_balance_fakes import Provider
from test_custom_design_quotes import create, decide, quote

import custom_design_api
import custom_design_balance as balance
import main
from custom_design_balance_models import CustomDesignBalanceCheckout as Checkout
from custom_design_balance_models import CustomDesignBalanceEntry as Entry
from custom_design_balance_models import CustomDesignBalanceRequest as BalanceRequest
from custom_design_models import CustomDesignAudit
from custom_design_payments import CustomDesignHold
from models import CustomOrderRequest, User


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(custom_design_api, "ENABLED", True)
    monkeypatch.setattr(balance, "ENABLED", True)
    monkeypatch.setattr(balance, "ONLINE", True)


@pytest.fixture
def provider(monkeypatch):
    fake = Provider()
    for name in [
        "call_razorpay",
        "razorpay_checkout_is_configured",
        "razorpay_checkout_signature_is_valid",
        "create_customer_action_lead",
    ]:
        monkeypatch.setattr(main, name, getattr(fake, name))
    monkeypatch.setattr(main, "RAZORPAY_KEY_ID", fake.RAZORPAY_KEY_ID)
    monkeypatch.setattr(main, "razorpay_webhook_is_configured", lambda: True)
    monkeypatch.setattr(main, "razorpay_signature_is_valid", lambda raw, s: s == "synthetic")
    return fake


def setup(client, auth_headers, admin_headers, advance=0):
    row = create(client, auth_headers).json()
    q = quote(client, admin_headers, row, advance_paise=advance).json()
    response = decide(client, auth_headers, row, q)
    assert response.status_code == 200
    return row, q


def request(client, headers, row, q, mode="manual", **changes):
    value = {"request_key": "balance-test", "quote_id": q["id"], "mode": mode}
    value.update(changes)
    return client.post(f"/custom-designs/{row['id']}/balance/request", headers=headers, json=value)


def manual(client, headers, row, rid, **changes):
    value = {
        "request_id": rid,
        "reference": "SYNTHETIC-CASH-001",
        "reason": "Synthetic verified cash receipt",
    }
    value.update(changes)
    return client.post(
        f"/admin/custom-designs/{row['id']}/balance/confirm-manual", headers=headers, json=value
    )


def manufacture(client, headers, row):
    return client.post(
        f"/admin/custom-designs/{row['id']}/manufacturing/start",
        headers=headers,
        json={"request_key": "manufacture-balance", "reason": "Synthetic manufacturing"},
    )


def complete(client, headers, row, **changes):
    value = {"request_key": "complete-balance", "reason": "Synthetic fully paid completion"}
    value.update(changes)
    return client.post(f"/admin/custom-designs/{row['id']}/complete", headers=headers, json=value)


def checkout(client, headers, row):
    return client.post(f"/custom-designs/{row['id']}/balance/checkout", headers=headers)


def verify(client, headers, row, oid, pid, **changes):
    value = {"razorpay_order_id": oid, "razorpay_payment_id": pid, "razorpay_signature": "0" * 64}
    value.update(changes)
    return client.post(f"/custom-designs/{row['id']}/balance/verify", headers=headers, json=value)


def webhook(client, oid, pid, path="/custom-designs/payments/razorpay/webhook"):
    return client.post(
        path,
        headers={"x-razorpay-signature": "synthetic"},
        content=json.dumps(
            {
                "event": "payment.captured",
                "payload": {"payment": {"entity": {"id": pid, "order_id": oid}}},
            }
        ),
    )


def test_manual_full_payment_and_idempotent_completion(
    client, db, auth_headers, admin_headers, provider
):
    row, q = setup(client, auth_headers, admin_headers)
    first = request(client, auth_headers, row, q)
    assert first.status_code == 200
    rid = first.json()["balance_request"]["id"]
    assert first.json()["balance_request"]["amount_paise"] == q["total_paise"]
    assert db.query(Entry).count() == 0
    assert (
        request(client, auth_headers, row, q, request_key="different-key").json()[
            "balance_request"
        ]["id"]
        == rid
    )
    assert db.query(BalanceRequest).count() == 1
    assert complete(client, admin_headers, row).status_code == 409
    posted = manual(client, admin_headers, row, rid)
    assert posted.status_code == 200
    assert posted.json()["paid_total_paise"] == q["total_paise"]
    assert posted.json()["balance_due_paise"] == 0
    assert posted.json()["completion_available"] is False
    assert manual(client, admin_headers, row, rid).status_code == 200
    assert manual(client, admin_headers, row, rid, reference="different-receipt").status_code == 409
    assert db.query(Entry).count() == 1
    assert (
        db.query(CustomDesignAudit).filter_by(design_id=row["id"], action="balance_posted").count()
        == 1
    )
    assert manufacture(client, admin_headers, row).status_code == 200
    done = complete(client, admin_headers, row)
    assert done.status_code == 200 and done.json()["completed"] is True
    assert complete(client, admin_headers, row).status_code == 200
    assert (
        complete(client, admin_headers, row, request_key="different-completion").status_code == 409
    )
    assert (
        db.query(CustomDesignAudit).filter_by(design_id=row["id"], action="completed").count() == 1
    )
    assert db.get(CustomOrderRequest, row["custom_order_id"]).status == "completed"
    assert provider.posts == 0


def test_body_cannot_choose_money_or_change_quote_or_mode(
    client, db, auth_headers, admin_headers, provider
):
    row, q = setup(client, auth_headers, admin_headers)
    assert request(client, auth_headers, row, q, amount_paise=1).status_code == 422
    assert request(client, auth_headers, row, q, quote_id=q["id"] + 100).status_code == 409
    assert request(client, auth_headers, row, q).status_code == 200
    assert request(client, auth_headers, row, q, mode="razorpay").status_code == 409
    changed = quote(
        client,
        admin_headers,
        row,
        request_key="new-quote",
        advance_paise=0,
        total_paise=q["total_paise"] + 100,
    )
    assert changed.status_code == 409
    assert db.query(BalanceRequest).one().amount_paise == q["total_paise"]


def test_held_manual_request_cannot_post_or_complete(
    client, db, auth_headers, admin_headers, provider
):
    row, q = setup(client, auth_headers, admin_headers)
    rid = request(client, auth_headers, row, q).json()["balance_request"]["id"]
    assert (
        client.post(
            f"/admin/custom-designs/{row['id']}/hold",
            headers=admin_headers,
            json={"reference": "synthetic-review", "reason": "Synthetic audited review"},
        ).status_code
        == 200
    )
    assert manual(client, admin_headers, row, rid).status_code == 409
    assert complete(client, admin_headers, row).status_code == 409
    assert db.query(Entry).count() == 0 and db.query(CustomDesignHold).count() == 1


def test_required_advance_must_be_verified_before_remaining_request(
    client, db, auth_headers, admin_headers, provider
):
    row, q = setup(client, auth_headers, admin_headers, advance=10000)
    assert request(client, auth_headers, row, q).status_code == 409
    assert db.query(BalanceRequest).count() == 0
    original = client.post(f"/custom-designs/{row['id']}/checkout", headers=auth_headers)
    assert original.status_code == 200
    oid = original.json()["order_id"]
    pid = provider.payment(oid)
    proof = {"razorpay_order_id": oid, "razorpay_payment_id": pid, "razorpay_signature": "0" * 64}
    assert (
        client.post(
            f"/custom-designs/{row['id']}/verify", headers=auth_headers, json=proof
        ).status_code
        == 200
    )
    result = request(client, auth_headers, row, q)
    assert result.status_code == 200
    assert result.json()["balance_request"]["amount_paise"] == q["total_paise"] - q["advance_paise"]


@pytest.mark.parametrize(
    "path", ["/custom-designs/payments/razorpay/webhook", "/payments/razorpay/webhook"]
)
def test_online_capture_duplicate_webhook_and_full_completion(
    client, db, auth_headers, admin_headers, provider, path
):
    row, q = setup(client, auth_headers, admin_headers)
    assert request(client, auth_headers, row, q, mode="razorpay").status_code == 200
    first = checkout(client, auth_headers, row)
    assert first.status_code == 200
    oid = first.json()["order_id"]
    assert checkout(client, auth_headers, row).json()["order_id"] == oid
    assert provider.posts == 1
    pid = provider.payment(oid)
    assert verify(client, auth_headers, row, oid, pid).status_code == 200
    assert webhook(client, oid, pid, path).status_code == 200
    assert webhook(client, oid, pid, path).status_code == 200
    assert db.query(Entry).count() == 1
    assert (
        db.query(CustomDesignAudit).filter_by(design_id=row["id"], action="balance_posted").count()
        == 1
    )
    assert manufacture(client, admin_headers, row).status_code == 200
    assert complete(client, admin_headers, row).status_code == 200
    assert (
        db.query(CustomDesignAudit).filter_by(design_id=row["id"], action="completed").count() == 1
    )


def test_missing_disputes_evidence_retains_paid_pending_and_blocks_new_payment(
    client, db, auth_headers, admin_headers, provider
):
    row, q = setup(client, auth_headers, admin_headers)
    request(client, auth_headers, row, q, mode="razorpay")
    opened = checkout(client, auth_headers, row).json()
    pid = provider.payment(opened["order_id"])
    provider.uncertain = True
    assert verify(client, auth_headers, row, opened["order_id"], pid).status_code == 503
    state = client.get(f"/custom-designs/{row['id']}", headers=auth_headers).json()
    assert state["balance_request"]["state"] == "awaiting_verification"
    assert state["balance_checkout_available"] is False
    assert checkout(client, auth_headers, row).status_code == 409
    assert provider.posts == 1 and db.query(Entry).count() == 0
    provider.uncertain = False
    assert (
        client.post(
            f"/custom-designs/{row['id']}/balance/refresh", headers=auth_headers
        ).status_code
        == 200
    )
    assert db.query(Entry).count() == 1


def test_verified_failed_attempt_creates_new_bound_order(
    client, db, auth_headers, admin_headers, provider
):
    row, q = setup(client, auth_headers, admin_headers)
    request(client, auth_headers, row, q, mode="razorpay")
    first = checkout(client, auth_headers, row).json()
    provider.payment(first["order_id"], status="failed")
    second = checkout(client, auth_headers, row)
    assert second.status_code == 200 and second.json()["order_id"] != first["order_id"]
    assert provider.posts == 2 and db.query(Checkout).count() == 2
    assert db.query(Checkout).order_by(Checkout.id).first().state == "closed_failed"


def test_uncertain_checkout_is_reconciled_without_second_order(
    client, db, auth_headers, admin_headers, provider
):
    row, q = setup(client, auth_headers, admin_headers)
    request(client, auth_headers, row, q, mode="razorpay")
    provider.unknown = True
    assert checkout(client, auth_headers, row).status_code == 503
    assert checkout(client, auth_headers, row).status_code == 409
    assert provider.posts == 1
    provider.unknown = False
    oid = next(iter(provider.orders))
    response = client.post(
        f"/admin/custom-designs/{row['id']}/balance/reconcile-checkout",
        headers=admin_headers,
        json={"provider_order_id": oid, "reason": "Synthetic existing-order evidence"},
    )
    assert response.status_code == 200
    assert checkout(client, auth_headers, row).json()["order_id"] == oid
    assert provider.posts == 1


def test_refund_holds_existing_posting_without_adjustment_or_completion(
    client, db, auth_headers, admin_headers, provider
):
    row, q = setup(client, auth_headers, admin_headers)
    request(client, auth_headers, row, q, mode="razorpay")
    opened = checkout(client, auth_headers, row).json()
    pid = provider.payment(opened["order_id"])
    assert verify(client, auth_headers, row, opened["order_id"], pid).status_code == 200
    assert manufacture(client, admin_headers, row).status_code == 200
    provider.payments[pid].update(amount_refunded=100, refund_status="partial")
    assert complete(client, admin_headers, row).status_code == 409
    assert db.query(Entry).one().amount_paise == q["total_paise"]
    assert db.query(CustomDesignHold).count() == 1
    assert (
        db.query(CustomDesignAudit).filter_by(design_id=row["id"], action="completed").count() == 0
    )


def test_customer_cannot_confirm_manual_or_complete_and_other_owner_cannot_read(
    client, db, auth_headers, admin_headers, provider
):
    row, q = setup(client, auth_headers, admin_headers)
    rid = request(client, auth_headers, row, q).json()["balance_request"]["id"]
    assert manual(client, auth_headers, row, rid).status_code == 403
    assert complete(client, auth_headers, row).status_code == 403
    other = User(
        username="balance-other",
        email="balance-other@example.test",
        hashed_password="synthetic-only",
        is_verified=True,
        is_admin=False,
    )
    db.add(other)
    db.commit()
    token = main.create_access_token({"sub": other.email})
    # Test-only credential stays in process memory and is never printed.
    headers = {"Authorization": "Bearer " + token}
    assert request(client, headers, row, q).status_code == 404
    assert client.get(f"/custom-designs/{row['id']}", headers=headers).status_code == 404
    assert db.query(Entry).count() == 0


def test_missing_advance_audit_blocks_balance_accounting(
    client, db, auth_headers, admin_headers, provider
):
    row, q = setup(client, auth_headers, admin_headers, advance=10000)
    opened = client.post(f"/custom-designs/{row['id']}/checkout", headers=auth_headers).json()
    pid = provider.payment(opened["order_id"])
    proof = {
        "razorpay_order_id": opened["order_id"],
        "razorpay_payment_id": pid,
        "razorpay_signature": "0" * 64,
    }
    assert (
        client.post(
            f"/custom-designs/{row['id']}/verify", headers=auth_headers, json=proof
        ).status_code
        == 200
    )
    # Corruption simulation in an isolated test database, never live history.
    db.query(CustomDesignAudit).filter_by(design_id=row["id"], action="advance_posted").delete()
    db.commit()
    assert request(client, auth_headers, row, q).status_code == 409
    saved = client.get(f"/custom-designs/{row['id']}", headers=auth_headers).json()
    assert saved["payment_consistent"] is False
    assert saved["balance_request_available"] is False
    assert db.query(BalanceRequest).count() == 0


def test_generic_order_only_paid_event_posts_existing_balance(
    client, db, auth_headers, admin_headers, provider
):
    row, q = setup(client, auth_headers, admin_headers)
    assert request(client, auth_headers, row, q, mode="razorpay").status_code == 200
    opened = checkout(client, auth_headers, row).json()
    provider.payment(opened["order_id"])
    payload = {"event": "order.paid", "payload": {"order": {"entity": {"id": opened["order_id"]}}}}
    for _ in range(2):
        response = client.post(
            "/payments/razorpay/webhook",
            headers={"x-razorpay-signature": "synthetic"},
            content=json.dumps(payload),
        )
        assert response.status_code == 200
    assert db.query(Entry).filter_by(design_id=row["id"]).count() == 1
    assert (
        db.query(CustomDesignAudit).filter_by(design_id=row["id"], action="balance_posted").count()
        == 1
    )
    assert provider.posts == 1


def test_unrelated_order_only_event_does_not_enter_custom_payment_domain(db, provider):
    import custom_design_payment_api

    assert (
        custom_design_payment_api.generic_operation(
            db,
            {"event": "order.paid", "payload": {"order": {"entity": {"id": "order_otherdomain"}}}},
            provider,
        )
        is None
    )
    assert provider.posts == 0
    assert db.query(Entry).count() == 0
