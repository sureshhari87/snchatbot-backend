from models import CustomerNotification


def payload(user_id, key="test-message-0001"):
    return {"user_id": user_id, "deduplication_key": key,
            "title": "Order update", "body": "Your order has an update.", "target": "/cart"}


def test_admin_creation_is_authorized_and_idempotent(client, db, auth_headers, admin_headers, verified_user):
    data = payload(verified_user.id)
    assert client.post("/admin/notifications", json=data).status_code == 401
    assert client.post("/admin/notifications", headers=auth_headers, json=data).status_code == 403
    first = client.post("/admin/notifications", headers=admin_headers, json=data)
    assert first.status_code == 201
    repeated = client.post("/admin/notifications", headers=admin_headers, json=data)
    assert repeated.json()["id"] == first.json()["id"]
    assert db.query(CustomerNotification).count() == 1
    changed = client.post("/admin/notifications", headers=admin_headers,
                          json={**data, "body": "Different content"})
    assert changed.status_code == 409
    assert db.query(CustomerNotification).one().body == data["body"]


def test_inbox_and_seen_are_recipient_scoped(client, db, auth_headers, admin_headers, verified_user, admin_user):
    own = client.post("/admin/notifications", headers=admin_headers,
                      json=payload(verified_user.id)).json()
    other = client.post("/admin/notifications", headers=admin_headers,
                        json=payload(admin_user.id)).json()
    assert client.get("/notifications/my").status_code == 401
    inbox = client.get("/notifications/my", headers=auth_headers).json()
    assert len(inbox) == 1 and inbox[0]["id"] == own["id"]
    assert "created_by" not in inbox[0] and "user_id" not in inbox[0]
    assert inbox[0]["createdAt"].endswith("+00:00")
    assert inbox[0]["read"] is False
    ids = {"ids": [own["id"], other["id"], 999999]}
    assert client.post("/notifications/seen", json=ids).status_code == 401
    for _ in range(2):
        assert client.post("/notifications/seen", headers=auth_headers, json=ids).status_code == 200
    db.expire_all()
    assert db.get(CustomerNotification, own["id"]).read_at is not None
    assert db.get(CustomerNotification, other["id"]).read_at is None


def test_notifications_reject_spoofing_invalid_targets_and_unbounded_requests(client, auth_headers, admin_headers, verified_user):
    data = payload(verified_user.id)
    for extra in ({"created_by": 1}, {"target": "https://example.com"}, {"title": "  "}):
        assert client.post("/admin/notifications", headers=admin_headers,
                           json={**data, **extra}).status_code == 422
    assert client.post("/admin/notifications", headers=admin_headers,
                       json=payload(999999)).status_code == 404
    assert client.get("/notifications/my?limit=1000", headers=auth_headers).status_code == 422
    for ids in ([True], [-1], [1] * 101):
        assert client.post("/notifications/seen", headers=auth_headers, json={"ids": ids}).status_code == 422
