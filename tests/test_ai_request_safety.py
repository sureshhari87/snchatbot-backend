import pytest
from pydantic import ValidationError

from sona_ai.models import ChatRequest, ConceptRequest, Product


def test_alternate_ai_chat_requires_authentication(client):
    assert client.post("/sona/chat", json={"message": "gold rings"}).status_code == 401


@pytest.mark.parametrize("price", [float("nan"), float("inf"), -float("inf")])
def test_ai_catalogue_never_accepts_nonfinite_price(price):
    with pytest.raises(ValidationError):
        Product(id=1, name="Ring", price=price)


def test_ai_chat_catalogue_is_bounded():
    with pytest.raises(ValidationError):
        ChatRequest(
            message="rings", products=[{"id": i, "name": "Ring", "price": 1} for i in range(501)]
        )


def test_concept_budget_must_be_finite():
    with pytest.raises(ValidationError):
        ConceptRequest(description="A gold floral ring", budget=float("inf"))


def test_catalogue_search_does_not_wait_for_or_charge_ai(auth_client, monkeypatch):
    import importlib
    from unittest.mock import AsyncMock

    ai_main = importlib.import_module("sona_ai.main")
    provider = AsyncMock(side_effect=AssertionError("Search must not call provider"))
    monkeypatch.setattr(ai_main.ai, "json_response", provider)
    response = auth_client.post(
        "/sona/ai/search",
        json={
            "query": "gold rings below 20k",
            "products": [
                {"id": 1, "name": "Gold Ring", "category": "Ring", "metal": "Gold", "price": 15000}
            ],
        },
    )
    assert response.status_code == 200
    assert response.json()["products"][0]["id"] == 1
    assert response.json()["answer_source"] == "rules"
    provider.assert_not_awaited()
