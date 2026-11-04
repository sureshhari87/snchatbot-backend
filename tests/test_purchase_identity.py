"""Synthetic PAN-like test fixtures only; no real customer identity/provider calls."""

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import purchase_identity as identity
import purchase_identity_api as api
from database import Base
from models import User
from purchase_identity_models import PurchaseIdentity, PurchaseIdentityReview, PurchaseIdentityUse

NUMBER = "ABCDE1234F"
NAME = "Synthetic Buyer"


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setenv("PURCHASE_IDENTITY_ENABLED", "1")
    monkeypatch.setenv("PURCHASE_IDENTITY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            PurchaseIdentity.__table__,
            PurchaseIdentityReview.__table__,
            PurchaseIdentityUse.__table__,
        ],
    )
    with Session(engine) as session:
        for uid in [1, 2, 3]:
            session.add(
                User(
                    id=uid,
                    username=f"synthetic-{uid}",
                    email=f"synthetic-{uid}@example.test",
                    hashed_password="synthetic-unused",
                    is_admin=uid == 3,
                )
            )
        session.commit()
        yield session
    engine.dispose()


def payload(key="synthetic-one", name=NAME):
    return dict(
        request_key=key,
        pan=NUMBER,
        legal_name=name,
        buyer_kind="retail",
        declaration_confirmed=True,
    )


def approval(**updates):
    return dict(
        decision="approved",
        verified_pan=NUMBER,
        verified_legal_name=NAME,
        evidence_reference="synthetic-original-card-check",
        reason="Synthetic administrator test",
        **updates,
    )


def approved(db):
    row = identity.submit(db, 1, payload())
    identity.review(db, row.id, db.get(User, 3), approval())
    return row


def test_threshold_exact_boundaries():
    assert not identity.amount_requires_identity(20_000_000)
    assert identity.amount_requires_identity(20_000_001)


@pytest.mark.parametrize("value", [True, -1, 20_000_000.1, "20000001", None])
def test_threshold_rejects_untrusted_money(value):
    with pytest.raises(HTTPException):
        identity.amount_requires_identity(value)


def test_default_off_preserves_existing_workflows(monkeypatch):
    monkeypatch.delenv("PURCHASE_IDENTITY_ENABLED", raising=False)
    assert identity.require_verified(None, 1, 20_000_001) is None


def test_submission_encrypts_and_masks_no_fake_verification(db):
    row = identity.submit(db, 1, payload())
    stored = db.execute(text("SELECT encrypted_facts FROM purchase_identities")).scalar_one()
    assert NUMBER not in stored and NAME not in stored
    shown = identity.document(db, row, include_name=True)
    assert shown["masked_pan"] == "******234F"
    assert shown["state"] == "pending_review"
    assert "pan" not in shown
    with pytest.raises(HTTPException):
        identity.require_verified(db, 1, 20_000_001, legal_name=NAME)


def test_idempotent_submission_and_changed_key_rejected(db):
    row = identity.submit(db, 1, payload())
    assert identity.submit(db, 1, payload()).id == row.id
    with pytest.raises(HTTPException):
        identity.submit(db, 1, payload(name="Different Synthetic Buyer"))
    assert db.query(PurchaseIdentity).count() == 1


def test_missing_key_prevents_collection(db, monkeypatch):
    monkeypatch.delenv("PURCHASE_IDENTITY_ENCRYPTION_KEY")
    with pytest.raises(HTTPException) as error:
        identity.submit(db, 1, payload())
    assert error.value.status_code == 503
    assert db.query(PurchaseIdentity).count() == 0


