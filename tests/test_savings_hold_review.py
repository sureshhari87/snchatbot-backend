import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError
from test_savings_api import open_checkout, payment, scheme, send_webhook, verification
from test_savings_api import provider as provider
from test_savings_api import savings_enabled as savings_enabled

import main
from savings_models import SavingsAudit, SavingsEntry, SavingsHoldDecision


@pytest.fixture
def reviewed(client, auth_headers, monkeypatch):
    row = scheme(client, auth_headers)
    p = payment(client, auth_headers, row, mode="razorpay")
    checkout = open_checkout(client, auth_headers, p)
    assert (
        client.post(
            f"/savings/payments/{p['id']}/verify", headers=auth_headers, json=verification(checkout)
        ).status_code
        == 200
    )
    captured = dict(
        id="pay_savings",
        entity="payment",
        order_id=checkout["order_id"],
        status="captured",
        captured=True,
        currency="INR",
        amount=50000,
        amount_refunded=0,
        refund_status=None,
    )
    dispute = dict(
        id="disp_review",
        entity="dispute",
        payment_id="pay_savings",
        status="won",
        currency="INR",
        amount=10000,
        amount_deducted=0,
    )
    evidence = {"payment": captured, "dispute": dispute, "refunds": [], "outage": False}
    calls = []

    def call(method, path, payload=None):
        assert method == "GET"
        calls.append(path)
        if evidence["outage"]:
            raise OSError("PRIVATE_PROVIDER_DETAIL")
        if path == "payments/pay_savings":
            return 200, dict(evidence["payment"])
        if path == "disputes/disp_review":
            return 200, dict(evidence["dispute"])
        if path.startswith("disputes?"):
            items = [dict(evidence["dispute"])] if path.endswith("skip=0") else []
        elif path.startswith("payments/pay_savings/refunds?"):
            items = evidence["refunds"] if path.endswith("skip=0") else []
        elif path.startswith("refunds/"):
            return 200, next(r for r in evidence["refunds"] if r["id"] == path.split("/")[1])
        else:
            return 404, {}
        return 200, {"entity": "collection", "count": len(items), "items": items}

    monkeypatch.setattr(main, "call_razorpay", call)
    body = {
        "event": "payment.dispute.won",
        "payload": {
            "dispute": {
                "entity": {
                    "id": "disp_review",
                    "payment_id": "pay_savings",
                }
            }
        },
    }
    result = send_webhook(client, body)
    assert result.status_code == 200
    return row, p, result.json()["hold_id"], evidence, calls, body


def release(client, headers, hold_id, key="review-release", reason="Verified retained funds"):
    return client.post(
        f"/admin/savings/holds/{hold_id}/release",
        headers=headers,
        json={"request_key": key, "reason": reason},
    )


def test_audited_release_is_idempotent_and_keeps_ledger(
    client, auth_headers, admin_headers, reviewed, db
):
    row, p, hold_id, evidence, calls, body = reviewed
    assert release(client, auth_headers, hold_id).status_code == 403
    first = release(client, admin_headers, hold_id)
    assert first.status_code == 200 and first.json()["action"] == "released"
    assert len(first.json()["evidence_sha256"]) == 64
    before = len(calls)
    assert release(client, admin_headers, hold_id).json() == first.json()
    assert len(calls) == before
    assert release(client, admin_headers, hold_id, reason="Changed").status_code == 409
    assert release(client, admin_headers, hold_id, key="different").status_code == 409
    state = client.get(f"/savings/schemes/{row['id']}", headers=auth_headers).json()
    assert not state["review_required"] and state["principal_paise"] == 50000
    assert db.query(SavingsEntry).count() == 1 and db.query(SavingsHoldDecision).count() == 1
    assert db.query(SavingsAudit).filter_by(action="review_hold_released").count() == 1
    history = client.get(f"/admin/savings/schemes/{row['id']}/holds", headers=admin_headers).json()[
        0
    ]
    assert not history["active"] and history["latest_decision"]["id"] == first.json()["id"]
    assert send_webhook(client, body).json()["status"] == "released"
    assert db.query(SavingsHoldDecision).count() == 1


@pytest.mark.parametrize(
    "alter",
    [
        "refund",
        "pending_refund",
        "open",
        "under_review",
        "lost",
        "closed",
        "deducted",
        "foreign",
        "missing",
        "outage",
    ],
)
def test_release_fails_closed(client, auth_headers, admin_headers, reviewed, db, alter):
    row, p, hold_id, evidence, calls, body = reviewed
    if alter == "refund":
        evidence["payment"]["amount_refunded"] = 1
    elif alter == "pending_refund":
        evidence["refunds"] = [
            dict(
                id="rfnd_pending",
                entity="refund",
                payment_id="pay_savings",
                amount=100,
                currency="INR",
                status="pending",
            )
        ]
    elif alter in ("open", "under_review", "lost", "closed"):
        evidence["dispute"]["status"] = alter
    elif alter == "deducted":
        evidence["dispute"]["amount_deducted"] = 100
    elif alter == "foreign":
        evidence["dispute"]["payment_id"] = "pay_other"
    elif alter == "missing":
        del evidence["payment"]["amount_refunded"]
    else:
        evidence["outage"] = True
    result = release(client, admin_headers, hold_id)
    assert result.status_code == (503 if alter == "outage" else 409)
    assert "PRIVATE_PROVIDER_DETAIL" not in result.text
    assert db.query(SavingsHoldDecision).count() == 0 and db.query(SavingsEntry).count() == 1
    assert client.get(f"/savings/schemes/{row['id']}", headers=auth_headers).json()[
        "review_required"
    ]


