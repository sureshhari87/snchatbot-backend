from fastapi import HTTPException

from main import hash_password
from models import RefreshToken, User


def firebase_payload(email="firebase.user@example.com", uid="firebaseUid123"):
    return {
        "aud": "sona-test-firebase-project",
        "iss": "https://securetoken.google.com/sona-test-firebase-project",
        "sub": uid,
        "email": email,
        "email_verified": True,
    }


def test_firebase_auth_creates_verified_sona_user(client, db, monkeypatch):
    import main

    monkeypatch.setattr(main, "verify_firebase_id_token", lambda token: firebase_payload())

    response = client.post("/auth/firebase", json={"id_token": "firebase-id-token-for-tests"})

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["token_type"] == "bearer"

    user = db.query(User).filter(User.email == "firebase.user@example.com").one()
    assert user.is_verified is True
    assert user.is_admin is False
    assert db.query(RefreshToken).filter(RefreshToken.user_id == user.id).count() == 1

    me = client.get("/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["email"] == "firebase.user@example.com"


def test_firebase_auth_reuses_existing_email_and_marks_verified(client, db, monkeypatch):
    import main

    existing = User(
        username="firebase_existing",
        email="existing.firebase@example.com",
        hashed_password=hash_password("testpass123"),
        is_verified=False,
    )
    db.add(existing)
    db.commit()

    monkeypatch.setattr(
        main,
        "verify_firebase_id_token",
        lambda token: firebase_payload("existing.firebase@example.com", "uidExisting"),
    )

    response = client.post("/auth/firebase", json={"id_token": "firebase-id-token-for-tests"})

    assert response.status_code == 200
    users = db.query(User).filter(User.email == "existing.firebase@example.com").all()
    assert len(users) == 1
    assert users[0].id == existing.id
    assert users[0].is_verified is True


def test_firebase_auth_creates_and_reuses_phone_only_customer(client, db, monkeypatch):
    import main

    payload = {
        "aud": "sona-test-firebase-project",
        "iss": "https://securetoken.google.com/sona-test-firebase-project",
        "sub": "firebasePhoneUid123",
        "phone_number": "+919876543210",
    }
    monkeypatch.setattr(main, "verify_firebase_id_token", lambda token: payload)

    first = client.post("/auth/firebase", json={"id_token": "firebase-phone-token-one"})
    second = client.post("/auth/firebase", json={"id_token": "firebase-phone-token-two"})

    assert first.status_code == 200
    assert second.status_code == 200
    users = db.query(User).filter(User.email.like("phone_%@phone.sona.invalid")).all()
    assert len(users) == 1
    assert users[0].is_verified is True
    assert users[0].is_admin is False


def test_firebase_phone_identity_is_stable_and_does_not_expose_phone_number():
    import main

    first = main.firebase_email_for_identity({"sub": "uid-one"})
    second = main.firebase_email_for_identity({"sub": "uid-one"})

    assert first == second
    assert first.endswith("@phone.sona.invalid")
    assert "uid-one" not in first


def test_firebase_auth_validation_failure_returns_401(client, monkeypatch):
    import main

    def reject(_token):
        raise HTTPException(status_code=401, detail="Invalid Firebase token")

    monkeypatch.setattr(main, "verify_firebase_id_token", reject)

    response = client.post("/auth/firebase", json={"id_token": "bad-firebase-token-for-tests"})

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid Firebase token"


def test_mobile_config_reports_firebase_token_auth(client, monkeypatch):
    import main

    monkeypatch.setattr(main, "FIREBASE_AUTH_ENABLED", True)
    monkeypatch.setattr(main, "FIREBASE_PROJECT_ID", "sona-test-firebase-project")

    response = client.get("/mobile/config")

    assert response.status_code == 200
    body = response.json()
    assert body["capabilities"]["firebase_token_auth"] is True
    assert body["integrations"]["firebase_auth"]["enabled"] is True
