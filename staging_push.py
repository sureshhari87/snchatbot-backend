"""Fail-closed staging identity and recipient checks. Never migrates."""

import os
import re

from sqlalchemy import text

from push_outbox import DeliveryUnavailableError


def recipients():
    if os.environ.get("APP_ENV") != "staging":
        raise DeliveryUnavailableError("Push sending is staging-only")
    raw = os.environ.get("PUSH_STAGING_ALLOWED_USER_IDS", "")
    if not re.fullmatch(r"[1-9][0-9]*(,[1-9][0-9]*){0,4}", raw):
        raise DeliveryUnavailableError("Explicit staging recipient allowlist required")
    ids = frozenset(map(int, raw.split(",")))
    if any(value > 2147483647 for value in ids):
        raise DeliveryUnavailableError("Invalid staging recipient allowlist")
    return ids


def verify_database(factory):
    """Read-only identity/revision check before credentials or queue writes."""
    recipients()
    try:
        with factory() as db:
            if db.get_bind().dialect.name != "postgresql":
                raise ValueError("PostgreSQL required")
            expected_host = os.environ.get("STAGING_NEON_HOST", "")
            if (
                not expected_host.endswith(".neon.tech")
                or db.get_bind().url.host != expected_host
                or db.get_bind().url.query.get("sslmode") not in {"require", "verify-ca", "verify-full"}
            ):
                raise ValueError("Staging host confirmation required")
            db.execute(text("SET TRANSACTION READ ONLY"))
            identity = db.execute(text("SELECT current_database(), current_user")).one()
            if tuple(identity) != ("snchatbot_staging", "staging_owner"):
                raise ValueError("Wrong database identity")
            versions = db.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
            if versions != ["0019_push_outbox"]:
                raise ValueError("Unverified schema")
    except Exception:
        raise DeliveryUnavailableError("Staging database/schema verification failed") from None
