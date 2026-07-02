import pytest


@pytest.mark.parametrize(
    "message, expected_word",
    [
        ("show me gold rings", "ring"),
        ("show me necklace options", "necklace"),
        ("i want earrings", "ring"),
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
    assert expected_word in body["reply"].lower()

def test_chat_with_token(client, auth_headers):
    response = client.post(
        "/chat",
        json={"message": "show me gold rings"},
        headers=auth_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert "reply" in body
    assert "products" in body

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

def test_chat_unknown_query_returns_general_results(client, auth_headers):
    response = client.post(
        "/chat",
        json={"message": "show me platinum crown under 1000"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert "products" in body
    assert len(body["products"]) >= 1
    assert "jewellery" in body["reply"].lower()

def test_chat_no_match_reply(client, auth_headers):
    response = client.post(
        "/chat",
        json={"message": "show me platinum crown under 1000"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["products"]) >= 1
    assert "jewellery" in body["reply"].lower()