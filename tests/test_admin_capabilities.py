"""Read-only console access follows the existing permission evaluator."""

import main


def test_capabilities_require_authenticated_admin(client, auth_headers, admin_headers):
    assert client.get("/admin/capabilities").status_code == 401
    assert client.get("/admin/capabilities", headers=auth_headers).status_code == 403
    response = client.get("/admin/capabilities", headers=admin_headers)
    assert response.status_code == 200
    assert response.json() == {"permissions": sorted(main.ADMIN_PERMISSIONS)}


def test_capabilities_return_only_caller_grants(client, admin_headers, monkeypatch):
    monkeypatch.setattr(main, "user_permissions", lambda user: {"catalog:manage", "unknown:grant"})
    assert client.get("/admin/capabilities", headers=admin_headers).json() == {
        "permissions": ["catalog:manage"]
    }


def test_revoked_permission_and_requested_identity_cannot_bypass_access(
    client, auth_headers, admin_headers, monkeypatch
):
    assert client.get("/admin/capabilities?user_id=1", headers=auth_headers).status_code == 403
    monkeypatch.setattr(main, "user_permissions", lambda user: {"customer:access"})
    assert client.get("/admin/capabilities", headers=admin_headers).status_code == 403
