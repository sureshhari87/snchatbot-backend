"""Explicit HTTP v1 adapter. No credentials loaded and no network at import."""

import json
import os
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx

from push_controls import delivery_enabled
from push_delivery_policy import ALLOWED_KINDS
from push_outbox import (
    DeliveryUnavailableError,
    InvalidDeviceError,
    PermanentDeliveryError,
    RetryableDeliveryError,
)
from staging_push import recipients, verify_database


def retry_delay(response):
    # Respect Retry-After and at least one minute for quota failures.
    delay = 60 if response.status_code == 429 else 30
    raw = response.headers.get("Retry-After", "")
    try:
        parsed = (
            int(raw)
            if raw.isdigit()
            else (parsedate_to_datetime(raw) - datetime.now(timezone.utc)).total_seconds()
        )
        return max(delay, min(86400, parsed))
    except (ValueError, TypeError, OverflowError):
        return delay


class FcmSender:
    def __init__(self, *, client, project_id, access_token, expires_at):
        if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", project_id):
            raise DeliveryUnavailableError("Invalid FCM project")
        if not access_token or expires_at.tzinfo is None:
            raise DeliveryUnavailableError("FCM credential unavailable")
        self._client = client
        self._url = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
        self._token = access_token
        self._expires_at = expires_at

    def __call__(self, token, data):
        if not delivery_enabled() or datetime.now(timezone.utc) >= self._expires_at - timedelta(
            seconds=30
        ):
            raise DeliveryUnavailableError("FCM delivery unavailable")
        if (
            set(data) != {"user_id", "event_id", "type"}
            or data["type"] not in ALLOWED_KINDS
            or any(
                not isinstance(data[k], str)
                or not data[k].isascii()
                or not data[k].isdigit()
                or int(data[k]) <= 0
                for k in ("user_id", "event_id")
            )
        ):
            raise PermanentDeliveryError("Invalid push payload")
        tag = "sona-event-" + data["event_id"]
        if int(data["user_id"]) not in recipients():
            raise DeliveryUnavailableError("Recipient not approved for staging push")
        message = {
            "token": token,
            "data": data,
            "notification": {
                "title": "Sona Jewellery",
                "body": "You have an update. Open the app to view it.",
            },
            # No provider store-and-forward after offline logout/account switch.
            "android": {
                "ttl": "0s",
                "priority": "normal",
                "notification": {"tag": tag, "channel_id": "sona_channel"},
            },
            "apns": {"headers": {"apns-expiration": "0", "apns-collapse-id": tag}},
        }
        try:
            response = self._client.post(
                self._url,
                headers={"Authorization": "Bearer " + self._token},
                json={"message": message},
                timeout=5.0,
            )
        except httpx.HTTPError:
            raise RetryableDeliveryError("FCM transport failure") from None
        if response.status_code in (401, 403):
            raise DeliveryUnavailableError("FCM authorization unavailable")
        if response.status_code == 429 or response.status_code >= 500:
            raise RetryableDeliveryError(
                "FCM temporarily unavailable", retry_after=retry_delay(response)
            )
        try:
            body = response.json()
        except ValueError:
            raise RetryableDeliveryError("FCM invalid response") from None
        if response.status_code == 200 and isinstance(body, dict) and body.get("name"):
            return
        details = (
            body.get("error", {}).get("details", [])
            if isinstance(body, dict) and isinstance(body.get("error"), dict)
            else []
        )
        if (
            response.status_code == 404
            and isinstance(details, list)
            and any(
                isinstance(item, dict)
                and item.get("@type") == "type.googleapis.com/google.firebase.fcm.v1.FcmError"
                and item.get("errorCode") == "UNREGISTERED"
                for item in details
            )
        ):
            raise InvalidDeviceError("FCM token unregistered")
        if response.status_code == 200:
            raise RetryableDeliveryError(
                "FCM temporarily unavailable", retry_after=retry_delay(response)
            )
        raise PermanentDeliveryError("FCM rejected message")

    def close(self):
        self._client.close()


def prepare_sender():
    """Refresh OAuth BEFORE opening any database transaction/owner lock.

    No ADC/default-Firebase fallback. Use dedicated, least-privilege secrets.
    Caller closes sender after its bounded batch. Nothing logs secret values.
    """
    if not delivery_enabled():
        raise DeliveryUnavailableError("FCM delivery disabled")
    recipients()
    try:
        import requests
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account

        info = json.loads(os.environ.get("PUSH_FCM_SERVICE_ACCOUNT_JSON", ""))
        project = os.environ.get("PUSH_FCM_PROJECT_ID", "")
        if (
            not isinstance(info, dict)
            or info.get("type") != "service_account"
            or info.get("project_id") != project
            or info.get("token_uri") != "https://oauth2.googleapis.com/token"
            or not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", project)
        ):
            raise ValueError("Invalid configuration")
        credentials = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/firebase.messaging"]
        )
        with requests.Session() as session:
            request = Request(session=session)

            def bounded_request(*args, **kwargs):
                kwargs["timeout"] = 10
                return request(*args, **kwargs)

            credentials.refresh(bounded_request)
        expiry = credentials.expiry
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry <= datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError("Credential lifetime too short")
        client = httpx.Client(
            transport=httpx.HTTPTransport(retries=0), follow_redirects=False, timeout=5.0
        )
        return FcmSender(
            client=client, project_id=project, access_token=credentials.token, expires_at=expiry
        )
    except Exception:
        raise DeliveryUnavailableError("FCM configuration or authentication unavailable") from None


def run_configured_batch(factory, *, limit=1):
    """Operator-only building block; not installed in startup or any API route."""
    if not delivery_enabled():
        return []
    if not 1 <= limit <= 5:
        raise ValueError("Invalid FCM batch limit")
    from push_outbox import run_batch

    allowed = recipients()
    verify_database(factory)
    sender = prepare_sender()
    try:
        return run_batch(factory, sender, enabled=True, limit=limit, allowed_user_ids=allowed)
    finally:
        sender.close()
