import os
from pathlib import Path


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


load_env_file()

APP_DEBUG = get_bool("APP_DEBUG", True)
DATABASE_URL = get_str("DATABASE_URL", "sqlite:///./jewellery.db")

SECRET_KEY = get_str("SECRET_KEY", "change-this-to-a-long-random-secret")
ALGORITHM = get_str("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = get_int("ACCESS_TOKEN_EXPIRE_MINUTES", 30)
REFRESH_TOKEN_EXPIRE_DAYS = get_int("REFRESH_TOKEN_EXPIRE_DAYS", 7)
PASSWORD_RESET_EXPIRE_MINUTES = get_int("PASSWORD_RESET_EXPIRE_MINUTES", 15)
EMAIL_VERIFICATION_EXPIRE_MINUTES = get_int("EMAIL_VERIFICATION_EXPIRE_MINUTES", 30)

FRONTEND_RESET_URL = get_str("FRONTEND_RESET_URL", "http://localhost:3000/reset-password")
FRONTEND_VERIFY_URL = get_str("FRONTEND_VERIFY_URL", "http://localhost:3000/verify-email")
CORS_ORIGINS = get_list("CORS_ORIGINS", ["*"])
CORS_ALLOW_CREDENTIALS = get_bool("CORS_ALLOW_CREDENTIALS", True)
RUN_MIGRATIONS_ON_STARTUP = get_bool("RUN_MIGRATIONS_ON_STARTUP", True)

EMAIL_HOST = get_str("EMAIL_HOST")
EMAIL_PORT = get_int("EMAIL_PORT", 587)
EMAIL_USERNAME = get_str("EMAIL_USERNAME")
EMAIL_PASSWORD = get_str("EMAIL_PASSWORD")
EMAIL_FROM = get_str("EMAIL_FROM", EMAIL_USERNAME or "no-reply@localhost")
EMAIL_FROM_NAME = get_str("EMAIL_FROM_NAME", "Jewellery Chat")
EMAIL_USE_TLS = get_bool("EMAIL_USE_TLS", True)
EMAIL_USE_SSL = get_bool("EMAIL_USE_SSL", False)
EMAIL_TIMEOUT_SECONDS = get_int("EMAIL_TIMEOUT_SECONDS", 10)


def is_testing() -> bool:
    return get_bool("TESTING", False)
