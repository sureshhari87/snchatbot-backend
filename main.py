from uuid import uuid4
import hashlib
import re
import secrets
import smtplib
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from typing import List

from fastapi import FastAPI, Depends, HTTPException, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from pwdlib import PasswordHash
from sqlalchemy import or_
from sqlalchemy.orm import Session

from database import Base, engine, SessionLocal
from models import (
    User,
    Product,
    ChatSession,
    ChatMessage,
    RefreshToken,
    PasswordResetToken,
    EmailVerificationToken,
    utc_now,
)
from schemas import (
    ChatRequest,
    ChatResponse,
    ProductOut,
    UserRegister,
    TokenResponse,
    UserOut,
    RefreshTokenRequest,
    ForgotPasswordRequest,
    ResetPasswordRequest,
    MessageResponse,
    VerifyEmailRequest,
    ResendVerificationRequest,
)

from slowapi import Limiter
from slowapi.util import get_remote_address
from config import (
    APP_DEBUG,
    SECRET_KEY,
    ALGORITHM,
    ACCESS_TOKEN_EXPIRE_MINUTES,
    REFRESH_TOKEN_EXPIRE_DAYS,
    PASSWORD_RESET_EXPIRE_MINUTES,
    EMAIL_VERIFICATION_EXPIRE_MINUTES,
    FRONTEND_RESET_URL,
    FRONTEND_VERIFY_URL,
    CORS_ORIGINS,
    CORS_ALLOW_CREDENTIALS,
    DATABASE_URL,
    RUN_MIGRATIONS_ON_STARTUP,
    EMAIL_HOST,
    EMAIL_PORT,
    EMAIL_USERNAME,
    EMAIL_PASSWORD,
    EMAIL_FROM,
    EMAIL_FROM_NAME,
    EMAIL_USE_TLS,
    EMAIL_USE_SSL,
    EMAIL_TIMEOUT_SECONDS,
    is_testing,
)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not is_testing():
        init_database()
    yield

app = FastAPI(
    title="Jewellery Chat API",
    version="2.1.0",
    description="Backend API for Flutter jewellery ecommerce chatbot",
    debug=APP_DEBUG,
    lifespan=lifespan,
)

limiter = Limiter(key_func=get_remote_address, enabled=not is_testing())


app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)

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


def init_database():
    if RUN_MIGRATIONS_ON_STARTUP:
        run_database_migrations()

    db = SessionLocal()
    try:
        seed_products(db)
    finally:
        db.close()


def run_database_migrations():
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    config_path = Path(__file__).with_name("alembic.ini")
    alembic_config = Config(str(config_path))
    alembic_config.set_main_option("sqlalchemy.url", DATABASE_URL.replace("%", "%%"))

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    model_tables = set(Base.metadata.tables.keys())

    if "alembic_version" not in tables and model_tables.issubset(tables):
        command.stamp(alembic_config, "head")
        return

    command.upgrade(alembic_config, "head")


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return password_hash.verify(plain_password, hashed_password)


def send_email(to_email: str, subject: str, body: str) -> bool:
    if is_testing() or not EMAIL_HOST:
        print(f"EMAIL NOT SENT: {subject} -> {to_email}\n{body}")
        return False

    message = EmailMessage()
    message["From"] = formataddr((EMAIL_FROM_NAME, EMAIL_FROM))
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body)

    try:
        if EMAIL_USE_SSL:
            with smtplib.SMTP_SSL(
                EMAIL_HOST,
                EMAIL_PORT,
                timeout=EMAIL_TIMEOUT_SECONDS,
            ) as smtp:
                if EMAIL_USERNAME and EMAIL_PASSWORD:
                    smtp.login(EMAIL_USERNAME, EMAIL_PASSWORD)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(
                EMAIL_HOST,
                EMAIL_PORT,
                timeout=EMAIL_TIMEOUT_SECONDS,
            ) as smtp:
                if EMAIL_USE_TLS:
                    smtp.starttls()
                if EMAIL_USERNAME and EMAIL_PASSWORD:
                    smtp.login(EMAIL_USERNAME, EMAIL_PASSWORD)
                smtp.send_message(message)
        return True
    except Exception as exc:
        print(f"EMAIL SEND FAILED: {subject} -> {to_email}: {exc}")
        return False


