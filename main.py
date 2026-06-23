import os
from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from pwdlib import PasswordHash
from sqlalchemy.orm import Session

from database import Base, engine, SessionLocal
from models import User, Product, ChatSession, ChatMessage
from schemas import (
    ChatRequest,
    ChatResponse,
    ProductOut,
    UserRegister,
    TokenResponse,
    UserOut,
)

SECRET_KEY = os.getenv("SECRET_KEY", "change-this-to-a-long-random-secret")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

app = FastAPI(
    title="Jewellery Chat API",
    version="2.1.0",
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

password_hash = PasswordHash.recommended()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login")


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
        Product(
            name="Classic Gold Ring",
            category="Ring",
            metal="Gold",
            price=18999,
            image="https://example.com/images/ring_101.jpg",
            in_stock=True
        ),
        Product(
            name="Rose Gold Diamond Ring",
            category="Ring",
            metal="Rose Gold",
            price=19999,
            image="https://example.com/images/ring_102.jpg",
            in_stock=True
        ),
        Product(
            name="Minimal Gold Necklace",
            category="Necklace",
            metal="Gold",
            price=24999,
            image="https://example.com/images/necklace_201.jpg",
            in_stock=True
        ),
        Product(
            name="Pearl Drop Earrings",
            category="Earring",
            metal="Silver",
            price=7999,
            image="https://example.com/images/earring_301.jpg",
            in_stock=True
        ),
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


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return password_hash.verify(plain_password, hashed_password)


def create_access_token(data: dict, expires_delta: timedelta | None = None):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email = payload.get("sub")
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.query(User).filter(User.email == email).first()
    if user is None:
        raise credentials_exception

    return user


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


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/docs")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/register", response_model=UserOut)
async def register(user: UserRegister, db: Session = Depends(get_db)):
    existing_email = db.query(User).filter(User.email == user.email).first()
    if existing_email:
        raise HTTPException(status_code=400, detail="Email already registered")

    existing_username = db.query(User).filter(User.username == user.username).first()
    if existing_username:
        raise HTTPException(status_code=400, detail="Username already taken")

    new_user = User(
        username=user.username,
        email=user.email,
        hashed_password=hash_password(user.password)
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return new_user


@app.post("/login", response_model=TokenResponse)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.email == form_data.username).first()

    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    access_token = create_access_token(data={"sub": user.email})
    return TokenResponse(access_token=access_token)


@app.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)):
    return current_user


@app.post("/chat", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    session_id = req.session_id or f"session_{current_user.id}"

    chat_session = db.query(ChatSession).filter(
        ChatSession.session_id == session_id
    ).first()

    if not chat_session:
        chat_session = ChatSession(
            session_id=session_id,
            user_id=str(current_user.id)
        )
        db.add(chat_session)
        db.commit()

    db.add(ChatMessage(
        session_id=session_id,
        user_id=str(current_user.id),
        role="user",
        content=req.message
    ))
    db.commit()

    products = filter_products(db, req.message)
    reply = build_reply(req.message, len(products))

    db.add(ChatMessage(
        session_id=session_id,
        user_id=str(current_user.id),
        role="assistant",
        content=reply
    ))
    db.commit()

    return ChatResponse(
        reply=reply,
        products=[ProductOut.model_validate(p) for p in products],
        session_id=session_id
    )