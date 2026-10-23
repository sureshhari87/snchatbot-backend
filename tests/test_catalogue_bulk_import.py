import base64
import csv
import io
import json
import zipfile
from uuid import uuid4

import jwt
import pytest
from PIL import Image
from sqlalchemy.exc import IntegrityError

import catalogue_bulk_api as api
import config
from catalogue_bulk_data import HEADERS, plan
from catalogue_data import RATES_KEY
from main import filter_products
from models import AppConfigEntry, CatalogueContentAudit, Product

REASON = "Synthetic catalogue import verification"
IMAGE = "https://res.cloudinary.com/example/image/upload/test.jpg"


def row(sku="TEST-GIFT"):
    return {
        "sku": sku,
        "name": "Synthetic gift",
        "category": "Gifts",
        "stock": "3",
        "useDynamicPricing": "false",
        "price": "500",
        "readyToShip": "true",
        "madeToOrder": "false",
        "handlingDays": "1",
        "manufacturingDays": "0",
        "sizeAdjustmentDays": "0",
        "image1": IMAGE,
    }


def csv_text(*rows):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=HEADERS)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def zip_image(name="TEST-GIFT_1.jpg", content=None):
    if content is None:
        image = io.BytesIO()
        Image.new("RGB", (8, 8), "white").save(image, format="JPEG")
        content = image.getvalue()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(name, content)
    return base64.b64encode(output.getvalue()).decode()


def bundle(*rows, photos=""):
    return {
        "request_key": str(uuid4()),
        "csv_text": csv_text(*(rows or [row()])),
        "photos_base64": photos,
    }


def preview(client, headers, value):
    result = client.post("/admin/catalogue/imports/preview", headers=headers, json=value)
    assert result.status_code == 200, result.json()
    return result.json()


def confirm(client, headers, value, token, **changes):
    return client.post(
        "/admin/catalogue/imports/confirm",
        headers=headers,
        json={
            **value,
            "preview_token": token,
            "uploaded_images": {},
            "reason": REASON,
            "confirmed": True,
            **changes,
        },
    )


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setenv("CATALOGUE_BULK_IMPORT_ENABLED", "1")


@pytest.mark.parametrize(
    "category,metal,purity,key",
    [
        ("Gold", "gold", "22K", "gold22k"),
        ("Gold", "gold", "18K", "gold18k"),
        ("Diamond", "gold", "18K", "gold18k"),
        ("Diamond", "gold", "22K", "gold22k"),
        ("Silver", "silver", "800", "silver"),
        ("Silver", "silver", "925", "silver"),
        ("Platinum", "platinum", "PT950", "platinum950"),
    ],
)
def test_materials_use_saved_rates_and_explicit_metadata(category, metal, purity, key):
    data = row() | {
        "category": category,
        "metalType": metal,
        "purity": purity,
        "productType": "Ring",
        "metalWeight": "2",
        "useDynamicPricing": "true",
        "makingCharge": "0",
        "stoneCharge": "0",
        "diamondCharge": "0",
        "shippingCharge": "0",
        "wastagePercent": "0",
        "gstPercent": "3",
        "goldColor": "Rose" if metal == "gold" else "",
    }
    products, photos = plan(csv_text(data), "", {key: 100})
    assert products[0]["price"] == "206.00"
    assert products[0]["data"]["active"] is False and photos == {}


@pytest.mark.parametrize(
    "changes",
    [
        {"stock": ""},
        {"stock": "-1"},
        {"stock": "1.5"},
        {"category": "Unknown"},
        {"readyToShip": "true", "madeToOrder": "true"},
        {"manufacturingDays": "3"},
        {"image1": "http://unsafe.test/a.jpg"},
        {"image1": "https://user:pass@example.test/x"},
        {"useDynamicPricing": "true"},
        {"name": ""},
        {"sku": "../bad"},
        {"sizeAdjustmentDays": "1"},
        {"dispatchNotBefore": "tomorrow"},
    ],
)
def test_invalid_rows_reject_entire_plan(changes):
    with pytest.raises(ValueError):
        plan(csv_text(row(), row("SECOND") | changes), "", {})