def send_verification_email(to_email: str, verify_link: str) -> None:
    body = (
        "Welcome to Jewellery Chat.\n\n"
        "Please verify your email address using this link:\n"
        f"{verify_link}\n\n"
        f"This link expires in {EMAIL_VERIFICATION_EXPIRE_MINUTES} minutes."
    )
    send_email(to_email, "Verify your Jewellery Chat email", body)


def send_password_reset_email(to_email: str, reset_link: str) -> None:
    body = (
        "We received a request to reset your Jewellery Chat password.\n\n"
        "Reset your password using this link:\n"
        f"{reset_link}\n\n"
        f"This link expires in {PASSWORD_RESET_EXPIRE_MINUTES} minutes."
    )
    send_email(to_email, "Reset your Jewellery Chat password", body)


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
        token_type = payload.get("type")
        if email is None or token_type != "access":
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.query(User).filter(User.email == email).first()
    if user is None:
        raise credentials_exception

    return user


BUYING_SUGGESTIONS = [
    "Show me gold rings under 20000",
    "Suggest a ring for engagement",
    "Which jewellery is best for daily wear?",
    "Gift ideas for mom under 15000",
    "Gift ideas for wife on anniversary",
    "Show earrings for office wear",
    "Suggest a necklace for a wedding saree",
    "What can I buy for a birthday gift?",
    "Show lightweight gold jewellery",
    "Show jewellery under 10000",
    "Suggest rose gold rings",
    "Show pearl earrings",
    "Which necklace suits a round face?",
    "Suggest jewellery for a bride",
    "Show simple studs for daily use",
    "Compare gold and silver jewellery",
    "What size ring should I buy?",
    "Show jewellery for a girlfriend",
    "Suggest a pendant for everyday wear",
    "Show premium jewellery above 20000",
    "What jewellery is good for sensitive skin?",
    "Show traditional earrings",
    "Suggest minimalist jewellery",
    "Show jewellery for party wear",
    "Which jewellery has good resale value?",
    "Do you have certified gold jewellery?",
    "Show matching necklace and earrings",
    "What is best for a first jewellery purchase?",
    "Suggest jewellery for sister under 12000",
    "Show in-stock jewellery only",
]

CATEGORY_KEYWORDS = {
    "Ring": ["ring", "rings", "engagement", "wedding band", "proposal"],
    "Necklace": ["necklace", "necklaces", "chain", "choker", "mangalsutra"],
    "Earring": ["earring", "earrings", "stud", "studs", "hoop", "hoops", "jhumka", "jhumkas"],
    "Bracelet": ["bracelet", "bracelets", "bangle", "bangles", "kada"],
    "Pendant": ["pendant", "pendants"],
    "Anklet": ["anklet", "anklets", "payal"],
}

OCCASION_KEYWORDS = {
    "engagement": ["engagement", "proposal"],
    "wedding": ["wedding", "bridal", "bride", "marriage"],
    "anniversary": ["anniversary"],
    "birthday": ["birthday"],
    "daily wear": ["daily", "everyday", "office", "workwear", "regular"],
    "party wear": ["party", "reception", "festive", "festival"],
}

GIFT_KEYWORDS = ["gift", "gifting", "mom", "mother", "wife", "girlfriend", "sister", "daughter"]


def message_has_any(message: str, keywords: list[str]) -> bool:
    return any(
        re.search(rf"\b{re.escape(keyword)}\b", message)
        for keyword in keywords
    )


def detect_category(message: str) -> str | None:
    for category, keywords in CATEGORY_KEYWORDS.items():
        if message_has_any(message, keywords):
            return category
    return None


def detect_occasion(message: str) -> str | None:
    for occasion, keywords in OCCASION_KEYWORDS.items():
        if message_has_any(message, keywords):
            return occasion
    return None


