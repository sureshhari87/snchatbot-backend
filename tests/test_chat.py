import json

import pytest

from models import ChatSession, Product


@pytest.mark.parametrize(
    "message, expected_word",
    [
        ("show me gold rings", "ring"),
        ("show me necklace options", "necklace"),
        ("i want earrings", "earring"),
        ("gift under 20000", "gift"),
        ("show me jewellery", "jewellery"),
    ],
)
def test_chat_queries(client, auth_headers, message, expected_word):
    response = client.post(
        "/chat",
        json={"message": message},
        headers=auth_headers,
    )

    assert response.status_code == 200

    body = response.json()
    assert "reply" in body
    assert "products" in body
    assert "suggestions" in body
    assert expected_word in body["reply"].lower()


def test_chat_with_token(client, auth_headers):
    response = client.post("/chat", json={"message": "show me gold rings"}, headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert "reply" in body
    assert "products" in body
    assert "suggestions" in body
    assert len(body["suggestions"]) == 30
    assert "applied_filters" in body
    assert "result_count" in body
    assert "suggested_next_questions" in body


def test_chat_without_token(client):
    response = client.post("/chat", json={"message": "hello"})
    assert response.status_code == 401


def test_chat_missing_message(client, auth_headers):
    response = client.post("/chat", json={}, headers=auth_headers)
    assert response.status_code == 422


@pytest.mark.parametrize(
    "message, max_price",
    [
        ("show me jewellery under 10000", 10000),
        ("show me gift under 20000", 20000),
        ("show me gold necklace under 25000", 25000),
    ],
)
def test_chat_budget_filters(client, auth_headers, message, max_price):
    response = client.post(
        "/chat",
        json={"message": message},
        headers=auth_headers,
    )

    assert response.status_code == 200

    body = response.json()
    assert "products" in body

    for product in body["products"]:
        assert product["price"] <= max_price


def test_chat_returns_applied_filters_and_result_count(auth_client):
    response = auth_client.post(
        "/chat",
        json={"message": "show me gold rings between 10000 and 20000 in stock"},
    )
    body = response.json()

    assert response.status_code == 200
    assert body["applied_filters"] == {
        "min_price": 10000,
        "max_price": 20000,
        "metal": "Gold",
        "category": "Ring",
        "in_stock_only": True,
    }
    assert body["result_count"] == 1
    assert body["products"][0]["name"] == "Classic Gold Ring"
    assert body["suggested_next_questions"]


def test_chat_followup_uses_previous_session_filters(auth_client):
    first_response = auth_client.post(
        "/chat",
        json={
            "message": "show me rings under 25000",
            "session_id": "filter-context",
        },
    )
    second_response = auth_client.post(
        "/chat",
        json={
            "message": "only gold",
            "session_id": "filter-context",
        },
    )
    body = second_response.json()

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert body["applied_filters"]["category"] == "Ring"
    assert body["applied_filters"]["max_price"] == 25000
    assert body["applied_filters"]["metal"] == "Gold"
    assert body["result_count"] == 1
    assert all(product["metal"] == "Gold" for product in body["products"])


def test_chat_cheaper_followup_reduces_previous_budget(auth_client):
    auth_client.post(
        "/chat",
        json={
            "message": "show gold jewellery under 25000",
            "session_id": "cheaper-context",
        },
    )
    response = auth_client.post(
        "/chat",
        json={
            "message": "show cheaper ones",
            "session_id": "cheaper-context",
        },
    )
    body = response.json()

    assert response.status_code == 200
    assert body["applied_filters"]["metal"] == "Gold"
    assert body["applied_filters"]["max_price"] == 20000
    assert body["result_count"] == 1
    assert body["products"][0]["name"] == "Classic Gold Ring"


def test_chat_saves_session_preferences(auth_client, db):
    response = auth_client.post(
        "/chat",
        json={
            "message": "show minimalist gold rings under 20000 for anniversary",
            "session_id": "preference-context",
        },
    )

    assert response.status_code == 200

    chat_session = (
        db.query(ChatSession).filter(ChatSession.session_id == "preference-context").first()
    )
    preferences = json.loads(chat_session.preferences)

    assert preferences["budget"]["max_price"] == 20000
    assert preferences["style"] == "minimalist"
    assert preferences["preferred_metal"] == "Gold"
    assert "Ring" in preferences["favorite_categories"]
    assert preferences["last_occasion"] == "anniversary"


def test_chat_in_stock_only_excludes_unavailable_products(auth_client, db):
    db.add(
        Product(
            name="Out of Stock Gold Ring",
            category="Ring",
            metal="Gold",
            price=9999,
            image="https://example.com/images/ring_out.jpg",
            in_stock=False,
        )
    )
    db.commit()

    response = auth_client.post(
        "/chat",
        json={"message": "show gold rings under 12000 in stock only"},
    )
    body = response.json()

    assert response.status_code == 200
    assert body["applied_filters"]["in_stock_only"] is True
    assert body["result_count"] == 0
    assert body["products"] == []


def test_chat_applies_gift_intent_and_occasion(auth_client):
    response = auth_client.post(
        "/chat",
        json={"message": "birthday gift under 10000"},
    )
    body = response.json()

    assert response.status_code == 200
    assert body["applied_filters"]["gift_intent"] is True
    assert body["applied_filters"]["occasion"] == "birthday"
    assert body["result_count"] == 1
    assert "gift options" in body["reply"].lower()


def test_chat_unknown_query_returns_general_results(client, auth_headers):
    response = client.post(
        "/chat",
        json={"message": "show me platinum crown items"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert "products" in body
    assert len(body["products"]) >= 1
    assert "jewellery" in body["reply"].lower()


def test_chat_low_budget_query_returns_no_results(client, auth_headers):
    response = client.post(
        "/chat",
        json={"message": "show me platinum crown under 1000"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["products"] == []
    assert "within that budget" in body["reply"].lower()


def test_chat_requires_auth(client):
    response = client.post("/chat", json={"message": "show gold rings"})
    assert response.status_code == 401


def test_chat_creates_session_when_missing(auth_client):
    response = auth_client.post("/chat", json={"message": "show gold rings"})
    body = response.json()

    assert response.status_code == 200
    assert "session_id" in body
    assert body["session_id"].startswith("session_")
    assert isinstance(body["products"], list)


def test_chat_reuses_given_session_id(auth_client):
    response = auth_client.post(
        "/chat", json={"message": "show necklaces", "session_id": "mysession123"}
    )
    body = response.json()

    assert response.status_code == 200
    assert body["session_id"] == "mysession123"


def test_chat_ring_reply_branch(auth_client):
    response = auth_client.post("/chat", json={"message": "show me a gold ring"})
    body = response.json()

    assert response.status_code == 200
    assert "ring options" in body["reply"].lower()


def test_chat_gift_reply_branch(auth_client):
    response = auth_client.post("/chat", json={"message": "need a gift under 10000"})
    body = response.json()

    assert response.status_code == 200
    assert "gift options" in body["reply"].lower()


def test_chat_generic_reply_when_no_known_keywords(auth_client):
    response = auth_client.post("/chat", json={"message": "I want platinum crown items"})
    body = response.json()

    assert response.status_code == 200
    assert body["reply"] == "I found 4 jewellery options for you."
    assert len(body["products"]) == 4


def test_chat_fallback_reply_when_filters_produce_no_results(auth_client):
    response = auth_client.post("/chat", json={"message": "show me silver rings under 10000"})
    body = response.json()

    assert response.status_code == 200
    assert "could not find matching jewellery" in body["reply"].lower()
    assert body["products"] == []


def test_chat_returns_buyer_suggestions(auth_client):
    response = auth_client.post("/chat", json={"message": "gift ideas for mom under 15000"})
    body = response.json()

    assert response.status_code == 200
    assert len(body["suggestions"]) == 30
    assert "Gift ideas for mom under 15000" in body["suggestions"]
    assert "What size ring should I buy?" in body["suggestions"]


def test_chat_understands_20k_budget(auth_client):
    response = auth_client.post("/chat", json={"message": "show me rose gold rings under 20k"})
    body = response.json()

    assert response.status_code == 200
    assert len(body["products"]) == 1
    assert body["products"][0]["name"] == "Rose Gold Diamond Ring"


def test_chat_suggests_occasion_specific_reply(auth_client):
    response = auth_client.post("/chat", json={"message": "suggest a ring for engagement"})
    body = response.json()

    assert response.status_code == 200
    assert "engagement" in body["reply"].lower()


@pytest.mark.parametrize(
    "message, expected_text",
    [
        ("what size ring should I buy?", "ring sizing"),
        ("what jewellery is good for sensitive skin?", "sensitive skin"),
        ("compare gold and silver jewellery", "gold"),
        ("do you have certified gold jewellery?", "hallmark"),
    ],
)
def test_chat_answers_buying_advice_questions(auth_client, message, expected_text):
    response = auth_client.post("/chat", json={"message": message})
    body = response.json()

    assert response.status_code == 200
    assert expected_text in body["reply"].lower()


def test_chat_understands_premium_price_range(auth_client):
    response = auth_client.post("/chat", json={"message": "show premium jewellery above 20000"})
    body = response.json()

    assert response.status_code == 200
    assert body["products"]
    assert all(product["price"] >= 20000 for product in body["products"])


def test_chat_with_malformed_token(client):
    response = client.post(
        "/chat",
        json={"message": "show gold rings"},
        headers={"Authorization": "Bearer bad.token.value"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Could not validate credentials"
