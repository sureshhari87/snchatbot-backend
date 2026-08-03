import importlib
from unittest.mock import AsyncMock

ai_main = importlib.import_module("sona_ai.main")


CATALOG = [
    {
        "id": 1,
        "name": "Bridal Gold Necklace",
        "description": "Traditional bridal necklace for weddings",
        "category": "Necklace",
        "metal": "Gold",
        "price": 85000,
        "image": "https://example.com/necklace.jpg",
        "in_stock": True,
        "stock_quantity": 2,
    },
    {
        "id": 2,
        "name": "Silver Everyday Ring",
        "description": "Minimal ring",
        "category": "Ring",
        "metal": "Silver",
        "price": 4500,
        "image": "https://example.com/ring.jpg",
        "in_stock": True,
        "stock_quantity": 4,
    },
    {
        "id": "sold-out",
        "name": "Bridal Diamond Necklace",
        "description": "Luxury bridal necklace",
        "category": "Necklace",
        "metal": "Diamond",
        "price": 95000,
        "image": "",
        "in_stock": False,
        "stock_quantity": 0,
    },
]


def test_sona_ai_routes_require_authentication(client):
    requests = {
        "/sona/ai/search": {"query": "gold necklace", "products": CATALOG},
        "/sona/ai/recommendations": {"products": CATALOG},
        "/sona/ai/recommendations/personalized": {"products": CATALOG},
        "/sona/ai/concepts": {"description": "A floral gold bridal necklace"},
    }
    for path, payload in requests.items():
        assert client.post(path, json=payload).status_code == 401


def test_ai_search_matches_flutter_contract_and_parses_lakh(auth_client):
    response = auth_client.post(
        "/sona/ai/search",
        json={
            "query": "show bridal gold necklaces under \u20b91 lakh",
            "products": CATALOG,
            "limit": 12,
            "in_stock_only": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert [product["id"] for product in body["products"]] == [1]
    assert body["applied_filters"]["max_price"] == 100000
    assert body["applied_filters"]["category"] == "necklace"
    assert body["applied_filters"]["metal"] == "gold"
    assert body["interpreted_query"]
    assert body["answer_source"] in {"ai", "rules"}


def test_ai_requests_reject_invalid_or_unknown_fields(auth_client):
    assert auth_client.post("/sona/ai/recommendations", json={"products": []}).status_code == 422
    assert (
        auth_client.post(
            "/sona/ai/concepts",
            json={"description": "too short", "unexpected": "field"},
        ).status_code
        == 422
    )


def test_recommendation_contract(auth_client):
    response = auth_client.post(
        "/sona/ai/recommendations",
        json={
            "products": CATALOG,
            "occasion": "wedding",
            "budget": 90000,
            "limit": 2,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["products"]
    assert set(body["reasons"]) == {str(product["id"]) for product in body["products"]}
    assert body["strategy"] == "content-and-occasion"


def test_personalized_recommendations_prioritize_preferences(auth_client):
    response = auth_client.post(
        "/sona/ai/recommendations/personalized",
        json={
            "products": CATALOG,
            "preferred_categories": ["Necklace"],
            "preferred_metals": ["Gold"],
            "events": [],
            "limit": 2,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["products"][0]["id"] == 1
    assert body["strategy"] == "privacy-preserving-session-affinity"


def test_concept_template_contract(auth_client):
    response = auth_client.post(
        "/sona/ai/concepts",
        json={
            "description": "A lotus-inspired necklace for a wedding",
            "category": "necklace",
            "metal": "18K rose gold",
            "gemstones": ["ruby"],
            "budget": 100000,
            "generate_image": False,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["concept_id"]
    assert body["name"]
    assert body["design_brief"]
    assert body["image_base64"] is None
    assert body["answer_source"] == "template"
    assert body["disclaimer"]


def test_concept_openai_text_and_image_contract(auth_client, monkeypatch):
    generated = {
        "name": "Lotus Light",
        "design_brief": "A refined lotus pendant.",
        "materials": ["18K gold", "ruby"],
        "craft_notes": ["Create CAD for approval."],
    }
    monkeypatch.setattr(ai_main.ai, "json_response", AsyncMock(return_value=generated))
    monkeypatch.setattr(ai_main.ai, "image", AsyncMock(return_value="dGVzdC1pbWFnZQ=="))
    response = auth_client.post(
        "/sona/ai/concepts",
        json={
            "description": "A refined lotus pendant with ruby petals",
            "generate_image": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Lotus Light"
    assert body["image_base64"] == "dGVzdC1pbWFnZQ=="
    assert body["image_mime_type"] == "image/png"
    assert body["answer_source"] == "ai"
    ai_main.ai.image.assert_awaited_once()


def test_concept_rate_limit_returns_429(auth_client):
    payload = {"description": "A detailed floral gold pendant concept"}
    limit = ai_main.settings.concept_requests_per_minute
    for _ in range(limit):
        assert auth_client.post("/sona/ai/concepts", json=payload).status_code == 200
    response = auth_client.post("/sona/ai/concepts", json=payload)
    assert response.status_code == 429
    assert "retry-after" in response.headers


def test_parent_mobile_config_advertises_flutter_ai_capabilities(client):
    response = client.get("/mobile/config")
    assert response.status_code == 200
    capabilities = response.json()["capabilities"]
    assert capabilities["ai_search"] is True
    assert capabilities["ai_recommendations"] is True
    assert capabilities["personalized_recommendations"] is True
    assert capabilities["ai_concepts"] is True
