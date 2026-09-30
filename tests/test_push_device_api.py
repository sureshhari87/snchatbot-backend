import pytest

import main
from push_devices import PushDevice


def body(pair):
    return {"refresh_token": pair["refresh_token"], "push_token": "test-device-token-00000001"}


def test_two_devices_single_logout_preserves_other(
    enabled, client, db, auth_headers, token_pair, verified_user
):
    second = client.post(
        "/login", data={"username": verified_user.email, "password": "testpass123"}
    ).json()
    assert (
        client.put("/users/me/push-device", headers=auth_headers, json=body(token_pair)).status_code
        == 200
    )
    assert (
        client.put(
            "/users/me/push-device",
            headers=auth_headers,
            json={**body(second), "push_token": "test-device-token-00000002"},
        ).status_code
        == 200
    )
    assert db.query(PushDevice).count() == 2
    assert (
        client.post(
            "/logout", headers=auth_headers, json={"refresh_token": token_pair["refresh_token"]}
        ).status_code
        == 200
    )
    assert db.query(PushDevice).count() == 1
    assert db.query(PushDevice).one().push_token == "test-device-token-00000002"


def test_security_revocation_keeps_pending_password_change(
    enabled, client, db, auth_headers, token_pair, verified_user
):
    assert (
        client.put("/users/me/push-device", headers=auth_headers, json=body(token_pair)).status_code
        == 200
    )
    verified_user.hashed_password = "synthetic-new-hash"
    main.revoke_user_refresh_tokens(db, verified_user.id, "password_reset")
    db.commit()
    db.refresh(verified_user)
    assert verified_user.hashed_password == "synthetic-new-hash"
    assert db.query(PushDevice).count() == 0


def test_refresh_reuse_detaches_devices(enabled, client, db, auth_headers, token_pair):
    assert (
        client.put("/users/me/push-device", headers=auth_headers, json=body(token_pair)).status_code
        == 200
    )
    assert (
        client.post("/refresh", json={"refresh_token": token_pair["refresh_token"]}).status_code
        == 200
    )
    assert (
        client.post("/refresh", json={"refresh_token": token_pair["refresh_token"]}).status_code
        == 401
    )
    assert db.query(PushDevice).count() == 0


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("PUSH_DEVICE_REGISTRATION_ENABLED", "1")


def test_disabled_by_default(client, auth_headers, token_pair, monkeypatch):
    monkeypatch.delenv("PUSH_DEVICE_REGISTRATION_ENABLED", raising=False)
    assert (
        client.put("/users/me/push-device", headers=auth_headers, json=body(token_pair)).status_code
        == 503
    )


def test_owner_auth_validation_and_idempotency(
    enabled, client, db, auth_headers, token_pair, admin_headers
):
    data = body(token_pair)
    assert client.put("/users/me/push-device", json=data).status_code == 401
    assert client.put("/users/me/push-device", headers=admin_headers, json=data).status_code == 409
    for _ in range(2):
        result = client.put("/users/me/push-device", headers=auth_headers, json=data)
        assert result.status_code == 200
        assert result.json() == {"registered": True}
    assert db.query(PushDevice).count() == 1
    for extra in ({"user_id": 999}, {"push_token": "bad token"}, {"refresh_token": ""}):
        result = client.put("/users/me/push-device", headers=auth_headers, json={**data, **extra})
        assert result.status_code == 422
        assert data["refresh_token"] not in result.text
        assert data["push_token"] not in result.text
    assert (
        client.post(
            "/users/me/push-device/detach",
            headers=admin_headers,
            json={"refresh_token": data["refresh_token"]},
        ).status_code
        == 200
    )
    assert db.query(PushDevice).count() == 1
    assert (
        client.post(
            "/users/me/push-device/detach",
            headers=auth_headers,
            json={"refresh_token": data["refresh_token"]},
        ).status_code
        == 200
    )
    assert db.query(PushDevice).count() == 0


@pytest.mark.parametrize("all_devices", [False, True])
def test_logout_revokes_device(enabled, client, db, auth_headers, token_pair, all_devices):
    assert (
        client.put("/users/me/push-device", headers=auth_headers, json=body(token_pair)).status_code
        == 200
    )
    path = "/logout-all-devices" if all_devices else "/logout"
    assert (
        client.post(
            path, headers=auth_headers, json={"refresh_token": token_pair["refresh_token"]}
        ).status_code
        == 200
    )
    assert db.query(PushDevice).count() == 0
    assert (
        client.put("/users/me/push-device", headers=auth_headers, json=body(token_pair)).status_code
        == 409
    )


def test_refresh_keeps_device_old_token_logout_revokes_family(
    enabled, client, db, auth_headers, token_pair
):
    assert (
        client.put("/users/me/push-device", headers=auth_headers, json=body(token_pair)).status_code
        == 200
    )
    rotated = client.post("/refresh", json={"refresh_token": token_pair["refresh_token"]})
    assert rotated.status_code == 200
    assert db.query(PushDevice).count() == 1
    assert (
        client.post(
            "/logout", headers=auth_headers, json={"refresh_token": token_pair["refresh_token"]}
        ).status_code
        == 200
    )
    assert db.query(PushDevice).count() == 0
    assert (
        client.put(
            "/users/me/push-device", headers=auth_headers, json=body(rotated.json())
        ).status_code
        == 409
    )
