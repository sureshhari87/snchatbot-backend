from models import AppConfigEntry, FeaturedItem, LeadCapture, Product, SeasonalCollection


def test_public_mobile_catalog_contracts(client, db):
    product = db.query(Product).filter(Product.category == "Ring", Product.metal == "Gold").first()
    db.add(
        FeaturedItem(
            product_id=product.id,
            title="Mobile Hero",
            subtitle="Shown on the Android home screen",
            display_order=1,
            is_active=True,
        )
    )
    db.add(
        SeasonalCollection(
            name="Wedding Picks",
            slug="wedding-picks",
            description="Wedding jewellery collection.",
            season="Wedding",
            is_active=True,
        )
    )
    db.commit()

    products_response = client.get(
        "/products",
        params={
            "category": "Ring",
            "metal": "Gold",
            "max_price": 20000,
            "in_stock_only": True,
        },
    )
    assert products_response.status_code == 200
    products = products_response.json()
    assert products[0]["name"] == "Classic Gold Ring"

    detail_response = client.get(f"/products/{product.id}")
    assert detail_response.status_code == 200
    assert detail_response.json()["id"] == product.id

    similar_response = client.get(f"/products/{product.id}/similar")
    assert similar_response.status_code == 200
    assert isinstance(similar_response.json(), list)

    featured_response = client.get("/featured-products")
    assert featured_response.status_code == 200
    assert any(item["id"] == product.id for item in featured_response.json())

    collections_response = client.get("/seasonal-collections")
    assert collections_response.status_code == 200
    assert collections_response.json()[0]["slug"] == "wedding-picks"

    categories_response = client.get("/categories")
    assert categories_response.status_code == 200
    assert any(category["slug"] == "ring" for category in categories_response.json())


def test_admin_knowledge_and_mobile_public_config(client, admin_headers, db):
    faq_response = client.post(
        "/admin/knowledge-base",
        headers=admin_headers,
        json={
            "kind": "faq",
            "title": "Do you support store pickup?",
            "content": "Yes, customers can request a store visit or callback.",
            "tags": ["pickup", "store"],
        },
    )
    assert faq_response.status_code == 200
    faq = faq_response.json()
    assert faq["slug"] == "do-you-support-store-pickup"
    assert faq["tags"] == ["pickup", "store"]

    public_faq_response = client.get("/faqs")
    assert public_faq_response.status_code == 200
    assert public_faq_response.json()[0]["title"] == "Do you support store pickup?"

    update_response = client.patch(
        f"/admin/knowledge-base/{faq['id']}",
        headers=admin_headers,
        json={"title": "Can I visit the store?", "tags": ["visit"]},
    )
    assert update_response.status_code == 200
    assert update_response.json()["slug"] == "can-i-visit-the-store"
    assert update_response.json()["tags"] == ["visit"]

    config_response = client.post(
        "/admin/config",
        headers=admin_headers,
        json={
            "key": "android_min_version",
            "value": "1",
            "description": "Minimum supported Android app version.",
            "is_public": True,
        },
    )
    assert config_response.status_code == 200

    mobile_response = client.get("/mobile/config")
    assert mobile_response.status_code == 200
    mobile_config = mobile_response.json()
    assert mobile_config["capabilities"]["chat"] is True
    assert mobile_config["capabilities"]["admin"] is False
    assert mobile_config["public_config"]["android_min_version"] == "1"

    config_entry = db.query(AppConfigEntry).filter(AppConfigEntry.key == "android_min_version").first()
    patch_response = client.patch(
        f"/admin/config/{config_entry.id}",
        headers=admin_headers,
        json={"value": "2"},
    )
    assert patch_response.status_code == 200
    assert patch_response.json()["value"] == "2"


def test_customer_support_capture_and_admin_status_updates(client, auth_headers, admin_headers, db):
    product = db.query(Product).first()

    custom_response = client.post(
        "/custom-orders",
        headers=auth_headers,
        json={
            "product_id": product.id,
            "session_id": "mobile-session-1",
            "description": "Need this ring with engraving",
            "budget": 25000,
            "metal": "Gold",
            "category": "Ring",
        },
    )
    assert custom_response.status_code == 200
    custom_order = custom_response.json()
    assert custom_order["status"] == "requested"

    my_custom_orders = client.get("/custom-orders/my", headers=auth_headers)
    assert my_custom_orders.status_code == 200
    assert my_custom_orders.json()[0]["description"] == "Need this ring with engraving"

    admin_custom_update = client.patch(
        f"/admin/custom-orders/{custom_order['id']}",
        headers=admin_headers,
        json={"status": "quoted"},
    )
    assert admin_custom_update.status_code == 200
    assert admin_custom_update.json()["status"] == "quoted"

    complaint_response = client.post(
        "/complaints",
        headers=auth_headers,
        json={
            "order_reference": "ORD-100",
            "category": "delivery",
            "message": "The package arrived late",
            "priority": "high",
        },
    )
    assert complaint_response.status_code == 200
    complaint = complaint_response.json()

    complaint_update = client.patch(
        f"/admin/complaints/{complaint['id']}",
        headers=admin_headers,
        json={"status": "reviewing"},
    )
    assert complaint_update.status_code == 200
    assert complaint_update.json()["status"] == "reviewing"

    order_support_response = client.post(
        "/orders/support",
        headers=auth_headers,
        json={
            "order_reference": "ORD-101",
            "request_type": "refund",
            "message": "I need a refund update",
        },
    )
    assert order_support_response.status_code == 200
    order_support = order_support_response.json()

    order_support_update = client.patch(
        f"/admin/orders/support/{order_support['id']}",
        headers=admin_headers,
        json={"status": "escalated"},
    )
    assert order_support_update.status_code == 200
    assert order_support_update.json()["status"] == "escalated"

    leads = db.query(LeadCapture).all()
    assert {lead.intent for lead in leads} >= {"custom_order", "complaint", "return_refund"}


def test_chat_response_exposes_ai_integration_metadata(auth_client):
    response = auth_client.post(
        "/chat",
        json={
            "message": "Track order ORD-102 and connect me with support",
            "session_id": "mobile-ai-contract",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "order_status"
    assert body["confidence"] > 0
    assert body["answer_source"] == "handoff_flow"
    assert "lead_capture" in body["tool_calls"]
    assert "oms-not-connected-capture-only" in body["guardrails"]
    assert body["lead_captured"] is True
