from typing import List
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from database import Base, engine, SessionLocal
from models import Product, ChatSession, ChatMessage
from schemas import ChatRequest, ChatResponse, ProductOut

app = FastAPI(
    title="Jewellery Chat API",
    version="1.0.0",
    description="Backend API for Flutter jewellery ecommerce chatbot",
    debug=True
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def seed_products(db: Session):
    if db.query(Product).count() > 0:
        return

    items = [
        Product(name="Classic Gold Ring", category="Ring", metal="Gold", price=18999, image="https://example.com/images/ring_101.jpg", in_stock=True),
        Product(name="Rose Gold Diamond Ring", category="Ring", metal="Rose Gold", price=19999, image="https://example.com/images/ring_102.jpg", in_stock=True),
        Product(name="Minimal Gold Necklace", category="Necklace", metal="Gold", price=24999, image="https://example.com/images/necklace_201.jpg", in_stock=True),
        Product(name="Pearl Drop Earrings", category="Earring", metal="Silver", price=7999, image="https://example.com/images/earring_301.jpg", in_stock=True),
    ]
    db.add_all(items)
    db.commit()

@app.on_event("startup")
def startup_event():
    db = SessionLocal()
    try:
        seed_products(db)
    finally:
        db.close()

@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/docs")

@app.get("/health")
async def health():
    return {"status": "ok"}

def filter_products(db: Session, message: str) -> List[Product]:
    msg = message.lower()
    query = db.query(Product)

    if "ring" in msg:
        query = query.filter(Product.category.ilike("ring"))
    elif "necklace" in msg:
        query = query.filter(Product.category.ilike("necklace"))
    elif "earring" in msg or "earrings" in msg:
        query = query.filter(Product.category.ilike("earring"))

    if "gold" in msg:
        query = query.filter(Product.metal.ilike("%gold%"))

    if "under 10000" in msg or "below 10000" in msg:
        query = query.filter(Product.price < 10000)
    elif "under 20000" in msg or "below 20000" in msg:
        query = query.filter(Product.price < 20000)
    elif "under 25000" in msg or "below 25000" in msg:
        query = query.filter(Product.price < 25000)

    return query.limit(5).all()

def build_reply(message: str, count: int) -> str:
    msg = message.lower()
    if count == 0:
        return "I could not find matching jewellery right now, but I can help with rings, earrings, necklaces, gifts, and budget-based suggestions."
    if "gift" in msg:
        return f"I found {count} jewellery gift options you may like."
    if "ring" in msg:
        return f"I found {count} ring options for you."
    if "necklace" in msg:
        return f"I found {count} necklace options for you."
    if "earring" in msg or "earrings" in msg:
        return f"I found {count} earring options for you."
    return f"I found {count} jewellery options for you."

@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, db: Session = Depends(get_db)):
    session_id = req.session_id or "session_demo_001"

    chat_session = db.query(ChatSession).filter(ChatSession.session_id == session_id).first()
    if not chat_session:
        chat_session = ChatSession(session_id=session_id, user_id=req.user_id)
        db.add(chat_session)
        db.commit()

    db.add(ChatMessage(
        session_id=session_id,
        user_id=req.user_id,
        role="user",
        content=req.message
    ))
    db.commit()

    products = filter_products(db, req.message)
    reply = build_reply(req.message, len(products))

    db.add(ChatMessage(
        session_id=session_id,
        user_id=req.user_id,
        role="assistant",
        content=reply
    ))
    db.commit()

    return ChatResponse(
    reply=reply,
    products=[ProductOut.model_validate(p) for p in products],
    session_id=session_id
)