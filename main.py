import hashlib
import json
import logging
import re
import secrets
import smtplib
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from typing import Any, List
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from pwdlib import PasswordHash
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import or_, text
from sqlalchemy.orm import Session
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from config import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    ALGORITHM,
    ADMIN_BOOTSTRAP_EMAIL,
    ADMIN_BOOTSTRAP_ENABLED,
    ADMIN_BOOTSTRAP_PASSWORD,
    ADMIN_BOOTSTRAP_USERNAME,
    APP_DEBUG,
    APP_ENV,
    CORS_ALLOW_CREDENTIALS,
    CORS_ORIGINS,
    DATABASE_URL,
    EMAIL_FROM,
    EMAIL_FROM_NAME,
    EMAIL_HOST,
    EMAIL_PASSWORD,
    EMAIL_PORT,
    EMAIL_TIMEOUT_SECONDS,
    EMAIL_USE_SSL,
    EMAIL_USE_TLS,
    EMAIL_USERNAME,
    EMAIL_VERIFICATION_EXPIRE_MINUTES,
    FRONTEND_RESET_URL,
    FRONTEND_VERIFY_URL,
    HTTPS_REDIRECT,
    ALERT_ERROR_THRESHOLD,
    LOGIN_FAILURE_LIMIT,
    LOGIN_LOCKOUT_MINUTES,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_ENABLED,
    LLM_MAX_TOKENS,
    LLM_MODEL,
    LLM_TIMEOUT_SECONDS,
    MAX_REQUEST_BODY_BYTES,
    MONITORING_WEBHOOK_TIMEOUT_SECONDS,
    MONITORING_WEBHOOK_URL,
    OMS_API_KEY,
    OMS_BASE_URL,
    OMS_ENABLED,
    OMS_TIMEOUT_SECONDS,
    PASSWORD_MIN_LENGTH,
    PASSWORD_RESET_EXPIRE_MINUTES,
    REFRESH_TOKEN_EXPIRE_DAYS,
    RESEND_VERIFICATION_COOLDOWN_SECONDS,
    RUN_MIGRATIONS_ON_STARTUP,
    SECRET_KEY,
    SENTRY_DSN,
    TRUSTED_HOSTS,
    is_testing,
)
from database import Base, SessionLocal, engine
from models import (
    AppConfigEntry,
    AppointmentBooking,
    CallbackRequest,
    ChatMessage,
    ChatResponseAnalytics,
    ChatSession,
    ComplaintTicket,
    CustomOrderRequest,
    EmailVerificationToken,
    ExternalIntegrationEvent,
    FeaturedItem,
    KnowledgeBaseItem,
    NotificationSettings,
    LeadCapture,
    OrderSupportRequest,
    PasswordResetToken,
    Product,
    ProductCategory,
    RefreshToken,
    ResponseFeedback,
    SaveForLaterItem,
    SeasonalCollection,
    User,
    UserAddress,
    WishlistItem,
    utc_now,
)
from schemas import (
    AppConfigCreate,
    AppConfigOut,
    AppConfigUpdate,
    AppointmentCreate,
    AppointmentOut,
    CallbackRequestCreate,
    CallbackRequestOut,
    CategoryCreate,
    CategoryOut,
    CategoryUpdate,
    ChatRequest,
    ChatResponse,
    ChatMessageOut,
    ChatSessionDetailOut,
    ChatSessionOut,
    ComplaintCreate,
    ComplaintOut,
    CustomOrderCreate,
    CustomOrderOut,
    ExternalIntegrationEventOut,
    FeaturedItemCreate,
    FeaturedItemOut,
    FeaturedItemUpdate,
    FeedbackCreate,
    ForgotPasswordRequest,
    HandoffInfo,
    InventoryUpdate,
    KnowledgeBaseCreate,
    KnowledgeBaseOut,
    KnowledgeBaseUpdate,
    LeadCaptureOut,
    LeadStatusUpdate,
    MessageResponse,
    NotificationSettingsOut,
    NotificationSettingsUpdate,
    OrderActionRequest,
    OrderLookupOut,
    OrderSupportCreate,
    OrderSupportOut,
    ProductCreate,
    ProductOut,
    ProductUpdate,
    RefreshTokenRequest,
    ResendVerificationRequest,
    ResetPasswordRequest,
    SavedItemCreate,
    SavedProductOut,
    SeasonalCollectionCreate,
    SeasonalCollectionOut,
    SeasonalCollectionUpdate,
    TokenResponse,
    TranscriptReviewUpdate,
    UserAddressCreate,
    UserAddressOut,
    UserAddressUpdate,
    UserOut,
    UserRegister,
    VerifyEmailRequest,
)


def configure_json_logger() -> logging.Logger:
    logger = logging.getLogger("snchatbot")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.propagate = False
    return logger


logger = configure_json_logger()


def configure_error_monitoring() -> None:
    if not SENTRY_DSN or is_testing():
        return
    try:
        import sentry_sdk
    except ImportError:
        log_event("monitoring.sentry_unavailable", dsn_configured=True)
        return
    sentry_sdk.init(dsn=SENTRY_DSN, environment=APP_ENV)
    log_event("monitoring.sentry_configured")


METRICS: dict[str, int] = {
    "total_chats": 0,
    "failed_logins": 0,
    "no_result_searches": 0,
    "unmatched_queries": 0,
    "low_conversion_searches": 0,
    "token_refreshes": 0,
    "feedback_total": 0,
    "feedback_not_helpful": 0,
    "errors_total": 0,
    "admin_actions": 0,
    "external_alerts_sent": 0,
    "oms_requests": 0,
    "oms_failures": 0,
    "llm_requests": 0,
    "llm_failures": 0,
}
FEEDBACK_COUNTS: dict[str, int] = {
    "positive": 0,
    "negative": 0,
    "neutral": 0,
    "not_helpful": 0,
}


def reset_observability_metrics() -> None:
    for key in METRICS:
        METRICS[key] = 0
    for key in FEEDBACK_COUNTS:
        FEEDBACK_COUNTS[key] = 0


def increment_metric(name: str, amount: int = 1) -> None:
    METRICS[name] = METRICS.get(name, 0) + amount


def request_id_from_request(request: Request | None) -> str | None:
    if request is None:
        return None
    return getattr(request.state, "request_id", None)


def log_event(
    event: str,
    request: Request | None = None,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "level": logging.getLevelName(level).lower(),
        "request_id": request_id_from_request(request),
    }
    if request is not None:
        payload.update(
            {
                "method": request.method,
                "path": request.url.path,
                "client_ip": client_ip(request),
            }
        )
    payload.update({key: value for key, value in fields.items() if value is not None})
    logger.log(level, json.dumps(payload, default=str, sort_keys=True))


def metrics_snapshot() -> dict[str, Any]:
    return {
        "counters": dict(METRICS),
        "feedback_counts": dict(FEEDBACK_COUNTS),
    }


def database_backend_name() -> str:
    if DATABASE_URL.startswith("sqlite"):
        return "sqlite"
    if DATABASE_URL.startswith("postgresql"):
        return "postgresql"
    return DATABASE_URL.split(":", 1)[0] or "unknown"


def log_startup_configuration() -> None:
    log_event(
        "startup.configuration",
        app_env=APP_ENV,
        run_migrations_on_startup=RUN_MIGRATIONS_ON_STARTUP,
        database_backend=database_backend_name(),
        admin_bootstrap_enabled=ADMIN_BOOTSTRAP_ENABLED,
        admin_bootstrap_email_configured=bool(ADMIN_BOOTSTRAP_EMAIL),
        admin_bootstrap_username_configured=bool(ADMIN_BOOTSTRAP_USERNAME),
        admin_bootstrap_password_configured=bool(ADMIN_BOOTSTRAP_PASSWORD),
    )


configure_error_monitoring()


def json_http_request(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 10,
) -> tuple[int, dict[str, Any]]:
    request_headers = {"Accept": "application/json", **(headers or {})}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        url,
        data=data,
        headers=request_headers,
        method=method,
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw_body = response.read().decode("utf-8")
        if not raw_body:
            return response.status, {}
        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError:
            body = {"raw": raw_body}
        return response.status, body if isinstance(body, dict) else {"data": body}


def send_monitoring_alert(
    event: str,
    severity: str = "error",
    payload: dict[str, Any] | None = None,
) -> bool:
    if not MONITORING_WEBHOOK_URL or is_testing():
        return False

    alert_payload = {
        "event": event,
        "severity": severity,
        "environment": APP_ENV,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": payload or {},
    }
    try:
        json_http_request(
            "POST",
            MONITORING_WEBHOOK_URL,
            alert_payload,
            timeout=MONITORING_WEBHOOK_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        log_event(
            "monitoring.alert_failed",
            level=logging.ERROR,
            error_type=exc.__class__.__name__,
            event=event,
        )
        return False

    increment_metric("external_alerts_sent")
    log_event("monitoring.alert_sent", event=event, severity=severity)
    return True


def record_integration_event(
    db: Session,
    service: str,
    action: str,
    status_value: str,
    user_id: int | None = None,
    reference: str | None = None,
    request_payload: dict[str, Any] | None = None,
    response_payload: dict[str, Any] | None = None,
    status_code: int | None = None,
    error: str | None = None,
) -> ExternalIntegrationEvent:
    event = ExternalIntegrationEvent(
        user_id=user_id,
        service=service,
        action=action,
        reference=reference,
        request_payload=dump_json_object(request_payload or {}) if request_payload else None,
        response_payload=dump_json_object(response_payload or {}) if response_payload else None,
        status=status_value,
        status_code=status_code,
        error=error,
    )
    db.add(event)
    return event


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
LOGIN_FAILURES: dict[str, list[datetime]] = {}


if TRUSTED_HOSTS and TRUSTED_HOSTS != ["*"]:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=TRUSTED_HOSTS)

if HTTPS_REDIRECT and not is_testing():
    app.add_middleware(HTTPSRedirectMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RequestBodyTooLarge(Exception):
    pass


def security_headers_for_request(request: Request) -> dict[str, str]:
    headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    }
    forwarded_proto = request.headers.get("x-forwarded-proto")
    if request.url.scheme == "https" or forwarded_proto == "https":
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return headers


def add_security_headers(response: Response, request: Request) -> None:
    for header_name, header_value in security_headers_for_request(request).items():
        response.headers.setdefault(header_name, header_value)


def error_response(
    request: Request,
    status_code: int,
    detail: str,
    errors: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "detail": detail,
        "request_id": request_id_from_request(request),
    }
    if errors is not None:
        body["errors"] = errors

    response = JSONResponse(status_code=status_code, content=body)
    for header_name, header_value in (headers or {}).items():
        response.headers[header_name] = header_value
    response.headers["X-Request-ID"] = request_id_from_request(request) or ""
    add_security_headers(response, request)
    return response


def normalize_validation_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for error in errors:
        loc = ".".join(str(item) for item in error.get("loc", []))
        normalized.append(
            {
                "field": loc or "request",
                "message": error.get("msg", "Invalid value"),
                "type": error.get("type", "validation_error"),
            }
        )
    return normalized


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid4())
    request.state.request_id = request_id
    started_at = time.perf_counter()
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > MAX_REQUEST_BODY_BYTES:
        log_event(
            "security.request_too_large",
            request,
            status_code=413,
            content_length=int(content_length),
            max_request_body_bytes=MAX_REQUEST_BODY_BYTES,
        )
        return error_response(request, 413, "Request body too large")

    received_bytes = 0
    original_receive = request.receive

    async def limited_receive():
        nonlocal received_bytes
        message = await original_receive()
        if message["type"] == "http.request":
            received_bytes += len(message.get("body", b""))
            if received_bytes > MAX_REQUEST_BODY_BYTES:
                raise RequestBodyTooLarge()
        return message

    limited_request = Request(request.scope, limited_receive)

    try:
        response = await call_next(limited_request)
    except RequestBodyTooLarge:
        log_event(
            "security.request_too_large",
            request,
            status_code=413,
            max_request_body_bytes=MAX_REQUEST_BODY_BYTES,
        )
        return error_response(request, 413, "Request body too large")
    except Exception as exc:
        increment_metric("errors_total")
        log_event(
            "error.unhandled",
            request,
            level=logging.ERROR,
            error_type=exc.__class__.__name__,
            duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
        )
        send_monitoring_alert(
            "error.unhandled",
            payload={
                "request_id": request_id,
                "path": request.url.path,
                "method": request.method,
                "error_type": exc.__class__.__name__,
            },
        )
        return error_response(request, 500, "Internal server error")

    response.headers["X-Request-ID"] = request_id
    add_security_headers(response, request)
    return response


@app.exception_handler(HTTPException)
async def observability_http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code >= 500:
        increment_metric("errors_total")
    log_event(
        "error.http",
        request,
        level=logging.WARNING if exc.status_code < 500 else logging.ERROR,
        status_code=exc.status_code,
        detail=exc.detail,
    )
    return error_response(
        request,
        exc.status_code,
        str(exc.detail),
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def observability_validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
):
    log_event(
        "error.validation",
        request,
        level=logging.WARNING,
        status_code=422,
        error_count=len(exc.errors()),
    )
    return error_response(
        request,
        422,
        "Validation error",
        errors=normalize_validation_errors(exc.errors()),
    )


password_hash = PasswordHash.recommended()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "item"


def seed_products(db: Session):
    for category_name in ["Ring", "Necklace", "Earring", "Bracelet", "Pendant", "Anklet"]:
        exists = db.query(ProductCategory).filter(ProductCategory.name == category_name).first()
        if not exists:
            db.add(
                ProductCategory(
                    name=category_name,
                    slug=slugify(category_name),
                    is_active=True,
                )
            )

    if db.query(Product).count() > 0:
        db.commit()
        return

    items = [
        Product(
            name="Classic Gold Ring",
            description="Classic daily-wear gold ring with a simple polished finish.",
            sku="RING-GOLD-101",
            category="Ring",
            metal="Gold",
            price=18999,
            image="https://example.com/images/ring_101.jpg",
            in_stock=True,
            stock_quantity=8,
            is_featured=True,
        ),
        Product(
            name="Rose Gold Diamond Ring",
            description="Modern rose gold ring with a diamond-inspired setting.",
            sku="RING-ROSE-102",
            category="Ring",
            metal="Rose Gold",
            price=19999,
            image="https://example.com/images/ring_102.jpg",
            in_stock=True,
            stock_quantity=5,
            is_featured=True,
        ),
        Product(
            name="Minimal Gold Necklace",
            description="Minimal gold necklace suited for daily and occasion wear.",
            sku="NECK-GOLD-201",
            category="Necklace",
            metal="Gold",
            price=24999,
            image="https://example.com/images/necklace_201.jpg",
            in_stock=True,
            stock_quantity=4,
            is_featured=False,
        ),
        Product(
            name="Pearl Drop Earrings",
            description="Pearl drop earrings with a soft occasion-ready look.",
            sku="EAR-SILVER-301",
            category="Earring",
            metal="Silver",
            price=7999,
            image="https://example.com/images/earring_301.jpg",
            in_stock=True,
            stock_quantity=10,
            is_featured=False,
        ),
    ]
    db.add_all(items)
    db.commit()


