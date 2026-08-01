import json
import re
from collections import defaultdict
from typing import Any

import httpx

from .config import Settings
from .models import Product, SearchRequest

PRICE_UNDER = re.compile(r"(?:under|below|up to|within)\s*(?:rs\.?|inr|₹)?\s*([\d,]+)", re.I)
PRICE_OVER = re.compile(r"(?:over|above|from)\s*(?:rs\.?|inr|₹)?\s*([\d,]+)", re.I)
KNOWN_METALS = ("gold", "rose gold", "white gold", "silver", "platinum", "diamond")
KNOWN_CATEGORIES = ("ring", "necklace", "earring", "bracelet", "bangle", "pendant", "chain")
STOP_WORDS = {"a", "an", "and", "for", "me", "show", "find", "jewellery", "jewelry", "the", "with"}


def _words(value: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", value.lower()) if word not in STOP_WORDS}


def parse_query(request: SearchRequest) -> dict[str, Any]:
    query = request.query.lower()
    under = PRICE_UNDER.search(query)
    over = PRICE_OVER.search(query)
    category = request.category or next((x for x in KNOWN_CATEGORIES if x in query), None)
    metal = request.metal or next((x for x in KNOWN_METALS if x in query), None)
    return {
        "category": category,
        "metal": metal,
        "min_price": request.min_price if request.min_price is not None else (_money(over.group(1)) if over else None),
        "max_price": request.max_price if request.max_price is not None else (_money(under.group(1)) if under else None),
        "in_stock_only": request.in_stock_only,
    }


def _money(value: str) -> float:
    return float(value.replace(",", ""))


def search_catalog(request: SearchRequest) -> tuple[list[Product], dict[str, Any]]:
    filters = parse_query(request)
    query_words = _words(request.query)
    scored: list[tuple[float, Product]] = []
    for product in request.products:
        if filters["in_stock_only"] and not product.in_stock:
            continue
        if filters["category"] and filters["category"].lower() not in product.category.lower():
            continue
        if filters["metal"] and filters["metal"].lower() not in product.metal.lower():
            continue
        if filters["min_price"] is not None and product.price < filters["min_price"]:
            continue
        if filters["max_price"] is not None and product.price > filters["max_price"]:
            continue
        text = " ".join([product.name, product.description, product.category, product.metal, *product.tags, *product.occasion, *product.style])
        matches = query_words & _words(text)
        score = len(matches) * 3 + (2 if filters["category"] else 0) + (2 if filters["metal"] else 0)
        if matches or any(filters[key] is not None for key in ("category", "metal", "min_price", "max_price")):
            scored.append((score, product))
    scored.sort(key=lambda item: (-item[0], item[1].price))
    return [product for _, product in scored[: request.limit]], filters


def recommend(products: list[Product], seed: Product | None, occasion: str | None, budget: float | None, limit: int) -> tuple[list[Product], dict[str, str]]:
    ranked: list[tuple[float, Product, str]] = []
    for product in products:
        if seed and str(product.id) == str(seed.id):
            continue
        if not product.in_stock or (budget is not None and product.price > budget):
            continue
        score = 0.0
        reasons: list[str] = []
        if seed and product.category.lower() == seed.category.lower():
            score += 4
            reasons.append(f"similar {product.category}")
        if seed and product.metal.lower() == seed.metal.lower():
            score += 3
            reasons.append(f"same {product.metal} finish")
        if seed:
            overlap = set(map(str.lower, product.style)) & set(map(str.lower, seed.style))
            score += len(overlap) * 2
            if overlap:
                reasons.append(f"matching {next(iter(overlap))} style")
        if occasion and occasion.lower() in {x.lower() for x in product.occasion}:
            score += 5
            reasons.append(f"suited to {occasion}")
        score += max(0, 1 - product.price / max(budget or product.price or 1, 1))
        ranked.append((score, product, ", ".join(reasons) or "popular in-stock choice"))
    ranked.sort(key=lambda item: (-item[0], item[1].price))
    chosen = ranked[:limit]
    return [x[1] for x in chosen], {str(x[1].id): x[2] for x in chosen}


def personalize(products: list[Product], events: list[Any], categories: list[str], metals: list[str], budget: float | None, limit: int) -> tuple[list[Product], dict[str, str]]:
    weights = {"view": 1, "search": 1, "wishlist": 3, "cart": 4, "purchase": 5}
    product_map = {str(product.id): product for product in products}
    category_affinity: defaultdict[str, int] = defaultdict(int)
    metal_affinity: defaultdict[str, int] = defaultdict(int)
    seen = set()
    for event in events:
        source = product_map.get(str(event.product_id))
        if source:
            seen.add(str(source.id))
            category_affinity[source.category.lower()] += weights[event.action]
            metal_affinity[source.metal.lower()] += weights[event.action]
    for value in categories:
        category_affinity[value.lower()] += 4
    for value in metals:
        metal_affinity[value.lower()] += 4
    ranked = []
    for product in products:
        if str(product.id) in seen or not product.in_stock or (budget and product.price > budget):
            continue
        score = category_affinity[product.category.lower()] + metal_affinity[product.metal.lower()]
        reason = "Based on your jewellery preferences" if score else "A popular in-stock discovery"
        ranked.append((score, product, reason))
    ranked.sort(key=lambda item: (-item[0], item[1].price))
    chosen = ranked[:limit]
    return [x[1] for x in chosen], {str(x[1].id): x[2] for x in chosen}


class OpenAIClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self.settings.openai_api_key)

    async def json_response(self, instructions: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        headers = {"Authorization": f"Bearer {self.settings.openai_api_key}", "Content-Type": "application/json"}
        body = {
            "model": self.settings.openai_model,
            "instructions": instructions,
            "input": json.dumps(payload, ensure_ascii=False),
            "text": {"format": {"type": "json_object"}},
        }
        async with httpx.AsyncClient(timeout=35) as client:
            response = await client.post("https://api.openai.com/v1/responses", headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
        for item in data.get("output", []):
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    return json.loads(content["text"])
        return None

    async def image(self, prompt: str) -> str | None:
        if not self.enabled:
            return None
        headers = {"Authorization": f"Bearer {self.settings.openai_api_key}"}
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(
                "https://api.openai.com/v1/images/generations",
                headers=headers,
                json={"model": self.settings.openai_image_model, "prompt": prompt, "size": "1024x1024"},
            )
            response.raise_for_status()
            return response.json().get("data", [{}])[0].get("b64_json")
