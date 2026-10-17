"""Own-customer HTTP contract and read-only referral reporting on synthetic SQLite."""

import pytest
from sqlalchemy import event

import financial_checkout
import financial_reporting
import referrals
from models import User
from referral_models import ReferralApplication, ReferralCode, ReferralCredit
from rewards_vouchers_models import RewardAccount


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(referrals, "ENABLED", True)
    monkeypatch.setattr(financial_checkout, "ENABLED", True)


def referrer(db):
    user = User(
        username="synthetic-referrer",
        email="synthetic-referrer@example.invalid",
        hashed_password="unused",
    )
    db.add(user)
    db.flush()
    code = referrals.code_for(db, user.id).code
    db.commit()
    return user.id, code


def test_anonymous_and_admin_cannot_manage_customer_referrals(client, admin_headers):
    assert client.get("/referrals").status_code == 401
    assert client.post("/referrals/code", headers=admin_headers).status_code == 403


def test_read_is_no_store_and_does_not_create_codes(client, auth_headers, db):
    response = client.get("/referrals", headers=auth_headers)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["code"] == ""
    assert db.query(ReferralCode).count() == 0


def test_code_generation_retries_and_application_are_customer_scoped(client, auth_headers, db):
    other, code = referrer(db)
    first = client.post("/referrals/code", headers=auth_headers)
    assert first.status_code == 200
    assert (
        client.post("/referrals/code", headers=auth_headers).json()["code"] == first.json()["code"]
    )
    response = client.put(
        "/referrals/application", headers=auth_headers, json={"code": code.lower()}
    )
    assert response.status_code == 200
    assert response.json()["applicationStatus"] == "pending"
    assert response.json()["appliedCode"] == code
    assert "referrerUid" not in response.json() and "referrer_id" not in response.json()
    assert (
        client.put("/referrals/application", headers=auth_headers, json={"code": code}).status_code
        == 200
    )
    assert db.query(ReferralApplication).count() == 1
    assert referrals.programme(db, other)["invitedCount"] == 1


def test_client_cannot_supply_owner_or_reward_amounts(client, auth_headers, db):
    _, code = referrer(db)
    response = client.put(
        "/referrals/application",
        headers=auth_headers,
        json={"code": code, "referrer_points": 999999, "user_id": 1},
    )
    assert response.status_code == 422
    assert db.query(ReferralApplication).count() == 0


@pytest.mark.parametrize("gate", ["referrals", "financial"])
def test_independent_feature_gates_block_calls(client, auth_headers, monkeypatch, gate):
    monkeypatch.setattr(referrals if gate == "referrals" else financial_checkout, "ENABLED", False)
    assert client.post("/referrals/code", headers=auth_headers).status_code == 503


def test_prior_verified_sql_order_blocks_application(client, auth_headers, verified_user, db):
    from models import OrderSnapshot

    _, code = referrer(db)
    db.add(
        OrderSnapshot(
            user_id=verified_user.id, order_reference="synthetic-prior", payment_status="verified"
        )
    )
    db.commit()
    response = client.put("/referrals/application", headers=auth_headers, json={"code": code})
    assert response.status_code == 409
    assert db.query(ReferralApplication).count() == 0


def test_referral_pair_reconciles_without_writes_and_history_hides_other_order(
    client, auth_headers, verified_user, db
):
    from test_financial_reporting import merchandise

    other, code = referrer(db)
    referrals.apply(db, verified_user.id, code)
    reservation, evidence, _ = merchandise(db, verified_user)
    context = referrals.prepare(db, verified_user.id)
    referrals.post(db, context, reservation, {"id": reservation.provider_payment_id}, evidence)
    db.commit()
    assert db.query(ReferralCredit).count() == 2
    assert db.get(RewardAccount, other).balance_points == 100
    changes = []

    def observe(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
            changes.append(statement)

    event.listen(db.get_bind(), "before_cursor_execute", observe)
    try:
        report = financial_reporting.report(db, "rewards")
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", observe)
    assert changes == []
    assert all(item["evidence_valid"] for item in report["items"])
    own = client.get("/rewards/history", headers=auth_headers)
    assert own.status_code == 200
    assert own.json()[0]["kind"] == "referral_credit"
    assert own.json()[0]["points_delta"] == 50

    import main

    main.app.dependency_overrides[main.get_current_user] = lambda: db.get(User, other)
    try:
        history = client.get("/rewards/history").json()
        assert history[0]["points_delta"] == 100
        assert history[0]["order_id"] is None
    finally:
        main.app.dependency_overrides.pop(main.get_current_user, None)
    credit = db.query(ReferralCredit).filter_by(user_id=other).one()
    credit.points_delta = 99
    db.commit()
    broken = financial_reporting.report(db, "rewards")
    assert all(not item["evidence_valid"] for item in broken["items"])


def test_admin_saved_review_is_private_bounded_and_available_after_disable(
    client, auth_headers, admin_headers, db, monkeypatch
):
    _, code = referrer(db)
    assert (
        client.put("/referrals/application", headers=auth_headers, json={"code": code}).status_code
        == 200
    )
    monkeypatch.setattr(referrals, "ENABLED", False)
    monkeypatch.setattr(financial_checkout, "ENABLED", False)
    assert client.get("/admin/referrals").status_code == 401
    assert client.get("/admin/referrals", headers=auth_headers).status_code == 403
    response = client.get("/admin/referrals?limit=1", headers=admin_headers)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["read_only"] and not body["provider_verified"] and not body["release_authorized"]
    assert len(body["items"]) == 1 and body["items"][0]["posting_state"] == "pending"
    assert "code" not in body["items"][0]
    assert client.get("/admin/referrals?limit=51", headers=admin_headers).status_code == 422
