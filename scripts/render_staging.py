"""Fail-closed Render staging launcher. Never migrate or print credentials."""

import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parents[1]
# The container must accept Render's TLS proxy connections, not just loopback.
BIND_HOST = "0.0.0.0"  # nosec B104
sys.path.insert(0, str(REPO))

from scripts.local_staging import (  # noqa: E402
    payment_test_environment,
    preflight,
    sms_staging_environment,
    validate_url,
    webhook_test_environment,
)


def render_environment(source):
    env = dict(source)
    if env.get("APP_ENV") != "staging":
        raise ValueError("APP_ENV must be staging")
    url = validate_url(env.get("DATABASE_URL", ""))
    expected_host = env.get("STAGING_NEON_HOST", "")
    if not expected_host or urlparse(url).hostname != expected_host:
        raise ValueError("Confirm STAGING_NEON_HOST from the Neon staging branch")
    secret = env.get("SECRET_KEY", "")
    if len(secret) < 32 or any(c.isspace() or not c.isprintable() for c in secret):
        raise ValueError("Generate a dedicated staging SECRET_KEY of at least 32 characters")
    hostname = env.get("RENDER_EXTERNAL_HOSTNAME", "")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.onrender\.com", hostname):
        raise ValueError("Render must supply its public service hostname")
    port = int(env.get("PORT", "10000"))
    if not 1024 <= port <= 65535:
        raise ValueError("Invalid service port")
    key_id, key_secret = env.get("RAZORPAY_KEY_ID", ""), env.get("RAZORPAY_KEY_SECRET", "")
    if key_id or key_secret:
        env.update(payment_test_environment(key_id, key_secret))
    if env.get("RAZORPAY_WEBHOOK_SECRET"):
        if not key_id:
            raise ValueError("Configure test payment keys before the test webhook")
        env.update(webhook_test_environment(env["RAZORPAY_WEBHOOK_SECRET"]))
    if env.get("SMS_OTP_ENABLED", "0").lower() in {"1", "true", "yes", "on"}:
        env.update(
            sms_staging_environment(
                env.get("ONHANDSMS_USERNAME", ""),
                env.get("ONHANDSMS_PASSWORD", ""),
                env.get("PHONE_AUTH_PEPPER", ""),
            )
        )
    env.update(
        DATABASE_URL=url,
        APP_DEBUG="0",
        TESTING="0",
        RUN_MIGRATIONS_ON_STARTUP="0",
        RUN_MIGRATIONS_BEFORE_START="0",
        FIRESTORE_COMMERCE_ENABLED="0",
        FIREBASE_AUTH_ENABLED="0",
        ADMIN_BOOTSTRAP_ENABLED="0",
        OMS_ENABLED="0",
        TRUSTED_HOSTS=hostname + ",127.0.0.1,localhost",
        CORS_ORIGINS="https://" + hostname,
        HTTPS_REDIRECT="1",
        PROXY_HEADERS="1",
        FORWARDED_ALLOW_IPS="*",
        HOST=BIND_HOST,
        PORT=str(port),
        WEB_CONCURRENCY="1",
    )
    env.setdefault("SMS_OTP_ENABLED", "0")
    env.setdefault("LLM_ENABLED", "0")
    return env


def verify_database(url):
    if preflight(url) != "ready":
        raise ValueError("Migrate/import the dedicated staging database locally first")
    import psycopg

    with psycopg.connect(url, connect_timeout=10) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        count = conn.execute("SELECT count(*) FROM products").fetchone()[0]
        if count == 0:
            raise ValueError("Staging catalogue is empty; refusing automatic demo seeding")


def main():
    phase = "configuration validation"
    try:
        if (REPO / ".env").exists():
            raise ValueError("Do not deploy a repository .env file")
        env = render_environment(os.environ)
        phase = "read-only staging schema/catalogue check"
        verify_database(env["DATABASE_URL"])
        os.environ.clear()
        os.environ.update(env)
        os.chdir(REPO)
        print("Render staging preflight passed. No migration ran.", flush=True)
    except Exception as error:
        print(
            "Render staging stopped during " + phase + " (" + type(error).__name__ + ").",
            flush=True,
        )
        print(
            "Check dashboard settings privately. Credentials and database errors withheld.",
            flush=True,
        )
        return 1
    # Fixed interpreter/module and validated numeric port; no shell is involved.
    os.execv(  # nosec B606
        sys.executable,
        [
            sys.executable,
            "-m",
            "uvicorn",
            "main:app",
            "--host",
            BIND_HOST,
            "--port",
            env["PORT"],
            "--workers",
            "1",
            "--proxy-headers",
            "--forwarded-allow-ips",
            "*",
        ],
    )


if __name__ == "__main__":
    raise SystemExit(main())
