"""One-time, operator-confirmed exact-PIN configuration; never targets production."""

import getpass
import json
import sys
import warnings
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.local_staging import DATABASE, ROLE, preflight, validate_url  # noqa: E402

KEY = "commerce.delivery_routes"
PIN = "625009"


def approved_routes():
    return [
        {
            "pin_prefix": PIN,
            "active": True,
            "method": "standard",
            "handling_days": 2,
            "minimum_transit_days": 5,
            "maximum_transit_days": 13,
            "working_weekdays": [0, 1, 2, 3, 4, 5],
            "cutoff": "16:00",
            "holidays": [],
            "fulfilment_group": "staging-test-625009",
        }
    ]


def validate_existing(value):
    routes = json.loads(value) if value is not None else []
    if routes not in ([], approved_routes()):
        raise ValueError("Existing delivery rules need review; nothing will be overwritten")
    return routes == approved_routes()


def configure(url, expected_host):
    import psycopg

    url = validate_url(url)
    if not expected_host or urlparse(url).hostname != expected_host:
        raise ValueError("Expected hostname must match Render's staging hostname setting")
    if preflight(url) != "ready":
        raise ValueError("Staging schema must already be migrated")
    with psycopg.connect(url, connect_timeout=10) as conn:
        identity = conn.execute("SELECT current_database(), current_user").fetchone()
        if identity != (DATABASE, ROLE):
            raise ValueError("Unexpected database identity")
        conn.execute("SET LOCAL lock_timeout = '5s'")
        conn.execute("SELECT pg_advisory_xact_lock(625009)")
        row = conn.execute(
            "SELECT value, is_public FROM app_config_entries WHERE key = %s FOR UPDATE", (KEY,)
        ).fetchone()
        if row and row[1]:
            raise ValueError("Existing public configuration needs review")
        if validate_existing(row[0] if row else None):
            return "already_configured"
        payload = json.dumps(approved_routes())
        description = "Owner-approved STAGING ONLY: exact PIN 625009, estimated 7-15 business days."
        if row:
            conn.execute(
                "UPDATE app_config_entries SET value=%s, description=%s, updated_at=CURRENT_TIMESTAMP WHERE key=%s",
                (payload, description, KEY),
            )
        else:
            conn.execute(
                "INSERT INTO app_config_entries (key,value,description,is_public,created_at,updated_at) "
                "VALUES (%s,%s,%s,false,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                (KEY, payload, description),
            )
        saved = conn.execute("SELECT value FROM app_config_entries WHERE key=%s", (KEY,)).fetchone()
        if json.loads(saved[0]) != approved_routes():
            raise RuntimeError("Delivery configuration verification failed")
    return "configured"


def main():
    if not sys.stdin.isatty():
        print("Run this helper in your own interactive terminal for hidden input.")
        return 1
    try:
        print("Select Neon branch STAGING, database snchatbot_staging, role staging_owner.")
        print("This enables ONLY test PIN 625009; no schema migration or payment is performed.")
        host = input("Copy STAGING_NEON_HOST from Render (hostname only): ").strip()
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            url = getpass.getpass("DIRECT staging database URL (hidden): ").strip()
        if (
            input("Type CONFIGURE_625009 to enable this staging test route: ").strip()
            != "CONFIGURE_625009"
        ):
            print("Cancelled. Nothing changed.")
            return 1
        result = configure(url, host)
        print(json.dumps({"delivery": result, "pin": PIN, "production_modified": False}))
        print("Test the delivery quote in the staging app. No Render redeployment is needed.")
        return 0
    except (Exception, KeyboardInterrupt) as error:
        print("Staging delivery setup stopped (" + type(error).__name__ + "). Details withheld.")
        print("Do not share the URL. Inspect existing rules/connection privately before retrying.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
