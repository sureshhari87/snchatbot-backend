import uuid

import httpx
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .engine import OpenAIClient, personalize, recommend, search_catalog
from .security import AuthenticatedUser, ai_rate_limit, concept_rate_limit
from .models import (
    ChatRequest, ChatResponse, ConceptRequest, ConceptResponse, PersonalizedRequest,
    Product, RecommendationRequest, RecommendationResponse, SearchRequest, SearchResult,
)

settings = get_settings()
ai = OpenAIClient(settings)
app = FastAPI(title="Sona AI Jewellery API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_credentials=settings.origins != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root() -> dict:
    return {"service": "Sona AI Jewellery API", "version": "2.0.0", "docs": "/docs"}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "ai_configured": ai.enabled}


@app.get("/mobile/config")
async def mobile_config() -> dict:
    return {"features": {"ai_search": True, "recommendations": True, "personalization": True, "chat": True, "concepts": True}}


@app.post("/ai/search", response_model=SearchResult)
async def ai_search(
    request: SearchRequest,
    _user: AuthenticatedUser = Depends(ai_rate_limit),
) -> SearchResult:
    products, filters = search_catalog(request)
    interpreted = request.query
    source = "rules"
    try:
        parsed = await ai.json_response(
            "Interpret a jewellery shopping query. Return JSON with interpreted_query only. Never invent inventory or prices.",
            {"query": request.query, "filters": filters},
        )
        if parsed and parsed.get("interpreted_query"):
            interpreted, source = str(parsed["interpreted_query"]), "ai"
    except (httpx.HTTPError, ValueError, KeyError):
        pass
    return SearchResult(products=products, applied_filters=filters, interpreted_query=interpreted, answer_source=source)


@app.post("/ai/recommendations", response_model=RecommendationResponse)
async def recommendations(
    request: RecommendationRequest,
    _user: AuthenticatedUser = Depends(ai_rate_limit),
) -> RecommendationResponse:
    seed = next((item for item in request.products if str(item.id) == str(request.product_id)), None)
    if request.product_id is not None and seed is None:
        raise HTTPException(404, "The selected product is not in the supplied catalog")
    products, reasons = recommend(request.products, seed, request.occasion, request.budget, request.limit)
    return RecommendationResponse(products=products, reasons=reasons, strategy="content-and-occasion")


@app.post("/ai/recommendations/personalized", response_model=RecommendationResponse)
async def personalized(
    request: PersonalizedRequest,
    _user: AuthenticatedUser = Depends(ai_rate_limit),
) -> RecommendationResponse:
    products, reasons = personalize(request.products, request.events, request.preferred_categories, request.preferred_metals, request.budget, request.limit)
    return RecommendationResponse(products=products, reasons=reasons, strategy="privacy-preserving-session-affinity")


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    search = SearchRequest(query=request.message, products=request.products, limit=6)
    products, filters = search_catalog(search)
    reply = "I found a few jewellery options that match your request." if products else "Tell me your preferred jewellery type, metal, occasion, and budget, and I’ll narrow it down."
    source = "rules"
    try:
        generated = await ai.json_response(
            "You are Sona, a concise jewellery shopping assistant. Use only supplied products. Return JSON with reply and next_questions (array). Do not claim purity, certification, availability, or price beyond the data. For final sizing and pricing advise confirmation with the jeweller.",
            {"message": request.message, "products": [item.model_dump() for item in products], "user_context": request.user_context},
        )
        if generated and generated.get("reply"):
            reply, source = str(generated["reply"]), "ai"
            next_questions = [str(x) for x in generated.get("next_questions", [])][:3]
        else:
            next_questions = []
    except (httpx.HTTPError, ValueError, KeyError):
        next_questions = []
    next_questions = next_questions or ["Would you like to set a budget?", "Which metal do you prefer?", "Is this for a special occasion?"]
    session_id = request.session_id or str(uuid.uuid4())
    return ChatResponse(
        response_id=str(uuid.uuid4()), reply=reply, products=products, session_id=session_id,
        suggestions=next_questions, applied_filters=filters, result_count=len(products),
        suggested_next_questions=next_questions, intent="product_discovery", confidence=0.82 if products else 0.55,
        answer_source=source, tool_calls=["catalog_search"], guardrails=["catalog_grounded", "verify_final_price_and_size"],
    )


@app.post("/feedback")
async def feedback(payload: dict) -> dict:
    response_id = str(payload.get("response_id", "")).strip()
    if not response_id:
        raise HTTPException(422, "response_id is required")
    return {"accepted": True, "response_id": response_id}


@app.post("/ai/concepts", response_model=ConceptResponse)
async def concepts(
    request: ConceptRequest,
    _user: AuthenticatedUser = Depends(concept_rate_limit),
) -> ConceptResponse:
    payload = request.model_dump(exclude={"generate_image"})
    generated = None
    try:
        generated = await ai.json_response(
            "Create an original, manufacturable jewellery concept. Return JSON keys name, design_brief, materials (array), craft_notes (array). Avoid copying brands or living artists.",
            payload,
        )
    except (httpx.HTTPError, ValueError, KeyError):
        pass
    source = "ai" if generated else "template"
    generated = generated or {
        "name": "Sona Signature Concept",
        "design_brief": request.description,
        "materials": [value for value in [request.metal, *request.gemstones] if value],
        "craft_notes": ["Confirm dimensions, weight, stone setting, hallmarking, and final estimate with a jeweller."],
    }
    image = None
    if request.generate_image:
        try:
            image = await ai.image(
                f"Studio product concept render of original {request.category or 'jewellery'}, {request.description}. "
                f"Metal: {request.metal or 'unspecified'}. Gemstones: {', '.join(request.gemstones) or 'unspecified'}. "
                "Neutral background, no logo, no text, realistic artisan jewellery visualization."
            )
        except httpx.HTTPError:
            pass
    return ConceptResponse(
        concept_id=str(uuid.uuid4()), name=str(generated.get("name", "Sona Concept")),
        design_brief=str(generated.get("design_brief", request.description)),
        materials=[str(x) for x in generated.get("materials", [])],
        craft_notes=[str(x) for x in generated.get("craft_notes", [])], image_base64=image,
        image_mime_type="image/png" if image else None, answer_source=source,
        disclaimer="This is a design concept, not a manufacturing specification or final price quote.",
    )
