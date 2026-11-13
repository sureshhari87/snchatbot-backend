from datetime import timedelta

from models import CustomerNotification, utc_now


def test_unread_count_is_owner_scoped_and_not_limited_to_inbox_page(
    client, auth_headers, admin_user, verified_user, db, monkeypatch
):
    monkeypatch.setenv("NOTIFICATION_RETENTION_ENABLED", "0")
    for index in range(75):
        db.add(
            CustomerNotification(
                user_id=verified_user.id,
                deduplication_key=f"badge-test:{index}",
                title="Update",
                body="Synthetic inbox",
            )
        )
    db.add(
        CustomerNotification(
            user_id=admin_user.id,
            deduplication_key="other-owner:test",
            title="Other",
            body="Other inbox",
        )
    )
    db.add(
        CustomerNotification(
            user_id=verified_user.id,
            deduplication_key="read-owner:test",
            title="Read",
            body="Read inbox",
            read_at=utc_now(),
        )
    )
    db.commit()
    response = client.get("/notifications/unread-count", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {"unread_count": 75}
    assert len(client.get("/notifications/my", headers=auth_headers).json()) == 50
    assert (
        db.query(CustomerNotification).filter_by(user_id=verified_user.id, read_at=None).count()
        == 75
    )


def test_unread_count_obeys_retention_and_seen_receipts(
    client, auth_headers, verified_user, db, monkeypatch
):
    monkeypatch.setenv("NOTIFICATION_RETENTION_ENABLED", "1")
    now = utc_now()
    fresh = CustomerNotification(
        user_id=verified_user.id,
        deduplication_key="badge-fresh:test",
        title="Fresh",
        body="Fresh",
        created_at=now,
    )
    db.add(fresh)
    db.add(
        CustomerNotification(
            user_id=verified_user.id,
            deduplication_key="badge-expired:test",
            title="Expired",
            body="Expired",
            created_at=now - timedelta(days=91),
        )
    )
    db.commit()
    assert client.get("/notifications/unread-count", headers=auth_headers).json() == {
        "unread_count": 1
    }
    assert (
        client.post(
            "/notifications/seen", headers=auth_headers, json={"ids": [fresh.id]}
        ).status_code
        == 200
    )
    assert client.get("/notifications/unread-count", headers=auth_headers).json() == {
        "unread_count": 0
    }


def test_unread_count_requires_sign_in(client):
    assert client.get("/notifications/unread-count").status_code == 401
