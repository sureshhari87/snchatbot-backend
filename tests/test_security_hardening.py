import config
import main


def test_production_cors_does_not_keep_wildcard():
    origins = config.normalize_cors_origins(
        "production",
        ["*"],
        ["https://shop.example.com/verify-email", "http://localhost:3000/reset"],
    )

    assert origins == ["https://shop.example.com"]


def test_staging_proxy_defaults_are_secure(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("TESTING", "0")
    monkeypatch.delenv("HTTPS_REDIRECT", raising=False)
    monkeypatch.delenv("TRUSTED_HOSTS", raising=False)
    monkeypatch.delenv("PROXY_HEADERS", raising=False)
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)

    settings = config.build_settings()

    assert settings.https_redirect is True
    assert settings.trusted_hosts == []
    assert settings.proxy_headers is True
    assert settings.forwarded_allow_ips == "*"


def test_security_headers_are_added(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "Permissions-Policy" in response.headers


def test_hsts_header_is_added_for_forwarded_https(client):
    response = client.get("/health", headers={"X-Forwarded-Proto": "https"})

    assert response.status_code == 200
    assert response.headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains"


def test_request_body_size_limit(client):
    oversized_body = "x" * (main.MAX_REQUEST_BODY_BYTES + 1)

    response = client.post(
        "/chat",
        data=oversized_body,
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413
    body = response.json()
    assert body["detail"] == "Request body too large"
    assert body["request_id"]


def test_validation_errors_are_normalized(client, auth_headers):
    response = client.post("/chat", json={}, headers=auth_headers)

    assert response.status_code == 422
    body = response.json()
    assert body["detail"] == "Validation error"
    assert body["request_id"]
    assert body["errors"][0]["field"]
    assert body["errors"][0]["message"]


def test_unhandled_errors_return_safe_response(client, monkeypatch):
    def broken_dependency_snapshot(db):
        raise RuntimeError("database password leaked in exception text")

    monkeypatch.setattr(main, "dependency_snapshot", broken_dependency_snapshot)

    response = client.get("/ready", headers={"X-Request-ID": "safe-error-test"})

    assert response.status_code == 500
    body = response.json()
    assert body == {
        "detail": "Internal server error",
        "request_id": "safe-error-test",
    }
