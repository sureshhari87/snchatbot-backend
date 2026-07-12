from models import ChatResponseAnalytics, ResponseFeedback


def find_named(items, name):
    return next(item for item in items if item["name"] == name)


def test_chat_response_feedback_is_saved_per_response(auth_client, client, admin_headers, db):
    chat_response = auth_client.post(
        "/chat",
        json={"message": "show me gold rings under 20000", "session_id": "feedback-session"},
    )
    body = chat_response.json()

    assert chat_response.status_code == 200
    assert body["response_id"]

    feedback_response = auth_client.post(
        "/feedback",
        json={
            "response_id": body["response_id"],
            "feedback_type": "not_helpful",
            "comment": "The designs were not close enough.",
        },
    )

    assert feedback_response.status_code == 200

    feedback = (
        db.query(ResponseFeedback)
        .filter(ResponseFeedback.response_id == body["response_id"])
        .first()
    )
    analytics = (
        db.query(ChatResponseAnalytics)
        .filter(ChatResponseAnalytics.response_id == body["response_id"])
        .first()
    )

    assert feedback is not None
    assert feedback.feedback_type == "not_helpful"
    assert feedback.session_id == "feedback-session"
    assert analytics is not None
    assert analytics.low_conversion is True

    metrics = client.get("/admin/metrics", headers=admin_headers).json()
    assert metrics["counters"]["feedback_not_helpful"] == 1
    assert metrics["feedback_counts"]["not_helpful"] == 1


def test_chat_analytics_dashboard_tracks_intents_filters_repeat_users_and_products(
    auth_client, client, admin_headers
):
    auth_client.post(
        "/chat",
        json={"message": "show me gold rings under 20000", "session_id": "analytics-session"},
    )
    auth_client.post(
        "/chat",
        json={"message": "show cheaper ones", "session_id": "analytics-session"},
    )
    auth_client.post(
        "/chat",
        json={"message": "mystery platinum crown", "session_id": "analytics-unmatched"},
    )

    response = client.get("/admin/analytics/chat", headers=admin_headers)
    analytics = response.json()

    assert response.status_code == 200
    assert analytics["total_interactions"] == 3
    assert find_named(analytics["top_intents"], "product_search")["count"] >= 2
    assert find_named(analytics["top_filters"], "metal:Gold")["count"] >= 2
    assert analytics["repeat_users"][0]["interactions"] == 3
    assert analytics["most_requested_products"]
    assert analytics["unmatched_queries"]
    assert analytics["low_conversion_searches"]


def test_transcript_review_queue_and_mark_reviewed(auth_client, client, admin_headers):
    chat_response = auth_client.post(
        "/chat",
        json={"message": "unknown jewellery from mars", "session_id": "review-session"},
    )
    response_id = chat_response.json()["response_id"]
    auth_client.post(
        "/feedback",
        json={"response_id": response_id, "feedback_type": "thumbs_down"},
    )

    review_response = client.get("/admin/chat-transcripts/review", headers=admin_headers)
    review_items = review_response.json()

    assert review_response.status_code == 200
    assert any(item["response_id"] == response_id for item in review_items)

    mark_response = client.patch(
        f"/admin/chat-transcripts/{response_id}/review",
        headers=admin_headers,
        json={"notes": "Add a better fallback for unusual jewellery requests."},
    )

    assert mark_response.status_code == 200
    assert mark_response.json()["response_id"] == response_id

    next_review_response = client.get("/admin/chat-transcripts/review", headers=admin_headers)
    assert all(item["response_id"] != response_id for item in next_review_response.json())
