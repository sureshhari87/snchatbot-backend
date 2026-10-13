"""Preview binding, atomic inbox-only writes and idempotent audit boundaries."""

import pytest

import config
import inbox_broadcast_api as api
from inbox_broadcast_models import InboxBroadcast
from models import CustomerNotification, User
from push_outbox import PushEvent


@pytest.fixture(autouse=True)
def enable(monkeypatch):
    monkeypatch.setattr(api, "ENABLED", True)
    monkeypatch.setattr(config, "APP_ENV", "staging")


def customer(db, name):
    row = User(
        username=name,
        email=f"{name}@example.test",
        hashed_password="synthetic-only",
        is_admin=False,
        is_verified=True,
    )
    db.add(row)
    db.commit()
    return row


def payload(ids):
    return {
        "request_key": "broadcast-test-key",
        "recipient_ids": ids,
        "title": "Synthetic staging notice",
        "body": "Synthetic inbox-only acceptance message",
        "target": "",
    }


def preview(client, headers, value):
    response = client.post("/admin/broadcasts/preview", headers=headers, json=value)
    assert response.status_code == 200
    return {**value, "preview_token": response.json()["preview_token"]}


def test_permission_and_production_guards(client, db, admin_headers, auth_headers, monkeypatch):
    row = customer(db, "broadcast-guard")
    value = payload([row.id])
    assert client.post("/admin/broadcasts/preview", json=value).status_code == 401
    assert (
        client.post("/admin/broadcasts/preview", headers=auth_headers, json=value).status_code
        == 403
    )
    monkeypatch.setattr(config, "APP_ENV", "production")
    monkeypatch.setattr(config, "is_testing", lambda: False)
    assert (
        client.post("/admin/broadcasts/preview", headers=admin_headers, json=value).status_code
        == 403
    )
    monkeypatch.setattr(config, "is_testing", lambda: True)
    assert (
        client.post("/admin/broadcasts/preview", headers=admin_headers, json=value).status_code
        == 403
    )
    assert db.query(InboxBroadcast).count() == 0
    monkeypatch.setattr(api, "ENABLED", False)
    assert (
        client.post("/admin/broadcasts/preview", headers=admin_headers, json=value).status_code
        == 503
    )


def test_preview_writes_nothing_and_confirm_is_inbox_only(client, db, admin_headers, admin_user):
    rows = [customer(db, "broadcast-one"), customer(db, "broadcast-two")]
    value = payload([rows[1].id, rows[0].id, rows[1].id])
    before = (db.query(CustomerNotification).count(), db.query(PushEvent).count())
    response = client.post("/admin/broadcasts/preview", headers=admin_headers, json=value)
    assert response.status_code == 200
    assert response.json()["recipient_ids"] == [rows[0].id, rows[1].id]
    assert response.json()["recipient_count"] == 2
    assert db.query(InboxBroadcast).count() == 0
    assert (db.query(CustomerNotification).count(), db.query(PushEvent).count()) == before
    confirmed = client.post(
        "/admin/broadcasts/confirm",
        headers=admin_headers,
        json={**value, "preview_token": response.json()["preview_token"]},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["notification_count"] == 2
    assert response.json()["payload_sha256"] == confirmed.json()["payload_sha256"]
    assert confirmed.json()["push_created"] is False and confirmed.json()["sms_created"] is False
    assert db.query(PushEvent).count() == before[1]
    messages = db.query(CustomerNotification).order_by(CustomerNotification.user_id).all()
    assert [m.user_id for m in messages] == [r.id for r in rows]
    assert all(m.created_by == admin_user.id and m.title == value["title"] for m in messages)
    audit = db.query(InboxBroadcast).one()
    assert audit.recipient_ids == [r.id for r in rows]
    assert audit.notification_ids == [m.id for m in messages]


def test_repeated_confirm_and_read_keep_single_snapshot(client, db, admin_headers):
    row = customer(db, "broadcast-replay")
    value = preview(client, admin_headers, payload([row.id]))
    first = client.post("/admin/broadcasts/confirm", headers=admin_headers, json=value)
    assert first.status_code == 200
    again = client.post("/admin/broadcasts/confirm", headers=admin_headers, json=value)
    assert again.status_code == 200 and again.json()["id"] == first.json()["id"]
    assert db.query(InboxBroadcast).count() == 1 and db.query(CustomerNotification).count() == 1
    read = client.get("/admin/broadcasts/key/broadcast-test-key", headers=admin_headers)
    assert read.status_code == 200 and read.json()["id"] == first.json()["id"]
    changed = client.post(
        "/admin/broadcasts/confirm", headers=admin_headers, json={**value, "body": "Changed intent"}
    )
    assert changed.status_code == 409
    assert db.query(CustomerNotification).count() == 1


def test_preview_binds_content_and_eligibility_is_rechecked(client, db, admin_headers):
    row = customer(db, "broadcast-drift")
    value = preview(client, admin_headers, payload([row.id]))
    changed = client.post(
        "/admin/broadcasts/confirm", headers=admin_headers, json={**value, "title": "Different"}
    )
    assert changed.status_code == 409
    row.is_admin = True
    db.commit()
    assert (
        client.post("/admin/broadcasts/confirm", headers=admin_headers, json=value).status_code
        == 409
    )
    assert db.query(CustomerNotification).count() == 0 and db.query(InboxBroadcast).count() == 0


def test_partial_notification_insert_rolls_back_all_new_messages(
    client, db, admin_headers, admin_user
):
    rows = [customer(db, "broadcast-atomic-one"), customer(db, "broadcast-atomic-two")]
    value = preview(client, admin_headers, payload([r.id for r in rows]))
    db.add(
        CustomerNotification(
            user_id=rows[1].id,
            created_by=admin_user.id,
            deduplication_key="broadcast:"
            + api.digest([value["request_key"], admin_user.id])
            + ":"
            + str(rows[1].id),
            title="Previous record",
            body="Previous record",
            target="",
        )
    )
    db.commit()
    assert (
        client.post("/admin/broadcasts/confirm", headers=admin_headers, json=value).status_code
        == 409
    )
    assert db.query(CustomerNotification).count() == 1 and db.query(InboxBroadcast).count() == 0
    assert db.query(CustomerNotification).one().title == "Previous record"


@pytest.mark.parametrize(
    "changes",
    [
        {"recipient_ids": []},
        {"recipient_ids": [True]},
        {"recipient_ids": list(range(1, 102))},
        {"audience": "global"},
        {"push": True},
        {"target": "https://example.test"},
    ],
)
def test_no_global_audience_or_transport_controls(client, admin_headers, changes):
    assert (
        client.post(
            "/admin/broadcasts/preview", headers=admin_headers, json={**payload([1]), **changes}
        ).status_code
        == 422
    )


def test_invalid_preview_and_unavailable_recipient_fail_without_writes(
    client, db, admin_headers, admin_user
):
    row = customer(db, "broadcast-invalid")
    value = {**payload([row.id]), "preview_token": "invalid-synthetic-token-value"}
    assert (
        client.post("/admin/broadcasts/confirm", headers=admin_headers, json=value).status_code
        == 409
    )
    assert (
        client.post(
            "/admin/broadcasts/preview", headers=admin_headers, json=payload([admin_user.id])
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/admin/broadcasts/preview", headers=admin_headers, json=payload([999999])
        ).status_code
        == 409
    )
    assert db.query(CustomerNotification).count() == 0
