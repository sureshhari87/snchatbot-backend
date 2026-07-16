import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def load_env_file(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def get_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def get_str(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value


def get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


def get_list(name: str, default: list[str]) -> list[str]:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


def origin_from_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def normalize_cors_origins(
    env: str,
    configured_origins: list[str],
    frontend_urls: list[str],
) -> list[str]:
    if env not in {"staging", "production"}:
        return configured_origins

    if "*" not in configured_origins:
        return configured_origins

    derived_origins = []
    for frontend_url in frontend_urls:
        origin = origin_from_url(frontend_url)
        if origin and "localhost" not in origin and "127.0.0.1" not in origin:
            derived_origins.append(origin)

    return list(dict.fromkeys(derived_origins))


def current_environment() -> str:
    if get_bool("TESTING", False):
        return "test"
    return (os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or "local").lower()


@dataclass(frozen=True)
class Settings:
    app_env: str
    app_debug: bool
    database_url: str
    run_migrations_on_startup: bool
    secret_key: str
    algorithm: str
    access_token_expire_minutes: int
    refresh_token_expire_days: int
    password_reset_expire_minutes: int
    email_verification_expire_minutes: int
    frontend_reset_url: str
    frontend_verify_url: str
    cors_origins: list[str]
    cors_allow_credentials: bool
    https_redirect: bool
    trusted_hosts: list[str]
    proxy_headers: bool
    forwarded_allow_ips: str
    host: str
    port: int
    web_concurrency: int
    uvicorn_log_level: str
    email_host: str | None
    email_port: int
    email_username: str | None
    email_password: str | None
    email_from: str
    email_from_name: str
    email_use_tls: bool
    email_use_ssl: bool
    email_timeout_seconds: int
    password_min_length: int
    login_failure_limit: int
    login_lockout_minutes: int
    resend_verification_cooldown_seconds: int
    max_request_body_bytes: int
    oms_base_url: str | None
    oms_api_key: str | None
    oms_timeout_seconds: int
    oms_enabled: bool
    llm_enabled: bool
    llm_base_url: str | None
    llm_api_key: str | None
    llm_model: str
    llm_timeout_seconds: int
    llm_max_tokens: int
    monitoring_webhook_url: str | None
    monitoring_webhook_timeout_seconds: int
    sentry_dsn: str | None
    alert_error_threshold: int

    @property
    def is_testing(self) -> bool:
        return self.app_env == "test" or get_bool("TESTING", False)


ENVIRONMENT_DEFAULTS: dict[str, dict[str, Any]] = {
    "local": {
        "APP_DEBUG": True,
        "DATABASE_URL": "sqlite:///./jewellery.db",
        "RUN_MIGRATIONS_ON_STARTUP": True,
        "CORS_ORIGINS": ["*"],
        "CORS_ALLOW_CREDENTIALS": True,
        "HTTPS_REDIRECT": False,
        "TRUSTED_HOSTS": ["*"],
    },
    "test": {
        "APP_DEBUG": False,
        "DATABASE_URL": "sqlite://",
        "RUN_MIGRATIONS_ON_STARTUP": False,
        "CORS_ORIGINS": ["*"],
        "CORS_ALLOW_CREDENTIALS": True,
        "HTTPS_REDIRECT": False,
        "TRUSTED_HOSTS": ["*"],
    },
    "staging": {
        "APP_DEBUG": False,
        "DATABASE_URL": "sqlite:///./jewellery-staging.db",
        "RUN_MIGRATIONS_ON_STARTUP": True,
        "CORS_ORIGINS": [],
        "CORS_ALLOW_CREDENTIALS": True,
        "HTTPS_REDIRECT": True,
        "TRUSTED_HOSTS": [],
    },
    "production": {
        "APP_DEBUG": False,
        "DATABASE_URL": "sqlite:///./jewellery.db",
        "RUN_MIGRATIONS_ON_STARTUP": True,
        "CORS_ORIGINS": [],
        "CORS_ALLOW_CREDENTIALS": True,
        "HTTPS_REDIRECT": True,
        "TRUSTED_HOSTS": [],
    },
}


def build_settings() -> Settings:
    env = current_environment()
    defaults = ENVIRONMENT_DEFAULTS.get(env, ENVIRONMENT_DEFAULTS["local"])

    database_url = get_str("DATABASE_URL", defaults["DATABASE_URL"])
    email_username = get_str("EMAIL_USERNAME")
    frontend_reset_url = get_str("FRONTEND_RESET_URL", "http://localhost:3000/reset-password")
    frontend_verify_url = get_str("FRONTEND_VERIFY_URL", "http://localhost:3000/verify-email")
    cors_origins = normalize_cors_origins(
        env,
        get_list("CORS_ORIGINS", defaults["CORS_ORIGINS"]),
        [frontend_reset_url, frontend_verify_url],
    )

    return Settings(
        app_env=env,
        app_debug=get_bool("APP_DEBUG", defaults["APP_DEBUG"]),
        database_url=database_url,
        run_migrations_on_startup=get_bool(
            "RUN_MIGRATIONS_ON_STARTUP",
            defaults["RUN_MIGRATIONS_ON_STARTUP"],
        ),
        secret_key=get_str("SECRET_KEY", "change-this-to-a-long-random-secret"),
        algorithm=get_str("ALGORITHM", "HS256"),
        access_token_expire_minutes=get_int("ACCESS_TOKEN_EXPIRE_MINUTES", 30),
        refresh_token_expire_days=get_int("REFRESH_TOKEN_EXPIRE_DAYS", 7),
        password_reset_expire_minutes=get_int("PASSWORD_RESET_EXPIRE_MINUTES", 15),
        email_verification_expire_minutes=get_int("EMAIL_VERIFICATION_EXPIRE_MINUTES", 30),
        frontend_reset_url=frontend_reset_url,
        frontend_verify_url=frontend_verify_url,
        cors_origins=cors_origins,
        cors_allow_credentials=get_bool(
            "CORS_ALLOW_CREDENTIALS",
            defaults["CORS_ALLOW_CREDENTIALS"],
        ),
        https_redirect=get_bool("HTTPS_REDIRECT", defaults["HTTPS_REDIRECT"]),
        trusted_hosts=get_list("TRUSTED_HOSTS", defaults["TRUSTED_HOSTS"]),
        proxy_headers=get_bool("PROXY_HEADERS", True),
        forwarded_allow_ips=get_str("FORWARDED_ALLOW_IPS", "*"),
        host=get_str("HOST", "0.0.0.0"),
        port=get_int("PORT", 7860),
        web_concurrency=max(get_int("WEB_CONCURRENCY", 1), 1),
        uvicorn_log_level=get_str("UVICORN_LOG_LEVEL", "info"),
        email_host=get_str("EMAIL_HOST"),
        email_port=get_int("EMAIL_PORT", 587),
        email_username=email_username,
        email_password=get_str("EMAIL_PASSWORD"),
        email_from=get_str("EMAIL_FROM", email_username or "no-reply@localhost"),
        email_from_name=get_str("EMAIL_FROM_NAME", "Jewellery Chat"),
        email_use_tls=get_bool("EMAIL_USE_TLS", True),
        email_use_ssl=get_bool("EMAIL_USE_SSL", False),
        email_timeout_seconds=get_int("EMAIL_TIMEOUT_SECONDS", 10),
        password_min_length=get_int("PASSWORD_MIN_LENGTH", 8),
        login_failure_limit=get_int("LOGIN_FAILURE_LIMIT", 5),
        login_lockout_minutes=get_int("LOGIN_LOCKOUT_MINUTES", 15),
        resend_verification_cooldown_seconds=get_int("RESEND_VERIFICATION_COOLDOWN_SECONDS", 60),
        max_request_body_bytes=get_int("MAX_REQUEST_BODY_BYTES", 1_048_576),
        oms_base_url=get_str("OMS_BASE_URL"),
        oms_api_key=get_str("OMS_API_KEY"),
        oms_timeout_seconds=get_int("OMS_TIMEOUT_SECONDS", 10),
        oms_enabled=get_bool("OMS_ENABLED", False),
        llm_enabled=get_bool("LLM_ENABLED", False),
        llm_base_url=get_str("LLM_BASE_URL"),
        llm_api_key=get_str("LLM_API_KEY"),
        llm_model=get_str("LLM_MODEL", "gpt-4o-mini"),
        llm_timeout_seconds=get_int("LLM_TIMEOUT_SECONDS", 20),
        llm_max_tokens=get_int("LLM_MAX_TOKENS", 350),
        monitoring_webhook_url=get_str("MONITORING_WEBHOOK_URL"),
        monitoring_webhook_timeout_seconds=get_int("MONITORING_WEBHOOK_TIMEOUT_SECONDS", 5),
        sentry_dsn=get_str("SENTRY_DSN"),
        alert_error_threshold=get_int("ALERT_ERROR_THRESHOLD", 5),
    )


load_env_file()
settings = build_settings()

APP_ENV = settings.app_env
APP_DEBUG = settings.app_debug
DATABASE_URL = settings.database_url
RUN_MIGRATIONS_ON_STARTUP = settings.run_migrations_on_startup

SECRET_KEY = settings.secret_key
ALGORITHM = settings.algorithm
ACCESS_TOKEN_EXPIRE_MINUTES = settings.access_token_expire_minutes
REFRESH_TOKEN_EXPIRE_DAYS = settings.refresh_token_expire_days
PASSWORD_RESET_EXPIRE_MINUTES = settings.password_reset_expire_minutes
EMAIL_VERIFICATION_EXPIRE_MINUTES = settings.email_verification_expire_minutes

FRONTEND_RESET_URL = settings.frontend_reset_url
FRONTEND_VERIFY_URL = settings.frontend_verify_url
CORS_ORIGINS = settings.cors_origins
CORS_ALLOW_CREDENTIALS = settings.cors_allow_credentials
HTTPS_REDIRECT = settings.https_redirect
TRUSTED_HOSTS = settings.trusted_hosts
PROXY_HEADERS = settings.proxy_headers
FORWARDED_ALLOW_IPS = settings.forwarded_allow_ips
HOST = settings.host
PORT = settings.port
WEB_CONCURRENCY = settings.web_concurrency
UVICORN_LOG_LEVEL = settings.uvicorn_log_level

EMAIL_HOST = settings.email_host
EMAIL_PORT = settings.email_port
EMAIL_USERNAME = settings.email_username
EMAIL_PASSWORD = settings.email_password
EMAIL_FROM = settings.email_from
EMAIL_FROM_NAME = settings.email_from_name
EMAIL_USE_TLS = settings.email_use_tls
EMAIL_USE_SSL = settings.email_use_ssl
EMAIL_TIMEOUT_SECONDS = settings.email_timeout_seconds
PASSWORD_MIN_LENGTH = settings.password_min_length
LOGIN_FAILURE_LIMIT = settings.login_failure_limit
LOGIN_LOCKOUT_MINUTES = settings.login_lockout_minutes
RESEND_VERIFICATION_COOLDOWN_SECONDS = settings.resend_verification_cooldown_seconds
MAX_REQUEST_BODY_BYTES = settings.max_request_body_bytes
OMS_BASE_URL = settings.oms_base_url
OMS_API_KEY = settings.oms_api_key
OMS_TIMEOUT_SECONDS = settings.oms_timeout_seconds
OMS_ENABLED = settings.oms_enabled
LLM_ENABLED = settings.llm_enabled
LLM_BASE_URL = settings.llm_base_url
LLM_API_KEY = settings.llm_api_key
LLM_MODEL = settings.llm_model
LLM_TIMEOUT_SECONDS = settings.llm_timeout_seconds
LLM_MAX_TOKENS = settings.llm_max_tokens
MONITORING_WEBHOOK_URL = settings.monitoring_webhook_url
MONITORING_WEBHOOK_TIMEOUT_SECONDS = settings.monitoring_webhook_timeout_seconds
SENTRY_DSN = settings.sentry_dsn
ALERT_ERROR_THRESHOLD = settings.alert_error_threshold


def is_testing() -> bool:
    return settings.is_testing
