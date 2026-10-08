import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError
from test_savings_api import (
    open_checkout,
    payment,
    scheme,
    send_webhook,
    verification,
)
from test_savings_api import (
    provider as provider,
)
from test_savings_api import (
    savings_enabled as savings_enabled,
)

import main
from savings_models import SavingsAudit, SavingsEntry, SavingsHold


def hold(client, headers, row, reason="Store reconciliation required"):
    return client.post(
        f"/admin/savings/schemes/{row['id']}/hold",
        headers=headers,
        json={"reference": "review-1", "reason": reason},
    )


def test_admin_hold_blocks_operations_without_changing_balance(
    client, auth_headers, admin_headers, db
):
    row = scheme(client, auth_headers)
    p = payment(client, auth_headers, row)
    assert hold(client, auth_headers, row).status_code == 403
    result = hold(client, admin_headers, row)
    assert result.status_code == 200
    assert hold(client, admin_headers, row).json()["id"] == result.json()["id"]
    assert hold(client, admin_headers, row, "Changed review").status_code == 409
    current = client.get(f"/savings/schemes/{row['id']}", headers=auth_headers).json()
    assert current["review_required"] and current["state"] == "held"
    assert not current["redeemable"] and current["principal_paise"] == 0
    assert (
        client.post(
            f"/admin/savings/payments/{p['id']}/confirm",
            headers=admin_headers,
            json={"reference": "receipt"},
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/savings/schemes/{row['id']}/payments",
            headers=auth_headers,
            json={"request_key": "another", "mode": "manual"},
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/admin/savings/schemes/{row['id']}/redeem",
            headers=admin_headers,
            json={"reference": "redemption"},
        ).status_code
        == 409
    )
    assert (
        client.get(f"/admin/savings/schemes/{row['id']}/holds", headers=auth_headers).status_code
        == 403
    )
    assert (
        len(client.get(f"/admin/savings/schemes/{row['id']}/holds", headers=admin_headers).json())
        == 1
    )
    pending = client.get(f"/savings/schemes/{row['id']}/payments", headers=auth_headers).json()[0]
    assert not pending["can_checkout"] and not pending["can_reject"] and not pending["can_cancel"]
    assert db.query(SavingsEntry).count() == 0
    assert db.query(SavingsAudit).filter_by(action="review_hold_created").count() == 1


@pytest.mark.parametrize(
    "kind,event,reference",
    [
        ("refund", "refund.processed", "rfnd_review"),
        ("refund", "refund.created", "rfnd_review"),
        ("dispute", "payment.dispute.created", "disp_review"),
        ("dispute", "payment.dispute.won", "disp_review"),
    ],
)
def test_verified_provider_review_holds_once_without_reversal(
    client, auth_headers, admin_headers, provider, monkeypatch, db, kind, event, reference
):
    row = scheme(client, auth_headers)
    p = payment(client, auth_headers, row, mode="razorpay")
    checkout = open_checkout(client, auth_headers, p)
    assert (
        client.post(
            f"/savings/payments/{p['id']}/verify", headers=auth_headers, json=verification(checkout)
        ).status_code
        == 200
    )
    original = main.call_razorpay
    evidence = dict(
        id=reference,
        entity=kind,
        payment_id="pay_savings",
        currency="INR",
        amount=10000,
        status="processed" if kind == "refund" else "won",
    )
    monkeypatch.setattr(
        main,
        "call_razorpay",
        lambda method, path, payload=None: (
            (200, evidence) if path.endswith(reference) else original(method, path, payload)
        ),
    )
    event_body = {
        "event": event,
        "payload": {
            kind: {"entity": {"id": reference, "payment_id": "pay_savings", "amount": 999999999}}
        },
    }
    assert send_webhook(client, event_body, "wrong").status_code == 400
    first = send_webhook(client, event_body)
    assert first.status_code == 200 and first.json()["status"] == "held"
    assert send_webhook(client, event_body).json()["hold_id"] == first.json()["hold_id"]
    current = client.get(f"/savings/schemes/{row['id']}", headers=auth_headers).json()
    assert current["principal_paise"] == 50000 and current["review_required"]
    assert db.query(SavingsEntry).count() == 1 and db.query(SavingsHold).count() == 1
    assert (
        client.post(f"/savings/payments/{p['id']}/checkout", headers=auth_headers).status_code
        == 409
    )


