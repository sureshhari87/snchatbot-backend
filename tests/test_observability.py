import json
import sys
from types import SimpleNamespace

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

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


def test_database_dependency_reports_missing_schema_tables():
    engine = create_engine("sqlite://")
    session_local = sessionmaker(bind=engine)
    db = session_local()
    try:
        db.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
        status = main.database_dependency_status(db)
    finally:
        db.close()
        engine.dispose()

    assert status["status"] == "error"
    assert status["error_type"] == "MissingDatabaseTables"
    assert status["missing_table_count"] > 0


def test_sentry_configuration_uses_safe_defaults(monkeypatch):
    init_calls = []

    fake_sentry = SimpleNamespace(init=lambda **kwargs: init_calls.append(kwargs))
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake_sentry)
    monkeypatch.setattr(main, "is_testing", lambda: False)
    monkeypatch.setattr(main, "SENTRY_DSN", "https://public@example.ingest.sentry.io/1")
    monkeypatch.setattr(main, "SENTRY_RELEASE", "test-release")
    monkeypatch.setattr(main, "SENTRY_TRACES_SAMPLE_RATE", 0.25)
    monkeypatch.setattr(main, "SENTRY_PROFILES_SAMPLE_RATE", 0.0)
    monkeypatch.setattr(main, "SENTRY_SEND_DEFAULT_PII", False)

    main.configure_error_monitoring()

    assert len(init_calls) == 1
    init_kwargs = init_calls[0]
    assert init_kwargs["dsn"] == "https://public@example.ingest.sentry.io/1"
    assert init_kwargs["environment"] == main.APP_ENV
    assert init_kwargs["release"] == "test-release"
    assert init_kwargs["traces_sample_rate"] == 0.25
    assert init_kwargs["profiles_sample_rate"] == 0.0
    assert init_kwargs["send_default_pii"] is False
    assert init_kwargs["before_send"] is main.scrub_sentry_event


def test_sentry_scrubber_filters_sensitive_fields():
    event = {
        "request": {
            "headers": {
                "Authorization": "Bearer secret-token",
                "User-Agent": "pytest",
                "Cookie": "session=secret",
            },
            "data": {
                "password": "secret",
                "nested": {"refresh_token": "secret-refresh"},
                "message": "hello",
            },
        }
    }

    scrubbed = main.scrub_sentry_event(event, {})

    assert scrubbed["request"]["headers"]["Authorization"] == "[Filtered]"
    assert scrubbed["request"]["headers"]["Cookie"] == "[Filtered]"
    assert scrubbed["request"]["data"]["password"] == "[Filtered]"
    assert scrubbed["request"]["data"]["nested"]["refresh_token"] == "[Filtered]"
    assert scrubbed["request"]["data"]["message"] == "hello"


def test_monitoring_helpers_log_monitored_event_without_type_error(monkeypatch):
    captured_logs = []
    captured_messages = []

    fake_sentry = SimpleNamespace(
        capture_message=lambda event, level=None: captured_messages.append((event, level))
    )
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake_sentry)
    monkeypatch.setattr(main, "is_testing", lambda: False)
    monkeypatch.setattr(main, "SENTRY_DSN", "https://public@example.ingest.sentry.io/1")
    monkeypatch.setattr(main, "MONITORING_WEBHOOK_URL", "https://monitoring.example.test/hook")
    monkeypatch.setattr(main, "json_http_request", lambda *args, **kwargs: (200, {}))
    monkeypatch.setattr(main.logger, "log", lambda level, message: captured_logs.append(message))

    webhook_sent = main.send_monitoring_alert("monitoring.test_alert", severity="info")
    sentry_sent = main.capture_message_for_monitoring("monitoring.test_alert", severity="info")

    assert webhook_sent is True
    assert sentry_sent is True
    assert captured_messages == [("monitoring.test_alert", "info")]
    log_payloads = [json.loads(message) for message in captured_logs]
    assert {
        (payload["event"], payload.get("monitored_event"))
        for payload in log_payloads
    } >= {
        ("monitoring.alert_sent", "monitoring.test_alert"),
        ("monitoring.sentry_message_sent", "monitoring.test_alert"),
    }


def test_admin_alert_test_reports_webhook_and_sentry(client, admin_headers, monkeypatch):
    monkeypatch.setattr(main, "send_monitoring_alert", lambda *args, **kwargs: True)
    monkeypatch.setattr(main, "capture_message_for_monitoring", lambda *args, **kwargs: True)

    response = client.post("/admin/alerts/test", headers=admin_headers)

    assert response.status_code == 200
    assert response.json()["message"] == "Monitoring alert sent (webhook=true, sentry=true)"


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
