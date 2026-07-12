from models import LeadCapture, Product


def test_wishlist_flow(client, auth_headers, db):
    product = db.query(Product).first()

    add_response = client.post(
        "/wishlist",
        headers=auth_headers,
        json={"product_id": product.id, "note": "Show this to family"},
    )

    assert add_response.status_code == 200
    saved_item = add_response.json()
    assert saved_item["product"]["id"] == product.id
    assert saved_item["note"] == "Show this to family"

    list_response = client.get("/wishlist", headers=auth_headers)

    assert list_response.status_code == 200
    assert len(list_response.json()) == 1

    delete_response = client.delete(
        f"/wishlist/{saved_item['id']}",
        headers=auth_headers,
    )

    assert delete_response.status_code == 200
    assert client.get("/wishlist", headers=auth_headers).json() == []


def test_save_for_later_flow(client, auth_headers, db):
    product = db.query(Product).filter(Product.category == "Necklace").first()

    add_response = client.post(
        "/save-for-later",
        headers=auth_headers,
        json={"product_id": product.id, "note": "Compare later"},
    )

    assert add_response.status_code == 200
    saved_item = add_response.json()
    assert saved_item["product"]["id"] == product.id

    list_response = client.get("/save-for-later", headers=auth_headers)

    assert list_response.status_code == 200
    assert list_response.json()[0]["note"] == "Compare later"

    delete_response = client.delete(
        f"/save-for-later/{saved_item['id']}",
        headers=auth_headers,
    )

    assert delete_response.status_code == 200


def test_request_callback_flow(client, auth_headers):
    response = client.post(
        "/request-callback",
        headers=auth_headers,
        json={
            "phone": "+919999999999",
            "reason": "Need help choosing an engagement ring",
            "preferred_time": "Tomorrow evening",
        },
    )

    assert response.status_code == 200
    callback = response.json()
    assert callback["phone"] == "+919999999999"
    assert callback["status"] == "new"

    list_response = client.get("/request-callbacks/my", headers=auth_headers)

    assert list_response.status_code == 200
    assert list_response.json()[0]["reason"] == "Need help choosing an engagement ring"


def test_appointment_booking_flow(client, auth_headers):
    response = client.post(
        "/appointments",
        headers=auth_headers,
        json={
            "phone": "+919999999999",
            "store_location": "Chennai T Nagar",
            "appointment_time": "2026-08-01T17:30:00",
            "purpose": "Store visit for wedding jewellery",
        },
    )

    assert response.status_code == 200
    appointment = response.json()
    assert appointment["store_location"] == "Chennai T Nagar"
    assert appointment["status"] == "requested"

    list_response = client.get("/appointments/my", headers=auth_headers)

    assert list_response.status_code == 200
    assert list_response.json()[0]["purpose"] == "Store visit for wedding jewellery"


def test_chat_captures_availability_lead_and_handoff(auth_client, db):
    response = auth_client.post(
        "/chat",
        json={
            "message": "Is this gold ring available? I want to talk to support",
            "session_id": "lead-session",
        },
    )
    body = response.json()

    assert response.status_code == 200
    assert body["lead_captured"] is True
    assert body["handoff"]["reason"] == "availability"
    assert "request_callback" in body["handoff"]["channels"]

    lead = db.query(LeadCapture).filter(LeadCapture.session_id == "lead-session").first()

    assert lead is not None
    assert lead.intent == "availability"
    assert lead.status == "new"


def test_chat_captures_gift_lead(auth_client, db):
    response = auth_client.post(
        "/chat",
        json={
            "message": "Need a birthday gift under 10000",
            "session_id": "gift-lead-session",
        },
    )
    body = response.json()

    assert response.status_code == 200
    assert body["lead_captured"] is True
    assert body["handoff"]["reason"] == "gift"

    lead = db.query(LeadCapture).filter(LeadCapture.session_id == "gift-lead-session").first()
    assert lead.intent == "gift"


def test_admin_can_view_and_update_leads(auth_client, admin_headers, client, db):
    auth_client.post(
        "/chat",
        json={
            "message": "Can I get a discount on this necklace?",
            "session_id": "discount-lead-session",
        },
    )

    leads_response = client.get("/admin/leads", headers=admin_headers)

    assert leads_response.status_code == 200
    leads = leads_response.json()
    assert len(leads) == 1
    assert leads[0]["intent"] == "discount"

    update_response = client.patch(
        f"/admin/leads/{leads[0]['id']}",
        headers=admin_headers,
        json={"status": "contacted"},
    )

    assert update_response.status_code == 200
    assert update_response.json()["status"] == "contacted"