def test_original_pan_and_full_name_required_for_admin_approval(db):
    row = identity.submit(db, 1, payload())
    bad = approval()
    bad["verified_legal_name"] = "Wrong Synthetic Name"
    with pytest.raises(HTTPException):
        identity.review(db, row.id, db.get(User, 3), bad)
    assert db.query(PurchaseIdentityReview).count() == 0
    identity.review(db, row.id, db.get(User, 3), approval())
    assert (
        identity.require_verified(db, 1, 20_000_001, legal_name="  synthetic  buyer  ").id == row.id
    )
    for name in ["Nickname", ""]:
        with pytest.raises(HTTPException):
            identity.require_verified(db, 1, 20_000_001, legal_name=name)


def test_admin_only_and_no_self_verification(db):
    row = identity.submit(db, 1, payload())
    for admin in [db.get(User, 1), db.get(User, 2)]:
        with pytest.raises(HTTPException) as error:
            identity.review(db, row.id, admin, approval())
        assert error.value.status_code == 403
    own = identity.submit(db, 3, payload())
    with pytest.raises(HTTPException):
        identity.review(db, own.id, db.get(User, 3), approval())


def test_decision_retry_and_no_overwrite(db):
    row = approved(db)
    original = db.query(PurchaseIdentityReview).one()
    assert identity.review(db, row.id, db.get(User, 3), approval()).id == original.id
    changed = approval()
    changed["decision"] = "rejected"
    with pytest.raises(HTTPException):
        identity.review(db, row.id, db.get(User, 3), changed)


def test_new_submission_invalidates_current_eligibility(db):
    first = approved(db)
    second = identity.submit(db, 1, payload("synthetic-two"))
    assert second.version == first.version + 1
    with pytest.raises(HTTPException):
        identity.require_verified(db, 1, 20_000_001, legal_name=NAME)
    assert db.query(PurchaseIdentityReview).count() == 1


def test_ciphertext_is_bound_to_owner_and_submission(db):
    row = approved(db)
    other = identity.submit(db, 2, payload())
    other.encrypted_facts = row.encrypted_facts
    with pytest.raises(HTTPException):
        identity.facts(other)


def test_no_pan_in_audit_or_request_references(db):
    bad = payload(NUMBER)
    with pytest.raises(HTTPException):
        identity.submit(db, 1, bad)
    row = identity.submit(db, 1, payload())
    bad = approval()
    bad["reason"] = "Do not store " + NUMBER
    with pytest.raises(HTTPException):
        identity.review(db, row.id, db.get(User, 3), bad)


def test_purchase_binding_exact_and_idempotent(db):
    row = approved(db)
    kwargs = dict(
        resource_kind="merchandise", resource_key="synthetic-order-one", amount_paise=20_000_001
    )
    item = identity.bind(db, row, **kwargs)
    assert identity.bind(db, row, **kwargs).id == item.id
    kwargs["amount_paise"] += 1
    with pytest.raises(HTTPException):
        identity.bind(db, row, **kwargs)
    assert db.query(PurchaseIdentityUse).count() == 1


def test_ownership_and_safe_api_validation(db):
    current = [db.get(User, 1)]
    app = FastAPI()
    api.install(app, lambda: db, lambda: current[0])
    client = TestClient(app)
    result = client.post("/purchase-identity", json=payload())
    assert result.status_code == 200 and NUMBER not in result.text
    record_id = result.json()["id"]
    current[0] = db.get(User, 2)
    assert client.get("/purchase-identity").json()["state"] == "not_submitted"
    assert client.get(f"/admin/purchase-identities/{record_id}").status_code == 403
    bad = payload()
    bad["pan"] = NUMBER + "invalid"
    result = client.post("/purchase-identity", json=bad)
    assert result.status_code == 422 and NUMBER not in result.text
    bad = payload()
    bad["user_id"] = 1
    assert client.post("/purchase-identity", json=bad).status_code == 422


