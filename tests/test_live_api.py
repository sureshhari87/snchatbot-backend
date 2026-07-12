import json
import os
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError

import pytest

LIVE_API_BASE_URL = os.getenv("LIVE_API_BASE_URL", "").rstrip("/")
LIVE_API_EXPECT_LATEST_CHAT_CONTRACT = os.getenv(
    "LIVE_API_EXPECT_LATEST_CHAT_CONTRACT",
    "",
).lower() in {"1", "true", "yes", "on"}

pytestmark = pytest.mark.skipif(
    not LIVE_API_BASE_URL,
    reason="Set LIVE_API_BASE_URL to run live Hugging Face API smoke tests.",
)


def live_request(method, path, body=None, token=None, form=False):
    headers = {}
    data = None

    if token:
        headers["Authorization"] = f"Bearer {token}"

    if body is not None:
        if form:
            data = urllib.parse.urlencode(body).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        f"{LIVE_API_BASE_URL}{path}",
        data=data,
        headers=headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.read().decode("utf-8")
            return response.status, json.loads(text)
    except HTTPError as exc:
        text = exc.read().decode("utf-8")
        return exc.code, json.loads(text)


def test_live_health_endpoint():
    status, body = live_request("GET", "/health")

    assert status == 200
    assert body == {"status": "ok"}


def test_live_openapi_core_contract():
    status, schema = live_request("GET", "/openapi.json")

    assert status == 200
    paths = schema["paths"]
    assert "/health" in paths
    assert "/register" in paths
    assert "/login" in paths
    assert "/refresh" in paths
    assert "/me" in paths
    assert "/chat" in paths
    assert "/forgot-password" in paths
    assert "/reset-password" in paths
    assert "/verify-email" in paths
    assert "/resend-verification" in paths
    chat_response = schema["components"]["schemas"]["ChatResponse"]
    assert "suggestions" in chat_response["properties"]


@pytest.mark.skipif(
    not LIVE_API_EXPECT_LATEST_CHAT_CONTRACT,
    reason="Set LIVE_API_EXPECT_LATEST_CHAT_CONTRACT=1 after deploying the latest chat API.",
)
def test_live_openapi_latest_chat_contract():
    status, schema = live_request("GET", "/openapi.json")

    assert status == 200
    chat_response = schema["components"]["schemas"]["ChatResponse"]
    assert "applied_filters" in chat_response["properties"]
    assert "result_count" in chat_response["properties"]
    assert "suggested_next_questions" in chat_response["properties"]


def test_live_register_requires_email_verification_before_login():
    unique = int(time.time() * 1000)
    user = {
        "username": f"live_smoke_{unique}",
        "email": f"live_smoke_{unique}@example.com",
        "password": "testpass123",
    }

    register_status, registered = live_request("POST", "/register", user)
    assert register_status == 200
    assert registered["email"] == user["email"]

    login_status, tokens = live_request(
        "POST",
        "/login",
        {"username": user["email"], "password": user["password"]},
        form=True,
    )
    assert login_status == 403
    assert tokens["detail"] == "Please verify your email before logging in"


def test_live_public_auth_helper_routes_exist():
    forgot_status, forgot = live_request(
        "POST",
        "/forgot-password",
        {"email": f"missing_{int(time.time() * 1000)}@example.com"},
    )
    assert forgot_status == 200
    assert "password reset" in forgot["message"].lower()

    resend_status, resend = live_request(
        "POST",
        "/resend-verification",
        {"email": f"missing_{int(time.time() * 1000)}@example.com"},
    )
    assert resend_status == 200
    assert "verification email" in resend["message"].lower()
