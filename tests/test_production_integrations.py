import main
from models import ExternalIntegrationEvent


def test_user_addresses_and_notification_settings(client, auth_headers):
    first = client.post(
        "/users/me/addresses",
        headers=auth_headers,
        json={
            "label": "home",
            "full_name": "Test User",
            "phone": "+919999999999",
            "line1": "No 1 Main Road",
            "city": "Chennai",
            "state": "Tamil Nadu",
            "postal_code": "600001",
            "country": "India",
        },
    )
    assert first.status_code == 200
    first_address = first.json()
    assert first_address["is_default"] is True

    second = client.post(
        "/users/me/addresses",
        headers=auth_headers,
        json={
            "label": "office",
            "full_name": "Test User",
            "line1": "Office Street",
            "city": "Chennai",
            "state": "Tamil Nadu",
            "postal_code": "600002",
            "is_default": True,
        },
    )
    assert second.status_code == 200
    assert second.json()["is_default"] is True

    addresses = client.get("/users/me/addresses", headers=auth_headers)
    assert addresses.status_code == 200
    assert addresses.json()[0]["label"] == "office"
    assert addresses.json()[1]["is_default"] is False

    updated = client.patch(
        f"/users/me/addresses/{first_address['id']}",
        headers=auth_headers,
        json={"city": "Bengaluru"},
    )
    assert updated.status_code == 200
    assert updated.json()["city"] == "Bengaluru"

    settings = client.get("/users/me/notification-settings", headers=auth_headers)
    assert settings.status_code == 200
    assert settings.json()["email_enabled"] is True

    patched = client.patch(
        "/users/me/notification-settings",
        headers=auth_headers,
        json={
            "marketing_enabled": True,
            "push_token": "android-push-token",
            "quiet_hours_start": "22:00",
            "quiet_hours_end": "07:00",
        },
    )
    assert patched.status_code == 200
    assert patched.json()["marketing_enabled"] is True
    assert patched.json()["push_token"] == "android-push-token"


def test_saved_chat_history_list_and_detail(auth_client):
    chat_response = auth_client.post(
        "/chat",
        json={"message": "show gold rings under 20000", "session_id": "history-session"},
    )
    assert chat_response.status_code == 200

    sessions = auth_client.get("/chat/sessions")
    assert sessions.status_code == 200
    session = sessions.json()[0]
    assert session["session_id"] == "history-session"
    assert session["message_count"] == 2
    assert session["last_filters"]["category"] == "Ring"

    detail = auth_client.get("/chat/sessions/history-session")
    assert detail.status_code == 200
    body = detail.json()
    assert body["session_id"] == "history-session"
    assert [message["role"] for message in body["messages"]] == ["user", "assistant"]


def test_oms_order_lookup_and_action_sync(client, auth_headers, admin_headers, db, monkeypatch):
    calls = []

    def fake_json_http_request(method, url, payload=None, headers=None, timeout=10):
        calls.append(
            {
                "method": method,
                "url": url,
                "payload": payload,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return 200, {"order_reference": "ORD-500", "status": "packed"}

    monkeypatch.setattr(main, "OMS_ENABLED", True)
    monkeypatch.setattr(main, "OMS_BASE_URL", "https://oms.example/api")
    monkeypatch.setattr(main, "OMS_API_KEY", "oms-token")
    monkeypatch.setattr(main, "json_http_request", fake_json_http_request)

    lookup = client.get("/orders/ORD-500", headers=auth_headers)
    assert lookup.status_code == 200
    assert lookup.json()["integration_status"] == "synced"
    assert lookup.json()["data"]["status"] == "packed"

    refund = client.post(
        "/orders/support",
        headers=auth_headers,
        json={
            "order_reference": "ORD-500",
            "request_type": "refund",
            "message": "Refund needed",
        },
    )
    assert refund.status_code == 200
    assert refund.json()["integration_status"] == "synced"
    assert refund.json()["status"] == "synced_to_oms"

    events = db.query(ExternalIntegrationEvent).filter(ExternalIntegrationEvent.service == "oms").all()
    assert {event.action for event in events} >= {"lookup", "refund"}
    assert calls[0]["headers"]["Authorization"] == "Bearer oms-token"
    assert calls[1]["method"] == "POST"

    admin_events = client.get("/admin/integrations/events?service=oms", headers=admin_headers)
    assert admin_events.status_code == 200
    assert len(admin_events.json()) >= 2


def test_llm_grounded_chat_layer_uses_configured_provider(auth_client, db, monkeypatch):
    def fake_json_http_request(method, url, payload=None, headers=None, timeout=10):
        assert method == "POST"
        assert url == "https://llm.example/v1/chat/completions"
        assert headers["Authorization"] == "Bearer llm-key"
        assert payload["model"] == "test-model"
        return 200, {
            "choices": [
                {
                    "message": {
                        "content": "The Classic Gold Ring is in stock and fits your budget."
                    }
                }
            ]
        }

    monkeypatch.setattr(main, "LLM_ENABLED", True)
    monkeypatch.setattr(main, "LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setattr(main, "LLM_API_KEY", "llm-key")
    monkeypatch.setattr(main, "LLM_MODEL", "test-model")
    monkeypatch.setattr(main, "json_http_request", fake_json_http_request)

    response = auth_client.post(
        "/chat",
        json={"message": "show gold rings under 20000", "session_id": "llm-session"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "The Classic Gold Ring is in stock and fits your budget."
    assert body["answer_source"] == "llm_grounded_catalog"
    assert "llm_completion" in body["tool_calls"]
    assert "llm-grounded-catalog" in body["guardrails"]

    event = db.query(ExternalIntegrationEvent).filter(ExternalIntegrationEvent.service == "llm").first()
    assert event is not None
    assert event.status == "synced"
