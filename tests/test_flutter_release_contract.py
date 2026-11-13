"""API contract used by the November customer and admin Flutter releases."""

import pytest

from main import app


@pytest.mark.parametrize(
    ("path", "methods"),
    [
        ("/admin/capabilities", {"get"}),
        ("/users/me/customer-profile", {"get", "patch"}),
        ("/users/me/customer-profile/email/request", {"post"}),
        ("/users/me/customer-profile/email/verify", {"post"}),
        ("/notifications/unread-count", {"get"}),
        ("/billing/payments/{source}/{source_id}/receipt", {"post"}),
        ("/billing/savings/payments/{payment_id}/receipt", {"post"}),
        ("/billing/sales/{source}/{source_id}", {"get"}),
        ("/billing/documents/{document_id}/pdf-data", {"get"}),
        ("/purchase-identity", {"get", "post"}),
        ("/admin/purchase-identities/{identity_id}", {"get"}),
        ("/admin/purchase-identities/{identity_id}/review", {"post"}),
        ("/orders/{order_id}/cancellation-review", {"get", "post"}),
        ("/orders/{order_id}/cancellation-review/decision", {"post"}),
        ("/admin/orders/{order_id}/cancellation-review", {"get"}),
    ],
)
def test_flutter_release_routes_remain_authenticated(path, methods):
    paths = app.openapi()["paths"]
    assert path in paths, f"Flutter release requires {path}"
    for method in methods:
        assert method in paths[path], f"Flutter release requires {method.upper()} {path}"
        assert paths[path][method].get("security"), f"{method.upper()} {path} must require auth"
