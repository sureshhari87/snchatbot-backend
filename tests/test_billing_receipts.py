"""Receipt ownership, immutable saved facts and no payment/provider side effects."""

import json
from datetime import datetime, timedelta

import pytest

import main
import savings
from billing_models import BillingReceipt, BillingReceiptAudit
from models import utc_now
from savings_models import (
    SavingsAudit,
    SavingsEntry,
    SavingsGoldRate,
    SavingsHold,
    SavingsPayment,
    SavingsScheme,
)

ISSUER = dict(
    legal_name="SONA JEWELLERS",
    billing_address="NO 32, KANSA METTU STREET, MADURAI-625001",
    state_code="33",
    postal_code="625001",
    gstin="33AEYFS4414C1ZZ",
)


@pytest.fixture(autouse=True)
def billing(monkeypatch):
    monkeypatch.setenv("BILLING_DOCUMENTS_ENABLED", "1")
    monkeypatch.setenv("BILLING_ISSUER_JSON", json.dumps(ISSUER))

    def forbidden(*args, **kwargs):
        raise AssertionError("Billing must not contact a payment or email provider")

    monkeypatch.setattr(main, "call_razorpay", forbidden)
    monkeypatch.setattr(main, "send_email", forbidden)


def payment(db, owner, admin, *, posted=True, gold=False):
    now = utc_now()
    plan = SavingsScheme(
        user_id=owner.id,
        request_key="receipt-plan",
        kind="digigold" if gold else "monthly",
        monthly_paise=None if gold else 50000,
        state="active",
        started_at=now - timedelta(days=10),
        matures_at=now + timedelta(days=330),
    )
    db.add(plan)
    db.flush()
    row = SavingsPayment(
        scheme_id=plan.id,
        user_id=owner.id,
        request_key="receipt-payment",
        amount_paise=10000 if gold else 50000,
        installment=None if gold else 1,
        mode="manual",
        state="pending",
        created_at=now - timedelta(seconds=1),
    )
    db.add(row)
    db.flush()
    if gold:
        db.add(
            SavingsGoldRate(
                paise_per_gram=1525000,
                purity="24K 995",
                source="Synthetic approved rate",
                effective_at=now - timedelta(hours=1),
                valid_until=now + timedelta(hours=1),
                created_by=admin.id,
                created_at=now,
            )
        )
        db.flush()
    if posted:
        savings.confirm_manual(db, row.id, admin, "synthetic-verified-reference", now)
    db.commit()
    return row, plan


def issue(client, headers, identity, body=None):
    return client.post(
        f"/billing/savings/payments/{identity}/receipt",
        headers=headers,
        json={} if body is None else body,
    )


def test_posted_payment_is_issued_once_without_a_second_credit(
    client, db, verified_user, admin_user, auth_headers
):
    row, plan = payment(db, verified_user, admin_user)
    first = issue(client, auth_headers, row.id)
    assert first.status_code == 200
    doc = first.json()["document"]
    assert doc["issuer"] == ISSUER
    assert doc["payment"]["amount_paise"] == 50000
    assert doc["payment"]["status_at_issue"] == "posted"
    assert doc["savings"]["installment"] == 1
    assert doc["document_type"] == "payment_acknowledgement"
    assert not first.json()["review_required"]
    assert "no-store" in first.headers["cache-control"]
    assert issue(client, auth_headers, row.id).json() == first.json()
    assert db.query(BillingReceipt).count() == 1
    assert db.query(BillingReceiptAudit).count() == 1
    assert db.query(SavingsEntry).count() == 1
    assert db.query(SavingsAudit).filter_by(action="payment_posted").count() == 1


def test_pending_request_cannot_generate_a_paid_document(
    client, db, verified_user, admin_user, auth_headers
):
    row, _ = payment(db, verified_user, admin_user, posted=False)
    assert issue(client, auth_headers, row.id).status_code == 409
    assert db.query(BillingReceipt).count() == 0
    assert row.state == "pending"
    assert db.query(SavingsEntry).count() == 0


def test_other_account_cannot_issue_or_read_receipt(
    client, db, verified_user, admin_user, auth_headers, admin_headers
):
    row, _ = payment(db, verified_user, admin_user)
    assert issue(client, admin_headers, row.id).status_code == 404
    receipt = issue(client, auth_headers, row.id).json()["document"]
    assert (
        client.get("/billing/receipts/" + receipt["document_id"], headers=admin_headers).status_code
        == 404
    )
    assert (
        client.get("/billing/receipts/" + receipt["document_id"], headers=auth_headers).status_code
        == 200
    )
    assert client.get("/billing/receipts/" + receipt["document_id"]).status_code == 401


def test_untrusted_document_values_are_rejected(
    client, db, verified_user, admin_user, auth_headers
):
    row, _ = payment(db, verified_user, admin_user)
    assert (
        issue(
            client, auth_headers, row.id, {"amount_paise": 1, "user_id": admin_user.id}
        ).status_code
        == 422
    )
    assert db.query(BillingReceipt).count() == 0


def test_issued_facts_do_not_follow_profile_or_customer_changes(
    client, db, verified_user, admin_user, auth_headers, monkeypatch
):
    row, _ = payment(db, verified_user, admin_user)
    original = issue(client, auth_headers, row.id).json()["document"]
    verified_user.username = "Renamed account"
    db.commit()
    monkeypatch.setenv("BILLING_ISSUER_JSON", "invalid replacement")
    repeat = issue(client, auth_headers, row.id)
    assert repeat.status_code == 200
    assert repeat.json()["document"] == original


