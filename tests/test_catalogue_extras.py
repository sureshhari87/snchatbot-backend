from datetime import timedelta

import pytest

from models import (
    CatalogueCampaign,
    CatalogueContentAudit,
    Product,
    RecentlyViewedProduct,
    User,
    utc_now,
)


def product_ids(db, count=1):
    return [row.id for row in db.query(Product).order_by(Product.id).limit(count).all()]


def campaign(**overrides):
    now = utc_now().isoformat() + "Z"
    end = (utc_now() + timedelta(days=1)).isoformat() + "Z"
    return {
        "title": "Test collection",
        "subtitle": "Current catalogue",
        "cta_text": "Browse",
        "target": "Gold",
        "active": True,
        "starts_at": now,
        "ends_at": end,
        "reason": "Synthetic catalogue test",
        **overrides,
    }


def test_recent_history_owned_current_and_deduplicated(client, db, auth_headers, admin_headers):
    pid = product_ids(db)[0]
    endpoint = "/catalogue/recently-viewed"
    assert client.get(endpoint).status_code == 401
    assert client.post(endpoint, json={"product_id": pid}).status_code == 401
    for _ in range(2):
        assert (
            client.post(endpoint, headers=auth_headers, json={"product_id": pid}).status_code == 200
        )
    assert db.query(RecentlyViewedProduct).count() == 1
    assert client.get(endpoint, headers=admin_headers).json() == []
    p = db.get(Product, pid)
    p.price = 4321
    p.stock_quantity = 0
    p.in_stock = False
    p.name = "Updated name"
    db.commit()
    rows = client.get(endpoint, headers=auth_headers).json()
    assert len(rows) == 1
    assert rows[0]["product"]["price"] == 4321
    assert rows[0]["product"]["name"] == "Updated name"
    assert rows[0]["product"]["stock_quantity"] == 0
    assert rows[0]["viewed_at"].endswith("+00:00")
    assert "user_id" not in rows[0] and "source_data" not in rows[0]["product"]


@pytest.mark.parametrize(
    "extra",
    [{"user_id": 123}, {"price": 1}, {"viewed_at": "2000-01-01"}, {"product_data": {"stock": 99}}],
)
def test_recent_rejects_client_ownership_snapshots_and_time(client, db, auth_headers, extra):
    assert (
        client.post(
            "/catalogue/recently-viewed",
            headers=auth_headers,
            json={"product_id": product_ids(db)[0], **extra},
        ).status_code
        == 422
    )
    assert db.query(RecentlyViewedProduct).count() == 0


@pytest.mark.parametrize("pid", [0, -1, True, "1", 999999])
def test_recent_requires_existing_numeric_product(client, auth_headers, pid):
    result = client.post(
        "/catalogue/recently-viewed", headers=auth_headers, json={"product_id": pid}
    )
    assert result.status_code == (404 if pid == 999999 else 422)


def test_recent_bound_revisit_and_missing_products(client, db, auth_headers):
    for i in range(12):
        p = Product(name=f"History test {i}", price=100, stock_quantity=1, in_stock=True)
        db.add(p)
    db.commit()
    ids = [
        p.id
        for p in db.query(Product)
        .filter(Product.name.like("History test%"))
        .order_by(Product.id)
        .all()
    ]
    for pid in ids:
        assert (
            client.post(
                "/catalogue/recently-viewed", headers=auth_headers, json={"product_id": pid}
            ).status_code
            == 200
        )
    rows = client.get("/catalogue/recently-viewed", headers=auth_headers).json()
    assert [r["product"]["id"] for r in rows] == list(reversed(ids[-10:]))
    assert db.query(RecentlyViewedProduct).count() == 10
    client.post("/catalogue/recently-viewed", headers=auth_headers, json={"product_id": ids[2]})
    assert (
        client.get("/catalogue/recently-viewed", headers=auth_headers).json()[0]["product"]["id"]
        == ids[2]
    )
    db.delete(db.get(Product, ids[2]))
    db.commit()
    assert all(
        r["product"]["id"] != ids[2]
        for r in client.get("/catalogue/recently-viewed", headers=auth_headers).json()
    )