def extract_budget_limit(message: str) -> float | None:
    patterns = [
        r"(?:under|below|less than|up to|upto|max|maximum|within|budget(?: of)?)\s*(?:rs\.?|inr)?\s*([0-9][0-9,]*)(k)?",
        r"(?:rs\.?|inr)?\s*([0-9][0-9,]*)(k)?\s*(?:or less|max|budget)",
    ]

    for pattern in patterns:
        match = re.search(pattern, message)
        if not match:
            continue

        amount = float(match.group(1).replace(",", ""))
        if match.group(2):
            amount *= 1000
        return amount

    return None


def extract_minimum_price(message: str) -> float | None:
    pattern = r"(?:above|over|more than|premium above|starting above)\s*(?:rs\.?|inr)?\s*([0-9][0-9,]*)(k)?"
    match = re.search(pattern, message)
    if not match:
        return None

    amount = float(match.group(1).replace(",", ""))
    if match.group(2):
        amount *= 1000
    return amount


def filter_products(db: Session, message: str) -> List[Product]:
    msg = message.lower()
    query = db.query(Product)

    category = detect_category(msg)
    if category:
        query = query.filter(Product.category.ilike(category))

    if "rose gold" in msg:
        query = query.filter(Product.metal.ilike("%rose gold%"))
    elif "gold" in msg:
        query = query.filter(Product.metal.ilike("%gold%"))
    elif "silver" in msg:
        query = query.filter(Product.metal.ilike("%silver%"))

    if "diamond" in msg:
        query = query.filter(Product.name.ilike("%diamond%"))
    if "pearl" in msg:
        query = query.filter(or_(Product.name.ilike("%pearl%"), Product.metal.ilike("%pearl%")))

    budget_limit = extract_budget_limit(msg)
    if budget_limit is not None:
        query = query.filter(Product.price <= budget_limit)

    minimum_price = extract_minimum_price(msg)
    if minimum_price is not None:
        query = query.filter(Product.price >= minimum_price)

    return query.limit(5).all()


def build_buying_advice_reply(message: str, count: int) -> str | None:
    if message_has_any(message, ["size", "measurement", "measure"]):
        return "For ring sizing, measure an existing ring or your finger in millimeters, then choose the closest size. I can also show ring options while you confirm the size."
    if message_has_any(message, ["sensitive skin", "allergy", "hypoallergenic"]):
        return "For sensitive skin, choose nickel-free pieces and prefer gold, silver, or clearly certified materials. I can show simple daily-wear options that are easier on skin."
    if message_has_any(message, ["certified", "certificate", "hallmark", "authentic"]):
        return "For gold jewellery, look for hallmarking and a clear invoice. For diamond pieces, ask for certification details before buying."
    if message_has_any(message, ["resale", "investment", "value"]):
        return "For resale value, gold usually performs better than fashion jewellery. Choose classic designs, keep the invoice, and check purity before buying."
    if message_has_any(message, ["compare", "difference"]):
        return "Gold is classic and has stronger resale value, silver is lighter on budget, rose gold feels modern, and pearls work well for soft occasion looks."
    if message_has_any(message, ["first purchase", "first jewellery"]):
        return "For a first jewellery purchase, start with a simple ring, studs, or a minimal necklace that matches daily outfits and stays within your budget."
    if message_has_any(message, ["matching", "set", "combo"]):
        return f"I found {count} jewellery options. For a matching look, pair a simple necklace with earrings in the same metal tone."
    return None


def build_reply(message: str, count: int) -> str:
    msg = message.lower()
    category = detect_category(msg)
    occasion = detect_occasion(msg)
    budget_limit = extract_budget_limit(msg)
    minimum_price = extract_minimum_price(msg)
    has_gift_intent = message_has_any(msg, GIFT_KEYWORDS)
    advice_reply = build_buying_advice_reply(msg, count)

    if advice_reply:
        return advice_reply

    if count == 0:
        if budget_limit is not None:
            return "I could not find matching jewellery within that budget right now. Try a higher budget, a simpler design, or another category."
        if minimum_price is not None:
            return "I could not find jewellery above that price right now. Try a lower premium range or a different category."
        if category:
            return f"I could not find matching {category.lower()} options right now, but I can suggest nearby styles, gifts, and budget-friendly alternatives."
        return "I could not find matching jewellery right now, but I can help with rings, earrings, necklaces, gifts, and budget-based suggestions."
    if has_gift_intent:
        return f"I found {count} jewellery gift options you may like."
    if occasion:
        return f"I found {count} jewellery options for {occasion}."
    if budget_limit is not None:
        return f"I found {count} jewellery options within your budget."
    if minimum_price is not None:
        return f"I found {count} premium jewellery options for you."
    if category in {"Ring", "Necklace", "Earring"}:
        return f"I found {count} {category.lower()} options for you."

    return f"I found {count} jewellery options for you."


