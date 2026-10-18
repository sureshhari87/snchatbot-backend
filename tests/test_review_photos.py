import base64
import io

import pytest
from PIL import Image

import review_photos as photos
from models import Product, ProductReview
from review_photo_models import ReviewPhoto


def image_data(fmt="PNG", **save):
    output = io.BytesIO()
    Image.new("RGB", (20, 12), "gold").save(output, format=fmt, **save)
    return output.getvalue()


def payload(key="image-1", data=None, mime="image/png"):
    return {
        "upload_key": key,
        "content_type": mime,
        "data_base64": base64.b64encode(data or image_data()).decode(),
    }


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(photos, "ENABLED", True)


def path(db):
    return f"/products/{db.query(Product).first().id}/review-photos"


def content(ids):
    return {"rating": 5, "body": "A beautiful piece of jewellery.", "photo_ids": ids}


def post(client, db, headers, key="image-1"):
    response = client.post(path(db), headers=headers, json=payload(key))
    assert response.status_code == 201, response.status_code
    return response.json()["id"]


def test_default_off_and_auth(client, db, auth_headers):
    assert client.post(path(db), json=payload()).status_code == 401
    assert client.post(path(db), headers=auth_headers, json=payload()).status_code == 503
    assert db.query(ReviewPhoto).count() == 0


def test_private_upload_idempotency_and_metadata_removal(
    client, db, auth_headers, admin_headers, enabled
):
    raw = (
        image_data("JPEG", exif=b"Exif\x00\x00customer-private-data") + b"<script>trailing</script>"
    )
    request = payload(data=raw, mime="image/jpeg")
    endpoint = path(db)
    first = client.post(endpoint, headers=auth_headers, json=request)
    assert first.status_code == 201
    photo_id = first.json()["id"]
    assert client.post(endpoint, headers=auth_headers, json=request).json()["id"] == photo_id
    assert db.query(ReviewPhoto).count() == 1
    assert client.get(f"/review-photos/{photo_id}").status_code == 404
    assert client.get(f"/review-photos/{photo_id}/private").status_code == 401
    assert (
        client.get(f"/review-photos/{photo_id}/private", headers=admin_headers).status_code == 404
    )
    private = client.get(f"/review-photos/{photo_id}/private", headers=auth_headers)
    decoded = base64.b64decode(private.json()["data_base64"])
    assert b"customer-private-data" not in decoded and b"<script>" not in decoded
    with Image.open(io.BytesIO(decoded)) as image:
        assert image.format == "JPEG" and not image.getexif()
    assert private.headers["cache-control"] == "private, no-store"
    assert client.get(f"/admin/review-photos/{photo_id}", headers=auth_headers).status_code == 403
    assert client.get(f"/admin/review-photos/{photo_id}", headers=admin_headers).status_code == 404
    changed = payload(data=image_data("PNG"))
    assert client.post(endpoint, headers=auth_headers, json=changed).status_code == 409


def test_only_approved_linked_photos_are_public(client, db, auth_headers, admin_headers, enabled):
    photo_id = post(client, db, auth_headers)
    endpoint = path(db).replace("/review-photos", "/reviews")
    created = client.post(endpoint, headers=auth_headers, json=content([photo_id]))
    assert created.status_code == 201
    review = created.json()
    assert review["photoIds"] == [photo_id] and review["photoUrls"] == []
    public = f"/review-photos/{photo_id}"
    assert client.get(public).status_code == 404
    assert client.get(f"/admin/review-photos/{photo_id}", headers=admin_headers).status_code == 200
    queue = client.get("/admin/reviews", headers=admin_headers).json()
    assert queue[0]["photoIds"] == [photo_id]
    moderated = client.patch(
        f"/admin/reviews/{review['id']}/moderation",
        headers=admin_headers,
        json={"status": "approved", "expected_updated_at": review["updatedAt"]},
    )
    assert moderated.status_code == 200
    assert len(moderated.json()["photoUrls"]) == 1
    response = client.get(public)
    assert response.status_code == 200 and response.headers["content-type"] == "image/jpeg"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert client.get(endpoint + "?photos_only=true").json()["items"][0]["id"] == review["id"]
    assert client.get(endpoint + "/summary").json()["photoReviewCount"] == 1
    edited = client.patch(
        f"/reviews/{review['id']}",
        headers=auth_headers,
        json={"rating": 4, "body": "Updated review keeps its existing photo."},
    )
    assert edited.status_code == 200 and edited.json()["photoIds"] == [photo_id]
    assert client.get(public).status_code == 404
    assert client.get(endpoint + "?photos_only=true").json()["items"] == []
    assert client.get(endpoint + "/summary").json()["photoReviewCount"] == 0
    assert client.delete(f"/reviews/{review['id']}", headers=auth_headers).status_code == 200
    db.expire_all()
    stored = db.get(ReviewPhoto, photo_id)
    assert stored.content is None and stored.stored_bytes == 0 and stored.removed_at is not None


