from typing import List, Optional
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

app = FastAPI(
    title="Jewellery Chat API",
    version="1.0.0",
    description="Backend API for Flutter jewellery ecommerce chatbot"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class Product(BaseModel):
    id: str
    name: str
    price: float
    image: str
    in_stock: bool
    metal: Optional[str] = None
    category: Optional[str] = None


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    user_id: Optional[str] = None
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    reply: str
    products: List[Product] = []
    session_id: Optional[str] = None


CATALOG = [
    {
        "id": "ring_101",
        "name": "Classic Gold Ring",
        "price": 18999,
        "image": "https://example.com/images/ring_101.jpg",
        "in_stock": True,
        "metal": "Gold",
        "category": "Ring",
    },
    {
        "id": "ring_102",
        "name": "Rose Gold Diamond Ring",
        "price": 19999,
        "image": "https://example.com/images/ring_102.jpg",
        "in_stock": True,
        "metal": "Rose Gold",
        "category": "Ring",
    },
    {
        "id": "necklace_201",
        "name": "Minimal Gold Necklace",
        "price": 24999,
        "image": "https://example.com/images/necklace_201.jpg",
        "in_stock": True,
        "metal": "Gold",
        "category": "Necklace",
    },
    {
        "id": "earring_301",
        "name": "Pearl Drop Earrings",
        "price": 7999,
        "image": "https://example.com/images/earring_301.jpg",
        "in_stock": True,
        "metal": "Silver",
        "category": "Earring",
    },
]


def filter_products_from_message(message: str) -> List[dict]:
    msg = message.lower()
    results = CATALOG[:]

    if "ring" in msg:
        results = [p for p in results if p["category"].lower() == "ring"]
    elif "necklace" in msg:
        results = [p for p in results if p["category"].lower() == "necklace"]
    elif "earring" in msg or "earrings" in msg:
        results = [p for p in results if p["category"].lower() == "earring"]

    if "gold" in msg:
        results = [p for p in results if "gold" in p["metal"].lower()]

    if "under 10000" in msg or "below 10000" in msg:
        results = [p for p in results if p["price"] < 10000]
    elif "under 20000" in msg or "below 20000" in msg:
        results = [p for p in results if p["price"] < 20000]
    elif "under 25000" in msg or "below 25000" in msg:
        results = [p for p in results if p["price"] < 25000]

    return results[:5]


def build_reply(message: str, products: List[dict]) -> str:
    msg = message.lower()

    if not products:
        return "I could not find matching jewellery right now, but I can help with rings, earrings, necklaces, gifts, and budget-based suggestions."

    if "gift" in msg:
        return f"I found {len(products)} jewellery gift options you may like."
    if "ring" in msg:
        return f"I found {len(products)} ring options for you."
    if "necklace" in msg:
        return f"I found {len(products)} necklace options for you."
    if "earring" in msg or "earrings" in msg:
        return f"I found {len(products)} earring options for you."

    return f"I found {len(products)} jewellery options for you."


@app.get("/")
async def root():
    return {"message": "Jewellery Chat API is running"}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    products = filter_products_from_message(req.message)
    reply = build_reply(req.message, products)

    return ChatResponse(
        reply=reply,
        products=[Product(**p) for p in products],
        session_id=req.session_id or "session_demo_001"
    )