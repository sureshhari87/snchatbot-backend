import json

import main


def test_request_id_header_is_returned(client):
    response = client.get("/health", headers={"X-Request-ID": "trace-test-123"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "trace-test-123"


def test_readiness_and_dependency_checks(client):
    ready_response = client.get("/ready")
    dependencies_response = client.get("/dependencies")

    assert ready_response.status_code == 200
    assert ready_response.json()["status"] == "ok"
    assert ready_response.json()["dependencies"]["database"]["status"] == "ok"

    assert dependencies_response.status_code == 200
    assert dependencies_response.json()["dependencies"]["database"]["critical"] is True
    assert "email" in dependencies_response.json()["dependencies"]


def test_structured_auth_log_contains_request_id(client, monkeypatch):
    events = []

    def capture_log(level, message):
        events.append(json.loads(message))

    monkeypatch.setattr(main.logger, "log", capture_log)

    response = client.post(
        "/login",
        data={"username": "missing@example.com", "password": "wrongpass123"},
        headers={"X-Request-ID": "login-log-test"},
    )

    assert response.status_code == 401
    assert any(
        event["event"] == "auth.login_failed"
        and event["request_id"] == "login-log-test"
        and event["reason"] == "invalid_credentials"
        for event in events
    )


def test_metrics_track_chats_no_results_and_feedback(auth_client, client, admin_headers):
    chat_response = auth_client.post(
        "/chat",
        json={"message": "show me silver rings under 10000"},
    )
    feedback_response = auth_client.post(
        "/feedback",
        json={"helpful": True, "rating": 5, "context": "chat"},
    )

    assert chat_response.status_code == 200
    assert chat_response.json()["result_count"] == 0
    assert feedback_response.status_code == 200

    metrics_response = client.get("/admin/metrics", headers=admin_headers)
    metrics = metrics_response.json()

    assert metrics_response.status_code == 200
    assert metrics["counters"]["total_chats"] == 1
    assert metrics["counters"]["no_result_searches"] == 1
    assert metrics["counters"]["feedback_total"] == 1
    assert metrics["feedback_counts"]["positive"] == 1


def test_metrics_track_failed_login(client, registered_user, admin_headers):
    response = client.post(
        "/login",
        data={
            "username": registered_user["email"],
            "password": "wrongpass123",
        },
    )

    assert response.status_code == 401

    metrics_response = client.get("/admin/metrics", headers=admin_headers)

    assert metrics_response.status_code == 200
    assert metrics_response.json()["counters"]["failed_logins"] == 1


def test_metrics_track_token_refresh(client, token_pair, admin_headers):
    response = client.post(
        "/refresh",
        json={"refresh_token": token_pair["refresh_token"]},
    )

    assert response.status_code == 200

    metrics_response = client.get("/admin/metrics", headers=admin_headers)

    assert metrics_response.status_code == 200
    assert metrics_response.json()["counters"]["token_refreshes"] == 1


def test_metrics_track_admin_actions(client, admin_headers):
    response = client.post(
        "/admin/products",
        headers=admin_headers,
        json={
            "name": "Metrics Gold Ring",
            "category": "Ring",
            "metal": "Gold",
            "price": 15999,
        },
    )

    assert response.status_code == 200

    metrics_response = client.get("/admin/metrics", headers=admin_headers)

    assert metrics_response.status_code == 200
    assert metrics_response.json()["counters"]["admin_actions"] == 1
