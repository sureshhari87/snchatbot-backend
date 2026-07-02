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
        data={
            "username": registered_user["email"],
            "password": registered_user["password"]
        }
    )
    assert response.status_code == 200
    assert "access_token" in response.json()

def test_me_with_token(client, auth_headers):
    response = client.get("/me", headers=auth_headers)
    assert response.status_code == 200

def test_me_without_token(client):
    response = client.get("/me")
    assert response.status_code == 401