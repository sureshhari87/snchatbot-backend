import json
import os
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError

import pytest


LIVE_API_BASE_URL = os.getenv("LIVE_API_BASE_URL", "").rstrip("/")

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
    assert "/me" in paths
    assert "/chat" in paths


def test_live_register_login_me_and_chat_flow():
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
    assert login_status == 200
    assert tokens["access_token"]
    assert tokens["token_type"] == "bearer"

    me_status, me = live_request("GET", "/me", token=tokens["access_token"])
    assert me_status == 200
    assert me["email"] == user["email"]

    chat_status, chat = live_request(
        "POST",
        "/chat",
        {"message": "show me gold rings under 20000"},
        token=tokens["access_token"],
    )
    assert chat_status == 200
    assert "reply" in chat
    assert "products" in chat
    assert isinstance(chat["products"], list)
    assert "session_id" in chat