def get_buying_suggestions(message: str, limit: int = 30) -> list[str]:
    msg = message.lower()
    priority_terms = [
        "gift",
        "ring",
        "earring",
        "necklace",
        "daily",
        "wedding",
        "anniversary",
        "budget",
        "gold",
        "silver",
    ]

    ranked = []
    for term in priority_terms:
        if term in msg:
            ranked.extend(
                suggestion
                for suggestion in BUYING_SUGGESTIONS
                if term in suggestion.lower()
            )

    ranked.extend(BUYING_SUGGESTIONS)

    deduped = []
    for suggestion in ranked:
        if suggestion not in deduped:
            deduped.append(suggestion)

    return deduped[:limit]


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
        hashed_password=hash_password(user.password),
        is_verified=False,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    raw_token = generate_opaque_token()
    token_hash = hash_opaque_token(raw_token)

    db.add(EmailVerificationToken(
        user_id=new_user.id,
        token_hash=token_hash,
        is_used=False,
        expires_at=utc_now() + timedelta(minutes=EMAIL_VERIFICATION_EXPIRE_MINUTES)
    ))
    db.commit()

    verify_link = f"{FRONTEND_VERIFY_URL}?token={raw_token}"
    send_verification_email(new_user.email, verify_link)

    return new_user


@app.post("/login", response_model=TokenResponse)
@limiter.limit("5/minute")
async def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.email == form_data.username).first()

    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not user.is_verified:
        raise HTTPException(
            status_code=403,
            detail="Please verify your email before logging in"
        )

    access_token = create_access_token({"sub": user.email})
    refresh_token = create_refresh_token({"sub": user.email})

    db.add(RefreshToken(
        user_id=user.id,
        token=refresh_token,
        is_revoked=False,
        expires_at=utc_now() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    ))
    db.commit()

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
    )

@app.post("/refresh", response_model=TokenResponse)
async def refresh_token_endpoint(
    req: RefreshTokenRequest,
    db: Session = Depends(get_db)
):
    credentials_exception = HTTPException(
        status_code=401,
        detail="Invalid refresh token"
    )

    try:
        payload = jwt.decode(req.refresh_token, SECRET_KEY, algorithms=[ALGORITHM])
        email = payload.get("sub")
        token_type = payload.get("type")
        if email is None or token_type != "refresh":
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    stored_token = db.query(RefreshToken).filter(
        RefreshToken.token == req.refresh_token
    ).first()

    if not stored_token or stored_token.is_revoked:
        raise credentials_exception

    if stored_token.expires_at < utc_now():
        raise credentials_exception

    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise credentials_exception

    stored_token.is_revoked = True

    new_access_token = create_access_token({"sub": user.email})
    new_refresh_token = create_refresh_token({"sub": user.email})

    db.add(RefreshToken(
        user_id=user.id,
        token=new_refresh_token,
        is_revoked=False,
        expires_at=utc_now() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    ))
    db.commit()

    return TokenResponse(
        access_token=new_access_token,
        refresh_token=new_refresh_token,
    )

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
    suggestions = get_buying_suggestions(req.message)

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
        session_id=session_id,
        suggestions=suggestions,
    )