def init_database():
    log_startup_configuration()

    if RUN_MIGRATIONS_ON_STARTUP:
        run_database_migrations()

    db = SessionLocal()
    try:
        bootstrap_admin_user(db)
        seed_products(db)
    finally:
        db.close()


def run_database_migrations():
    from alembic.config import Config
    from sqlalchemy import inspect

    from alembic import command

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


def password_strength_error(password: str) -> str | None:
    if (
        len(password) < PASSWORD_MIN_LENGTH
        or not re.search(r"[A-Za-z]", password)
        or not re.search(r"\d", password)
    ):
        return (
            f"Password must be at least {PASSWORD_MIN_LENGTH} characters "
            "and include at least one letter and one number"
        )
    return None


def validate_password_strength(password: str) -> None:
    error_message = password_strength_error(password)
    if error_message:
        raise HTTPException(status_code=422, detail=error_message)


def default_admin_username(email: str) -> str:
    return slugify(email.split("@", 1)[0]).replace("-", "_") or "admin"


def bootstrap_admin_user(db: Session) -> None:
    if not ADMIN_BOOTSTRAP_ENABLED:
        return
    if not ADMIN_BOOTSTRAP_EMAIL or not ADMIN_BOOTSTRAP_PASSWORD:
        raise RuntimeError(
            "ADMIN_BOOTSTRAP_ENABLED=1 requires ADMIN_BOOTSTRAP_EMAIL and ADMIN_BOOTSTRAP_PASSWORD"
        )

    password_error = password_strength_error(ADMIN_BOOTSTRAP_PASSWORD)
    if password_error:
        raise RuntimeError(f"Admin bootstrap password is invalid: {password_error}")

    email = ADMIN_BOOTSTRAP_EMAIL.lower().strip()
    username = (ADMIN_BOOTSTRAP_USERNAME or default_admin_username(email)).strip()
    existing_user = db.query(User).filter(User.email == email).first()
    username_owner = db.query(User).filter(User.username == username).first()

    if username_owner and (not existing_user or username_owner.id != existing_user.id):
        raise RuntimeError("ADMIN_BOOTSTRAP_USERNAME is already used by another account")

    if existing_user:
        changed = False
        if not verify_password(ADMIN_BOOTSTRAP_PASSWORD, existing_user.hashed_password):
            existing_user.hashed_password = hash_password(ADMIN_BOOTSTRAP_PASSWORD)
            changed = True
        if not existing_user.is_admin:
            existing_user.is_admin = True
            changed = True
        if not existing_user.is_verified:
            existing_user.is_verified = True
            changed = True
        if changed:
            db.commit()
            log_event("admin.bootstrap_promoted", user_id=existing_user.id, email=email)
        else:
            log_event("admin.bootstrap_already_exists", user_id=existing_user.id, email=email)
        return

    admin_user = User(
        username=username,
        email=email,
        hashed_password=hash_password(ADMIN_BOOTSTRAP_PASSWORD),
        is_verified=True,
        is_admin=True,
    )
    db.add(admin_user)
    db.commit()
    db.refresh(admin_user)
    log_event("admin.bootstrap_created", user_id=admin_user.id, email=email)


def client_ip(request: Request) -> str | None:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


def client_user_agent(request: Request) -> str | None:
    return request.headers.get("user-agent")


def login_failure_key(email: str, request: Request) -> str:
    return f"{email.lower()}:{client_ip(request) or 'unknown'}"


def login_is_locked(email: str, request: Request) -> bool:
    key = login_failure_key(email, request)
    cutoff = utc_now() - timedelta(minutes=LOGIN_LOCKOUT_MINUTES)
    failures = [failed_at for failed_at in LOGIN_FAILURES.get(key, []) if failed_at >= cutoff]
    LOGIN_FAILURES[key] = failures
    return len(failures) >= LOGIN_FAILURE_LIMIT


def record_login_failure(email: str, request: Request) -> None:
    key = login_failure_key(email, request)
    LOGIN_FAILURES.setdefault(key, []).append(utc_now())


def clear_login_failures(email: str, request: Request) -> None:
    LOGIN_FAILURES.pop(login_failure_key(email, request), None)


def mark_refresh_token_used(token_row: RefreshToken, request: Request) -> None:
    token_row.last_used_at = utc_now()
    token_row.last_used_ip = client_ip(request)
    token_row.last_used_user_agent = client_user_agent(request)


def revoke_refresh_token(
    token_row: RefreshToken, reason: str, request: Request | None = None
) -> None:
    if request:
        mark_refresh_token_used(token_row, request)
    if not token_row.is_revoked:
        token_row.is_revoked = True
        token_row.revoked_at = utc_now()
        token_row.revoked_reason = reason


def revoke_user_refresh_tokens(
    db: Session,
    user_id: int,
    reason: str,
    request: Request | None = None,
) -> int:
    tokens = (
        db.query(RefreshToken)
        .filter(
            RefreshToken.user_id == user_id,
            RefreshToken.is_revoked == False,
        )
        .all()
    )
    for token_row in tokens:
        revoke_refresh_token(token_row, reason, request)
    return len(tokens)


def latest_verification_token(db: Session, user_id: int) -> EmailVerificationToken | None:
    return (
        db.query(EmailVerificationToken)
        .filter(
            EmailVerificationToken.user_id == user_id,
        )
        .order_by(EmailVerificationToken.created_at.desc())
        .first()
    )


def build_refresh_token_for_user(email: str, family_id: str | None = None) -> tuple[str, str, str]:
    token_jti = str(uuid4())
    family = family_id or str(uuid4())
    token = create_refresh_token({"sub": email}, jti=token_jti, family_id=family)
    return token, token_jti, family


def store_refresh_token(
    db: Session,
    user_id: int,
    token: str,
    token_jti: str,
    family_id: str,
    request: Request,
    parent_token_id: int | None = None,
) -> RefreshToken:
    token_row = RefreshToken(
        user_id=user_id,
        token=token,
        token_jti=token_jti,
        family_id=family_id,
        parent_token_id=parent_token_id,
        is_revoked=False,
        expires_at=utc_now() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
        created_ip=client_ip(request),
        created_user_agent=client_user_agent(request),
    )
    db.add(token_row)
    db.flush()
    return token_row


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


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
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
            raise credentials_exception from None
    except JWTError:
        raise credentials_exception from None

    user = db.query(User).filter(User.email == email).first()
    if user is None:
        raise credentials_exception

    return user


ADMIN_PERMISSIONS = {
    "products:manage",
    "catalog:manage",
    "leads:manage",
    "metrics:read",
    "support:manage",
    "knowledge:manage",
    "config:manage",
}


def user_permissions(user: User) -> set[str]:
    if user.is_admin:
        return set(ADMIN_PERMISSIONS)
    return {"customer:access"}


def require_permission(permission: str):
    def dependency(current_user: User = Depends(get_current_user)) -> User:
        if permission not in user_permissions(current_user):
            raise HTTPException(status_code=403, detail="Admin access required")
        return current_user

    return dependency


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    return require_permission("catalog:manage")(current_user)


def log_admin_action(
    request: Request,
    admin_user: User,
    action: str,
    resource: str,
    resource_id: int | None = None,
) -> None:
    increment_metric("admin_actions")
    log_event(
        "admin.action",
        request,
        user_id=admin_user.id,
        action=action,
        resource=resource,
        resource_id=resource_id,
    )


def get_product_or_404(db: Session, product_id: int) -> Product:
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