def test_foreign_product_and_duplicate_references_rejected(
    client, db, auth_headers, admin_headers, enabled
):
    foreign = post(client, db, admin_headers)
    own = post(client, db, auth_headers)
    endpoint = path(db).replace("/review-photos", "/reviews")
    for ids in [[foreign], [own, own], ["https://example.test/photo.jpg"]]:
        assert client.post(endpoint, headers=auth_headers, json=content(ids)).status_code == 422
        assert db.query(ProductReview).count() == 0
    product = db.query(Product).filter(Product.id != db.query(Product).first().id).first()
    other = f"/products/{product.id}/reviews"
    assert client.post(other, headers=auth_headers, json=content([own])).status_code == 422
    assert db.query(ProductReview).count() == 0


@pytest.mark.parametrize(
    "data,mime",
    [
        (b"<svg><script/></svg>", "image/png"),
        (b"not an image", "image/jpeg"),
        (image_data("PNG"), "image/jpeg"),
    ],
)
def test_invalid_images_rejected(client, db, auth_headers, enabled, data, mime):
    assert (
        client.post(path(db), headers=auth_headers, json=payload(data=data, mime=mime)).status_code
        == 422
    )
    assert db.query(ReviewPhoto).count() == 0


def test_decode_and_storage_limits(client, db, auth_headers, enabled, monkeypatch):
    monkeypatch.setattr(photos, "MAX_PIXELS", 10)
    assert client.post(path(db), headers=auth_headers, json=payload()).status_code == 422
    monkeypatch.setattr(photos, "MAX_PIXELS", 16_000_000)
    monkeypatch.setattr(photos, "MAX_USER_BYTES", 1)
    assert client.post(path(db), headers=auth_headers, json=payload()).status_code == 413
    assert db.query(ReviewPhoto).count() == 0


def test_removed_upload_cannot_be_replayed_and_daily_limit_counts_it(
    client, db, auth_headers, enabled, monkeypatch
):
    photo_id = post(client, db, auth_headers)
    assert client.delete(f"/review-photos/{photo_id}", headers=auth_headers).status_code == 200
    assert client.get(f"/review-photos/{photo_id}/private", headers=auth_headers).status_code == 404
    assert client.post(path(db), headers=auth_headers, json=payload()).status_code == 409
    monkeypatch.setattr(photos, "MAX_DAILY_UPLOADS", 1)
    assert client.post(path(db), headers=auth_headers, json=payload("image-2")).status_code == 429


def test_animated_images_rejected(client, db, auth_headers, enabled):
    output = io.BytesIO()
    Image.new("RGB", (10, 10), "red").save(
        output,
        format="PNG",
        save_all=True,
        append_images=[Image.new("RGB", (10, 10), "blue")],
        duration=10,
    )
    assert (
        client.post(
            path(db), headers=auth_headers, json=payload(data=output.getvalue())
        ).status_code
        == 422
    )


def test_uncertain_upload_lookup_is_owner_scoped(client, db, auth_headers, admin_headers, enabled):
    photo_id = post(client, db, auth_headers)
    endpoint = path(db) + "/by-key/image-1"
    assert client.get(endpoint).status_code == 401
    assert client.get(endpoint, headers=admin_headers).status_code == 404
    response = client.get(endpoint, headers=auth_headers)
    assert response.json() == {"id": photo_id, "removed": False}
    assert response.headers["cache-control"] == "private, no-store"


def test_photo_migration_preserves_review_content(test_engine, monkeypatch):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text

    path = Path(__file__).resolve().parents[1] / "alembic/versions/0036_review_photos.py"
    spec = importlib.util.spec_from_file_location("photo_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with test_engine.begin() as connection:
        ReviewPhoto.__table__.drop(connection)
        connection.execute(
            text(
                "INSERT INTO product_reviews (id, product_id, user_id, rating, title, body, status, display_name, fit, size_feedback, created_at, updated_at) VALUES (998, 1, 1, 5, 'Legacy', 'Preserved legacy review', 'approved', 'Sona customer', 'notApplicable', 'notApplicable', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        before = connection.execute(text("SELECT * FROM product_reviews")).all()
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        assert connection.execute(text("SELECT * FROM product_reviews")).all() == before
        assert connection.execute(text("SELECT count(*) FROM review_photos")).scalar_one() == 0
        migration.downgrade()
        assert connection.execute(text("SELECT * FROM product_reviews")).all() == before
        migration.upgrade()


def test_busy_decoder_rejects_without_creating_photo(
    client, db, auth_headers, enabled, monkeypatch
):
    from unittest.mock import MagicMock

    decoder = MagicMock()
    decoder.acquire.return_value = False
    monkeypatch.setattr(photos, "_DECODER", decoder)
    assert client.post(path(db), headers=auth_headers, json=payload()).status_code == 503
    assert db.query(ReviewPhoto).count() == 0
    decoder.release.assert_not_called()


def test_invalid_photo_releases_decoder_for_next_upload(client, db, auth_headers, enabled):
    assert (
        client.post(path(db), headers=auth_headers, json=payload(data=b"invalid")).status_code
        == 422
    )
    assert (
        client.post(path(db), headers=auth_headers, json=payload("valid-next")).status_code == 201
    )
