"""Read-only staging diagnostics. Never echo database/provider error text."""

import getpass
import json
import re
import sys
import warnings
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.configure_staging_delivery import KEY, validate_existing  # noqa: E402
from scripts.local_staging import DATABASE, REVISION, ROLE, validate_url  # noqa: E402


def error_category(error):
    state = getattr(error, "sqlstate", None)
    # Inspect privately; only a fixed category ever leaves this function.
    message = re.sub(r"\bpostgres(?:ql)?(?:\+psycopg)?://\S+", "[connection]", str(error).lower())
    if state in {"28P01", "28000"} or "password authentication failed" in message:
        return "AUTHENTICATION"
    if state == "42501" or "permission denied" in message:
        return "PERMISSION"
    if state == "55P03" or "lock timeout" in message:
        return "LOCK_TIMEOUT"
    if (
        "could not translate host" in message
        or "getaddrinfo" in message
        or "name or service not known" in message
    ):
        return "DNS"
    if "timeout" in message or "timed out" in message:
        return "CONNECTION_TIMEOUT"
    if "connection refused" in message:
        return "CONNECTION_REFUSED"
    if any(
        word in message
        for word in (
            "ssl error",
            "ssl connection",
            "tls error",
            "tls handshake",
            "ssl handshake",
            "certificate verify",
            "certificate verification",
            "certificate expired",
            "certificate has expired",
            "root certificate",
            "channel binding",
            "does not support ssl",
        )
    ):
        return "TLS"
    if state == "3D000":
        return "DATABASE_NOT_FOUND"
    if state in {"42P01", "42703"}:
        return "SCHEMA"
    if "server closed" in message or "connection reset" in message:
        return "DISCONNECTED"
    return "UNCLASSIFIED"


def diagnose(url, expected_host):
    phase = "URL_VALIDATION"
    try:
        url = validate_url(url)
        if not expected_host or urlparse(url).hostname != expected_host:
            return {"status": "stopped", "phase": phase, "category": "HOST_MISMATCH"}
        import psycopg

        phase = "CONNECT"
        with psycopg.connect(url, connect_timeout=30) as conn:
            phase = "READ_ONLY_TRANSACTION"
            conn.execute("SET TRANSACTION READ ONLY")
            phase = "DATABASE_IDENTITY"
            if conn.execute("SELECT current_database(), current_user").fetchone() != (
                DATABASE,
                ROLE,
            ):
                return {"status": "stopped", "phase": phase, "category": "IDENTITY_MISMATCH"}
            phase = "SCHEMA"
            if conn.execute("SELECT version_num FROM alembic_version").fetchall() != [(REVISION,)]:
                return {"status": "stopped", "phase": phase, "category": "REVISION_MISMATCH"}
            phase = "DELIVERY_READ"
            row = conn.execute(
                "SELECT value, is_public FROM app_config_entries WHERE key=%s", (KEY,)
            ).fetchone()
            try:
                configured = validate_existing(row[0] if row else None)
                route = "already_configured" if configured else "not_configured"
                if row and row[1]:
                    route = "needs_review"
            except ValueError:
                route = "needs_review"
            phase = "PERMISSIONS_READ"
            privileges = conn.execute(
                "SELECT has_table_privilege(current_user, 'app_config_entries', 'INSERT'), "
                "has_table_privilege(current_user, 'app_config_entries', 'UPDATE')"
            ).fetchone()
            result = {
                "status": "ok",
                "connection": "verified",
                "schema": "verified",
                "delivery": route,
                "can_insert_config": bool(privileges[0]),
                "can_update_config": bool(privileges[1]),
                "writes_performed": False,
            }
            phase = "CLOSE_READ_ONLY_TRANSACTION"
        return result
    except Exception as error:
        category = "INVALID_STAGING_URL" if phase == "URL_VALIDATION" else error_category(error)
        return {
            "status": "stopped",
            "phase": phase,
            "category": category,
            "writes_performed": False,
        }


def main():
    if not sys.stdin.isatty() or sys.argv[1:]:
        print("Run interactively without arguments. No command-line credentials accepted.")
        return 1
    try:
        print("READ-ONLY check: no delivery changes, migration, SMS or payment.")
        host = input("Copy STAGING_NEON_HOST from Render (hostname only): ").strip()
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            url = getpass.getpass("DIRECT staging database URL (hidden): ").strip()
        result = diagnose(url, host)
        print(json.dumps(result))
        print("Share only this result, never the connection URL or password.")
        return 0 if result["status"] == "ok" else 1
    except (Exception, KeyboardInterrupt):
        print("Read-only check interrupted. Credentials withheld; no writes performed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
