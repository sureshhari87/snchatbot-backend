"""Synthetic contacts only; no network email or real account changes."""

from datetime import timedelta

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import customer_profile as service
import customer_profile_api as api
from database import Base
from models import User, utc_now


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setenv("CUSTOMER_PROFILE_ENABLED", "1")
    monkeypatch.setenv("SECRET_KEY", "synthetic-contact-key-not-production")
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            service.CustomerProfile.__table__,
            service.ContactChallenge.__table__,
        ],
    )
    with Session(engine) as db:
        db.add_all(
            [
                User(
                    id=i,
                    username=f"phone_hash_{i}",
                    email=f"phone_{i}@phone.sona.invalid",
                    hashed_password="unused",
                    is_verified=True,
                )
                for i in [1, 2]
            ]
        )
        db.commit()
        yield db
    engine.dispose()


def request(key="a" * 32, email="synthetic@example.com"):
    return dict(email=email, request_key=key)


def test_phone_placeholders_are_not_customer_facts(db):
    assert service.receipt_customer(db, db.get(User, 1)) == dict(
        account_name="Not provided", email="Not provided"
    )


def test_name_is_owner_scoped_versioned_and_does_not_change_login(db):
    user = db.get(User, 1)
    before = (user.username, user.email, user.hashed_password)
    result = service.save_name(db, user, dict(version=0, display_name="  Synthetic   Buyer  "))
    db.commit()
    assert result["display_name"] == "Synthetic Buyer" and result["version"] == 1
    assert (user.username, user.email, user.hashed_password) == before
    assert service.read(db, db.get(User, 2))["display_name"] == ""
    with pytest.raises(HTTPException):
        service.save_name(db, user, dict(version=0, display_name="Other Name"))


@pytest.mark.parametrize("value", ["123456", "x", "Name\nBad", None])
def test_invalid_names_rejected(db, value):
    with pytest.raises(HTTPException):
        service.save_name(db, db.get(User, 1), dict(version=0, display_name=value))


def test_code_hash_owner_expiry_attempts_and_retries(db):
    user = db.get(User, 1)
    row, code = service.request_email(db, user, request())
    db.commit()
    assert code not in row.code_hash and len(row.code_hash) == 64
    assert service.read(db, user)["email"] is None
    retry, again = service.request_email(db, user, request())
    assert retry.id == row.id and again is None
    with pytest.raises(HTTPException):
        service.verify_email(db, db.get(User, 2), dict(challenge_id=row.id, code=code))
    bad = "000000" if code != "000000" else "111111"
    for _ in range(5):
        _, ok = service.verify_email(db, user, dict(challenge_id=row.id, code=bad))
        assert not ok
        db.commit()
    with pytest.raises(HTTPException):
        service.verify_email(db, user, dict(challenge_id=row.id, code=code))
    assert service.read(db, user)["email"] is None


def test_verified_contact_does_not_change_auth_or_saved_snapshot(db):
    user = db.get(User, 1)
    original = user.email
    service.save_name(db, user, dict(version=0, display_name="Synthetic Buyer"))
    db.commit()
    saved = service.receipt_customer(db, user)
    row, code = service.request_email(db, user, request())
    db.commit()
    result, ok = service.verify_email(db, user, dict(challenge_id=row.id, code=code))
    db.commit()
    assert ok and result["email"] == "synthetic@example.com" and user.email == original
    assert saved == dict(account_name="Synthetic Buyer", email="Not provided")
    assert service.receipt_customer(db, user)["email"] == "synthetic@example.com"
    assert service.verify_email(db, user, dict(challenge_id=row.id, code=code))[1]


def test_expired_and_replaced_codes_cannot_change_contact(db):
    user = db.get(User, 1)
    old, code = service.request_email(db, user, request())
    db.commit()
    old.created_at = utc_now() - timedelta(seconds=61)
    db.commit()
    newer, _ = service.request_email(db, user, request("b" * 32))
    db.commit()
    with pytest.raises(HTTPException):
        service.verify_email(db, user, dict(challenge_id=old.id, code=code))
    newer.expires_at = utc_now() - timedelta(seconds=1)
    db.commit()
    with pytest.raises(HTTPException):
        service.verify_email(db, user, dict(challenge_id=newer.id, code="123456"))


def test_send_throttling_and_placeholder_rejection(db):
    user = db.get(User, 1)
    with pytest.raises(HTTPException):
        service.request_email(db, user, request(email="phone_1@phone.sona.invalid"))
    service.request_email(db, user, request())
    db.commit()
    with pytest.raises(HTTPException) as error:
        service.request_email(db, user, request("b" * 32))
    assert error.value.status_code == 429


def test_uncertain_mail_delivery_is_not_repeated_and_bad_code_is_persisted(db):
    calls = []
    app = FastAPI()
    user = db.get(User, 1)

    def mail(*args):
        calls.append(args)
        raise TimeoutError()

    api.install(app, lambda: db, lambda: user, mail)
    with TestClient(app) as client:
        path = "/users/me/customer-profile/email"
        response = client.post(path + "/request", json=request())
        assert response.status_code == 200
        data = response.json()
        assert data["state"] == "delivery_unknown" and "code" not in data
        assert client.post(path + "/request", json=request()).json() == data and len(calls) == 1
        row = db.get(service.ContactChallenge, data["challenge_id"])
        bad = next(
            str(i).zfill(6)
            for i in range(3)
            if service.code_digest(row, str(i).zfill(6)) != row.code_hash
        )
        assert (
            client.post(path + "/verify", json=dict(challenge_id=row.id, code=bad)).status_code
            == 400
        )
        assert db.get(service.ContactChallenge, row.id).attempts == 1
        assert (
            client.patch(
                "/users/me/customer-profile",
                json=dict(user_id=2, display_name="Other Buyer", version=0),
            ).status_code
            == 422
        )
        assert client.get("/users/me/customer-profile").headers["cache-control"] == "no-store"