def test_duplicate_missing_unknown_headers_and_extra_photos():
    with pytest.raises(ValueError):
        plan(csv_text(row(), row("test-gift")), "", {})
    with pytest.raises(ValueError):
        plan("sku,sku,name\nA,A,Name\n", "", {})
    with pytest.raises(ValueError):
        plan(csv_text(row()).replace("sku,name", "unexpected,name"), "", {})
    with pytest.raises(ValueError):
        plan(csv_text(row()), zip_image(), {})


@pytest.mark.parametrize(
    "filename,content",
    [
        ("../TEST-GIFT_1.jpg", None),
        ("folder/TEST-GIFT_1.jpg", None),
        ("TEST-GIFT_1.svg", b"<svg/>"),
        ("TEST-GIFT_1.png", None),
        ("TEST-GIFT_1.jpg", b"not an image"),
    ],
)
def test_zip_rejects_traversal_unsupported_and_mismatched_images(filename, content):
    with pytest.raises(ValueError):
        plan(csv_text(row() | {"image1": "TEST-GIFT_1.jpg"}), zip_image(filename, content), {})


def test_no_zip_extraction_and_manifest_matches_sku():
    products, photos = plan(csv_text(row() | {"image1": "TEST-GIFT_1.jpg"}), zip_image(), {})
    assert products[0]["images"] == ["TEST-GIFT_1.jpg"]
    assert len(photos["TEST-GIFT_1.jpg"]["sha256"]) == 64
    with pytest.raises(ValueError):
        plan(csv_text(row() | {"image1": "WRONG_1.jpg"}), zip_image(), {})


def test_permissions_and_disabled_default(client, admin_headers, auth_headers, monkeypatch):
    value = bundle()
    assert client.post("/admin/catalogue/imports/preview", json=value).status_code == 401
    assert (
        client.post(
            "/admin/catalogue/imports/preview", headers=auth_headers, json=value
        ).status_code
        == 403
    )
    monkeypatch.delenv("CATALOGUE_BULK_IMPORT_ENABLED")
    assert (
        client.post(
            "/admin/catalogue/imports/preview", headers=admin_headers, json=value
        ).status_code
        == 503
    )
    assert (
        client.get(
            "/admin/catalogue/imports/" + value["request_key"], headers=admin_headers
        ).status_code
        == 404
    )


def test_preview_readonly_confirm_atomic_draft_and_repeat_preserves_edits(
    client, db, admin_headers
):
    before = [(p.id, p.stock_quantity, p.price) for p in db.query(Product).order_by(Product.id)]
    value = bundle()
    check = preview(client, admin_headers, value)
    assert check["products"][0]["price"] == "500.00"
    assert db.query(Product).count() == len(before)
    assert db.query(CatalogueContentAudit).count() == 0
    first = confirm(client, admin_headers, value, check["preview_token"])
    assert first.status_code == 200, first.json()
    product_id = first.json()["product_ids"][0]
    product = db.get(Product, product_id)
    assert product.in_stock is False and product.stock_quantity == 3
    assert product.source_data["active"] is False
    assert db.query(CatalogueContentAudit).one().resource_id == product_id
    product.stock_quantity = 1
    product.name = "Later admin edit"
    db.commit()
    again = confirm(client, admin_headers, value, "expired-or-unavailable-token")
    assert again.status_code == 200 and again.json()["product_ids"] == [product_id]
    assert db.get(Product, product_id).stock_quantity == 1
    assert db.get(Product, product_id).name == "Later admin edit"
    assert db.query(CatalogueContentAudit).count() == 1
    assert [
        (p.id, p.stock_quantity, p.price)
        for p in db.query(Product).filter(Product.id != product_id).order_by(Product.id)
    ] == before


