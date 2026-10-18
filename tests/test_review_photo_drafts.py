import pytest
from sqlalchemy.exc import IntegrityError
from test_review_photos import content, path, post
from test_review_photos import enabled as enabled

from models import ProductReview
from review_photo_models import ReviewPhoto


def test_draft_list_is_owner_scoped_and_excludes_submitted(
    client, db, auth_headers, admin_headers, enabled
):
    photo_id = post(client, db, auth_headers)
    endpoint = path(db) + "/drafts"
    assert client.get(endpoint).status_code == 401
    assert client.get(endpoint, headers=admin_headers).json() == {"items": []}
    own = client.get(endpoint, headers=auth_headers)
    assert own.json() == {"items": [photo_id]}
    assert own.headers["cache-control"] == "private, no-store"
    review_path = path(db).replace("/review-photos", "/reviews")
    assert (
        client.post(review_path, headers=auth_headers, json=content([photo_id])).status_code == 201
    )
    assert client.get(endpoint, headers=auth_headers).json() == {"items": []}


def test_stored_byte_count_cannot_differ_from_content(client, db, auth_headers, enabled):
    photo_id = post(client, db, auth_headers)
    row = db.get(ReviewPhoto, photo_id)
    row.stored_bytes += 1
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_mismatched_database_binding_never_publishes_another_owner_photo(
    client, db, auth_headers, admin_headers, enabled
):
    photo_id = post(client, db, auth_headers)
    review_path = path(db).replace("/review-photos", "/reviews")
    response = client.post(review_path, headers=admin_headers, json=content([]))
    review = db.get(ProductReview, int(response.json()["id"]))
    review.status = "approved"
    db.get(ReviewPhoto, photo_id).review_id = review.id
    db.commit()
    assert client.get(f"/review-photos/{photo_id}").status_code == 404
    assert client.get(f"/admin/review-photos/{photo_id}", headers=admin_headers).status_code == 404
    assert client.get(review_path + "?photos_only=true").json()["items"] == []
    assert client.get(review_path + "/summary").json()["photoReviewCount"] == 0
