import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from fcm_sender import FcmSender, prepare_sender, run_configured_batch
from push_outbox import (
    DeliveryUnavailableError,
    InvalidDeviceError,
    PermanentDeliveryError,
    RetryableDeliveryError,
)


@pytest.fixture(autouse=True)
def gates(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("PUSH_STAGING_ALLOWED_USER_IDS", "3")
    for key in ("PUSH_DEVICE_REGISTRATION_ENABLED", "PUSH_OUTBOX_ENABLED", "PUSH_DELIVERY_ENABLED"):
        monkeypatch.setenv(key, "1")


def sender(handler):
    return FcmSender(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        project_id="sona-test-project",
        access_token="synthetic-oauth",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


DATA = {"user_id": "3", "event_id": "12", "type": "verified_payment"}


def test_generic_payload_fixed_destination_and_no_redirects():
    def handle(request):
        assert (
            str(request.url)
            == "https://fcm.googleapis.com/v1/projects/sona-test-project/messages:send"
        )
        assert request.headers["Authorization"] == "Bearer synthetic-oauth"
        message = json.loads(request.content)["message"]
        assert message["token"] == "synthetic-device"
        assert message["data"] == DATA
        assert set(message["notification"]) == {"title", "body"}
        assert message["notification"]["body"] == "You have an update. Open the app to view it."
        assert message["android"]["ttl"] == "0s"
        assert message["android"]["notification"]["tag"] == "sona-event-12"
        assert request.extensions["timeout"]["read"] == 5
        return httpx.Response(200, json={"name": "projects/test/messages/accepted"})

    with_sender = sender(handle)
    try:
        with_sender("synthetic-device", DATA)
    finally:
        with_sender.close()


@pytest.mark.parametrize(
    "status,body,error",
    [
        (
            404,
            {
                "error": {
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.firebase.fcm.v1.FcmError",
                            "errorCode": "UNREGISTERED",
                        }
                    ]
                }
            },
            InvalidDeviceError,
        ),
        (404, {"error": {"status": "NOT_FOUND"}}, PermanentDeliveryError),
        (400, {"error": {"details": [{"errorCode": "INVALID_ARGUMENT"}]}}, PermanentDeliveryError),
        (401, {}, DeliveryUnavailableError),
        (403, {}, DeliveryUnavailableError),
        (429, {}, RetryableDeliveryError),
        (503, {}, RetryableDeliveryError),
        (200, {}, RetryableDeliveryError),
        (302, {}, PermanentDeliveryError),
    ],
)
def test_sanitized_errors_and_only_explicit_unregistered_deletes(status, body, error):
    def handle(request):
        return httpx.Response(status, json=body)

    adapter = sender(handle)
    try:
        with pytest.raises(error) as exc:
            adapter("synthetic-device", DATA)
        assert "synthetic-device" not in str(exc.value)
        assert "synthetic-oauth" not in str(exc.value)
    finally:
        adapter.close()


def test_retry_after_and_transport_timeout():
    adapter = sender(lambda request: httpx.Response(429, json={}, headers={"Retry-After": "120"}))
    try:
        with pytest.raises(RetryableDeliveryError) as exc:
            adapter("synthetic-device", DATA)
        assert exc.value.retry_after == 120
    finally:
        adapter.close()

    def timeout(request):
        raise httpx.ReadTimeout("private-token-in-provider-error")

    adapter = sender(timeout)
    try:
        with pytest.raises(RetryableDeliveryError, match="^FCM transport failure$"):
            adapter("synthetic-device", DATA)
    finally:
        adapter.close()


@pytest.mark.parametrize(
    "gate", ["PUSH_DEVICE_REGISTRATION_ENABLED", "PUSH_OUTBOX_ENABLED", "PUSH_DELIVERY_ENABLED"]
)
def test_disabled_never_loads_credentials_or_opens_db(monkeypatch, gate):
    monkeypatch.setenv(gate, "0")
    monkeypatch.setenv("PUSH_FCM_SERVICE_ACCOUNT_JSON", "not-json")
    with pytest.raises(DeliveryUnavailableError, match="disabled"):
        prepare_sender()
    assert run_configured_batch(lambda: pytest.fail("Database opened while disabled")) == []


def test_invalid_settings_fail_before_network(monkeypatch):
    monkeypatch.setenv("PUSH_FCM_PROJECT_ID", "sona-test-project")
    monkeypatch.setenv(
        "PUSH_FCM_SERVICE_ACCOUNT_JSON",
        json.dumps(
            {
                "type": "service_account",
                "project_id": "sona-test-project",
                "token_uri": "https://untrusted.example/token",
            }
        ),
    )
    with pytest.raises(DeliveryUnavailableError, match="configuration"):
        prepare_sender()


def test_runtime_disable_and_unapproved_payload_never_send(monkeypatch):
    adapter = sender(lambda request: pytest.fail("Unexpected request"))
    try:
        with pytest.raises(PermanentDeliveryError):
            adapter("synthetic-device", {**DATA, "amount": "1000"})
        monkeypatch.setenv("PUSH_DELIVERY_ENABLED", "0")
        with pytest.raises(DeliveryUnavailableError):
            adapter("synthetic-device", DATA)
    finally:
        adapter.close()


def test_oauth_preparation_is_explicit_bounded_and_outside_worker(monkeypatch):
    from google.auth.transport import requests as auth_requests
    from google.oauth2 import service_account

    import fcm_sender

    calls = []

    class Credentials:
        expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        token = "synthetic-oauth"

        def refresh(self, request):
            request(url="https://oauth2.googleapis.com/token", timeout=999)
            calls.append("refreshed")

    def create(info, scopes):
        assert scopes == ["https://www.googleapis.com/auth/firebase.messaging"]
        calls.append("explicit-service-account")
        return Credentials()

    def request(**kwargs):
        assert kwargs["timeout"] == 10
        calls.append("bounded-oauth")

    monkeypatch.setattr(service_account.Credentials, "from_service_account_info", create)
    monkeypatch.setattr(auth_requests, "Request", lambda **kwargs: request)
    monkeypatch.setenv("PUSH_FCM_PROJECT_ID", "sona-test-project")
    monkeypatch.setenv(
        "PUSH_FCM_SERVICE_ACCOUNT_JSON",
        json.dumps(
            {
                "type": "service_account",
                "project_id": "sona-test-project",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        ),
    )
    adapter = fcm_sender.prepare_sender()
    adapter.close()
    assert calls == ["explicit-service-account", "bounded-oauth", "refreshed"]


def test_quota_html_still_honours_retry_after():
    adapter = sender(
        lambda request: httpx.Response(429, text="temporary error", headers={"Retry-After": "180"})
    )
    try:
        with pytest.raises(RetryableDeliveryError) as exc:
            adapter("synthetic-device", DATA)
        assert exc.value.retry_after == 180
    finally:
        adapter.close()


def test_runtime_recipient_and_environment_guard(monkeypatch):
    adapter = sender(lambda request: pytest.fail("Unapproved provider call"))
    try:
        with pytest.raises(DeliveryUnavailableError):
            adapter("synthetic-device", {**DATA, "user_id": "5"})
        monkeypatch.setenv("APP_ENV", "production")
        with pytest.raises(DeliveryUnavailableError):
            adapter("synthetic-device", DATA)
    finally:
        adapter.close()