def test_zip_confirm_requires_exact_uploaded_mapping(client, db, admin_headers):
    value = bundle(row() | {"image1": "TEST-GIFT_1.jpg"}, photos=zip_image())
    check = preview(client, admin_headers, value)
    assert confirm(client, admin_headers, value, check["preview_token"]).status_code == 422
    assert (
        confirm(
            client,
            admin_headers,
            value,
            check["preview_token"],
            uploaded_images={"TEST-GIFT_1.jpg": "http://bad.test/x"},
        ).status_code
        == 422
    )
    done = confirm(
        client,
        admin_headers,
        value,
        check["preview_token"],
        uploaded_images={"TEST-GIFT_1.jpg": IMAGE},
    )
    assert done.status_code == 200
    assert db.get(Product, done.json()["product_ids"][0]).image == IMAGE


def test_confirm_rechecks_files_actor_rates_and_expiry(client, db, admin_headers):
    value = bundle()
    check = preview(client, admin_headers, value)
    changed = value | {"csv_text": csv_text(row("CHANGED"))}
    assert confirm(client, admin_headers, changed, check["preview_token"]).status_code == 409
    claims = jwt.decode(
        check["preview_token"],
        config.SECRET_KEY,
        algorithms=[config.ALGORITHM],
        audience=api.AUDIENCE,
    )
    for delta in ({"actor_id": claims["actor_id"] + 1}, {"exp": 1}, {"environment": "wrong"}):
        token = jwt.encode(claims | delta, config.SECRET_KEY, algorithm=config.ALGORITHM)
        assert confirm(client, admin_headers, value, token).status_code == 409
    db.add(AppConfigEntry(key=RATES_KEY, value=json.dumps({"silver": 100}), is_public=False))
    db.commit()
    assert confirm(client, admin_headers, value, check["preview_token"]).status_code == 409


def test_existing_sku_duplicate_batch_key_and_status_are_protected(
    client, db, admin_headers, auth_headers
):
    value = bundle()
    check = preview(client, admin_headers, value)
    done = confirm(client, admin_headers, value, check["preview_token"])
    assert done.status_code == 200
    assert (
        client.get(
            "/admin/catalogue/imports/" + value["request_key"], headers=admin_headers
        ).json()["product_ids"]
        == done.json()["product_ids"]
    )
    assert (
        client.get(
            "/admin/catalogue/imports/" + value["request_key"], headers=auth_headers
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/admin/catalogue/imports/preview", headers=admin_headers, json=bundle(row("test-gift"))
        ).status_code
        == 409
    )
    changed = value | {"csv_text": csv_text(row("ANOTHER"))}
    assert (
        client.post(
            "/admin/catalogue/imports/preview", headers=admin_headers, json=changed
        ).status_code
        == 409
    )
    assert (
        confirm(
            client, admin_headers, value, check["preview_token"], reason="Changed reason"
        ).status_code
        == 409
    )


def test_failed_batch_rolls_back_all_products_marker_and_audits(
    client, db, admin_headers, monkeypatch
):
    value = bundle(row("ONE"), row("TWO"))
    check = preview(client, admin_headers, value)
    original = api.product_values
    before = db.query(Product).count()

    def fail_second(data, rates):
        if data["sku"] == "TWO":
            raise IntegrityError("synthetic", {}, Exception("synthetic"))
        return original(data, rates)

    monkeypatch.setattr(api, "product_values", fail_second)
    assert confirm(client, admin_headers, value, check["preview_token"]).status_code == 409
    assert db.query(Product).count() == before
    assert db.query(CatalogueContentAudit).count() == 0
    assert (
        client.get(
            "/admin/catalogue/imports/" + value["request_key"], headers=admin_headers
        ).status_code
        == 404
    )