@app.post("/forgot-password", response_model=MessageResponse)
@limiter.limit("3/minute")
async def forgot_password(
    request: Request,
    req: ForgotPasswordRequest,
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.email == req.email).first()

    generic_response = MessageResponse(
        message="If that email is registered, password reset instructions have been generated."
    )

    if not user:
        return generic_response

    raw_token = generate_password_reset_token()
    token_hash = hash_reset_token(raw_token)

    db.add(PasswordResetToken(
        user_id=user.id,
        token_hash=token_hash,
        is_used=False,
        expires_at=utc_now() + timedelta(minutes=PASSWORD_RESET_EXPIRE_MINUTES)
    ))
    db.commit()

    reset_link = f"{FRONTEND_RESET_URL}?token={raw_token}"
    send_password_reset_email(user.email, reset_link)

    return generic_response

@app.post("/reset-password", response_model=MessageResponse)
@limiter.limit("5/minute")
async def reset_password(
    request: Request,
    req: ResetPasswordRequest,
    db: Session = Depends(get_db)
):
    token_hash = hash_reset_token(req.token)

    reset_row = db.query(PasswordResetToken).filter(
        PasswordResetToken.token_hash == token_hash
    ).first()

    if not reset_row or reset_row.is_used:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    if reset_row.expires_at < utc_now():
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    user = db.query(User).filter(User.id == reset_row.user_id).first()
    if not user:
        raise HTTPException(status_code=400, detail="Invalid reset request")

    user.hashed_password = hash_password(req.new_password)
    reset_row.is_used = True

    db.query(RefreshToken).filter(
        RefreshToken.user_id == user.id,
        RefreshToken.is_revoked == False
    ).update({"is_revoked": True})

    db.commit()

    return MessageResponse(message="Password reset successful")

@app.post("/verify-email", response_model=MessageResponse)
async def verify_email(
    req: VerifyEmailRequest,
    db: Session = Depends(get_db)
):
    token_hash = hash_opaque_token(req.token)

    row = db.query(EmailVerificationToken).filter(
        EmailVerificationToken.token_hash == token_hash
    ).first()

    if not row or row.is_used:
        raise HTTPException(status_code=400, detail="Invalid or expired verification token")

    if row.expires_at < utc_now():
        raise HTTPException(status_code=400, detail="Invalid or expired verification token")

    user = db.query(User).filter(User.id == row.user_id).first()
    if not user:
        raise HTTPException(status_code=400, detail="Invalid verification request")

    user.is_verified = True
    row.is_used = True
    db.commit()

    return MessageResponse(message="Email verified successfully")

@app.post("/resend-verification", response_model=MessageResponse)
async def resend_verification(
    req: ResendVerificationRequest,
    db: Session = Depends(get_db)
):
    generic_response = MessageResponse(
        message="If the email exists and is not yet verified, a verification email has been generated."
    )

    user = db.query(User).filter(User.email == req.email).first()
    if not user or user.is_verified:
        return generic_response

    db.query(EmailVerificationToken).filter(
        EmailVerificationToken.user_id == user.id,
        EmailVerificationToken.is_used == False
    ).update({"is_used": True})

    raw_token = generate_opaque_token()
    token_hash = hash_opaque_token(raw_token)

    db.add(EmailVerificationToken(
        user_id=user.id,
        token_hash=token_hash,
        is_used=False,
        expires_at=utc_now() + timedelta(minutes=EMAIL_VERIFICATION_EXPIRE_MINUTES)
    ))
    db.commit()

    verify_link = f"{FRONTEND_VERIFY_URL}?token={raw_token}"
    send_verification_email(user.email, verify_link)

    return generic_response

def create_token(data: dict, expires_delta: timedelta, token_type: str):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + expires_delta
    to_encode.update({"exp": expire, "type": token_type})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_access_token(data: dict, expires_delta: timedelta | None = None):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({
        "exp": expire,
        "type": "access",
    })
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(data: dict, expires_delta: timedelta | None = None):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    )
    to_encode.update({
        "exp": expire,
        "type": "refresh",
        "jti": str(uuid4()),
    })
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def generate_password_reset_token() -> str:
    return secrets.token_urlsafe(32)


def hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

def generate_opaque_token() -> str:
    return secrets.token_urlsafe(32)


def hash_opaque_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
