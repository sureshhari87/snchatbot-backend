from models import Product


def test_admin_required_for_catalogue_routes(client, auth_headers):
    response = client.post(
        "/admin/products",
        headers=auth_headers,
        json={
            "name": "Admin Only Ring",
            "category": "Ring",
            "metal": "Gold",
            "price": 12000,
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Admin access required"


def test_admin_product_crud_and_inventory(client, admin_headers):
    create_response = client.post(
        "/admin/products",
        headers=admin_headers,
        json={
            "name": "Emerald Gold Ring",
            "description": "A green stone ring for occasion wear.",
            "sku": "RING-EMERALD-999",
            "category": "Ring",
            "metal": "Gold",
            "price": 32999,
            "stock_quantity": 3,
            "in_stock": True,
        },
    )

    assert create_response.status_code == 200
    product = create_response.json()
    assert product["id"]
    assert product["sku"] == "RING-EMERALD-999"

    inventory_response = client.patch(
        f"/admin/products/{product['id']}/inventory",
        headers=admin_headers,
        json={"stock_quantity": 0},
    )

    assert inventory_response.status_code == 200
    assert inventory_response.json()["stock_quantity"] == 0
    assert inventory_response.json()["in_stock"] is False

    update_response = client.patch(
        f"/admin/products/{product['id']}",
        headers=admin_headers,
        json={"price": 29999, "is_featured": True},
    )

    assert update_response.status_code == 200
    assert update_response.json()["price"] == 29999
    assert update_response.json()["is_featured"] is True

    delete_response = client.delete(
        f"/admin/products/{product['id']}",
        headers=admin_headers,
    )

    assert delete_response.status_code == 200
    assert delete_response.json()["message"] == "Product deleted"


def test_admin_category_crud(client, admin_headers):
    create_response = client.post(
        "/admin/categories",
        headers=admin_headers,
        json={
            "name": "Nose Pins",
            "description": "Small occasion and daily-wear nose pins.",
        },
    )

    assert create_response.status_code == 200
    category = create_response.json()
    assert category["slug"] == "nose-pins"

    update_response = client.patch(
        f"/admin/categories/{category['id']}",
        headers=admin_headers,
        json={"name": "Nose Jewellery", "is_active": False},
    )

    assert update_response.status_code == 200
    assert update_response.json()["slug"] == "nose-jewellery"
    assert update_response.json()["is_active"] is False

    delete_response = client.delete(
        f"/admin/categories/{category['id']}",
        headers=admin_headers,
    )

    assert delete_response.status_code == 200


def test_admin_featured_items_and_collections(client, db, admin_headers):
    product = db.query(Product).first()

    featured_response = client.post(
        "/admin/featured-items",
        headers=admin_headers,
        json={
            "product_id": product.id,
            "title": "Festive Pick",
            "subtitle": "Best for gifting",
            "display_order": 2,
        },
    )

    assert featured_response.status_code == 200
    featured = featured_response.json()
    assert featured["product_id"] == product.id

    featured_update = client.patch(
        f"/admin/featured-items/{featured['id']}",
        headers=admin_headers,
        json={"display_order": 1},
    )

    assert featured_update.status_code == 200
    assert featured_update.json()["display_order"] == 1

    collection_response = client.post(
        "/admin/seasonal-collections",
        headers=admin_headers,
        json={
            "name": "Diwali Edit",
            "description": "Festive jewellery collection.",
            "season": "Diwali",
            "starts_at": "2026-10-01T10:00:00",
            "ends_at": "2026-11-15T22:00:00",
        },
    )

    assert collection_response.status_code == 200
    collection = collection_response.json()
    assert collection["slug"] == "diwali-edit"

    collection_update = client.patch(
        f"/admin/seasonal-collections/{collection['id']}",
        headers=admin_headers,
        json={"is_active": False},
    )

    assert collection_update.status_code == 200
    assert collection_update.json()["is_active"] is False
