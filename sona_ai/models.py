from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class Product(StrictModel):
    id: str | int
    name: str
    description: str = ""
    category: str = ""
    metal: str = ""
    price: float = Field(ge=0)
    image: str = ""
    in_stock: bool = True
    stock_quantity: int = Field(default=0, ge=0)
    tags: list[str] = Field(default_factory=list)
    occasion: list[str] = Field(default_factory=list)
    style: list[str] = Field(default_factory=list)


class SearchRequest(StrictModel):
    query: str = Field(min_length=2, max_length=500)
    products: list[Product] = Field(default_factory=list)
    limit: int = Field(default=12, ge=1, le=50)
    min_price: float | None = Field(default=None, ge=0)
    max_price: float | None = Field(default=None, ge=0)
    category: str | None = None
    metal: str | None = None
    in_stock_only: bool = True

    @field_validator("products")
    @classmethod
    def catalog_size(cls, value: list[Product]) -> list[Product]:
        if len(value) > 500:
            raise ValueError("A request may contain at most 500 products")
        return value


class SearchResult(StrictModel):
    products: list[Product]
    applied_filters: dict[str, Any]
    interpreted_query: str
    answer_source: Literal["ai", "rules"]


class RecommendationRequest(StrictModel):
    products: list[Product] = Field(min_length=1, max_length=500)
    product_id: str | int | None = None
    occasion: str | None = None
    budget: float | None = Field(default=None, ge=0)
    limit: int = Field(default=8, ge=1, le=30)


class UserEvent(StrictModel):
    product_id: str | int
    action: Literal["view", "search", "wishlist", "cart", "purchase"]


class PersonalizedRequest(StrictModel):
    products: list[Product] = Field(min_length=1, max_length=500)
    events: list[UserEvent] = Field(default_factory=list, max_length=200)
    preferred_categories: list[str] = Field(default_factory=list, max_length=20)
    preferred_metals: list[str] = Field(default_factory=list, max_length=20)
    budget: float | None = Field(default=None, ge=0)
    limit: int = Field(default=10, ge=1, le=30)


class RecommendationResponse(StrictModel):
    products: list[Product]
    reasons: dict[str, str]
    strategy: str


class ChatRequest(StrictModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = None
    products: list[Product] = Field(default_factory=list)
    user_context: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(StrictModel):
    response_id: str
    reply: str
    products: list[Product]
    session_id: str
    suggestions: list[str]
    applied_filters: dict[str, Any]
    result_count: int
    suggested_next_questions: list[str]
    intent: str
    confidence: float
    answer_source: Literal["ai", "rules"]
    tool_calls: list[str]
    guardrails: list[str]
    lead_captured: bool = False
    handoff: dict[str, Any] | None = None


class ConceptRequest(StrictModel):
    description: str = Field(min_length=10, max_length=1500)
    category: str | None = None
    metal: str | None = None
    gemstones: list[str] = Field(default_factory=list, max_length=10)
    budget: float | None = Field(default=None, ge=0)
    occasion: str | None = None
    generate_image: bool = False


class ConceptResponse(StrictModel):
    concept_id: str
    name: str
    design_brief: str
    materials: list[str]
    craft_notes: list[str]
    image_base64: str | None = None
    image_mime_type: str | None = None
    answer_source: Literal["ai", "template"]
    disclaimer: str

