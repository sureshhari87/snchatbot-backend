from models import Product, ProductReview, ReviewHelpfulVote


def test_helpful_votes_idempotent_private_and_owner_checked(
    client, db, auth_headers, admin_headers
):
    path = paths(db)
    row = client.post(path, headers=auth_headers, json=content()).json()
    endpoint = f"/reviews/{row['id']}/helpful"
    assert client.put(endpoint, headers=admin_headers, json={"helpful": True}).status_code == 404
    client.patch(
        f"/admin/reviews/{row['id']}/moderation",
        headers=admin_headers,
        json={"status": "approved", "expected_updated_at": row["updatedAt"]},
    )
    assert client.put(endpoint, json={"helpful": True}).status_code == 401
    assert client.put(endpoint, headers=auth_headers, json={"helpful": True}).status_code == 409
    assert client.put(endpoint, headers=admin_headers, json={"helpful": "true"}).status_code == 422
    for _ in range(2):
        assert client.put(endpoint, headers=admin_headers, json={"helpful": True}).json() == {
            "helpful": True,
            "helpfulCount": 1,
        }
    assert client.get(endpoint, headers=auth_headers).json()["helpful"] is False
    assert client.get(endpoint, headers=admin_headers).json()["helpful"] is True
    assert client.get(path + "?sort=mostHelpful").json()["items"][0]["helpfulCount"] == 1
    assert db.query(ReviewHelpfulVote).count() == 1
    for _ in range(2):
        assert (
            client.put(endpoint, headers=admin_headers, json={"helpful": False}).json()[
                "helpfulCount"
            ]
            == 0
        )
    client.put(endpoint, headers=admin_headers, json={"helpful": True})
    client.delete(f"/reviews/{row['id']}", headers=auth_headers)
    assert db.query(ReviewHelpfulVote).count() == 0


def paths(db):
    product = db.query(Product).first()
    return f"/products/{product.id}/reviews"


def test_fit_reply_and_rejection_reason_preserve_moderation_boundary(client, db, auth_headers, admin_headers):
    path = paths(db)
    row = client.post(path, headers=auth_headers,
                      json=content(fit="trueToSize", size_feedback="comfortable")).json()
    assert row["fit"] == "trueToSize" and row["sizeFeedback"] == "comfortable"
    endpoint = f"/admin/reviews/{row['id']}/moderation"
    payload = {"status": "rejected", "expected_updated_at": row["updatedAt"]}
    assert client.patch(endpoint, headers=admin_headers, json=payload).status_code == 422
    assert client.patch(endpoint, headers=admin_headers, json={**payload, "reason": "   "}).status_code == 422
    rejected = client.patch(endpoint, headers=admin_headers, json={**payload, "reason": "Needs correction"})
    assert rejected.status_code == 200
    assert "moderation_reason" not in rejected.json()
    approved = client.patch(endpoint, headers=admin_headers, json={
        "status": "approved", "expected_updated_at": rejected.json()["updatedAt"],
        "seller_response": "Thank you for your feedback.",
    })
    assert approved.status_code == 200
    assert client.get(path).json()["items"][0]["sellerResponse"] == "Thank you for your feedback."
    assert client.patch(f"/reviews/{row['id']}", headers=auth_headers,
                        json=content(seller_response="Forged seller response")).status_code == 422
    edited = client.patch(f"/reviews/{row['id']}", headers=auth_headers, json=content())
    assert edited.json()["moderationStatus"] == "pending"
    assert edited.json()["sellerResponse"] is None
    assert client.get(path).json()["items"] == []


def content(**changes):
    return {"rating": 5, "title": "Lovely piece", "body": "Comfortable and well made.", **changes}


def test_pending_private_and_moderation_required(client, db, auth_headers, admin_headers):
    path = paths(db)
    assert client.post(path, json=content()).status_code == 401
    result = client.post(path, headers=auth_headers, json=content())
    assert result.status_code == 201
    row = result.json()
    assert row["moderationStatus"] == "pending"
    assert row["verifiedPurchase"] is False
    assert "user_id" not in row and "email" not in row
    assert client.get(path).json()["items"] == []
    assert client.get(path + "/summary").json()["approvedCount"] == 0
    assert client.get(path + "/me", headers=auth_headers).json()["id"] == row["id"]
    assert client.get("/admin/reviews", headers=auth_headers).status_code == 403
    moderation = f"/admin/reviews/{row['id']}/moderation"
    payload = {"status": "approved", "expected_updated_at": row["updatedAt"]}
    assert client.patch(moderation, headers=auth_headers, json=payload).status_code == 403
    assert client.patch(moderation, headers=admin_headers, json=payload).status_code == 200
    assert len(client.get(path).json()["items"]) == 1
    summary = client.get(path + "/summary").json()
    assert summary["approvedCount"] == 1 and summary["averageRating"] == 5
    assert summary["starCounts"]["5"] == 1
    assert db.query(ProductReview).first().moderated_by is not None
    assert client.patch(moderation, headers=admin_headers, json=payload).status_code == 409


def test_ownership_duplicates_edits_and_deletion(client, db, auth_headers, admin_headers):
    path = paths(db)
    row = client.post(path, headers=auth_headers, json=content()).json()
    endpoint = f"/reviews/{row['id']}"
    assert client.post(path, headers=auth_headers, json=content()).status_code == 409
    assert client.patch(endpoint, headers=admin_headers, json=content()).status_code == 404
    assert client.delete(endpoint, headers=admin_headers).status_code == 404
    assert client.get(path + "/me", headers=admin_headers).status_code == 404
    moderation = f"/admin/reviews/{row['id']}/moderation"
    client.patch(
        moderation,
        headers=admin_headers,
        json={"status": "approved", "expected_updated_at": row["updatedAt"]},
    )
    edited = client.patch(endpoint, headers=auth_headers, json=content(rating=2))
    assert edited.status_code == 200 and edited.json()["moderationStatus"] == "pending"
    assert client.get(path + "/summary").json()["approvedCount"] == 0
    assert client.delete(endpoint, headers=auth_headers).status_code == 200
    assert db.query(ProductReview).count() == 0


def test_rejects_spoofed_fields_invalid_content_and_unbounded_queries(client, db, auth_headers):
    path = paths(db)
    for patch in (
        {"verifiedPurchase": True},
        {"user_id": 1},
        {"status": "approved"},
        {"rating": 0},
        {"rating": 6},
        {"rating": True},
        {"body": " " * 20},
        {"photoUrls": ["https://example.com/image.png"]},
    ):
        assert client.post(path, headers=auth_headers, json=content(**patch)).status_code == 422
    for suffix in ("?limit=1000", "?offset=-1", "?stars=8", "?sort=invalid"):
        assert client.get(path + suffix).status_code == 422
    assert db.query(ProductReview).count() == 0
    assert client.get("/products/999999/reviews").status_code == 404
