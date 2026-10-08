from datetime import timedelta

import pytest
from test_savings_api import open_checkout, payment, scheme, send_webhook, verification
from test_savings_api import provider as provider
from test_savings_api import savings_enabled as savings_enabled

import main
import savings_reconciliation
from models import utc_now
from savings_models import SavingsAudit, SavingsEntry, SavingsHold, SavingsPayment


def report(client, headers, row):
    return client.get(f"/admin/savings/schemes/{row['id']}/reconciliation", headers=headers)


def test_report_is_admin_only_disabled_and_read_only(
    client, auth_headers, admin_headers, db, monkeypatch
):
    row = scheme(client, auth_headers)
    p = payment(client, auth_headers, row)
    assert (
        client.post(
            f"/admin/savings/payments/{p['id']}/confirm",
            headers=admin_headers,
            json={"reference": "receipt"},
        ).status_code
        == 200
    )
    before = db.query(SavingsAudit).count()
    assert report(client, auth_headers, row).status_code == 403
    result = report(client, admin_headers, row)
    assert result.status_code == 200
    assert result.json()["consistent"]
    assert result.json()["balances"]["principal_paise"] == 50000
    assert not result.json()["provider_verified"] and not result.json()["release_authorized"]
    assert db.query(SavingsAudit).count() == before
    assert db.query(SavingsEntry).count() == 1
    import savings_api

    monkeypatch.setattr(savings_api, "ENABLED", False)
    assert report(client, admin_headers, row).status_code == 503


def test_held_plan_report_does_not_release_or_credit(client, auth_headers, admin_headers, db):
    row = scheme(client, auth_headers)
    payment(client, auth_headers, row)
    assert (
        client.post(
            f"/admin/savings/schemes/{row['id']}/hold",
            headers=admin_headers,
            json={"reference": "review", "reason": "Review receipts"},
        ).status_code
        == 200
    )
    result = report(client, admin_headers, row).json()
    assert result["consistent"] and result["review_required"]
    assert result["hold_count"] == 1 and not result["release_authorized"]
    assert db.query(SavingsHold).count() == 1 and db.query(SavingsEntry).count() == 0


@pytest.mark.parametrize(
    "alter,code",
    [
        ("missing", "posted_payment_without_credit"),
        ("amount", "posting_evidence_mismatch"),
        ("reference", "posting_evidence_mismatch"),
    ],
)
def test_report_detects_inconsistent_posting(client, auth_headers, admin_headers, db, alter, code):
    row = scheme(client, auth_headers)
    p = payment(client, auth_headers, row)
    client.post(
        f"/admin/savings/payments/{p['id']}/confirm",
        headers=admin_headers,
        json={"reference": "receipt"},
    )
    entry = db.query(SavingsEntry).one()
    if alter == "missing":
        db.delete(entry)
    elif alter == "amount":
        entry.principal_paise += 1
    else:
        entry.evidence_reference = "other"
    db.commit()
    result = report(client, admin_headers, row).json()
    assert not result["consistent"]
    assert {"code": code, "payment_id": p["id"]} in result["issues"]


def test_report_checks_gold_rate_and_bonus_snapshot(client, auth_headers, admin_headers, db):
    client.post(
        "/admin/savings/rates",
        headers=admin_headers,
        json={
            "paise_per_gram": 700000,
            "source": "synthetic",
            "valid_until": (utc_now() + timedelta(days=1)).isoformat() + "Z",
        },
    )
    row = scheme(client, auth_headers, kind="digigold")
    p = payment(client, auth_headers, row, amount=10000)
    client.post(
        f"/admin/savings/payments/{p['id']}/confirm",
        headers=admin_headers,
        json={"reference": "receipt"},
    )
    assert report(client, admin_headers, row).json()["consistent"]
    entry = db.query(SavingsEntry).one()
    entry.bonus_micrograms += 1
    db.commit()
    result = report(client, admin_headers, row).json()
    assert {"code": "gold_credit_mismatch", "payment_id": p["id"]} in result["issues"]


def test_report_bounds_records_and_flags_unknown_checkout(
    client, auth_headers, admin_headers, monkeypatch
):
    row = scheme(client, auth_headers)
    p = payment(client, auth_headers, row, mode="razorpay")

    def timeout(*args, **kwargs):
        raise OSError("synthetic timeout")

    monkeypatch.setattr(main, "call_razorpay", timeout)
    assert (
        client.post(f"/savings/payments/{p['id']}/checkout", headers=auth_headers).status_code
        == 503
    )
    result = report(client, admin_headers, row).json()
    assert {"code": "checkout_requires_recovery", "payment_id": p["id"]} in result["issues"]
    monkeypatch.setattr(savings_reconciliation, "MAX_RECORDS", 0)
    assert report(client, admin_headers, row).status_code == 409


def test_fully_refunded_payment_records_hold_without_changing_ledger(
    client, auth_headers, admin_headers, monkeypatch, db
):
    row = scheme(client, auth_headers)
    p = payment(client, auth_headers, row, mode="razorpay")
    checkout = open_checkout(client, auth_headers, p)
    assert (
        client.post(
            f"/savings/payments/{p['id']}/verify",
            headers=auth_headers,
            json=verification(checkout),
        ).status_code
        == 200
    )
    monkeypatch.setattr(
        main,
        "fetch_razorpay_payment",
        lambda _: (
            200,
            {
                "id": "pay_savings",
                "order_id": checkout["order_id"],
                "currency": "INR",
                "amount": 50000,
                "status": "refunded",
                "amount_refunded": 50000,
                "refund_status": "full",
                "captured": True,
            },
        ),
    )
    monkeypatch.setattr(
        main,
        "call_razorpay",
        lambda *args: (
            200,
            {
                "id": "rfnd_full",
                "entity": "refund",
                "payment_id": "pay_savings",
                "currency": "INR",
                "amount": 50000,
                "status": "processed",
            },
        ),
    )
    result = send_webhook(
        client,
        {
            "event": "refund.processed",
            "payload": {
                "refund": {
                    "entity": {
                        "id": "rfnd_full",
                        "payment_id": "pay_savings",
                    }
                }
            },
        },
    )
    assert result.status_code == 200 and result.json()["status"] == "held"
    assert db.query(SavingsEntry).one().principal_paise == 50000
    assert db.get(SavingsPayment, p["id"]).state == "posted"
    assert db.query(SavingsHold).count() == 1