def test_drafts_hidden_from_public_but_admin_can_review_and_publish(
    client, db, admin_headers, auth_headers
):
    value = bundle()
    check = preview(client, admin_headers, value)
    done = confirm(client, admin_headers, value, check["preview_token"])
    assert done.status_code == 200
    product_id = done.json()["product_ids"][0]
    assert product_id not in {p["id"] for p in client.get("/products").json()}
    assert client.get(f"/products/{product_id}").status_code == 404
    assert client.get(f"/products/{product_id}/similar").status_code == 404
    assert (
        client.post(f"/products/{product_id}/back-in-stock", headers=auth_headers).status_code
        == 404
    )
    assert (
        client.post("/wishlist", headers=auth_headers, json={"product_id": product_id}).status_code
        == 404
    )
    assert client.get(f"/admin/products/{product_id}", headers=admin_headers).status_code == 200
    assert (
        client.post("/cart", headers=auth_headers, json={"product_id": product_id}).status_code
        == 404
    )

    assert (
        client.post(
            "/catalogue/recently-viewed", headers=auth_headers, json={"product_id": product_id}
        ).status_code
        == 404
    )
    assert product_id not in {p.id for p in filter_products(db, {})[0]}
    assert str(product_id) in {
        p["id"] for p in client.get("/admin/catalogue/products", headers=admin_headers).json()
    }
    published = client.patch(
        f"/admin/catalogue/products/{product_id}", headers=admin_headers, json={"active": True}
    )
    assert published.status_code == 200
    assert client.get(f"/products/{product_id}").status_code == 200


def test_explicit_confirmation_required(client, admin_headers):
    value = bundle()
    check = preview(client, admin_headers, value)
    assert (
        confirm(client, admin_headers, value, check["preview_token"], confirmed=False).status_code
        == 422
    )
    assert (
        confirm(client, admin_headers, value, check["preview_token"], confirmed=1).status_code
        == 422
    )
    assert (
        confirm(client, admin_headers, value, check["preview_token"], reason=" ").status_code == 422
    )


def test_preview_thumbnail_is_bounded_and_original_is_preserved():
    original = io.BytesIO()
    Image.new("RGB", (640, 320), "white").save(original, format="JPEG")
    content = original.getvalue()
    products, photos = plan(
        csv_text(row() | {"image1": "TEST-GIFT_1.jpg"}), zip_image(content=content), {}
    )
    thumbnail = base64.b64decode(photos["TEST-GIFT_1.jpg"]["thumbnail_base64"])
    with Image.open(io.BytesIO(thumbnail)) as image:
        assert image.size == (160, 80)
        assert image.format == "JPEG"
    import hashlib

    assert photos["TEST-GIFT_1.jpg"]["sha256"] == hashlib.sha256(content).hexdigest()
    assert products[0]["data"]["active"] is False


def test_too_many_rows_and_oversized_request_stream_are_rejected():
    import asyncio

    from fastapi import HTTPException

    with pytest.raises(ValueError):
        plan(csv_text(*(row(f"SKU-{n}") for n in range(51))), "", {})

    class Request:
        async def stream(self):
            chunk = b"x" * 1024 * 1024
            for _ in range(31):
                yield chunk

    with pytest.raises(HTTPException) as error:
        asyncio.run(api.bounded_body(Request(), api.Bundle))
    assert error.value.status_code == 413


def test_fixed_jewellery_still_requires_actual_positive_weight():
    data = row("FIXED-GOLD") | {
        "category": "Gold",
        "metalType": "gold",
        "purity": "22K",
        "productType": "Ring",
        "goldColor": "Yellow",
    }
    with pytest.raises(ValueError, match="metalWeight"):
        plan(csv_text(data), "", {})
    products, _ = plan(csv_text(data | {"metalWeight": "1.25"}), "", {})
    assert products[0]["price"] == "500.00"
    assert products[0]["data"]["metalWeight"] == 1.25


def test_inventory_flag_alone_cannot_publish_an_imported_draft(
    client, db, admin_headers, auth_headers
):
    value = bundle()
    check = preview(client, admin_headers, value)
    done = confirm(client, admin_headers, value, check["preview_token"])
    assert done.status_code == 200
    product_id = done.json()["product_ids"][0]
    product = db.get(Product, product_id)
    product.in_stock = True
    db.commit()
    assert (
        client.post("/cart", headers=auth_headers, json={"product_id": product_id}).status_code
        == 404
    )
    assert client.get(f"/products/{product_id}").status_code == 404