def test_missing_profile_does_not_undo_verified_payment(
    client, db, verified_user, admin_user, auth_headers, monkeypatch
):
    row, _ = payment(db, verified_user, admin_user)
    monkeypatch.delenv("BILLING_ISSUER_JSON")
    assert issue(client, auth_headers, row.id).status_code == 409
    db.refresh(row)
    assert row.state == "posted"
    assert db.query(SavingsEntry).count() == 1
    assert db.query(BillingReceipt).count() == 0


def test_digigold_uses_the_saved_rate_and_separate_rounded_credits(
    client, db, verified_user, admin_user, auth_headers
):
    row, _ = payment(db, verified_user, admin_user, gold=True)
    doc = issue(client, auth_headers, row.id).json()["document"]
    assert doc["savings"]["rate_paise_per_gram"] == 1525000
    assert doc["savings"]["gold_micrograms"] == 6557
    assert doc["savings"]["bonus_micrograms"] == 327
    rate = db.query(SavingsGoldRate).one()
    assert doc["savings"]["rate_reference_id"] == rate.id
    for field, recorded in [
        ("rate_effective_at", rate.effective_at),
        ("rate_valid_until", rate.valid_until),
    ]:
        supplied = datetime.fromisoformat(doc["savings"][field])
        assert supplied.utcoffset() == timedelta(0)
        assert supplied.replace(tzinfo=None) == recorded.replace(tzinfo=None)
    rate.paise_per_gram = 9999999
    db.commit()
    assert issue(client, auth_headers, row.id).json()["document"] == doc


def test_mismatched_ledger_is_not_printed_as_verified(
    client, db, verified_user, admin_user, auth_headers
):
    row, _ = payment(db, verified_user, admin_user)
    db.query(SavingsEntry).one().principal_paise += 1
    db.commit()
    assert issue(client, auth_headers, row.id).status_code == 409
    assert db.query(BillingReceipt).count() == 0


def test_hold_is_current_but_does_not_rewrite_original_document(
    client, db, verified_user, admin_user, auth_headers
):
    row, plan = payment(db, verified_user, admin_user)
    original = issue(client, auth_headers, row.id).json()["document"]
    db.add(
        SavingsHold(
            scheme_id=plan.id,
            payment_id=row.id,
            kind="admin_review",
            reference="synthetic-hold",
            reason="Review",
            actor_id=admin_user.id,
            created_at=utc_now(),
        )
    )
    db.commit()
    result = client.get("/billing/receipts/" + original["document_id"], headers=auth_headers).json()
    assert result["document"] == original
    assert result["review_required"]
    assert db.query(BillingReceipt).count() == 1


def test_tampered_saved_document_is_not_served(client, db, verified_user, admin_user, auth_headers):
    row, _ = payment(db, verified_user, admin_user)
    identity = issue(client, auth_headers, row.id).json()["document"]["document_id"]
    db.query(BillingReceipt).one().snapshot_json = "{}"
    db.commit()
    assert client.get("/billing/receipts/" + identity, headers=auth_headers).status_code == 409


def test_disabled_feature_cannot_issue(
    client, db, verified_user, admin_user, auth_headers, monkeypatch
):
    row, _ = payment(db, verified_user, admin_user)
    monkeypatch.delenv("BILLING_DOCUMENTS_ENABLED")
    assert issue(client, auth_headers, row.id).status_code == 503
    assert db.query(BillingReceipt).count() == 0


@pytest.mark.parametrize("environment, expected", [("staging", True), ("production", False)])
def test_document_environment_marker_is_frozen(
    client, db, verified_user, admin_user, auth_headers, monkeypatch, environment, expected
):
    import config

    row, _ = payment(db, verified_user, admin_user)
    monkeypatch.setattr(config, "APP_ENV", environment)
    original = issue(client, auth_headers, row.id).json()["document"]
    assert original["is_test_document"] is expected
    monkeypatch.setattr(config, "APP_ENV", "production" if expected else "staging")
    assert issue(client, auth_headers, row.id).json()["document"] == original


def test_admin_saved_document_reads_are_scoped_and_never_issue(
    client, db, verified_user, admin_user, auth_headers, admin_headers
):
    row, _ = payment(db, verified_user, admin_user)
    path = f"/admin/billing/savings/payments/{row.id}/receipt"
    assert client.get(path).status_code == 401
    assert client.get(path, headers=auth_headers).status_code == 403
    assert client.get(path, headers=admin_headers).status_code == 404
    assert db.query(BillingReceipt).count() == 0
    document = issue(client, auth_headers, row.id).json()
    identity = document["document"]["document_id"]
    for endpoint in [path, f"/admin/billing/receipts/{identity}"]:
        assert client.get(endpoint, headers=auth_headers).status_code == 403
        response = client.get(endpoint, headers=admin_headers)
        assert response.status_code == 200
        assert response.json() == document
        assert "no-store" in response.headers["cache-control"]
    assert db.query(BillingReceipt).count() == 1
    assert db.query(BillingReceiptAudit).count() == 1
    assert db.query(SavingsEntry).count() == 1
    assert db.query(SavingsAudit).filter_by(action="payment_posted").count() == 1


def test_admin_saved_document_reads_respect_feature_gate(client, admin_headers, monkeypatch):
    monkeypatch.setenv("BILLING_DOCUMENTS_ENABLED", "0")
    assert (
        client.get("/admin/billing/savings/payments/1/receipt", headers=admin_headers).status_code
        == 503
    )
    assert (
        client.get("/admin/billing/receipts/" + "a" * 32, headers=admin_headers).status_code == 503
    )
