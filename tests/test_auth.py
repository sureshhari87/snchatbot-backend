from datetime import timedelta

from jose import jwt

from main import ALGORITHM, SECRET_KEY, create_access_token
from models import User


def test_me_with_malformed_token(client):
    response = client.get("/me", headers={"Authorization": "Bearer not-a-real-jwt"})

    assert response.status_code == 401
    assert response.json()["detail"] == "Could not validate credentials"


def test_me_with_token_for_missing_user(client):
    token = create_access_token(data={"sub": "nobody@example.com"})

    response = client.get("/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json()["detail"] == "Could not validate credentials"


def test_me_with_expired_token(client):
    expired_token = create_access_token(
        data={"sub": "test@example.com"}, expires_delta=timedelta(minutes=-1)
    )

    response = client.get("/me", headers={"Authorization": f"Bearer {expired_token}"})

    assert response.status_code == 401
    assert response.json()["detail"] == "Could not validate credentials"


def test_me_with_token_missing_sub_claim(client):
    token_without_sub = jwt.encode({"exp": 9999999999}, SECRET_KEY, algorithm=ALGORITHM)

    response = client.get("/me", headers={"Authorization": f"Bearer {token_without_sub}"})

    assert response.status_code == 401
    assert response.json()["detail"] == "Could not validate credentials"


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_register_user(client, user_data):
    response = client.post("/register", json=user_data)
    assert response.status_code == 200


def test_login_success(client, registered_user):
    response = client.post(
        "/login",
        data={"username": registered_user["email"], "password": registered_user["password"]},
    )
    assert response.status_code == 200
    assert "access_token" in response.json()


def test_me_with_token(client, auth_headers):
    response = client.get("/me", headers=auth_headers)
    assert response.status_code == 200


def test_me_endpoint_without_token(client):
    response = client.get("/me")
    assert response.status_code == 401


def test_register_duplicate_email(client):
    payload1 = {"username": "user1", "email": "dup@example.com", "password": "secret123"}
    payload2 = {"username": "user2", "email": "dup@example.com", "password": "secret123"}

    r1 = client.post("/register", json=payload1)
    r2 = client.post("/register", json=payload2)

    assert r1.status_code == 200
    assert r2.status_code == 400
    assert r2.json()["detail"] == "Email already registered"


def test_register_duplicate_username(client):
    payload1 = {"username": "sameuser", "email": "one@example.com", "password": "secret123"}
    payload2 = {"username": "sameuser", "email": "two@example.com", "password": "secret123"}

    r1 = client.post("/register", json=payload1)
    r2 = client.post("/register", json=payload2)

    assert r1.status_code == 200
    assert r2.status_code == 400
    assert r2.json()["detail"] == "Username already taken"


def test_login_wrong_password(client, registered_user):
    response = client.post(
        "/login", data={"username": registered_user["email"], "password": "wrongpass"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"


def test_login_unknown_email(client):
    response = client.post(
        "/login", data={"username": "missing@example.com", "password": "secret123"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"


def test_login_with_invalid_stored_password_hash_returns_401(client, db):
    user = User(
        username="bad_hash_user",
        email="bad-hash@example.com",
        hashed_password="not-a-valid-password-hash",
        is_verified=True,
    )
    db.add(user)
    db.commit()

    response = client.post(
        "/login", data={"username": "bad-hash@example.com", "password": "secret123"}
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"


def test_me_without_token(client):
    response = client.get("/me")
    assert response.status_code == 401


def test_me_with_invalid_token(client):
    response = client.get("/me", headers={"Authorization": "Bearer invalidtoken"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Could not validate credentials"


def test_login_returns_access_and_refresh_tokens(login_response):
    body = login_response.json()

    assert "access_token" in body
    assert "refresh_token" in body
    assert body["token_type"] == "bearer"


def test_refresh_returns_new_token_pair(client, refresh_token):
    response = client.post("/refresh", json={"refresh_token": refresh_token})
    body = response.json()

    assert response.status_code == 200
    assert "access_token" in body
    assert "refresh_token" in body
    assert body["token_type"] == "bearer"