def test_immutable_migration_preserves_users(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "identity.sqlite"))
    User.__table__.create(engine)
    path = Path(__file__).parents[1] / "alembic/versions/0040_purchase_identity.py"
    spec = importlib.util.spec_from_file_location("identity_migration_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as conn:
        conn.execute(
            User.__table__.insert().values(
                id=1, username="synthetic", email="synthetic@example.test", hashed_password="unused"
            )
        )
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
        assert conn.execute(text("SELECT count(*) FROM users")).scalar_one() == 1
        conn.execute(
            text(
                "INSERT INTO purchase_identities VALUES (:id,1,1,'test','encrypted','234F',CURRENT_TIMESTAMP)"
            ),
            {"id": "a" * 32},
        )
        for statement in [
            "UPDATE purchase_identities SET pan_suffix='0000'",
            "DELETE FROM purchase_identities",
        ]:
            with pytest.raises(IntegrityError):
                conn.execute(text(statement))
    with pytest.raises(RuntimeError):
        module.downgrade()
    engine.dispose()


def test_invoice_keeps_original_identity_after_a_new_submission(db):
    row = approved(db)
    identity.bind(
        db, row, resource_kind="merchandise", resource_key="order:19", amount_paise=20_000_001
    )
    identity.submit(db, 1, payload("synthetic-replacement", "New Synthetic Buyer"))
    kwargs = dict(
        resource_kind="merchandise", resource_key="order:19", legal_name=NAME, buyer_kind="retail"
    )
    assert identity.require_bound(db, 1, 20_000_001, **kwargs).id == row.id
    for updates in [
        dict(legal_name="New Synthetic Buyer"),
        dict(buyer_kind="business"),
        dict(resource_key="order:20"),
    ]:
        with pytest.raises(HTTPException):
            identity.require_bound(db, 1, 20_000_001, **{**kwargs, **updates})
    with pytest.raises(HTTPException):
        identity.require_bound(db, 2, 20_000_001, **kwargs)


def test_unverified_identity_cannot_be_bound(db):
    row = identity.submit(db, 1, payload())
    with pytest.raises(HTTPException):
        identity.bind(
            db, row, resource_kind="merchandise", resource_key="order:20", amount_paise=20_000_001
        )


def test_existing_payment_cannot_adopt_a_later_identity(db):
    approved(db)
    with pytest.raises(HTTPException):
        identity.require_existing_payment(
            db, 1, 20_000_001, resource_kind="merchandise", resource_key="order:99"
        )
    assert db.query(PurchaseIdentityUse).count() == 0


def test_business_gstin_must_belong_to_the_frozen_pan(db):
    data = payload()
    data["buyer_kind"] = "business"
    row = identity.submit(db, 1, data)
    identity.review(db, row.id, db.get(User, 3), approval())
    identity.bind(
        db, row, resource_kind="merchandise", resource_key="order:42", amount_paise=20_000_001
    )
    kwargs = dict(
        resource_kind="merchandise", resource_key="order:42", legal_name=NAME, buyer_kind="business"
    )
    with pytest.raises(HTTPException):
        identity.require_bound(db, 1, 20_000_001, gstin="33ZZZZZ9999Z1ZZ", **kwargs)
    assert (
        identity.require_bound(db, 1, 20_000_001, gstin="33" + NUMBER + "1ZZ", **kwargs).id
        == row.id
    )


def test_real_runtime_high_value_stays_closed_when_feature_is_off(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TESTING", "0")
    monkeypatch.delenv("PURCHASE_IDENTITY_ENABLED", raising=False)
    assert identity.require_verified(None, 1, 20_000_000) is None
    with pytest.raises(HTTPException) as error:
        identity.require_verified(None, 1, 20_000_001)
    assert error.value.status_code == 503


def test_wrong_recovery_key_refuses_new_collection(db, monkeypatch):
    identity.submit(db, 1, payload())
    monkeypatch.setenv("PURCHASE_IDENTITY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with pytest.raises(HTTPException) as error:
        identity.submit(db, 2, payload("synthetic-other"))
    assert error.value.status_code == 503
    assert db.query(PurchaseIdentity).count() == 1