def test_new_adverse_evidence_reopens_and_old_release_key_cannot_clear_it(
    client, auth_headers, admin_headers, reviewed, db
):
    row, p, hold_id, evidence, calls, body = reviewed
    assert release(client, admin_headers, hold_id).status_code == 200
    evidence["dispute"]["status"] = "lost"
    body["event"] = "payment.dispute.lost"
    assert send_webhook(client, body).json()["status"] == "held"
    assert send_webhook(client, body).json()["status"] == "held"
    assert db.query(SavingsHoldDecision).count() == 2
    assert release(client, admin_headers, hold_id).status_code == 409
    assert release(client, admin_headers, hold_id, key="fresh").status_code == 409
    assert client.get(f"/savings/schemes/{row['id']}", headers=auth_headers).json()[
        "review_required"
    ]
    evidence["dispute"]["status"] = "won"
    assert release(client, admin_headers, hold_id, key="fresh").status_code == 200
    assert db.query(SavingsHoldDecision).count() == 3
    assert db.query(SavingsEntry).count() == 1


def test_manual_hold_release_remains_unavailable(client, auth_headers, admin_headers, db):
    row = scheme(client, auth_headers)
    h = client.post(
        f"/admin/savings/schemes/{row['id']}/hold",
        headers=admin_headers,
        json={"reference": "manual-review", "reason": "Receipt review"},
    ).json()
    assert release(client, admin_headers, h["id"]).status_code == 409
    assert db.query(SavingsHoldDecision).count() == 0


def test_release_input_cannot_assert_provider_or_financial_authority(
    client, admin_headers, reviewed
):
    hold_id = reviewed[2]
    for extra in ({"balance": 1}, {"no_refund": True}, {"provider_status": "won"}):
        result = client.post(
            f"/admin/savings/holds/{hold_id}/release",
            headers=admin_headers,
            json=dict(request_key="key", reason="Review", **extra),
        )
        assert result.status_code == 422


def test_decision_history_migration_is_immutable(
    client, auth_headers, admin_headers, reviewed, db, monkeypatch
):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0024_savings_hold_review.py"
    spec = importlib.util.spec_from_file_location("review_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(db.connection())))
    module.downgrade()
    module.upgrade()
    db.commit()
    assert release(client, admin_headers, reviewed[2]).status_code == 200
    for command in (
        "UPDATE savings_hold_decisions SET reason='changed'",
        "DELETE FROM savings_hold_decisions",
    ):
        with pytest.raises(DatabaseError, match="immutable"):
            db.execute(text(command))
        db.rollback()
    monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(db.connection())))
    with pytest.raises(RuntimeError, match="preservation"):
        module.downgrade()


def test_release_preserves_other_active_holds(client, auth_headers, admin_headers, reviewed):
    row, _, hold_id, _, _, _ = reviewed
    assert (
        client.post(
            f"/admin/savings/schemes/{row['id']}/hold",
            headers=admin_headers,
            json={"reference": "other", "reason": "Separate manual review"},
        ).status_code
        == 200
    )
    assert release(client, admin_headers, hold_id).status_code == 200
    assert client.get(f"/savings/schemes/{row['id']}", headers=auth_headers).json()[
        "review_required"
    ]
    history = client.get(f"/admin/savings/holds/{hold_id}/decisions", headers=admin_headers)
    assert history.status_code == 200 and history.json()[0]["action"] == "released"
    assert (
        client.get(f"/admin/savings/holds/{hold_id}/decisions", headers=auth_headers).status_code
        == 403
    )


@pytest.mark.parametrize("alter", ["changed_payment", "boolean_amount", "bad_collection"])
def test_release_rejects_changed_or_incomplete_fresh_proof(
    client, admin_headers, reviewed, monkeypatch, db, alter
):
    original = main.call_razorpay
    seen = 0

    def call(method, path, payload=None):
        nonlocal seen
        code, data = original(method, path, payload)
        if path == "payments/pay_savings":
            seen += 1
            if seen == 2:
                data = dict(data)
                if alter == "changed_payment":
                    data["status"] = "refunded"
                elif alter == "boolean_amount":
                    data["amount_refunded"] = False
        if alter == "bad_collection" and path.startswith("disputes?"):
            data = dict(data, count=100)
        return code, data

    monkeypatch.setattr(main, "call_razorpay", call)
    assert release(client, admin_headers, reviewed[2]).status_code == 409
    assert db.query(SavingsHoldDecision).count() == 0


def test_release_rejects_nonadvancing_provider_pagination(
    client, admin_headers, reviewed, monkeypatch, db
):
    original = main.call_razorpay

    def call(method, path, payload=None):
        if path.startswith("disputes?"):
            path = "disputes?count=100&skip=0"
        return original(method, path, payload)

    monkeypatch.setattr(main, "call_razorpay", call)
    assert release(client, admin_headers, reviewed[2]).status_code == 409
    assert db.query(SavingsHoldDecision).count() == 0


@pytest.mark.parametrize("include_count", [False, True])
def test_release_rejects_null_test_dispute_collection_without_financial_writes(
    client, auth_headers, admin_headers, reviewed, monkeypatch, db, include_count
):
    row, _, hold_id, _, _, _ = reviewed
    original = main.call_razorpay

    def call(method, path, payload=None):
        if path.startswith("disputes?"):
            data = {"entity": "collection", "items": None}
            if include_count:
                data["count"] = None
            return 200, data
        return original(method, path, payload)

    monkeypatch.setattr(main, "call_razorpay", call)
    assert release(client, admin_headers, hold_id).status_code == 409
    state = client.get(f"/savings/schemes/{row['id']}", headers=auth_headers).json()
    assert state["review_required"] and state["principal_paise"] == 50000
    assert db.query(SavingsEntry).count() == 1
    assert db.query(SavingsHoldDecision).count() == 0
    assert db.query(SavingsAudit).filter_by(action="review_hold_released").count() == 0
