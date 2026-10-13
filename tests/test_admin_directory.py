"""Directory privacy, permission and cursor boundaries."""

import main
from models import OrderSnapshot, Product, User


def add_customer(db, name):
    row = User(
        username=name,
        email=f"{name}@example.test",
        hashed_password="synthetic-only",
        is_verified=True,
        is_admin=False,
    )
    db.add(row)
    db.commit()
    return row


def test_directory_and_summary_deny_anonymous_and_customers(client, auth_headers):
    for path in [
        "/admin/customers",
        "/admin/customers/1",
        "/admin/customers/1/orders",
        "/admin/commerce-summary",
    ]:
        assert client.get(path).status_code == 401
        assert client.get(path, headers=auth_headers).status_code == 403


def test_directory_pages_exclude_admins_and_authentication_fields(
    client, db, admin_user, admin_headers
):
    rows = [add_customer(db, f"directory-{i}") for i in range(3)]
    first = client.get("/admin/customers?limit=2", headers=admin_headers)
    assert first.status_code == 200
    page = first.json()
    assert [r["id"] for r in page["items"]] == [rows[0].id, rows[1].id]
    assert page["next_after_id"] == rows[1].id
    last = client.get(
        f"/admin/customers?limit=2&after_id={page['next_after_id']}", headers=admin_headers
    ).json()
    assert [r["id"] for r in last["items"]] == [rows[2].id]
    assert last["next_after_id"] is None
    for row in page["items"] + last["items"]:
        assert set(row) == {"id", "username", "email", "is_verified"}
        assert row["id"] != admin_user.id
    assert client.get("/admin/customers?limit=101", headers=admin_headers).status_code == 422
    assert client.get("/admin/customers?after_id=-1", headers=admin_headers).status_code == 422


def test_customer_details_hide_admin_records_and_unknown_ids(client, db, admin_user, admin_headers):
    row = add_customer(db, "directory-details")
    details = client.get(f"/admin/customers/{row.id}", headers=admin_headers)
    assert details.status_code == 200
    assert details.json() == {
        "id": row.id,
        "username": row.username,
        "email": row.email,
        "is_verified": True,
    }
    assert client.get(f"/admin/customers/{admin_user.id}", headers=admin_headers).status_code == 404
    assert client.get("/admin/customers/999999", headers=admin_headers).status_code == 404


def test_customer_orders_filter_owner_before_cursor_pagination(client, db, admin_headers):
    first = add_customer(db, "directory-owner")
    other = add_customer(db, "directory-other")
    orders = []
    for owner, ref in [(first, "one"), (other, "foreign"), (first, "two")]:
        order = OrderSnapshot(user_id=owner.id, order_reference=ref, status="placed", total=100)
        db.add(order)
        db.commit()
        orders.append(order)
    page = client.get(
        f"/admin/customers/{first.id}/orders?limit=1&user_id={other.id}", headers=admin_headers
    ).json()
    assert len(page) == 1 and page[0]["id"] == orders[2].id
    older = client.get(
        f"/admin/customers/{first.id}/orders?before_id={orders[2].id}", headers=admin_headers
    ).json()
    assert [r["id"] for r in older] == [orders[0].id]
    assert client.get("/admin/customers/999999/orders", headers=admin_headers).status_code == 404


def test_summary_counts_database_rows_without_financial_totals(client, db, admin_headers):
    before = client.get("/admin/commerce-summary", headers=admin_headers).json()
    row = add_customer(db, "summary-owner")
    db.add(Product(name="Synthetic summary item", price=100))
    db.add(
        OrderSnapshot(
            user_id=row.id, order_reference="summary", status="payment_pending", total=999999
        )
    )
    db.commit()
    after = client.get("/admin/commerce-summary", headers=admin_headers).json()
    assert after["customers"] == before["customers"] + 1
    assert after["products"] == before["products"] + 1
    assert after["orders"] == before["orders"] + 1
    assert (
        after["order_status_counts"].get("payment_pending")
        == before["order_status_counts"].get("payment_pending", 0) + 1
    )
    assert set(after) == {"customers", "products", "orders", "order_status_counts"}


def test_directory_and_metrics_use_distinct_existing_permissions(
    client, admin_headers, monkeypatch
):
    monkeypatch.setattr(main, "user_permissions", lambda user: {"metrics:read"})
    assert client.get("/admin/commerce-summary", headers=admin_headers).status_code == 200
    assert client.get("/admin/customers", headers=admin_headers).status_code == 403
    monkeypatch.setattr(main, "user_permissions", lambda user: {"support:manage"})
    assert client.get("/admin/customers", headers=admin_headers).status_code == 200
    assert client.get("/admin/commerce-summary", headers=admin_headers).status_code == 403