def test_provider_review_outage_and_mismatch_never_hold(client, auth_headers, monkeypatch, db):
    row = scheme(client, auth_headers)
    p = payment(client, auth_headers, row, mode="razorpay")
    open_checkout(client, auth_headers, p)
    body = {
        "event": "refund.processed",
        "payload": {"refund": {"entity": {"id": "rfnd_review", "payment_id": "pay_savings"}}},
    }

    def fail(*args):
        raise RuntimeError("PRIVATE_PROVIDER_DETAIL")

    monkeypatch.setattr(main, "call_razorpay", fail)
    result = send_webhook(client, body)
    assert result.status_code == 503 and "PRIVATE_PROVIDER_DETAIL" not in result.text
    monkeypatch.setattr(
        main,
        "call_razorpay",
        lambda *args: (
            200,
            {
                "id": "rfnd_review",
                "entity": "refund",
                "payment_id": "pay_foreign",
                "amount": 10000,
                "currency": "INR",
            },
        ),
    )
    assert send_webhook(client, body).status_code == 409
    assert db.query(SavingsHold).count() == 0


def test_hold_migration_preserves_immutable_history(
    client, auth_headers, admin_headers, db, monkeypatch
):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0023_savings_holds.py"
    spec = importlib.util.spec_from_file_location("hold_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(db.connection())))
    module.downgrade()
    module.upgrade()
    db.commit()
    row = scheme(client, auth_headers)
    assert hold(client, admin_headers, row).status_code == 200
    for sql in ["UPDATE savings_holds SET reason = 'changed'", "DELETE FROM savings_holds"]:
        with pytest.raises(DatabaseError, match="immutable"):
            db.execute(text(sql))
        db.rollback()
    monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(db.connection())))
    with pytest.raises(RuntimeError, match="preservation"):
        module.downgrade()
    assert db.query(SavingsHold).count() == 1


@pytest.mark.parametrize("alter", ["none", "receipt", "purpose"])
def test_refund_matches_uncertain_checkout_using_trusted_receipt(
    client, auth_headers, provider, monkeypatch, db, alter
):
    row = scheme(client, auth_headers)
    p = payment(client, auth_headers, row, mode="razorpay")
    original = main.call_razorpay

    def timeout(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("synthetic timeout")

    monkeypatch.setattr(main, "call_razorpay", timeout)
    assert (
        client.post(f"/savings/payments/{p['id']}/checkout", headers=auth_headers).status_code
        == 503
    )
    order = next(iter(provider[0].values()))
    order["status"] = "paid"
    if alter == "receipt":
        order["receipt"] = "foreign_receipt"
    if alter == "purpose":
        order["notes"] = dict(order["notes"], purpose="merchandise")
    refund = dict(
        id="rfnd_uncertain",
        entity="refund",
        payment_id="pay_savings",
        currency="INR",
        amount=10000,
        status="processed",
    )
    monkeypatch.setattr(
        main,
        "call_razorpay",
        lambda method, path, payload=None: (
            (200, refund) if path == "refunds/rfnd_uncertain" else original(method, path, payload)
        ),
    )
    body = {
        "event": "refund.processed",
        "payload": {"refund": {"entity": {"id": "rfnd_uncertain", "payment_id": "pay_savings"}}},
    }
    result = send_webhook(client, body)
    assert result.status_code == (409 if alter == "receipt" else 200)
    if alter != "receipt":
        assert result.json()["status"] == ("held" if alter == "none" else "ignored")
    assert db.query(SavingsHold).count() == int(alter == "none")
    assert db.query(SavingsEntry).count() == 0
    from savings_models import SavingsPayment

    assert db.get(SavingsPayment, p["id"]).provider_order_id is None


@pytest.mark.parametrize("bad_id", ["0", "-1", str(2**31), str(2**100)])
def test_financial_path_ids_are_bounded(client, auth_headers, admin_headers, bad_id):
    assert client.get(f"/savings/schemes/{bad_id}", headers=auth_headers).status_code == 422
    assert (
        client.post(
            f"/savings/payments/{bad_id}/cancel", headers=auth_headers, json={"reason": "unused"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/admin/savings/schemes/{bad_id}/hold",
            headers=admin_headers,
            json={"reference": "case", "reason": "review"},
        ).status_code
        == 422
    )


def test_malformed_provider_ids_do_not_make_provider_calls(client, provider):
    body = {
        "event": "refund.processed",
        "payload": {"refund": {"entity": {"id": "rfnd_/../payments", "payment_id": "pay_savings"}}},
    }
    before = len(provider[1])
    assert send_webhook(client, body).status_code == 400
    assert len(provider[1]) == before