def test_server_targeting_priority_and_no_audience_leak(client, db, auth_headers, admin_headers):
    user_id = db.query(User).filter_by(is_admin=False).one().id
    global_row = client.post(
        "/admin/catalogue/campaigns", headers=admin_headers, json=campaign()
    ).json()
    targeted = client.post(
        "/admin/catalogue/campaigns",
        headers=admin_headers,
        json=campaign(title="Private collection", audience="customer", customer_ids=[user_id]),
    ).json()
    public = client.get("/catalogue/home").json()["campaign"]
    assert public["id"] == global_row["id"]
    private = client.get("/catalogue/home/my", headers=auth_headers).json()["campaign"]
    assert private["id"] == targeted["id"]
    assert (
        client.get("/catalogue/home/my", headers=admin_headers).json()["campaign"]["id"]
        == global_row["id"]
    )
    assert "customer_ids" not in private and "customerIds" not in private["data"]
    assert "audience" not in private["data"]
    assert client.get("/catalogue/home/my").status_code == 401


@pytest.mark.parametrize(
    "overrides",
    [
        {"active": False},
        {"starts_at": (utc_now() + timedelta(hours=1)).isoformat() + "Z"},
        {
            "starts_at": (utc_now() - timedelta(days=2)).isoformat() + "Z",
            "ends_at": (utc_now() - timedelta(days=1)).isoformat() + "Z",
        },
    ],
)
def test_inactive_future_and_expired_campaigns_hidden(client, admin_headers, overrides):
    assert (
        client.post(
            "/admin/catalogue/campaigns", headers=admin_headers, json=campaign(**overrides)
        ).status_code
        == 201
    )
    assert client.get("/catalogue/home").json()["campaign"] is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"audience": "customer"},
        {"customer_ids": [99999]},
        {"audience": "customer", "customer_ids": [99999]},
        {"target": "https://example.invalid"},
        {"image": "http://example.invalid/banner.jpg"},
        {"image": "https://test-user:test-pass@example.invalid/banner.jpg"},
        {"ends_at": "2000-01-01T00:00:00Z"},
        {"starts_at": "2026-10-12T00:00:00"},
        {"reason": "   "},
        {"active": "true"},
        {"customer_ids": ["3"]},
    ],
)
def test_campaign_validation_no_partial_audit(client, db, admin_headers, overrides):
    assert (
        client.post(
            "/admin/catalogue/campaigns", headers=admin_headers, json=campaign(**overrides)
        ).status_code
        == 422
    )
    assert db.query(CatalogueCampaign).count() == 0
    assert db.query(CatalogueContentAudit).count() == 0


def test_catalogue_admin_permission_and_conflicting_versions(
    client, db, auth_headers, admin_headers
):
    assert client.get("/admin/catalogue/campaigns", headers=auth_headers).status_code == 403
    assert (
        client.post("/admin/catalogue/campaigns", headers=auth_headers, json=campaign()).status_code
        == 403
    )
    row = client.post("/admin/catalogue/campaigns", headers=admin_headers, json=campaign()).json()
    url = f"/admin/catalogue/campaigns/{row['id']}"
    payload = campaign(title="Changed", expected_version=1)
    assert client.put(url, headers=auth_headers, json=payload).status_code == 403
    assert client.put(url, headers=admin_headers, json=payload).json()["version"] == 2
    assert client.put(url, headers=admin_headers, json=payload).status_code == 409
    assert db.query(CatalogueContentAudit).count() == 2
    audit = client.get("/admin/catalogue/content-audit", headers=admin_headers).json()
    assert audit[0]["before"]["data"]["title"] == "Test collection"
    assert audit[0]["after"]["data"]["title"] == "Changed"
    assert client.get("/admin/catalogue/content-audit", headers=auth_headers).status_code == 403


def test_layout_versions_preserve_other_toggles_and_disable_campaign(
    client, db, auth_headers, admin_headers
):
    client.post("/admin/catalogue/campaigns", headers=admin_headers, json=campaign())
    layout = client.get("/admin/catalogue/home-layout", headers=admin_headers).json()
    assert layout["version"] == 0
    body = {
        "expected_version": 0,
        "sections": {**layout["sections"], "banners": False, "voucher": False},
        "reason": "Synthetic visibility test",
    }
    assert (
        client.put("/admin/catalogue/home-layout", headers=auth_headers, json=body).status_code
        == 403
    )
    result = client.put("/admin/catalogue/home-layout", headers=admin_headers, json=body)
    assert result.status_code == 200 and result.json()["version"] == 1
    assert client.get("/catalogue/home").json()["campaign"] is None
    assert client.get("/catalogue/home").json()["sections"]["voucher"] is False
    assert (
        client.put("/admin/catalogue/home-layout", headers=admin_headers, json=body).status_code
        == 409
    )
    assert db.query(CatalogueContentAudit).filter_by(resource="home_layout").count() == 1
