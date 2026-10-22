from datetime import datetime, timedelta

import pytest

import notification_api
import notification_retention as retention
from models import CustomerNotification

NOW = datetime(2026, 10, 23, 12)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setenv("NOTIFICATION_RETENTION_ENABLED", "1")
    monkeypatch.setattr(retention, "utc_now", lambda: NOW)
    monkeypatch.setattr(notification_api, "utc_now", lambda: NOW)


def add(db, uid, key, age, read_age=None):
    row = CustomerNotification(
        user_id=uid,
        deduplication_key=key,
        title="Test",
        body="Preserve",
        target="",
        created_at=NOW - timedelta(days=age),
        read_at=None if read_age is None else NOW - timedelta(days=read_age),
    )
    db.add(row)
    db.commit()
    return row


@pytest.mark.parametrize(
    "age,read_age,visible",
    [
        (89, None, True),
        (90, None, False),
        (91, None, False),
        (100, 44, True),
        (100, 45, False),
        (100, 46, False),
        (91, 1, True),
    ],
)
def test_cutoffs_and_first_read_clock(
    client, db, verified_user, auth_headers, age, read_age, visible
):
    row = add(db, verified_user.id, "boundary", age, read_age)
    response = client.get("/notifications/my", headers=auth_headers)
    assert response.status_code == 200
    assert bool(response.json()) is visible
    assert db.get(CustomerNotification, row.id).body == "Preserve"


def test_disabled_by_default_and_preview_is_read_only(
    client, db, verified_user, admin_headers, monkeypatch
):
    monkeypatch.delenv("NOTIFICATION_RETENTION_ENABLED")
    assert retention.enabled() is False
    add(db, verified_user.id, "expired-read", 100, 45)
    add(db, verified_user.id, "expired-unread", 90)
    before = [(r.id, r.read_at) for r in db.query(CustomerNotification).all()]
    response = client.get("/admin/notifications/retention-preview", headers=admin_headers)
    assert response.status_code == 200
    result = response.json()
    assert result["enabled"] is False and result["expired_total_count"] == 2
    assert result["expired_read_count"] == result["expired_unread_count"] == 1
    assert result["records_deleted"] is False
    assert [(r.id, r.read_at) for r in db.query(CustomerNotification).all()] == before


def test_preview_requires_admin(client, auth_headers):
    assert client.get("/admin/notifications/retention-preview").status_code == 401
    assert (
        client.get("/admin/notifications/retention-preview", headers=auth_headers).status_code
        == 403
    )


def test_disabled_preserves_legacy_visibility(client, db, verified_user, auth_headers, monkeypatch):
    monkeypatch.setenv("NOTIFICATION_RETENTION_ENABLED", "0")
    row = add(db, verified_user.id, "disabled", 200)
    assert client.get("/notifications/my", headers=auth_headers).json()[0]["id"] == row.id


def test_seen_cannot_revive_expired_or_other_accounts(
    client, db, verified_user, admin_user, auth_headers, monkeypatch
):
    old = add(db, verified_user.id, "expired", 90)
    own = add(db, verified_user.id, "recent", 89)
    other = add(db, admin_user.id, "other", 1)
    for day in range(2):
        monkeypatch.setattr(notification_api, "utc_now", lambda day=day: NOW + timedelta(days=day))
        assert (
            client.post(
                "/notifications/seen",
                headers=auth_headers,
                json={"ids": [old.id, own.id, other.id]},
            ).status_code
            == 200
        )
    db.expire_all()
    assert db.get(CustomerNotification, old.id).read_at is None
    assert db.get(CustomerNotification, other.id).read_at is None
    assert db.get(CustomerNotification, own.id).read_at == NOW


def test_pagination_filters_before_limit_and_badge_source(
    client, db, verified_user, admin_user, auth_headers
):
    keep = add(db, verified_user.id, "keep", 1)
    add(db, verified_user.id, "hidden", 100)
    add(db, admin_user.id, "not-yours", 1)
    result = client.get("/notifications/my?limit=1", headers=auth_headers).json()
    assert [r["id"] for r in result] == [keep.id]
    assert sum(not r["read"] for r in result) == 1
    assert client.get("/notifications/my?limit=1&offset=1", headers=auth_headers).json() == []


def test_expired_deduplication_key_is_not_recreated(
    client, db, verified_user, auth_headers, admin_headers
):
    data = {
        "user_id": verified_user.id,
        "deduplication_key": "retention-retry",
        "title": "Test",
        "body": "Preserve",
        "target": "",
    }
    first = client.post("/admin/notifications", headers=admin_headers, json=data)
    assert first.status_code == 201
    row = db.get(CustomerNotification, first.json()["id"])
    row.created_at = NOW - timedelta(days=90)
    db.commit()
    repeated = client.post("/admin/notifications", headers=admin_headers, json=data)
    assert repeated.json()["id"] == row.id
    assert db.query(CustomerNotification).count() == 1
    assert client.get("/notifications/my", headers=auth_headers).json() == []


def test_expiry_preserves_broadcast_audit_and_retry(
    client, db, verified_user, auth_headers, admin_headers, monkeypatch
):
    import config
    import inbox_broadcast_api as broadcast
    from inbox_broadcast_models import InboxBroadcast

    monkeypatch.setattr(config, "APP_ENV", "staging")
    monkeypatch.setattr(broadcast, "ENABLED", True)
    data = {
        "request_key": "retention-broadcast",
        "recipient_ids": [verified_user.id],
        "title": "Preserve audit",
        "body": "Synthetic retention test",
        "target": "",
    }
    preview = client.post("/admin/broadcasts/preview", headers=admin_headers, json=data)
    assert preview.status_code == 200
    confirmed = {**data, "preview_token": preview.json()["preview_token"]}
    first = client.post("/admin/broadcasts/confirm", headers=admin_headers, json=confirmed)
    assert first.status_code == 200
    audit = db.query(InboxBroadcast).one()
    ids = list(audit.notification_ids)
    row = db.get(CustomerNotification, ids[0])
    row.created_at = NOW - timedelta(days=90)
    db.commit()
    path = "/admin/broadcasts/key/retention-broadcast"
    before = client.get(path, headers=admin_headers).json()
    assert client.get("/notifications/my", headers=auth_headers).json() == []
    assert client.get(path, headers=admin_headers).json() == before
    again = client.post("/admin/broadcasts/confirm", headers=admin_headers, json=confirmed)
    assert again.status_code == 200 and again.json()["id"] == first.json()["id"]
    assert db.query(InboxBroadcast).one().notification_ids == ids
    assert db.query(CustomerNotification).count() == 1