def get_category_or_404(db: Session, category_id: int) -> ProductCategory:
    category = db.query(ProductCategory).filter(ProductCategory.id == category_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    return category


def get_featured_item_or_404(db: Session, item_id: int) -> FeaturedItem:
    item = db.query(FeaturedItem).filter(FeaturedItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Featured item not found")
    return item


def get_collection_or_404(db: Session, collection_id: int) -> SeasonalCollection:
    collection = db.query(SeasonalCollection).filter(SeasonalCollection.id == collection_id).first()
    if not collection:
        raise HTTPException(status_code=404, detail="Seasonal collection not found")
    return collection


def get_knowledge_item_or_404(db: Session, item_id: int) -> KnowledgeBaseItem:
    item = db.query(KnowledgeBaseItem).filter(KnowledgeBaseItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Knowledge-base item not found")
    return item


def get_config_entry_or_404(db: Session, entry_id: int) -> AppConfigEntry:
    entry = db.query(AppConfigEntry).filter(AppConfigEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Config entry not found")
    return entry


def get_record_or_404(db: Session, model, record_id: int, detail: str):
    record = db.query(model).filter(model.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail=detail)
    return record


def get_user_address_or_404(db: Session, user_id: int, address_id: int) -> UserAddress:
    address = (
        db.query(UserAddress)
        .filter(UserAddress.id == address_id, UserAddress.user_id == user_id)
        .first()
    )
    if not address:
        raise HTTPException(status_code=404, detail="Address not found")
    return address


def clear_default_addresses(db: Session, user_id: int) -> None:
    for address in db.query(UserAddress).filter(UserAddress.user_id == user_id).all():
        address.is_default = False
        address.updated_at = utc_now()


def notification_settings_for_user(db: Session, user_id: int) -> NotificationSettings:
    settings_row = (
        db.query(NotificationSettings)
        .filter(NotificationSettings.user_id == user_id)
        .first()
    )
    if settings_row:
        return settings_row

    settings_row = NotificationSettings(user_id=user_id)
    db.add(settings_row)
    db.commit()
    db.refresh(settings_row)
    return settings_row


def ensure_unique_slug(
    db: Session,
    model,
    slug: str,
    current_id: int | None = None,
) -> None:
    query = db.query(model).filter(model.slug == slug)
    if current_id is not None:
        query = query.filter(model.id != current_id)
    if query.first():
        raise HTTPException(status_code=400, detail="Slug already exists")


def ensure_unique_key(
    db: Session,
    model,
    key: str,
    current_id: int | None = None,
) -> None:
    query = db.query(model).filter(model.key == key)
    if current_id is not None:
        query = query.filter(model.id != current_id)
    if query.first():
        raise HTTPException(status_code=400, detail="Key already exists")


def apply_model_updates(target, updates: dict[str, Any]) -> None:
    for field, value in updates.items():
        setattr(target, field, value)


def knowledge_item_response(item: KnowledgeBaseItem) -> KnowledgeBaseOut:
    return KnowledgeBaseOut(
        id=item.id,
        kind=item.kind,
        slug=item.slug,
        title=item.title,
        content=item.content,
        tags=load_json_list(item.tags),
        is_active=item.is_active,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def clamp_limit(limit: int, maximum: int = 50) -> int:
    return max(1, min(limit, maximum))


def product_search_query(
    db: Session,
    q: str | None = None,
    category: str | None = None,
    metal: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    in_stock_only: bool = False,
):
    query = db.query(Product)

    if q:
        search = f"%{q.strip()}%"
        query = query.filter(
            or_(
                Product.name.ilike(search),
                Product.description.ilike(search),
                Product.sku.ilike(search),
                Product.category.ilike(search),
                Product.metal.ilike(search),
            )
        )
    if category:
        query = query.filter(Product.category.ilike(category))
    if metal:
        query = query.filter(Product.metal.ilike(metal))
    if min_price is not None:
        query = query.filter(Product.price >= min_price)
    if max_price is not None:
        query = query.filter(Product.price <= max_price)
    if in_stock_only:
        query = query.filter(Product.in_stock == True)

    return query


def public_config_values(db: Session) -> dict[str, str]:
    entries = (
        db.query(AppConfigEntry)
        .filter(AppConfigEntry.is_public == True)
        .order_by(AppConfigEntry.key.asc())
        .all()
    )
    return {entry.key: entry.value for entry in entries}


def saved_product_response(item, product: Product) -> SavedProductOut:
    return SavedProductOut(
        id=item.id,
        product=ProductOut.model_validate(product),
        note=item.note,
        created_at=item.created_at,
    )


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
LEAD_INTENT_KEYWORDS = {
    "availability": ["available", "availability", "in stock", "stock", "ready to ship"],
    "discount": ["discount", "offer", "deal", "coupon", "sale", "best price"],
    "custom_order": [
        "custom",
        "customize",
        "customise",
        "custom order",
        "engrave",
        "made to order",
    ],
    "gift": GIFT_KEYWORDS,
    "store_visit": ["store visit", "visit store", "appointment", "book visit"],
    "callback": ["call me", "callback", "phone call", "talk to support", "human support"],
    "order_status": ["order status", "track order", "delivery status", "where is my order"],
    "return_refund": ["return", "refund", "cancel", "cancellation"],
    "complaint": ["complaint", "problem", "issue", "damaged", "wrong item"],
}
HANDOFF_KEYWORDS = [
    "call me",
    "callback",
    "talk to support",
    "human support",
    "sales",
    "store visit",
    "appointment",
    "book visit",
    "buy now",
    "place order",
    "order status",
    "track order",
    "return",
    "refund",
    "complaint",
]
METAL_KEYWORDS = {
    "Rose Gold": ["rose gold"],
    "Gold": ["gold"],
    "Silver": ["silver"],
}
STYLE_KEYWORDS = {
    "minimalist": ["minimalist", "minimal", "simple", "sleek"],
    "traditional": ["traditional", "ethnic", "temple", "jhumka", "jhumkas"],
    "modern": ["modern", "contemporary", "rose gold"],
    "lightweight": ["lightweight", "light weight", "light"],
    "premium": ["premium", "luxury", "high end"],
    "office": ["office", "workwear", "work wear"],
    "bridal": ["bridal", "bride"],
}
RECIPIENT_KEYWORDS = {
    "mom": ["mom", "mother"],
    "wife": ["wife"],
    "girlfriend": ["girlfriend"],
    "sister": ["sister"],
    "daughter": ["daughter"],
}
PRICE_AMOUNT_PATTERN = r"(?:rs\.?|inr)?\s*([0-9][0-9,]*)(k)?"
FILTER_KEYS = {
    "min_price",
    "max_price",
    "metal",
    "category",
    "gift_intent",
    "occasion",
    "in_stock_only",
    "style",
    "recipient",
    "feature",
}


def message_has_any(message: str, keywords: list[str]) -> bool:
    return any(re.search(rf"\b{re.escape(keyword)}\b", message) for keyword in keywords)


def detect_lead_intent(message: str, filters: dict[str, Any] | None = None) -> str | None:
    filters = filters or {}
    for intent, keywords in LEAD_INTENT_KEYWORDS.items():
        if message_has_any(message, keywords):
            return intent
    if filters.get("gift_intent"):
        return "gift"
    return None


def should_offer_handoff(message: str, intent: str | None) -> bool:
    return intent is not None or message_has_any(message, HANDOFF_KEYWORDS)


def create_chat_lead(
    db: Session,
    user: User,
    session_id: str,
    message: str,
    intent: str,
) -> LeadCapture:
    lead = LeadCapture(
        user_id=user.id,
        session_id=session_id,
        source="chat",
        intent=intent,
        message=message,
        contact_name=user.username,
        contact_email=user.email,
        status="new",
    )
    db.add(lead)
    db.flush()
    return lead


def create_customer_action_lead(
    db: Session,
    user: User,
    source: str,
    intent: str,
    message: str,
    session_id: str | None = None,
) -> LeadCapture:
    lead = LeadCapture(
        user_id=user.id,
        session_id=session_id,
        source=source,
        intent=intent,
        message=message,
        contact_name=user.username,
        contact_email=user.email,
        status="new",
    )
    db.add(lead)
    db.flush()
    return lead


def build_handoff_info(intent: str, lead_id: int | None = None) -> HandoffInfo:
    return HandoffInfo(
        reason=intent,
        message=(
            "I can connect you with jewellery support for availability, pricing, "
            "custom orders, store visits, or gift help."
        ),
        channels=["request_callback", "appointment", "human_support"],
        lead_id=lead_id,
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


def detect_metal(message: str) -> str | None:
    matched_metals = [
        metal for metal, keywords in METAL_KEYWORDS.items() if message_has_any(message, keywords)
    ]
    if "Rose Gold" in matched_metals:
        return "Rose Gold"
    if len(matched_metals) == 1:
        return matched_metals[0]
    if "only gold" in message or "pure gold" in message:
        return "Gold"
    return None


def detect_style(message: str) -> str | None:
    for style, keywords in STYLE_KEYWORDS.items():
        if message_has_any(message, keywords):
            return style
    return None


def detect_recipient(message: str) -> str | None:
    for recipient, keywords in RECIPIENT_KEYWORDS.items():
        if message_has_any(message, keywords):
            return recipient
    return None


def detect_feature(message: str) -> str | None:
    if message_has_any(message, ["diamond", "diamonds"]):
        return "diamond"
    if message_has_any(message, ["pearl", "pearls"]):
        return "pearl"
    return None


def parse_price_amount(amount: str, thousands_marker: str | None) -> float:
    value = float(amount.replace(",", ""))
    if thousands_marker:
        value *= 1000
    return value


def normalize_price(value: float | int | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def extract_price_range(message: str) -> tuple[float, float] | None:
    patterns = [
        rf"(?:between|from)\s*{PRICE_AMOUNT_PATTERN}\s*(?:and|to|-)\s*{PRICE_AMOUNT_PATTERN}",
        rf"{PRICE_AMOUNT_PATTERN}\s*(?:to|-)\s*{PRICE_AMOUNT_PATTERN}",
    ]

    for pattern in patterns:
        match = re.search(pattern, message)
        if not match:
            continue

        first_amount = parse_price_amount(match.group(1), match.group(2))
        second_amount = parse_price_amount(match.group(3), match.group(4))
        return min(first_amount, second_amount), max(first_amount, second_amount)

    return None


def extract_budget_limit(message: str) -> float | None:
    patterns = [
        rf"(?:under|below|less than|up to|upto|max|maximum|within|budget(?: of)?)\s*{PRICE_AMOUNT_PATTERN}",
        rf"{PRICE_AMOUNT_PATTERN}\s*(?:or less|max|budget)",
    ]

    for pattern in patterns:
        match = re.search(pattern, message)
        if not match:
            continue

        return parse_price_amount(match.group(1), match.group(2))

    return None


def extract_minimum_price(message: str) -> float | None:
    pattern = rf"(?:above|over|more than|premium above|starting above)\s*{PRICE_AMOUNT_PATTERN}"
    match = re.search(pattern, message)
    if not match:
        return None

    return parse_price_amount(match.group(1), match.group(2))


def empty_filter_state() -> dict[str, Any]:
    return {
        "min_price": None,
        "max_price": None,
        "metal": None,
        "category": None,
        "gift_intent": False,
        "occasion": None,
        "in_stock_only": False,
        "style": None,
        "recipient": None,
        "feature": None,
    }


def load_json_object(raw_value: str | None) -> dict[str, Any]:
    if not raw_value:
        return {}
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def dump_json_object(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True)


def load_json_list(raw_value: str | None) -> list[Any]:
    if not raw_value:
        return []
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def dump_json_list(value: list[Any]) -> str:
    return json.dumps(value)


def integration_status() -> dict[str, Any]:
    return {
        "oms": {
            "enabled": OMS_ENABLED and bool(OMS_BASE_URL),
            "base_url_configured": bool(OMS_BASE_URL),
            "api_key_configured": bool(OMS_API_KEY),
        },
        "llm": {
            "enabled": LLM_ENABLED and bool(LLM_BASE_URL and LLM_API_KEY),
            "base_url_configured": bool(LLM_BASE_URL),
            "api_key_configured": bool(LLM_API_KEY),
            "model": LLM_MODEL,
        },
        "monitoring": {
            "webhook_configured": bool(MONITORING_WEBHOOK_URL),
            "sentry_configured": bool(SENTRY_DSN),
            "alert_error_threshold": ALERT_ERROR_THRESHOLD,
        },
    }


def oms_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    if OMS_API_KEY:
        headers["Authorization"] = f"Bearer {OMS_API_KEY}"
    return headers


def call_oms(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    if not OMS_ENABLED or not OMS_BASE_URL:
        raise RuntimeError("OMS integration is not configured")
    url = f"{OMS_BASE_URL.rstrip('/')}/{path.lstrip('/')}"
    increment_metric("oms_requests")
    return json_http_request(method, url, payload, oms_headers(), OMS_TIMEOUT_SECONDS)


def lookup_oms_order(order_reference: str) -> tuple[str, dict[str, Any], str | None, int | None]:
    if not OMS_ENABLED or not OMS_BASE_URL:
        return "disabled", {}, "OMS integration is not configured", None

    try:
        status_code, body = call_oms("GET", f"/orders/{order_reference}")
    except urllib.error.HTTPError as exc:
        increment_metric("oms_failures")
        return "failed", {}, f"OMS returned {exc.code}", exc.code
    except Exception as exc:
        increment_metric("oms_failures")
        return "failed", {}, exc.__class__.__name__, None

    return "synced", body, None, status_code


def submit_oms_order_action(
    order_reference: str | None,
    action: str,
    payload: dict[str, Any],
) -> tuple[str, dict[str, Any], str | None, int | None]:
    if not order_reference:
        return "capture_only", {}, "Missing order reference", None
    if not OMS_ENABLED or not OMS_BASE_URL:
        return "capture_only", {}, "OMS integration is not configured", None

    try:
        status_code, body = call_oms("POST", f"/orders/{order_reference}/{action}", payload)
    except urllib.error.HTTPError as exc:
        increment_metric("oms_failures")
        return "failed", {}, f"OMS returned {exc.code}", exc.code
    except Exception as exc:
        increment_metric("oms_failures")
        return "failed", {}, exc.__class__.__name__, None

    return "synced", body, None, status_code


def llm_is_configured() -> bool:
    return LLM_ENABLED and bool(LLM_BASE_URL and LLM_API_KEY)


def llm_endpoint_url() -> str:
    base_url = LLM_BASE_URL.rstrip("/") if LLM_BASE_URL else ""
    if base_url.endswith("/chat/completions"):
        return base_url
    return f"{base_url}/chat/completions"


def build_llm_prompt(
    message: str,
    products: list[Product],
    filters: dict[str, Any],
    fallback_reply: str,
) -> dict[str, Any]:
    product_context = [
        {
            "id": product.id,
            "name": product.name,
            "category": product.category,
            "metal": product.metal,
            "price": product.price,
            "in_stock": product.in_stock,
        }
        for product in products[:5]
    ]
    return {
        "model": LLM_MODEL,
        "temperature": 0.2,
        "max_tokens": LLM_MAX_TOKENS,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a jewellery ecommerce assistant. Answer only from the supplied "
                    "catalog context, do not invent stock, pricing, discounts, medical claims, "
                    "investment guarantees, or order actions. If the catalog is insufficient, "
                    "ask a short follow-up or offer support handoff."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "customer_message": message,
                        "applied_filters": compact_filters(filters),
                        "catalog_products": product_context,
                        "fallback_reply": fallback_reply,
                    },
                    default=str,
                ),
            },
        ],
    }


def try_llm_reply(
    db: Session,
    user: User,
    message: str,
    products: list[Product],
    filters: dict[str, Any],
    fallback_reply: str,
) -> tuple[str, str, list[str]]:
    if not llm_is_configured():
        return fallback_reply, "rules", []

    payload = build_llm_prompt(message, products, filters, fallback_reply)
    increment_metric("llm_requests")
    try:
        status_code, body = json_http_request(
            "POST",
            llm_endpoint_url(),
            payload,
            {"Authorization": f"Bearer {LLM_API_KEY}"},
            LLM_TIMEOUT_SECONDS,
        )
        choices = body.get("choices", [])
        generated = ""
        if choices:
            message_body = choices[0].get("message", {})
            generated = str(message_body.get("content") or "").strip()
        if not generated:
            raise ValueError("LLM returned an empty response")
        record_integration_event(
            db,
            service="llm",
            action="chat_completion",
            status_value="synced",
            user_id=user.id,
            request_payload={"message": message, "model": LLM_MODEL},
            response_payload={"reply": generated},
            status_code=status_code,
        )
        return generated, "llm_grounded_catalog", ["llm_completion"]
    except Exception as exc:
        increment_metric("llm_failures")
        record_integration_event(
            db,
            service="llm",
            action="chat_completion",
            status_value="failed",
            user_id=user.id,
            request_payload={"message": message, "model": LLM_MODEL},
            error=exc.__class__.__name__,
        )
        log_event(
            "llm.request_failed",
            level=logging.WARNING,
            user_id=user.id,
            error_type=exc.__class__.__name__,
        )
        return fallback_reply, "rules_fallback", []


def has_product_search_signal(filters: dict[str, Any]) -> bool:
    return any(
        filters.get(key)
        for key in [
            "min_price",
            "max_price",
            "metal",
            "category",
            "gift_intent",
            "occasion",
            "in_stock_only",
            "style",
            "recipient",
            "feature",
        ]
    )


def detect_chat_intent(message: str, filters: dict[str, Any], lead_intent: str | None) -> str:
    msg = message.lower()
    if lead_intent:
        return lead_intent
    if filters.get("gift_intent"):
        return "gift"
    if filters.get("occasion"):
        return "occasion"
    if has_product_search_signal(filters):
        return "product_search"
    if build_buying_advice_reply(msg, 0):
        return "buying_advice"
    if message_has_any(msg, ["hello", "hi", "hey", "help"]):
        return "greeting"
    return "unmatched"


def is_unmatched_query(intent: str, result_count: int, filters: dict[str, Any]) -> bool:
    return intent == "unmatched" or (result_count == 0 and not has_product_search_signal(filters))


def is_low_conversion_search(result_count: int, filters: dict[str, Any], lead_captured: bool) -> bool:
    return has_product_search_signal(filters) and not lead_captured and 0 < result_count <= 2


def chat_confidence(intent: str, result_count: int, filters: dict[str, Any], unmatched: bool) -> float:
    if unmatched:
        return 0.35
    if result_count > 0 and has_product_search_signal(filters):
        return 0.92
    if intent in {
        "gift",
        "availability",
        "discount",
        "custom_order",
        "store_visit",
        "callback",
        "order_status",
        "return_refund",
        "complaint",
    }:
        return 0.84
    if intent == "buying_advice":
        return 0.78
    if result_count == 0:
        return 0.55
    return 0.7


def answer_source_for_chat(intent: str, result_count: int, lead_captured: bool) -> str:
    if lead_captured:
        return "handoff_flow"
    if result_count > 0:
        return "catalog_search"
    if intent == "buying_advice":
        return "reply_template"
    return "catalog_fallback"


def guardrails_for_chat(message: str, intent: str, unmatched: bool) -> list[str]:
    guardrails = ["jewellery-domain", "catalog-grounded", "no-payment-or-order-creation"]
    if unmatched:
        guardrails.append("fallback-used")
    if intent in {"order_status", "return_refund", "complaint"}:
        guardrails.append("oms-not-connected-capture-only")
    if message_has_any(message.lower(), ["investment guarantee", "medical", "allergy cure"]):
        guardrails.append("no-medical-or-financial-guarantee")
    return guardrails


def feedback_type_from_payload(payload: FeedbackCreate) -> str:
    if payload.feedback_type:
        return payload.feedback_type
    if payload.helpful is True or (payload.rating is not None and payload.rating >= 4):
        return "thumbs_up"
    if payload.helpful is False:
        return "not_helpful"
    if payload.rating is not None and payload.rating <= 2:
        return "thumbs_down"
    return "neutral"


def feedback_sentiment(feedback_type: str, rating: int | None) -> str:
    if feedback_type == "thumbs_up" or (rating is not None and rating >= 4):
        return "positive"
    if feedback_type in {"thumbs_down", "not_helpful"} or (rating is not None and rating <= 2):
        return "negative"
    return "neutral"


def feedback_needs_review(feedback_type: str, sentiment: str) -> bool:
    return feedback_type == "not_helpful" or sentiment == "negative"


def record_chat_analytics(
    db: Session,
    response_id: str,
    user: User,
    session_id: str,
    user_message: str,
    assistant_reply: str,
    intent: str,
    applied_filters: dict[str, Any],
    products: list[Product],
    result_count: int,
    unmatched: bool,
    low_conversion: bool,
    lead_captured: bool,
) -> ChatResponseAnalytics:
    analytics = ChatResponseAnalytics(
        response_id=response_id,
        user_id=user.id,
        session_id=session_id,
        user_message=user_message,
        assistant_reply=assistant_reply,
        intent=intent,
        applied_filters=dump_json_object(applied_filters),
        product_ids=dump_json_list([product.id for product in products]),
        product_names=dump_json_list([product.name for product in products]),
        result_count=result_count,
        unmatched=unmatched,
        low_conversion=low_conversion,
        lead_captured=lead_captured,
    )
    db.add(analytics)
    return analytics


def top_counter_items(counter: Counter, limit: int = 10) -> list[dict[str, Any]]:
    return [{"name": str(name), "count": count} for name, count in counter.most_common(limit)]


def analytics_row_summary(row: ChatResponseAnalytics) -> dict[str, Any]:
    return {
        "response_id": row.response_id,
        "session_id": row.session_id,
        "user_id": row.user_id,
        "message": row.user_message,
        "intent": row.intent,
        "applied_filters": load_json_object(row.applied_filters),
        "result_count": row.result_count,
        "created_at": row.created_at,
    }


def chat_analytics_snapshot(db: Session, limit: int = 500) -> dict[str, Any]:
    limit = min(max(limit, 1), 1000)
    rows = (
        db.query(ChatResponseAnalytics)
        .order_by(ChatResponseAnalytics.created_at.desc())
        .limit(limit)
        .all()
    )
    feedback_rows = (
        db.query(ResponseFeedback).order_by(ResponseFeedback.created_at.desc()).limit(limit).all()
    )

    intent_counter: Counter[str] = Counter()
    filter_counter: Counter[str] = Counter()
    product_counter: Counter[tuple[int | None, str]] = Counter()
    user_sessions: dict[int, set[str]] = defaultdict(set)
    user_interactions: Counter[int] = Counter()

    for row in rows:
        intent_counter[row.intent] += 1
        user_interactions[row.user_id] += 1
        user_sessions[row.user_id].add(row.session_id)

        for key, value in load_json_object(row.applied_filters).items():
            filter_counter[f"{key}:{value}"] += 1

        product_ids = load_json_list(row.product_ids)
        product_names = load_json_list(row.product_names)
        for index, product_name in enumerate(product_names):
            product_id = product_ids[index] if index < len(product_ids) else None
            product_counter[(product_id, str(product_name))] += 1

    feedback_counter: Counter[str] = Counter(row.feedback_type for row in feedback_rows)
    repeat_users = [
        {
            "user_id": user_id,
            "interactions": user_interactions[user_id],
            "sessions": len(sessions),
        }
        for user_id, sessions in user_sessions.items()
        if user_interactions[user_id] > 1 or len(sessions) > 1
    ]
    repeat_users.sort(key=lambda item: (item["interactions"], item["sessions"]), reverse=True)

    most_requested_products = [
        {"product_id": product_id, "name": name, "count": count}
        for (product_id, name), count in product_counter.most_common(10)
    ]

    unmatched_queries = [
        analytics_row_summary(row) for row in rows if row.unmatched or row.result_count == 0
    ][:10]
    low_conversion_searches = [
        analytics_row_summary(row) for row in rows if row.low_conversion
    ][:10]

    return {
        "total_interactions": len(rows),
        "top_intents": top_counter_items(intent_counter),
        "top_filters": top_counter_items(filter_counter),
        "repeat_users": repeat_users[:10],
        "most_requested_products": most_requested_products,
        "unmatched_queries": unmatched_queries,
        "low_conversion_searches": low_conversion_searches,
        "feedback_summary": dict(feedback_counter),
    }


def review_reasons(
    row: ChatResponseAnalytics,
    feedback: ResponseFeedback | None = None,
) -> list[str]:
    reasons = []
    if row.unmatched:
        reasons.append("unmatched_query")
    if row.result_count == 0:
        reasons.append("no_results")
    if row.low_conversion:
        reasons.append("low_conversion_search")
    if feedback and feedback.feedback_type in {"thumbs_down", "not_helpful"}:
        reasons.append(feedback.feedback_type)
    return reasons


def transcript_review_items(
    db: Session,
    limit: int = 25,
    include_reviewed: bool = False,
) -> list[dict[str, Any]]:
    limit = min(max(limit, 1), 100)
    negative_feedback = (
        db.query(ResponseFeedback)
        .filter(ResponseFeedback.feedback_type.in_(["thumbs_down", "not_helpful"]))
        .order_by(ResponseFeedback.created_at.desc())
        .all()
    )
    latest_feedback_by_response = {}
    for feedback in negative_feedback:
        if feedback.response_id and feedback.response_id not in latest_feedback_by_response:
            latest_feedback_by_response[feedback.response_id] = feedback
    negative_response_ids = list(latest_feedback_by_response.keys())

    filters = [
        ChatResponseAnalytics.unmatched == True,
        ChatResponseAnalytics.low_conversion == True,
        ChatResponseAnalytics.result_count == 0,
    ]
    if negative_response_ids:
        filters.append(ChatResponseAnalytics.response_id.in_(negative_response_ids))

    query = db.query(ChatResponseAnalytics).filter(or_(*filters))
    if not include_reviewed:
        query = query.filter(ChatResponseAnalytics.reviewed_at.is_(None))

    rows = query.order_by(ChatResponseAnalytics.created_at.desc()).limit(limit).all()
    items = []
    for row in rows:
        feedback = latest_feedback_by_response.get(row.response_id)
        items.append(
            {
                "response_id": row.response_id,
                "session_id": row.session_id,
                "user_id": row.user_id,
                "user_message": row.user_message,
                "assistant_reply": row.assistant_reply,
                "intent": row.intent,
                "applied_filters": load_json_object(row.applied_filters),
                "result_count": row.result_count,
                "product_names": load_json_list(row.product_names),
                "reasons": review_reasons(row, feedback),
                "feedback": (
                    {
                        "feedback_type": feedback.feedback_type,
                        "rating": feedback.rating,
                        "comment": feedback.comment,
                    }
                    if feedback
                    else None
                ),
                "reviewed_at": row.reviewed_at,
                "review_notes": row.review_notes,
                "created_at": row.created_at,
            }
        )
    return items


def normalize_filters(filters: dict[str, Any] | None) -> dict[str, Any]:
    state = empty_filter_state()
    if not filters:
        return state

    for key in FILTER_KEYS:
        if key in filters:
            state[key] = filters[key]

    state["min_price"] = normalize_price(state.get("min_price"))
    state["max_price"] = normalize_price(state.get("max_price"))
    state["gift_intent"] = bool(state.get("gift_intent"))
    state["in_stock_only"] = bool(state.get("in_stock_only"))
    return state


def compact_filters(filters: dict[str, Any]) -> dict[str, Any]:
    compacted = {}
    for key in [
        "min_price",
        "max_price",
        "metal",
        "category",
        "gift_intent",
        "occasion",
        "in_stock_only",
        "style",
        "recipient",
        "feature",
    ]:
        value = filters.get(key)
        if value is None or value is False or value == "":
            continue
        if isinstance(value, float) and value.is_integer():
            compacted[key] = int(value)
        else:
            compacted[key] = value
    return compacted


def should_reset_filters(message: str) -> bool:
    return any(
        phrase in message
        for phrase in [
            "start over",
            "reset filters",
            "clear filters",
            "all jewellery",
            "show me jewellery",
            "show jewellery",
        ]
    )


def parse_filter_updates(message: str) -> tuple[dict[str, Any], set[str], bool, str | None]:
    msg = message.lower()
    updates: dict[str, Any] = {}
    clear_keys: set[str] = set()
    relative_price: str | None = None

    category = detect_category(msg)
    if category:
        updates["category"] = category

    metal = detect_metal(msg)
    if metal:
        updates["metal"] = metal
    elif message_has_any(msg, ["any metal", "all metals"]):
        clear_keys.add("metal")

    price_range = extract_price_range(msg)
    if price_range:
        updates["min_price"], updates["max_price"] = price_range
    else:
        budget_limit = extract_budget_limit(msg)
        if budget_limit is not None:
            updates["max_price"] = budget_limit

        minimum_price = extract_minimum_price(msg)
        if minimum_price is not None:
            updates["min_price"] = minimum_price

    if message_has_any(msg, ["any price", "no budget", "remove budget"]):
        clear_keys.update({"min_price", "max_price"})

    if message_has_any(msg, ["cheaper", "lower price", "less expensive"]):
        relative_price = "cheaper"
    elif message_has_any(msg, ["more expensive", "higher price", "premium", "luxury"]):
        relative_price = "premium"

    if message_has_any(msg, ["in stock", "available", "ready to ship"]):
        updates["in_stock_only"] = True
    elif message_has_any(msg, ["include out of stock", "show out of stock"]):
        updates["in_stock_only"] = False

    occasion = detect_occasion(msg)
    if occasion:
        updates["occasion"] = occasion

    if message_has_any(msg, GIFT_KEYWORDS):
        updates["gift_intent"] = True

    style = detect_style(msg)
    if style:
        updates["style"] = style

    recipient = detect_recipient(msg)
    if recipient:
        updates["recipient"] = recipient

    feature = detect_feature(msg)
    if feature:
        updates["feature"] = feature

    return updates, clear_keys, should_reset_filters(msg), relative_price


def cheaper_price_limit(current_limit: float | None) -> float:
    if current_limit is None:
        return 20000
    discount = max(1000, current_limit * 0.2)
    return max(1000, round((current_limit - discount) / 1000) * 1000)


def merge_filter_state(previous_filters: dict[str, Any], message: str) -> dict[str, Any]:
    updates, clear_keys, reset_filters, relative_price = parse_filter_updates(message)
    filters = empty_filter_state() if reset_filters else normalize_filters(previous_filters)

    for key in clear_keys:
        filters[key] = False if key in {"gift_intent", "in_stock_only"} else None

    filters.update(updates)

    if relative_price == "cheaper":
        filters["max_price"] = cheaper_price_limit(filters.get("max_price"))
        filters["min_price"] = None
    elif relative_price == "premium" and "min_price" not in updates:
        filters["min_price"] = filters.get("max_price") or filters.get("min_price") or 20000
        filters["max_price"] = None

    return normalize_filters(filters)


def category_options_for_filters(filters: dict[str, Any]) -> list[str] | None:
    if filters.get("category"):
        return [filters["category"]]

    occasion = filters.get("occasion")
    if occasion == "engagement":
        return ["Ring"]
    if occasion == "wedding":
        return ["Ring", "Necklace", "Earring"]
    if occasion == "party wear":
        return ["Necklace", "Earring", "Ring"]
    if filters.get("gift_intent"):
        return ["Ring", "Necklace", "Earring", "Bracelet", "Pendant"]
    return None


def filter_products(
    db: Session,
    filters: dict[str, Any],
    limit: int = 5,
) -> tuple[List[Product], int]:
    query = db.query(Product)

    categories = category_options_for_filters(filters)
    if categories:
        query = query.filter(or_(*[Product.category.ilike(category) for category in categories]))

    if filters.get("metal"):
        query = query.filter(Product.metal.ilike(filters["metal"]))

    feature = filters.get("feature")
    if feature == "diamond":
        query = query.filter(Product.name.ilike("%diamond%"))
    elif feature == "pearl":
        query = query.filter(or_(Product.name.ilike("%pearl%"), Product.metal.ilike("%pearl%")))

    if filters.get("min_price") is not None:
        query = query.filter(Product.price >= filters["min_price"])

    if filters.get("max_price") is not None:
        query = query.filter(Product.price <= filters["max_price"])

    if filters.get("in_stock_only"):
        query = query.filter(Product.in_stock == True)

    result_count = query.count()
    products = query.order_by(Product.price.asc(), Product.id.asc()).limit(limit).all()
    return products, result_count


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


def build_reply(message: str, count: int, filters: dict[str, Any]) -> str:
    msg = message.lower()
    category = filters.get("category")
    occasion = filters.get("occasion")
    budget_limit = filters.get("max_price")
    minimum_price = filters.get("min_price")
    metal = filters.get("metal")
    has_gift_intent = filters.get("gift_intent") or message_has_any(msg, GIFT_KEYWORDS)
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
    if filters.get("in_stock_only"):
        return f"I found {count} in-stock jewellery options for you."
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
    if metal:
        return f"I found {count} {metal.lower()} jewellery options for you."

    return f"I found {count} jewellery options for you."


def update_session_preferences(chat_session: ChatSession, filters: dict[str, Any]) -> None:
    preferences = load_json_object(chat_session.preferences)

    if filters.get("min_price") is not None or filters.get("max_price") is not None:
        preferences["budget"] = {
            "min_price": filters.get("min_price"),
            "max_price": filters.get("max_price"),
        }

    if filters.get("style"):
        preferences["style"] = filters["style"]

    if filters.get("metal"):
        preferences["preferred_metal"] = filters["metal"]

    if filters.get("category"):
        favorite_categories = preferences.get("favorite_categories", [])
        if not isinstance(favorite_categories, list):
            favorite_categories = []
        if filters["category"] not in favorite_categories:
            favorite_categories.append(filters["category"])
        preferences["favorite_categories"] = favorite_categories[-5:]

    if filters.get("occasion"):
        preferences["last_occasion"] = filters["occasion"]

    if filters.get("gift_intent"):
        preferences["gift_shopping"] = True

    if filters.get("recipient"):
        preferences["gift_recipient"] = filters["recipient"]

    chat_session.preferences = dump_json_object(preferences)


def build_suggested_next_questions(filters: dict[str, Any], result_count: int) -> list[str]:
    questions: list[str] = []

    if result_count == 0:
        if filters.get("max_price") is not None:
            questions.append("Show similar options with a higher budget")
        if filters.get("metal"):
            questions.append("Show the same style in another metal")
        if filters.get("category"):
            questions.append("Show nearby categories")

    if filters.get("category") and not filters.get("metal"):
        questions.append("Do you prefer gold, rose gold, or silver?")
    if not filters.get("category"):
        questions.append("Are you looking for rings, earrings, or necklaces?")
    if filters.get("max_price") is None and filters.get("min_price") is None:
        questions.append("What budget should I stay within?")
    if not filters.get("occasion"):
        questions.append("Is this for daily wear, gifting, or a special occasion?")
    if filters.get("gift_intent") and not filters.get("recipient"):
        questions.append("Who is the gift for?")
    if not filters.get("in_stock_only"):
        questions.append("Should I show in-stock items only?")

    fallback_questions = [
        "Show cheaper options",
        "Only show gold jewellery",
        "Show matching necklace and earrings",
    ]
    questions.extend(fallback_questions)

    deduped = []
    for question in questions:
        if question not in deduped:
            deduped.append(question)

    return deduped[:5]


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
                suggestion for suggestion in BUYING_SUGGESTIONS if term in suggestion.lower()
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


def database_dependency_status(db: Session) -> dict[str, Any]:
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok", "critical": True}
    except Exception as exc:
        return {
            "status": "error",
            "critical": True,
            "error_type": exc.__class__.__name__,
        }


def email_dependency_status() -> dict[str, Any]:
    if EMAIL_HOST:
        return {
            "status": "configured",
            "critical": False,
            "host": EMAIL_HOST,
            "port": EMAIL_PORT,
        }
    return {"status": "disabled", "critical": False}


def optional_service_status(enabled: bool, configured: bool, name: str) -> dict[str, Any]:
    if enabled and configured:
        return {"status": "configured", "critical": False, "service": name}
    if enabled and not configured:
        return {"status": "misconfigured", "critical": False, "service": name}
    return {"status": "disabled", "critical": False, "service": name}


def dependency_snapshot(db: Session) -> dict[str, Any]:
    dependencies = {
        "database": database_dependency_status(db),
        "email": email_dependency_status(),
        "oms": optional_service_status(OMS_ENABLED, bool(OMS_BASE_URL), "oms"),
        "llm": optional_service_status(LLM_ENABLED, bool(LLM_BASE_URL and LLM_API_KEY), "llm"),
        "monitoring": optional_service_status(
            bool(MONITORING_WEBHOOK_URL or SENTRY_DSN),
            bool(MONITORING_WEBHOOK_URL or SENTRY_DSN),
            "monitoring",
        ),
    }
    status_value = "ok"
    for dependency in dependencies.values():
        if dependency["critical"] and dependency["status"] != "ok":
            status_value = "error"
            break
    return {"status": status_value, "dependencies": dependencies}


@app.get("/ready")
async def readiness(response: Response, db: Session = Depends(get_db)):
    snapshot = dependency_snapshot(db)
    if snapshot["status"] != "ok":
        response.status_code = 503
    return snapshot


@app.get("/readiness")
async def readiness_alias(response: Response, db: Session = Depends(get_db)):
    return await readiness(response, db)


@app.get("/dependencies")
async def dependencies(response: Response, db: Session = Depends(get_db)):
    snapshot = dependency_snapshot(db)
    if snapshot["status"] != "ok":
        response.status_code = 503
    return snapshot


@app.post("/register", response_model=UserOut)
@limiter.limit("5/minute")
async def register(
    request: Request,
    user: UserRegister,
    db: Session = Depends(get_db),
):
    validate_password_strength(user.password)

    existing_email = db.query(User).filter(User.email == user.email).first()
    if existing_email:
        log_event("auth.register_duplicate_email", request, email=user.email)
        raise HTTPException(status_code=400, detail="Email already registered")

    existing_username = db.query(User).filter(User.username == user.username).first()
    if existing_username:
        log_event("auth.register_duplicate_username", request, username=user.username)
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

    db.add(
        EmailVerificationToken(
            user_id=new_user.id,
            token_hash=token_hash,
            is_used=False,
            expires_at=utc_now() + timedelta(minutes=EMAIL_VERIFICATION_EXPIRE_MINUTES),
        )
    )
    db.commit()

    verify_link = f"{FRONTEND_VERIFY_URL}?token={raw_token}"
    send_verification_email(new_user.email, verify_link)
    log_event("auth.register_success", request, user_id=new_user.id, email=new_user.email)

    return new_user


@app.post("/login", response_model=TokenResponse)
@limiter.limit("5/minute")
async def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    if login_is_locked(form_data.username, request):
        increment_metric("failed_logins")
        log_event(
            "auth.login_locked",
            request,
            email=form_data.username,
            success=False,
            reason="lockout",
        )
        raise HTTPException(
            status_code=429,
            detail="Too many failed login attempts. Try again later.",
        )

    user = db.query(User).filter(User.email == form_data.username).first()

    if not user or not verify_password(form_data.password, user.hashed_password):
        record_login_failure(form_data.username, request)
        increment_metric("failed_logins")
        log_event(
            "auth.login_failed",
            request,
            email=form_data.username,
            success=False,
            reason="invalid_credentials",
        )
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not user.is_verified:
        log_event(
            "auth.login_blocked_unverified",
            request,
            user_id=user.id,
            email=user.email,
            success=False,
        )
        raise HTTPException(status_code=403, detail="Please verify your email before logging in")

    clear_login_failures(form_data.username, request)

    access_token = create_access_token({"sub": user.email})
    refresh_token, token_jti, family_id = build_refresh_token_for_user(user.email)
    store_refresh_token(db, user.id, refresh_token, token_jti, family_id, request)
    db.commit()
    log_event("auth.login_success", request, user_id=user.id, email=user.email, success=True)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
    )


@app.post("/refresh", response_model=TokenResponse)
@limiter.limit("10/minute")
async def refresh_token_endpoint(
    request: Request, req: RefreshTokenRequest, db: Session = Depends(get_db)
):
    credentials_exception = HTTPException(status_code=401, detail="Invalid refresh token")

    try:
        payload = jwt.decode(req.refresh_token, SECRET_KEY, algorithms=[ALGORITHM])
        email = payload.get("sub")
        token_type = payload.get("type")
        token_jti = payload.get("jti")
        family_id = payload.get("family_id")
        if email is None or token_type != "refresh":
            log_event("auth.refresh_failed", request, reason="invalid_claims")
            raise credentials_exception from None
    except JWTError:
        log_event("auth.refresh_failed", request, reason="jwt_decode_error")
        raise credentials_exception from None

    stored_token = db.query(RefreshToken).filter(RefreshToken.token == req.refresh_token).first()

    if not stored_token:
        log_event("auth.refresh_failed", request, email=email, reason="token_not_found")
        raise credentials_exception

    mark_refresh_token_used(stored_token, request)

    if stored_token.is_revoked:
        revoke_user_refresh_tokens(
            db,
            stored_token.user_id,
            "suspected_reuse",
            request,
        )
        db.commit()
        log_event(
            "auth.refresh_reuse_detected",
            request,
            user_id=stored_token.user_id,
            reason="suspected_reuse",
        )
        raise credentials_exception

    if stored_token.token_jti and token_jti and stored_token.token_jti != token_jti:
        revoke_user_refresh_tokens(
            db,
            stored_token.user_id,
            "token_jti_mismatch",
            request,
        )
        db.commit()
        log_event(
            "auth.refresh_failed",
            request,
            user_id=stored_token.user_id,
            reason="token_jti_mismatch",
        )
        raise credentials_exception

    if stored_token.expires_at < utc_now():
        revoke_refresh_token(stored_token, "expired", request)
        db.commit()
        log_event(
            "auth.refresh_failed",
            request,
            user_id=stored_token.user_id,
            reason="expired",
        )
        raise credentials_exception

    user = db.query(User).filter(User.email == email).first()
    if not user:
        log_event("auth.refresh_failed", request, email=email, reason="user_not_found")
        raise credentials_exception
    if user.id != stored_token.user_id:
        revoke_user_refresh_tokens(
            db,
            stored_token.user_id,
            "token_user_mismatch",
            request,
        )
        db.commit()
        log_event(
            "auth.refresh_failed",
            request,
            user_id=stored_token.user_id,
            email=email,
            reason="token_user_mismatch",
        )
        raise credentials_exception

    revoke_refresh_token(stored_token, "rotated", request)

    new_access_token = create_access_token({"sub": user.email})
    new_refresh_token, new_token_jti, new_family_id = build_refresh_token_for_user(
        user.email,
        stored_token.family_id or family_id,
    )
    new_token_row = store_refresh_token(
        db,
        user.id,
        new_refresh_token,
        new_token_jti,
        new_family_id,
        request,
        parent_token_id=stored_token.id,
    )
    stored_token.replaced_by_token_id = new_token_row.id
    db.commit()
    increment_metric("token_refreshes")
    log_event(
        "auth.refresh_success",
        request,
        user_id=user.id,
        token_id=stored_token.id,
        replacement_token_id=new_token_row.id,
    )

    return TokenResponse(
        access_token=new_access_token,
        refresh_token=new_refresh_token,
    )


@app.post("/logout", response_model=MessageResponse)
@limiter.limit("10/minute")
async def logout(
    request: Request,
    req: RefreshTokenRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    token_row = (
        db.query(RefreshToken)
        .filter(
            RefreshToken.token == req.refresh_token,
            RefreshToken.user_id == current_user.id,
        )
        .first()
    )

    if token_row:
        revoke_refresh_token(token_row, "logout", request)
        db.commit()
        log_event(
            "auth.logout",
            request,
            user_id=current_user.id,
            refresh_token_id=token_row.id,
        )

    return MessageResponse(message="Logged out successfully")


@app.post("/logout-all-devices", response_model=MessageResponse)
@limiter.limit("5/minute")
async def logout_all_devices(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    revoked_count = revoke_user_refresh_tokens(
        db,
        current_user.id,
        "logout_all_devices",
        request,
    )
    db.commit()
    log_event(
        "auth.logout_all_devices",
        request,
        user_id=current_user.id,
        revoked_count=revoked_count,
    )
    return MessageResponse(message=f"Logged out from {revoked_count} device sessions")


@app.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)):
    return current_user


@app.get("/users/me/addresses", response_model=list[UserAddressOut])
async def list_my_addresses(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return (
        db.query(UserAddress)
        .filter(UserAddress.user_id == current_user.id)
        .order_by(UserAddress.is_default.desc(), UserAddress.created_at.desc())
        .all()
    )


@app.post("/users/me/addresses", response_model=UserAddressOut)
async def create_my_address(
    payload: UserAddressCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    existing_count = db.query(UserAddress).filter(UserAddress.user_id == current_user.id).count()
    make_default = payload.is_default or existing_count == 0
    if make_default:
        clear_default_addresses(db, current_user.id)

    address = UserAddress(
        user_id=current_user.id,
        **payload.model_dump(exclude={"is_default"}),
        is_default=make_default,
    )
    db.add(address)
    db.commit()
    db.refresh(address)
    return address


@app.patch("/users/me/addresses/{address_id}", response_model=UserAddressOut)
async def update_my_address(
    address_id: int,
    payload: UserAddressUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    address = get_user_address_or_404(db, current_user.id, address_id)
    updates = payload.model_dump(exclude_unset=True)
    if updates.get("is_default") is True:
        clear_default_addresses(db, current_user.id)
    apply_model_updates(address, updates)
    address.updated_at = utc_now()
    db.commit()
    db.refresh(address)
    return address


@app.delete("/users/me/addresses/{address_id}", response_model=MessageResponse)
async def delete_my_address(
    address_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    address = get_user_address_or_404(db, current_user.id, address_id)
    was_default = address.is_default
    db.delete(address)
    db.commit()

    if was_default:
        replacement = (
            db.query(UserAddress)
            .filter(UserAddress.user_id == current_user.id)
            .order_by(UserAddress.created_at.desc())
            .first()
        )
        if replacement:
            replacement.is_default = True
            replacement.updated_at = utc_now()
            db.commit()

    return MessageResponse(message="Address deleted")


@app.get("/users/me/notification-settings", response_model=NotificationSettingsOut)
async def get_my_notification_settings(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return notification_settings_for_user(db, current_user.id)


@app.patch("/users/me/notification-settings", response_model=NotificationSettingsOut)
async def update_my_notification_settings(
    payload: NotificationSettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    settings_row = notification_settings_for_user(db, current_user.id)
    apply_model_updates(settings_row, payload.model_dump(exclude_unset=True))
    settings_row.updated_at = utc_now()
    db.commit()
    db.refresh(settings_row)
    return settings_row


def chat_session_summary(db: Session, session: ChatSession) -> ChatSessionOut:
    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session.session_id)
        .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
        .all()
    )
    last_message = messages[-1] if messages else None
    return ChatSessionOut(
        session_id=session.session_id,
        last_filters=load_json_object(session.last_filters),
        preferences=load_json_object(session.preferences),
        created_at=session.created_at,
        message_count=len(messages),
        last_message_at=last_message.created_at if last_message else None,
    )


@app.get("/chat/sessions", response_model=list[ChatSessionOut])
async def list_chat_sessions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sessions = (
        db.query(ChatSession)
        .filter(ChatSession.user_id == str(current_user.id))
        .order_by(ChatSession.created_at.desc())
        .all()
    )
    return [chat_session_summary(db, session) for session in sessions]


@app.get("/chat/sessions/{session_id}", response_model=ChatSessionDetailOut)
async def get_chat_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    session = (
        db.query(ChatSession)
        .filter(
            ChatSession.session_id == session_id,
            ChatSession.user_id == str(current_user.id),
        )
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")

    summary = chat_session_summary(db, session)
    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session.session_id)
        .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
        .all()
    )
    return ChatSessionDetailOut(
        **summary.model_dump(),
        messages=[ChatMessageOut.model_validate(message) for message in messages],
    )


@app.get("/products", response_model=list[ProductOut])
async def list_products(
    q: str | None = None,
    category: str | None = None,
    metal: str | None = None,
    min_price: float | None = Query(default=None, ge=0),
    max_price: float | None = Query(default=None, ge=0),
    in_stock_only: bool = False,
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    query = product_search_query(
        db,
        q=q,
        category=category,
        metal=metal,
        min_price=min_price,
        max_price=max_price,
        in_stock_only=in_stock_only,
    )
    return query.order_by(Product.price.asc(), Product.id.asc()).limit(clamp_limit(limit)).all()


@app.get("/products/{product_id}/similar", response_model=list[ProductOut])
async def similar_products(
    product_id: int,
    limit: int = Query(default=6, ge=1, le=20),
    db: Session = Depends(get_db),
):
    product = get_product_or_404(db, product_id)
    filters = []
    if product.category:
        filters.append(Product.category.ilike(product.category))
    if product.metal:
        filters.append(Product.metal.ilike(product.metal))

    query = db.query(Product).filter(Product.id != product.id)
    if filters:
        query = query.filter(or_(*filters))

    return (
        query.order_by(Product.in_stock.desc(), Product.price.asc(), Product.id.asc())
        .limit(clamp_limit(limit, 20))
        .all()
    )


@app.get("/products/{product_id}", response_model=ProductOut)
async def get_product(product_id: int, db: Session = Depends(get_db)):
    return get_product_or_404(db, product_id)


@app.get("/featured-products", response_model=list[ProductOut])
async def featured_products(
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    featured_items = (
        db.query(FeaturedItem)
        .filter(FeaturedItem.is_active == True)
        .order_by(FeaturedItem.display_order.asc(), FeaturedItem.id.asc())
        .limit(clamp_limit(limit))
        .all()
    )
    products: list[Product] = []
    for item in featured_items:
        product = db.query(Product).filter(Product.id == item.product_id).first()
        if product:
            products.append(product)

    if len(products) < limit:
        seen_ids = {product.id for product in products}
        fallback_products = (
            db.query(Product)
            .filter(Product.is_featured == True, Product.id.notin_(seen_ids))
            .order_by(Product.id.asc())
            .limit(clamp_limit(limit) - len(products))
            .all()
        )
        products.extend(fallback_products)

    return products[: clamp_limit(limit)]


@app.get("/seasonal-collections", response_model=list[SeasonalCollectionOut])
async def seasonal_collections(db: Session = Depends(get_db)):
    now = utc_now()
    return (
        db.query(SeasonalCollection)
        .filter(
            SeasonalCollection.is_active == True,
            or_(SeasonalCollection.starts_at == None, SeasonalCollection.starts_at <= now),
            or_(SeasonalCollection.ends_at == None, SeasonalCollection.ends_at >= now),
        )
        .order_by(SeasonalCollection.starts_at.asc(), SeasonalCollection.id.asc())
        .all()
    )


@app.get("/categories", response_model=list[CategoryOut])
async def list_categories(db: Session = Depends(get_db)):
    return (
        db.query(ProductCategory)
        .filter(ProductCategory.is_active == True)
        .order_by(ProductCategory.name.asc())
        .all()
    )


def list_public_knowledge(db: Session, kind: str) -> list[KnowledgeBaseOut]:
    items = (
        db.query(KnowledgeBaseItem)
        .filter(KnowledgeBaseItem.kind == kind, KnowledgeBaseItem.is_active == True)
        .order_by(KnowledgeBaseItem.title.asc(), KnowledgeBaseItem.id.asc())
        .all()
    )
    return [knowledge_item_response(item) for item in items]


@app.get("/faqs", response_model=list[KnowledgeBaseOut])
async def list_faqs(db: Session = Depends(get_db)):
    return list_public_knowledge(db, "faq")


@app.get("/policies", response_model=list[KnowledgeBaseOut])
async def list_policies(db: Session = Depends(get_db)):
    return list_public_knowledge(db, "policy")


@app.get("/mobile/config")
async def mobile_config(db: Session = Depends(get_db)):
    return {
        "api_version": "v1",
        "oms_connected": False,
        "capabilities": {
            "auth": True,
            "refresh_token_rotation": True,
            "chat": True,
            "chat_session_memory": True,
            "product_search": True,
            "wishlist": True,
            "save_for_later": True,
            "feedback": True,
            "customer_actions": True,
            "order_support_capture": True,
            "oms_order_lookup": OMS_ENABLED and bool(OMS_BASE_URL),
            "llm_grounded_answers": llm_is_configured(),
            "addresses": True,
            "notification_settings": True,
            "saved_chat_history": True,
            "human_handoff": True,
            "admin": False,
        },
        "chat_contract": {
            "response_fields": [
                "response_id",
                "reply",
                "products",
                "applied_filters",
                "result_count",
                "suggested_next_questions",
                "intent",
                "confidence",
                "answer_source",
                "tool_calls",
                "guardrails",
                "lead_captured",
                "handoff",
            ],
            "guardrail_mode": "catalog-grounded",
        },
        "public_config": public_config_values(db),
        "integrations": integration_status(),
    }


@app.get("/admin/products", response_model=list[ProductOut])
async def admin_list_products(
    admin_user: User = Depends(require_permission("products:manage")),
    db: Session = Depends(get_db),
):
    return db.query(Product).order_by(Product.id.asc()).all()


@app.post("/admin/products", response_model=ProductOut)
async def admin_create_product(
    request: Request,
    product_in: ProductCreate,
    admin_user: User = Depends(require_permission("products:manage")),
    db: Session = Depends(get_db),
):
    product = Product(**product_in.model_dump())
    db.add(product)
    db.commit()
    db.refresh(product)
    log_admin_action(request, admin_user, "create", "product", product.id)
    return product


@app.get("/admin/products/{product_id}", response_model=ProductOut)
async def admin_get_product(
    product_id: int,
    admin_user: User = Depends(require_permission("products:manage")),
    db: Session = Depends(get_db),
):
    return get_product_or_404(db, product_id)


@app.patch("/admin/products/{product_id}", response_model=ProductOut)
async def admin_update_product(
    product_id: int,
    request: Request,
    product_in: ProductUpdate,
    admin_user: User = Depends(require_permission("products:manage")),
    db: Session = Depends(get_db),
):
    product = get_product_or_404(db, product_id)
    apply_model_updates(product, product_in.model_dump(exclude_unset=True))
    db.commit()
    db.refresh(product)
    log_admin_action(request, admin_user, "update", "product", product.id)
    return product


@app.patch("/admin/products/{product_id}/inventory", response_model=ProductOut)
async def admin_update_inventory(
    product_id: int,
    request: Request,
    inventory: InventoryUpdate,
    admin_user: User = Depends(require_permission("products:manage")),
    db: Session = Depends(get_db),
):
    product = get_product_or_404(db, product_id)
    updates = inventory.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No inventory updates provided")

    if "stock_quantity" in updates:
        product.stock_quantity = updates["stock_quantity"]
        if "in_stock" not in updates:
            product.in_stock = product.stock_quantity > 0
    if "in_stock" in updates:
        product.in_stock = updates["in_stock"]

    db.commit()
    db.refresh(product)
    log_admin_action(request, admin_user, "update_inventory", "product", product.id)
    return product


@app.delete("/admin/products/{product_id}", response_model=MessageResponse)
async def admin_delete_product(
    product_id: int,
    request: Request,
    admin_user: User = Depends(require_permission("products:manage")),
    db: Session = Depends(get_db),
):
    product = get_product_or_404(db, product_id)
    db.delete(product)
    db.commit()
    log_admin_action(request, admin_user, "delete", "product", product_id)
    return MessageResponse(message="Product deleted")


@app.get("/admin/categories", response_model=list[CategoryOut])
async def admin_list_categories(
    admin_user: User = Depends(require_permission("catalog:manage")),
    db: Session = Depends(get_db),
):
    return db.query(ProductCategory).order_by(ProductCategory.name.asc()).all()


@app.post("/admin/categories", response_model=CategoryOut)
async def admin_create_category(
    request: Request,
    category_in: CategoryCreate,
    admin_user: User = Depends(require_permission("catalog:manage")),
    db: Session = Depends(get_db),
):
    slug = category_in.slug or slugify(category_in.name)
    ensure_unique_slug(db, ProductCategory, slug)
    category = ProductCategory(
        name=category_in.name,
        slug=slug,
        description=category_in.description,
        is_active=category_in.is_active,
    )
    db.add(category)
    db.commit()
    db.refresh(category)
    log_admin_action(request, admin_user, "create", "category", category.id)
    return category


@app.patch("/admin/categories/{category_id}", response_model=CategoryOut)
async def admin_update_category(
    category_id: int,
    request: Request,
    category_in: CategoryUpdate,
    admin_user: User = Depends(require_permission("catalog:manage")),
    db: Session = Depends(get_db),
):
    category = get_category_or_404(db, category_id)
    updates = category_in.model_dump(exclude_unset=True)
    if "name" in updates and "slug" not in updates:
        updates["slug"] = slugify(updates["name"])
    if "slug" in updates:
        ensure_unique_slug(db, ProductCategory, updates["slug"], category.id)
    apply_model_updates(category, updates)
    db.commit()
    db.refresh(category)
    log_admin_action(request, admin_user, "update", "category", category.id)
    return category


@app.delete("/admin/categories/{category_id}", response_model=MessageResponse)
async def admin_delete_category(
    category_id: int,
    request: Request,
    admin_user: User = Depends(require_permission("catalog:manage")),
    db: Session = Depends(get_db),
):
    category = get_category_or_404(db, category_id)
    db.delete(category)
    db.commit()
    log_admin_action(request, admin_user, "delete", "category", category_id)
    return MessageResponse(message="Category deleted")


@app.get("/admin/featured-items", response_model=list[FeaturedItemOut])
async def admin_list_featured_items(
    admin_user: User = Depends(require_permission("catalog:manage")),
    db: Session = Depends(get_db),
):
    return (
        db.query(FeaturedItem)
        .order_by(
            FeaturedItem.display_order.asc(),
            FeaturedItem.id.asc(),
        )
        .all()
    )


@app.post("/admin/featured-items", response_model=FeaturedItemOut)
async def admin_create_featured_item(
    request: Request,
    item_in: FeaturedItemCreate,
    admin_user: User = Depends(require_permission("catalog:manage")),
    db: Session = Depends(get_db),
):
    get_product_or_404(db, item_in.product_id)
    item = FeaturedItem(**item_in.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    log_admin_action(request, admin_user, "create", "featured_item", item.id)
    return item


@app.patch("/admin/featured-items/{item_id}", response_model=FeaturedItemOut)
async def admin_update_featured_item(
    item_id: int,
    request: Request,
    item_in: FeaturedItemUpdate,
    admin_user: User = Depends(require_permission("catalog:manage")),
    db: Session = Depends(get_db),
):
    item = get_featured_item_or_404(db, item_id)
    updates = item_in.model_dump(exclude_unset=True)
    if "product_id" in updates:
        get_product_or_404(db, updates["product_id"])
    apply_model_updates(item, updates)
    db.commit()
    db.refresh(item)
    log_admin_action(request, admin_user, "update", "featured_item", item.id)
    return item


@app.delete("/admin/featured-items/{item_id}", response_model=MessageResponse)
async def admin_delete_featured_item(
    item_id: int,
    request: Request,
    admin_user: User = Depends(require_permission("catalog:manage")),
    db: Session = Depends(get_db),
):
    item = get_featured_item_or_404(db, item_id)
    db.delete(item)
    db.commit()
    log_admin_action(request, admin_user, "delete", "featured_item", item_id)
    return MessageResponse(message="Featured item deleted")


@app.get("/admin/seasonal-collections", response_model=list[SeasonalCollectionOut])
async def admin_list_seasonal_collections(
    admin_user: User = Depends(require_permission("catalog:manage")),
    db: Session = Depends(get_db),
):
    return db.query(SeasonalCollection).order_by(SeasonalCollection.id.asc()).all()


@app.post("/admin/seasonal-collections", response_model=SeasonalCollectionOut)
async def admin_create_seasonal_collection(
    request: Request,
    collection_in: SeasonalCollectionCreate,
    admin_user: User = Depends(require_permission("catalog:manage")),
    db: Session = Depends(get_db),
):
    slug = collection_in.slug or slugify(collection_in.name)
    ensure_unique_slug(db, SeasonalCollection, slug)
    collection = SeasonalCollection(
        **collection_in.model_dump(exclude={"slug"}),
        slug=slug,
    )
    db.add(collection)
    db.commit()
    db.refresh(collection)
    log_admin_action(request, admin_user, "create", "seasonal_collection", collection.id)
    return collection


@app.patch("/admin/seasonal-collections/{collection_id}", response_model=SeasonalCollectionOut)
async def admin_update_seasonal_collection(
    collection_id: int,
    request: Request,
    collection_in: SeasonalCollectionUpdate,
    admin_user: User = Depends(require_permission("catalog:manage")),
    db: Session = Depends(get_db),
):
    collection = get_collection_or_404(db, collection_id)
    updates = collection_in.model_dump(exclude_unset=True)
    if "name" in updates and "slug" not in updates:
        updates["slug"] = slugify(updates["name"])
    if "slug" in updates:
        ensure_unique_slug(db, SeasonalCollection, updates["slug"], collection.id)
    apply_model_updates(collection, updates)
    db.commit()
    db.refresh(collection)
    log_admin_action(request, admin_user, "update", "seasonal_collection", collection.id)
    return collection


@app.delete("/admin/seasonal-collections/{collection_id}", response_model=MessageResponse)
async def admin_delete_seasonal_collection(
    collection_id: int,
    request: Request,
    admin_user: User = Depends(require_permission("catalog:manage")),
    db: Session = Depends(get_db),
):
    collection = get_collection_or_404(db, collection_id)
    db.delete(collection)
    db.commit()
    log_admin_action(request, admin_user, "delete", "seasonal_collection", collection_id)
    return MessageResponse(message="Seasonal collection deleted")


@app.get("/admin/knowledge-base", response_model=list[KnowledgeBaseOut])
async def admin_list_knowledge_base(
    kind: str | None = None,
    admin_user: User = Depends(require_permission("knowledge:manage")),
    db: Session = Depends(get_db),
):
    query = db.query(KnowledgeBaseItem)
    if kind:
        query = query.filter(KnowledgeBaseItem.kind == kind)
    items = query.order_by(KnowledgeBaseItem.kind.asc(), KnowledgeBaseItem.title.asc()).all()
    return [knowledge_item_response(item) for item in items]


@app.post("/admin/knowledge-base", response_model=KnowledgeBaseOut)
async def admin_create_knowledge_base_item(
    request: Request,
    item_in: KnowledgeBaseCreate,
    admin_user: User = Depends(require_permission("knowledge:manage")),
    db: Session = Depends(get_db),
):
    slug = item_in.slug or slugify(item_in.title)
    ensure_unique_slug(db, KnowledgeBaseItem, slug)
    item = KnowledgeBaseItem(
        kind=item_in.kind,
        slug=slug,
        title=item_in.title,
        content=item_in.content,
        tags=dump_json_list(item_in.tags or []),
        is_active=item_in.is_active,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    log_admin_action(request, admin_user, "create", "knowledge_base", item.id)
    return knowledge_item_response(item)


@app.patch("/admin/knowledge-base/{item_id}", response_model=KnowledgeBaseOut)
async def admin_update_knowledge_base_item(
    item_id: int,
    request: Request,
    item_in: KnowledgeBaseUpdate,
    admin_user: User = Depends(require_permission("knowledge:manage")),
    db: Session = Depends(get_db),
):
    item = get_knowledge_item_or_404(db, item_id)
    updates = item_in.model_dump(exclude_unset=True)
    if "title" in updates and "slug" not in updates:
        updates["slug"] = slugify(updates["title"])
    if "slug" in updates:
        ensure_unique_slug(db, KnowledgeBaseItem, updates["slug"], item.id)
    if "tags" in updates:
        updates["tags"] = dump_json_list(updates["tags"] or [])
    apply_model_updates(item, updates)
    item.updated_at = utc_now()
    db.commit()
    db.refresh(item)
    log_admin_action(request, admin_user, "update", "knowledge_base", item.id)
    return knowledge_item_response(item)


@app.delete("/admin/knowledge-base/{item_id}", response_model=MessageResponse)
async def admin_delete_knowledge_base_item(
    item_id: int,
    request: Request,
    admin_user: User = Depends(require_permission("knowledge:manage")),
    db: Session = Depends(get_db),
):
    item = get_knowledge_item_or_404(db, item_id)
    db.delete(item)
    db.commit()
    log_admin_action(request, admin_user, "delete", "knowledge_base", item_id)
    return MessageResponse(message="Knowledge-base item deleted")


@app.get("/admin/config", response_model=list[AppConfigOut])
async def admin_list_config(
    admin_user: User = Depends(require_permission("config:manage")),
    db: Session = Depends(get_db),
):
    return db.query(AppConfigEntry).order_by(AppConfigEntry.key.asc()).all()


@app.post("/admin/config", response_model=AppConfigOut)
async def admin_create_config_entry(
    request: Request,
    entry_in: AppConfigCreate,
    admin_user: User = Depends(require_permission("config:manage")),
    db: Session = Depends(get_db),
):
    ensure_unique_key(db, AppConfigEntry, entry_in.key)
    entry = AppConfigEntry(**entry_in.model_dump())
    db.add(entry)
    db.commit()
    db.refresh(entry)
    log_admin_action(request, admin_user, "create", "config", entry.id)
    return entry


@app.patch("/admin/config/{entry_id}", response_model=AppConfigOut)
async def admin_update_config_entry(
    entry_id: int,
    request: Request,
    entry_in: AppConfigUpdate,
    admin_user: User = Depends(require_permission("config:manage")),
    db: Session = Depends(get_db),
):
    entry = get_config_entry_or_404(db, entry_id)
    updates = entry_in.model_dump(exclude_unset=True)
    apply_model_updates(entry, updates)
    entry.updated_at = utc_now()
    db.commit()
    db.refresh(entry)
    log_admin_action(request, admin_user, "update", "config", entry.id)
    return entry


@app.delete("/admin/config/{entry_id}", response_model=MessageResponse)
async def admin_delete_config_entry(
    entry_id: int,
    request: Request,
    admin_user: User = Depends(require_permission("config:manage")),
    db: Session = Depends(get_db),
):
    entry = get_config_entry_or_404(db, entry_id)
    db.delete(entry)
    db.commit()
    log_admin_action(request, admin_user, "delete", "config", entry_id)
    return MessageResponse(message="Config entry deleted")


@app.get("/admin/leads", response_model=list[LeadCaptureOut])
async def admin_list_leads(
    admin_user: User = Depends(require_permission("leads:manage")),
    db: Session = Depends(get_db),
):
    return db.query(LeadCapture).order_by(LeadCapture.created_at.desc()).all()


@app.get("/admin/metrics")
async def admin_metrics(
    admin_user: User = Depends(require_permission("metrics:read")),
):
    return metrics_snapshot()


@app.get("/admin/integrations/status")
async def admin_integrations_status(
    admin_user: User = Depends(require_permission("metrics:read")),
):
    return integration_status()


@app.get("/admin/integrations/events", response_model=list[ExternalIntegrationEventOut])
async def admin_integration_events(
    service: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    admin_user: User = Depends(require_permission("metrics:read")),
    db: Session = Depends(get_db),
):
    query = db.query(ExternalIntegrationEvent)
    if service:
        query = query.filter(ExternalIntegrationEvent.service == service)
    return query.order_by(ExternalIntegrationEvent.created_at.desc()).limit(limit).all()


@app.post("/admin/alerts/test", response_model=MessageResponse)
async def admin_test_alert(
    request: Request,
    admin_user: User = Depends(require_permission("metrics:read")),
):
    sent = send_monitoring_alert(
        "monitoring.test_alert",
        severity="info",
        payload={
            "admin_user_id": admin_user.id,
            "request_id": request_id_from_request(request),
        },
    )
    if sent:
        log_admin_action(request, admin_user, "test_alert", "monitoring")
        return MessageResponse(message="Monitoring alert sent")
    return MessageResponse(message="Monitoring alert webhook is not configured")


@app.get("/admin/analytics/chat")
async def admin_chat_analytics(
    limit: int = 500,
    admin_user: User = Depends(require_permission("metrics:read")),
    db: Session = Depends(get_db),
):
    return chat_analytics_snapshot(db, limit)


@app.get("/admin/chat-transcripts/review")
async def admin_chat_transcript_review(
    limit: int = 25,
    include_reviewed: bool = False,
    admin_user: User = Depends(require_permission("metrics:read")),
    db: Session = Depends(get_db),
):
    return transcript_review_items(db, limit, include_reviewed)


@app.patch("/admin/chat-transcripts/{response_id}/review")
async def admin_mark_chat_transcript_reviewed(
    response_id: str,
    payload: TranscriptReviewUpdate,
    request: Request,
    admin_user: User = Depends(require_permission("metrics:read")),
    db: Session = Depends(get_db),
):
    row = (
        db.query(ChatResponseAnalytics)
        .filter(ChatResponseAnalytics.response_id == response_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Chat response not found")

    row.reviewed_at = utc_now()
    row.review_notes = payload.notes
    db.commit()
    log_admin_action(request, admin_user, "review", "chat_transcript", row.id)
    return {
        "message": "Chat transcript marked reviewed",
        "response_id": row.response_id,
        "reviewed_at": row.reviewed_at,
    }


@app.patch("/admin/leads/{lead_id}", response_model=LeadCaptureOut)
async def admin_update_lead_status(
    lead_id: int,
    request: Request,
    lead_in: LeadStatusUpdate,
    admin_user: User = Depends(require_permission("leads:manage")),
    db: Session = Depends(get_db),
):
    lead = db.query(LeadCapture).filter(LeadCapture.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    lead.status = lead_in.status
    db.commit()
    db.refresh(lead)
    log_admin_action(request, admin_user, "update_status", "lead", lead.id)
    return lead


@app.get("/admin/request-callbacks", response_model=list[CallbackRequestOut])
async def admin_list_callback_requests(
    limit: int = Query(default=100, ge=1, le=500),
    admin_user: User = Depends(require_permission("support:manage")),
    db: Session = Depends(get_db),
):
    return (
        db.query(CallbackRequest)
        .order_by(CallbackRequest.created_at.desc())
        .limit(limit)
        .all()
    )


@app.patch("/admin/request-callbacks/{callback_id}", response_model=CallbackRequestOut)
async def admin_update_callback_request(
    callback_id: int,
    request: Request,
    status_in: LeadStatusUpdate,
    admin_user: User = Depends(require_permission("support:manage")),
    db: Session = Depends(get_db),
):
    callback = get_record_or_404(db, CallbackRequest, callback_id, "Callback request not found")
    callback.status = status_in.status
    db.commit()
    db.refresh(callback)
    log_admin_action(request, admin_user, "update_status", "callback_request", callback.id)
    return callback


@app.get("/admin/appointments", response_model=list[AppointmentOut])
async def admin_list_appointments(
    limit: int = Query(default=100, ge=1, le=500),
    admin_user: User = Depends(require_permission("support:manage")),
    db: Session = Depends(get_db),
):
    return (
        db.query(AppointmentBooking)
        .order_by(AppointmentBooking.appointment_time.asc())
        .limit(limit)
        .all()
    )


@app.patch("/admin/appointments/{appointment_id}", response_model=AppointmentOut)
async def admin_update_appointment(
    appointment_id: int,
    request: Request,
    status_in: LeadStatusUpdate,
    admin_user: User = Depends(require_permission("support:manage")),
    db: Session = Depends(get_db),
):
    appointment = get_record_or_404(
        db, AppointmentBooking, appointment_id, "Appointment not found"
    )
    appointment.status = status_in.status
    db.commit()
    db.refresh(appointment)
    log_admin_action(request, admin_user, "update_status", "appointment", appointment.id)
    return appointment


@app.get("/admin/custom-orders", response_model=list[CustomOrderOut])
async def admin_list_custom_orders(
    limit: int = Query(default=100, ge=1, le=500),
    admin_user: User = Depends(require_permission("support:manage")),
    db: Session = Depends(get_db),
):
    return (
        db.query(CustomOrderRequest)
        .order_by(CustomOrderRequest.created_at.desc())
        .limit(limit)
        .all()
    )


@app.patch("/admin/custom-orders/{request_id}", response_model=CustomOrderOut)
async def admin_update_custom_order(
    request_id: int,
    request: Request,
    status_in: LeadStatusUpdate,
    admin_user: User = Depends(require_permission("support:manage")),
    db: Session = Depends(get_db),
):
    custom_order = get_record_or_404(
        db, CustomOrderRequest, request_id, "Custom order request not found"
    )
    custom_order.status = status_in.status
    db.commit()
    db.refresh(custom_order)
    log_admin_action(request, admin_user, "update_status", "custom_order", custom_order.id)
    return custom_order


@app.get("/admin/complaints", response_model=list[ComplaintOut])
async def admin_list_complaints(
    limit: int = Query(default=100, ge=1, le=500),
    admin_user: User = Depends(require_permission("support:manage")),
    db: Session = Depends(get_db),
):
    return (
        db.query(ComplaintTicket)
        .order_by(ComplaintTicket.created_at.desc())
        .limit(limit)
        .all()
    )


@app.patch("/admin/complaints/{ticket_id}", response_model=ComplaintOut)
async def admin_update_complaint(
    ticket_id: int,
    request: Request,
    status_in: LeadStatusUpdate,
    admin_user: User = Depends(require_permission("support:manage")),
    db: Session = Depends(get_db),
):
    complaint = get_record_or_404(db, ComplaintTicket, ticket_id, "Complaint ticket not found")
    complaint.status = status_in.status
    db.commit()
    db.refresh(complaint)
    log_admin_action(request, admin_user, "update_status", "complaint", complaint.id)
    return complaint


@app.get("/admin/orders/support", response_model=list[OrderSupportOut])
async def admin_list_order_support_requests(
    limit: int = Query(default=100, ge=1, le=500),
    admin_user: User = Depends(require_permission("support:manage")),
    db: Session = Depends(get_db),
):
    return (
        db.query(OrderSupportRequest)
        .order_by(OrderSupportRequest.created_at.desc())
        .limit(limit)
        .all()
    )


@app.patch("/admin/orders/support/{request_id}", response_model=OrderSupportOut)
async def admin_update_order_support_request(
    request_id: int,
    request: Request,
    status_in: LeadStatusUpdate,
    admin_user: User = Depends(require_permission("support:manage")),
    db: Session = Depends(get_db),
):
    order_request = get_record_or_404(
        db, OrderSupportRequest, request_id, "Order support request not found"
    )
    order_request.status = status_in.status
    db.commit()
    db.refresh(order_request)
    log_admin_action(request, admin_user, "update_status", "order_support", order_request.id)
    return order_request


def create_or_update_saved_item(
    db: Session,
    model,
    user_id: int,
    payload: SavedItemCreate,
):
    product = get_product_or_404(db, payload.product_id)
    item = (
        db.query(model)
        .filter(
            model.user_id == user_id,
            model.product_id == payload.product_id,
        )
        .first()
    )
    if item:
        item.note = payload.note
    else:
        item = model(
            user_id=user_id,
            product_id=payload.product_id,
            note=payload.note,
        )
        db.add(item)
    db.commit()
    db.refresh(item)
    return saved_product_response(item, product)


def list_saved_items(db: Session, model, user_id: int) -> list[SavedProductOut]:
    items = db.query(model).filter(model.user_id == user_id).order_by(model.created_at.desc()).all()
    responses = []
    for item in items:
        product = db.query(Product).filter(Product.id == item.product_id).first()
        if product:
            responses.append(saved_product_response(item, product))
    return responses


def delete_saved_item(db: Session, model, user_id: int, item_id: int) -> None:
    item = (
        db.query(model)
        .filter(
            model.id == item_id,
            model.user_id == user_id,
        )
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Saved item not found")
    db.delete(item)
    db.commit()


@app.get("/wishlist", response_model=list[SavedProductOut])
async def list_wishlist(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return list_saved_items(db, WishlistItem, current_user.id)


@app.post("/wishlist", response_model=SavedProductOut)
async def add_to_wishlist(
    payload: SavedItemCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return create_or_update_saved_item(db, WishlistItem, current_user.id, payload)


@app.delete("/wishlist/{item_id}", response_model=MessageResponse)
async def remove_from_wishlist(
    item_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    delete_saved_item(db, WishlistItem, current_user.id, item_id)
    return MessageResponse(message="Wishlist item removed")


@app.get("/save-for-later", response_model=list[SavedProductOut])
async def list_save_for_later(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return list_saved_items(db, SaveForLaterItem, current_user.id)


@app.post("/save-for-later", response_model=SavedProductOut)
async def add_to_save_for_later(
    payload: SavedItemCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return create_or_update_saved_item(db, SaveForLaterItem, current_user.id, payload)


@app.delete("/save-for-later/{item_id}", response_model=MessageResponse)
async def remove_from_save_for_later(
    item_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    delete_saved_item(db, SaveForLaterItem, current_user.id, item_id)
    return MessageResponse(message="Saved item removed")


@app.post("/request-callback", response_model=CallbackRequestOut)
async def request_callback(
    payload: CallbackRequestCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    callback = CallbackRequest(
        user_id=current_user.id,
        name=payload.name or current_user.username,
        phone=payload.phone,
        email=str(payload.email) if payload.email else current_user.email,
        reason=payload.reason,
        preferred_time=payload.preferred_time,
        status="new",
    )
    db.add(callback)
    db.commit()
    db.refresh(callback)
    return callback


@app.get("/request-callbacks/my", response_model=list[CallbackRequestOut])
async def list_my_callback_requests(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return (
        db.query(CallbackRequest)
        .filter(CallbackRequest.user_id == current_user.id)
        .order_by(CallbackRequest.created_at.desc())
        .all()
    )


@app.post("/appointments", response_model=AppointmentOut)
async def book_appointment(
    payload: AppointmentCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    appointment = AppointmentBooking(
        user_id=current_user.id,
        name=payload.name or current_user.username,
        phone=payload.phone,
        email=str(payload.email) if payload.email else current_user.email,
        store_location=payload.store_location,
        appointment_time=payload.appointment_time,
        purpose=payload.purpose,
        status="requested",
    )
    db.add(appointment)
    db.commit()
    db.refresh(appointment)
    return appointment


@app.get("/appointments/my", response_model=list[AppointmentOut])
async def list_my_appointments(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return (
        db.query(AppointmentBooking)
        .filter(AppointmentBooking.user_id == current_user.id)
        .order_by(AppointmentBooking.appointment_time.asc())
        .all()
    )


@app.post("/custom-orders", response_model=CustomOrderOut)
async def create_custom_order_request(
    payload: CustomOrderCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if payload.product_id is not None:
        get_product_or_404(db, payload.product_id)

    custom_order = CustomOrderRequest(
        user_id=current_user.id,
        product_id=payload.product_id,
        session_id=payload.session_id,
        description=payload.description,
        budget=payload.budget,
        metal=payload.metal,
        category=payload.category,
        status="requested",
    )
    db.add(custom_order)
    create_customer_action_lead(
        db,
        current_user,
        source="custom_order",
        intent="custom_order",
        message=payload.description,
        session_id=payload.session_id,
    )
    db.commit()
    db.refresh(custom_order)
    return custom_order


@app.get("/custom-orders/my", response_model=list[CustomOrderOut])
async def list_my_custom_orders(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return (
        db.query(CustomOrderRequest)
        .filter(CustomOrderRequest.user_id == current_user.id)
        .order_by(CustomOrderRequest.created_at.desc())
        .all()
    )


@app.post("/complaints", response_model=ComplaintOut)
async def create_complaint_ticket(
    payload: ComplaintCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    complaint = ComplaintTicket(
        user_id=current_user.id,
        order_reference=payload.order_reference,
        category=payload.category,
        message=payload.message,
        priority=payload.priority,
        status="open",
    )
    db.add(complaint)
    create_customer_action_lead(
        db,
        current_user,
        source="complaint",
        intent="complaint",
        message=payload.message,
    )
    db.commit()
    db.refresh(complaint)
    return complaint


@app.get("/complaints/my", response_model=list[ComplaintOut])
async def list_my_complaints(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return (
        db.query(ComplaintTicket)
        .filter(ComplaintTicket.user_id == current_user.id)
        .order_by(ComplaintTicket.created_at.desc())
        .all()
    )


@app.post("/orders/support", response_model=OrderSupportOut)
async def create_order_support_request(
    payload: OrderSupportCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    integration_state = "capture_only"
    oms_response: dict[str, Any] = {}
    integration_error = None
    status_code = None
    oms_payload = {
        "request_type": payload.request_type,
        "message": payload.message,
        "source": "chatbot_api",
    }
    if payload.request_type in {"cancel", "return", "refund"}:
        integration_state, oms_response, integration_error, status_code = submit_oms_order_action(
            payload.order_reference,
            payload.request_type,
            oms_payload,
        )
    elif payload.request_type in {"status", "delivery"} and payload.order_reference:
        integration_state, oms_response, integration_error, status_code = lookup_oms_order(
            payload.order_reference
        )

    order_request = OrderSupportRequest(
        user_id=current_user.id,
        order_reference=payload.order_reference,
        request_type=payload.request_type,
        message=payload.message,
        status=(
            "synced_to_oms"
            if integration_state == "synced"
            else "oms_failed"
            if integration_state == "failed"
            else "received"
        ),
    )
    db.add(order_request)
    record_integration_event(
        db,
        service="oms",
        action=payload.request_type,
        status_value=integration_state,
        user_id=current_user.id,
        reference=payload.order_reference,
        request_payload=oms_payload,
        response_payload=oms_response,
        status_code=status_code,
        error=integration_error,
    )
    create_customer_action_lead(
        db,
        current_user,
        source="order_support",
        intent=(
            "return_refund"
            if payload.request_type in {"cancel", "return", "refund"}
            else "order_status"
        ),
        message=payload.message or payload.request_type,
    )
    db.commit()
    db.refresh(order_request)
    if integration_state == "failed":
        send_monitoring_alert(
            "oms.request_failed",
            payload={
                "order_reference": payload.order_reference,
                "request_type": payload.request_type,
                "error": integration_error,
            },
        )
    return OrderSupportOut.model_validate(order_request).model_copy(
        update={"integration_status": integration_state, "oms_response": oms_response}
    )


@app.get("/orders/support/my", response_model=list[OrderSupportOut])
async def list_my_order_support_requests(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return (
        db.query(OrderSupportRequest)
        .filter(OrderSupportRequest.user_id == current_user.id)
        .order_by(OrderSupportRequest.created_at.desc())
        .all()
    )


@app.get("/orders/{order_reference}", response_model=OrderLookupOut)
async def get_order_status(
    order_reference: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    integration_state, data, error, status_code = lookup_oms_order(order_reference)
    record_integration_event(
        db,
        service="oms",
        action="lookup",
        status_value=integration_state,
        user_id=current_user.id,
        reference=order_reference,
        response_payload=data,
        status_code=status_code,
        error=error,
    )
    db.commit()
    if integration_state == "failed":
        send_monitoring_alert(
            "oms.lookup_failed",
            payload={"order_reference": order_reference, "error": error},
        )
    return OrderLookupOut(
        order_reference=order_reference,
        integration_status=integration_state,
        data=data,
        message=error,
    )


@app.post("/orders/{order_reference}/cancel")
async def cancel_order(
    order_reference: str,
    payload: OrderActionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    request_payload = payload.model_dump()
    integration_state, data, error, status_code = submit_oms_order_action(
        order_reference, "cancel", request_payload
    )
    record_integration_event(
        db,
        service="oms",
        action="cancel",
        status_value=integration_state,
        user_id=current_user.id,
        reference=order_reference,
        request_payload=request_payload,
        response_payload=data,
        status_code=status_code,
        error=error,
    )
    db.commit()
    return {
        "order_reference": order_reference,
        "action": "cancel",
        "integration_status": integration_state,
        "data": data,
        "message": error,
    }


@app.post("/orders/{order_reference}/return")
async def request_order_return(
    order_reference: str,
    payload: OrderActionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    request_payload = payload.model_dump()
    integration_state, data, error, status_code = submit_oms_order_action(
        order_reference, "return", request_payload
    )
    record_integration_event(
        db,
        service="oms",
        action="return",
        status_value=integration_state,
        user_id=current_user.id,
        reference=order_reference,
        request_payload=request_payload,
        response_payload=data,
        status_code=status_code,
        error=error,
    )
    db.commit()
    return {
        "order_reference": order_reference,
        "action": "return",
        "integration_status": integration_state,
        "data": data,
        "message": error,
    }


@app.post("/orders/{order_reference}/refund")
async def request_order_refund(
    order_reference: str,
    payload: OrderActionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    request_payload = payload.model_dump()
    integration_state, data, error, status_code = submit_oms_order_action(
        order_reference, "refund", request_payload
    )
    record_integration_event(
        db,
        service="oms",
        action="refund",
        status_value=integration_state,
        user_id=current_user.id,
        reference=order_reference,
        request_payload=request_payload,
        response_payload=data,
        status_code=status_code,
        error=error,
    )
    db.commit()
    return {
        "order_reference": order_reference,
        "action": "refund",
        "integration_status": integration_state,
        "data": data,
        "message": error,
    }


@app.post("/feedback", response_model=MessageResponse)
async def submit_feedback(
    request: Request,
    payload: FeedbackCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    analytics = None
    if payload.response_id:
        analytics = (
            db.query(ChatResponseAnalytics)
            .filter(
                ChatResponseAnalytics.response_id == payload.response_id,
                ChatResponseAnalytics.user_id == current_user.id,
            )
            .first()
        )
        if not analytics:
            raise HTTPException(status_code=404, detail="Chat response not found")

    feedback_type = feedback_type_from_payload(payload)
    sentiment = feedback_sentiment(feedback_type, payload.rating)
    increment_metric("feedback_total")
    FEEDBACK_COUNTS[sentiment] += 1
    if feedback_type == "not_helpful":
        increment_metric("feedback_not_helpful")
        FEEDBACK_COUNTS["not_helpful"] += 1

    feedback = ResponseFeedback(
        response_id=payload.response_id,
        user_id=current_user.id,
        session_id=analytics.session_id if analytics else None,
        feedback_type=feedback_type,
        helpful=payload.helpful,
        rating=payload.rating,
        context=payload.context,
        comment=payload.comment,
    )
    db.add(feedback)

    if analytics and feedback_needs_review(feedback_type, sentiment):
        if not analytics.low_conversion:
            increment_metric("low_conversion_searches")
        analytics.low_conversion = True

    db.commit()

    log_event(
        "feedback.submitted",
        request,
        user_id=current_user.id,
        response_id=payload.response_id,
        feedback_type=feedback_type,
        sentiment=sentiment,
        rating=payload.rating,
        context=payload.context,
    )
    return MessageResponse(message="Feedback received")


@app.post("/chat", response_model=ChatResponse)
async def chat(
    request: Request,
    req: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    session_id = req.session_id or f"session_{current_user.id}"

    chat_session = db.query(ChatSession).filter(ChatSession.session_id == session_id).first()

    if not chat_session:
        chat_session = ChatSession(session_id=session_id, user_id=str(current_user.id))
        db.add(chat_session)
        db.commit()

    db.add(
        ChatMessage(
            session_id=session_id, user_id=str(current_user.id), role="user", content=req.message
        )
    )
    db.commit()

    previous_filters = load_json_object(chat_session.last_filters)
    filters = merge_filter_state(previous_filters, req.message)
    applied_filters = compact_filters(filters)
    products, result_count = filter_products(db, filters)
    fallback_reply = build_reply(req.message, result_count, filters)
    reply = fallback_reply
    llm_source = "rules"
    llm_tool_calls: list[str] = []
    suggestions = get_buying_suggestions(req.message)
    suggested_next_questions = build_suggested_next_questions(filters, result_count)
    chat_session.last_filters = dump_json_object(applied_filters)
    update_session_preferences(chat_session, filters)
    increment_metric("total_chats")
    if result_count == 0:
        increment_metric("no_result_searches")
    lead_intent = detect_lead_intent(req.message.lower(), filters)
    handoff = None
    lead_captured = False
    if should_offer_handoff(req.message.lower(), lead_intent):
        lead = create_chat_lead(
            db,
            current_user,
            session_id,
            req.message,
            lead_intent or "human_support",
        )
        handoff = build_handoff_info(lead.intent, lead.id)
        lead_captured = True

    response_id = str(uuid4())
    chat_intent = detect_chat_intent(req.message, filters, lead_intent)
    if chat_intent not in {"order_status", "return_refund", "complaint"} and not lead_captured:
        reply, llm_source, llm_tool_calls = try_llm_reply(
            db,
            current_user,
            req.message,
            products,
            filters,
            fallback_reply,
        )
    unmatched = is_unmatched_query(chat_intent, result_count, filters)
    low_conversion = is_low_conversion_search(result_count, filters, lead_captured)
    if unmatched:
        increment_metric("unmatched_queries")
    if low_conversion:
        increment_metric("low_conversion_searches")

    log_event(
        "chat.request",
        request=request,
        response_id=response_id,
        user_id=current_user.id,
        session_id=session_id,
        intent=chat_intent,
        result_count=result_count,
        no_results=result_count == 0,
        unmatched=unmatched,
        low_conversion=low_conversion,
        lead_captured=lead_captured,
        applied_filters=applied_filters,
    )

    db.add(
        ChatMessage(
            session_id=session_id, user_id=str(current_user.id), role="assistant", content=reply
        )
    )
    record_chat_analytics(
        db,
        response_id=response_id,
        user=current_user,
        session_id=session_id,
        user_message=req.message,
        assistant_reply=reply,
        intent=chat_intent,
        applied_filters=applied_filters,
        products=products,
        result_count=result_count,
        unmatched=unmatched,
        low_conversion=low_conversion,
        lead_captured=lead_captured,
    )
    db.commit()

    tool_calls = []
    if has_product_search_signal(filters) or result_count > 0:
        tool_calls.append("catalog_search")
    if lead_captured:
        tool_calls.append("lead_capture")
    tool_calls.extend(llm_tool_calls)
    answer_source = answer_source_for_chat(chat_intent, result_count, lead_captured)
    if not lead_captured and llm_source != "rules":
        answer_source = llm_source
    guardrails = guardrails_for_chat(req.message, chat_intent, unmatched)
    if llm_tool_calls:
        guardrails.append("llm-grounded-catalog")

    return ChatResponse(
        response_id=response_id,
        reply=reply,
        products=[ProductOut.model_validate(p) for p in products],
        session_id=session_id,
        suggestions=suggestions,
        applied_filters=applied_filters,
        result_count=result_count,
        suggested_next_questions=suggested_next_questions,
        intent=chat_intent,
        confidence=chat_confidence(chat_intent, result_count, filters, unmatched),
        answer_source=answer_source,
        tool_calls=tool_calls,
        guardrails=guardrails,
        lead_captured=lead_captured,
        handoff=handoff,
    )


@app.post("/forgot-password", response_model=MessageResponse)
@limiter.limit("3/minute")
async def forgot_password(
    request: Request, req: ForgotPasswordRequest, db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.email == req.email).first()

    generic_response = MessageResponse(
        message="If that email is registered, password reset instructions have been generated."
    )

    if not user:
        return generic_response

    raw_token = generate_password_reset_token()
    token_hash = hash_reset_token(raw_token)

    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=token_hash,
            is_used=False,
            expires_at=utc_now() + timedelta(minutes=PASSWORD_RESET_EXPIRE_MINUTES),
        )
    )
    db.commit()

    reset_link = f"{FRONTEND_RESET_URL}?token={raw_token}"
    send_password_reset_email(user.email, reset_link)

    return generic_response


@app.post("/reset-password", response_model=MessageResponse)
@limiter.limit("5/minute")
async def reset_password(
    request: Request, req: ResetPasswordRequest, db: Session = Depends(get_db)
):
    validate_password_strength(req.new_password)

    token_hash = hash_reset_token(req.token)

    reset_row = (
        db.query(PasswordResetToken).filter(PasswordResetToken.token_hash == token_hash).first()
    )

    if not reset_row or reset_row.is_used:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    if reset_row.expires_at < utc_now():
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    user = db.query(User).filter(User.id == reset_row.user_id).first()
    if not user:
        raise HTTPException(status_code=400, detail="Invalid reset request")

    user.hashed_password = hash_password(req.new_password)
    reset_row.is_used = True

    revoke_user_refresh_tokens(db, user.id, "password_reset", request)

    db.commit()

    return MessageResponse(message="Password reset successful")


@app.post("/verify-email", response_model=MessageResponse)
async def verify_email(req: VerifyEmailRequest, db: Session = Depends(get_db)):
    token_hash = hash_opaque_token(req.token)

    row = (
        db.query(EmailVerificationToken)
        .filter(EmailVerificationToken.token_hash == token_hash)
        .first()
    )

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
@limiter.limit("3/minute")
async def resend_verification(
    request: Request, req: ResendVerificationRequest, db: Session = Depends(get_db)
):
    generic_response = MessageResponse(
        message="If the email exists and is not yet verified, a verification email has been generated."
    )

    user = db.query(User).filter(User.email == req.email).first()
    if not user or user.is_verified:
        return generic_response

    latest_token = latest_verification_token(db, user.id)
    if latest_token and latest_token.created_at:
        cooldown_started_at = utc_now() - timedelta(seconds=RESEND_VERIFICATION_COOLDOWN_SECONDS)
        if latest_token.created_at >= cooldown_started_at:
            return generic_response

    db.query(EmailVerificationToken).filter(
        EmailVerificationToken.user_id == user.id, EmailVerificationToken.is_used == False
    ).update({"is_used": True})

    raw_token = generate_opaque_token()
    token_hash = hash_opaque_token(raw_token)

    db.add(
        EmailVerificationToken(
            user_id=user.id,
            token_hash=token_hash,
            is_used=False,
            expires_at=utc_now() + timedelta(minutes=EMAIL_VERIFICATION_EXPIRE_MINUTES),
        )
    )
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
    to_encode.update(
        {
            "exp": expire,
            "type": "access",
        }
    )
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(
    data: dict,
    expires_delta: timedelta | None = None,
    jti: str | None = None,
    family_id: str | None = None,
):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    )
    to_encode.update(
        {
            "exp": expire,
            "type": "refresh",
            "jti": jti or str(uuid4()),
            "family_id": family_id or str(uuid4()),
        }
    )
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def generate_password_reset_token() -> str:
    return secrets.token_urlsafe(32)


def hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def generate_opaque_token() -> str:
    return secrets.token_urlsafe(32)


def hash_opaque_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
