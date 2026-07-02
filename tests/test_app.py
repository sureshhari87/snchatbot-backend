from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def create_test_user():
    user_data = {
        "username": "testuser1",
        "email": "testuser1@example.com",
        "password": "testpass123"
    }
    return client.post("/register", json=user_data)


def login_and_get_token():
    response = client.post(
        "/login",
        data={
            "username": "testuser1@example.com",
            "password": "testpass123"
        }
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_register_user():
    response = create_test_user()
    assert response.status_code in [200, 400]


def test_login_success():
    create_test_user()
    response = client.post(
        "/login",
        data={
            "username": "testuser1@example.com",
            "password": "testpass123"
        }
    )
    assert response.status_code == 200
    body = response.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"


def test_login_bad_password():
    create_test_user()
    response = client.post(
        "/login",
        data={
            "username": "testuser1@example.com",
            "password": "wrongpass"
        }
    )
    assert response.status_code == 401


def test_me_without_token():
    response = client.get("/me")
    assert response.status_code == 401


def test_me_with_token():
    create_test_user()
    token = login_and_get_token()
    response = client.get(
        "/me",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "testuser1@example.com"


def test_chat_without_token():
    response = client.post(
        "/chat",
        json={"message": "show me gold rings"}
    )
    assert response.status_code == 401


def test_chat_with_token():
    create_test_user()
    token = login_and_get_token()
    response = client.post(
        "/chat",
        json={"message": "show me gold rings"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert "reply" in body
    assert "products" in body
    assert "session_id" in body or "sessionid" in body

def test_register_duplicate_email():
    user1 = {
        "username": "dupuser1",
        "email": "dup@example.com",
        "password": "testpass123"
    }
    user2 = {
        "username": "dupuser2",
        "email": "dup@example.com",
        "password": "testpass123"
    }

    first = client.post("/register", json=user1)
    second = client.post("/register", json=user2)

    assert first.status_code in [200, 400]
    assert second.status_code == 400


def test_register_duplicate_username():
    user1 = {
        "username": "sameuser",
        "email": "same1@example.com",
        "password": "testpass123"
    }
    user2 = {
        "username": "sameuser",
        "email": "same2@example.com",
        "password": "testpass123"
    }

    first = client.post("/register", json=user1)
    second = client.post("/register", json=user2)

    assert first.status_code in [200, 400]
    assert second.status_code == 400


def test_me_with_invalid_token():
    response = client.get(
        "/me",
        headers={"Authorization": "Bearer invalidtoken123"}
    )
    assert response.status_code == 401


def test_chat_with_invalid_token():
    response = client.post(
        "/chat",
        json={"message": "show me rings"},
        headers={"Authorization": "Bearer invalidtoken123"}
    )
    assert response.status_code == 401


def test_chat_missing_message_field():
    create_test_user()
    token = login_and_get_token()
    response = client.post(
        "/chat",
        json={},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 422


def test_register_missing_password():
    response = client.post("/register", json={
        "username": "userx",
        "email": "userx@example.com"
    })
    assert response.status_code == 422


def test_login_missing_password():
    response = client.post(
        "/login",
        data={"username": "testuser1@example.com"}
    )
    assert response.status_code == 422