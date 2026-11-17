"""Offline compatibility checks using synthetic credentials and a disposable DB."""

# Configuration/provenance guards must run before application imports.
# ruff: noqa: E402

import faulthandler
import hashlib
import importlib
import importlib.metadata
import io
import json
import logging
import os
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = Path(os.environ.get("RECOVERY_VALIDATION_SOURCE", ROOT / "source")).resolve()
SYNTHETIC = "synthetic-test-pinned-review-only-not-a-real-secret"
assert os.environ.get("SECRET_KEY") == SYNTHETIC
assert os.environ.get("DATABASE_URL") == "sqlite://"
assert os.environ.get("TESTING") == "1"
assert not any(SOURCE.glob(".env*"))
manifest = json.loads((ROOT / "SOURCE-MANIFEST.json").read_text())
assert manifest["source_commit"] == "23f1352ebcecdf21df18ab03b5aa5363862be045"
for entry in manifest["files"]:
    path = (SOURCE / entry["path"]).resolve()
    assert path.is_relative_to(SOURCE)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
if os.environ.get("RECOVERY_VALIDATION_REQUIRE_LINUX_311") == "1":
    assert sys.platform == "linux" and sys.version_info[:2] == (3, 11)
sys.path.insert(0, str(SOURCE))


def no_external_network(event, args):
    if event == "socket.connect":
        address = args[1]
        if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1"}:
            raise RuntimeError("External connections are forbidden during review")
    if event == "socket.getaddrinfo" and args[0] not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("External name lookup is forbidden during review")


sys.addaudithook(no_external_network)
log_capture = io.StringIO()
logging.basicConfig(stream=log_capture, level=logging.INFO, force=True)
logging.getLogger("snchatbot").addHandler(logging.StreamHandler(log_capture))
faulthandler.dump_traceback_later(60)
print("review: importing pinned dependencies", file=sys.stderr, flush=True)

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
import recovery_export_api as api
import runtime_recovery as crypto
from models import User

print("review: pinned imports completed", file=sys.stderr, flush=True)
checks = []


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    checks.append(name)
    print("review: " + name, file=sys.stderr, flush=True)


check("loaded_exact_pinned_source", Path(main.__file__).resolve().parent == SOURCE)
check("source_manifest_matched", len(manifest["files"]) == 123)
check("synthetic_test_configuration", main.is_testing() and main.DATABASE_URL == "sqlite://")
check("normal_route_absent", api.EXPORT_PATH not in {r.path for r in main.app.routes})
check("normal_openapi_unmodified", api.EXPORT_PATH not in main.app.openapi()["paths"])
import recovery_app

check("disabled_wrapper_route_absent", api.EXPORT_PATH not in {r.path for r in main.app.routes})
key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
pem = key.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
)
window_id = secrets.token_hex(16)
os.environ.update(
    {
        "RECOVERY_EXPORT_ENABLED": "1",
        "RECOVERY_EXPORT_PUBLIC_KEY": pem.decode(),
        "RECOVERY_EXPORT_RECIPIENT_SHA256": crypto.recipient_fingerprint(pem),
        "RECOVERY_EXPORT_SOURCE_ID": "sureshhari/snchatbot-backend",
        "RECOVERY_EXPORT_WINDOW_ID": window_id,
        "RECOVERY_EXPORT_EXPIRES_AT": datetime.fromtimestamp(
            time.time() + 300, timezone.utc
        ).isoformat(),
    }
)
importlib.reload(recovery_app)
check("explicit_wrapper_enabled", api.EXPORT_PATH in {r.path for r in main.app.routes})
check("enabled_route_hidden_from_schema", api.EXPORT_PATH not in main.app.openapi()["paths"])

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
# Only an in-memory fixture table is created; no Alembic or live DB is used.
User.__table__.create(bind=engine)
factory = sessionmaker(bind=engine)
with factory() as session:
    session.add_all(
        [
            User(
                id=1,
                username="synthetic-admin",
                email="admin@example.invalid",
                hashed_password="unused-synthetic-fixture",
                is_admin=True,
                is_verified=True,
            ),
            User(
                id=2,
                username="synthetic-customer",
                email="customer@example.invalid",
                hashed_password="unused-synthetic-fixture",
                is_admin=False,
                is_verified=True,
            ),
        ]
    )
    session.commit()


def fixture_db():
    with factory() as session:
        yield session


def bearer(email, token_type="access"):
    return {
        "Authorization": "Bearer "
        + jwt.encode(
            {"sub": email, "type": token_type, "exp": int(time.time()) + 300},
            SYNTHETIC,
            algorithm="HS256",
        )
    }


main.app.dependency_overrides[main.get_db] = fixture_db
print("review: fixture database prepared", file=sys.stderr, flush=True)
challenge = secrets.token_hex(32)
admin = bearer("admin@example.invalid")
try:
    print("review: starting in-process client", file=sys.stderr, flush=True)
    with TestClient(main.app, base_url="https://testserver") as client:
        print("review: in-process client started", file=sys.stderr, flush=True)
        check("health_unchanged", client.get("/health").json().get("status") == "ok")
        response = client.post(api.EXPORT_PATH, json={"challenge": challenge})
        check("anonymous_denied", response.status_code == 401)
        response = client.post(
            api.EXPORT_PATH,
            json={"challenge": challenge},
            headers=bearer("customer@example.invalid"),
        )
        check("customer_denied", response.status_code == 403)
        response = client.post(
            api.EXPORT_PATH,
            json={"challenge": challenge},
            headers=bearer("admin@example.invalid", "refresh"),
        )
        check("refresh_token_denied", response.status_code == 401)
        response = client.post(
            api.EXPORT_PATH,
            json={"challenge": challenge},
            headers={**admin, "Origin": "https://example.invalid"},
        )
        check("browser_request_denied", response.status_code == 403)
        response = client.post(
            api.EXPORT_PATH + "?extra=1", json={"challenge": challenge}, headers=admin
        )
        check("query_override_denied", response.status_code == 400)
        response = client.post(
            api.EXPORT_PATH,
            content=b"x" * 257,
            headers={**admin, "Content-Type": "application/json"},
        )
        check("oversized_body_denied", response.status_code == 413)
        response = client.post(api.EXPORT_PATH, json={"challenge": challenge}, headers=admin)
        check("real_isolated_worker_completed", response.status_code == 200)
        check("no_store_response", response.headers.get("cache-control") == "no-store, private")
        check("response_contains_no_plaintext_secret", SYNTHETIC not in response.text)
        header, settings = crypto.decrypt_export(
            response.content,
            key,
            source="sureshhari/snchatbot-backend",
            window=window_id,
            challenge=challenge,
        )
        check(
            "pinned_recipient_matched",
            header["recipient_sha256"] == crypto.recipient_fingerprint(pem),
        )
        check("synthetic_value_recovered_only_in_memory", settings.get("SECRET_KEY") == SYNTHETIC)
        check(
            "unlisted_platform_values_excluded",
            "HF_TOKEN" not in settings and "PATH" not in settings,
        )
        response = client.post(api.EXPORT_PATH, json={"challenge": challenge}, headers=admin)
        check("repeated_export_denied", response.status_code == 409)
    with TestClient(main.app, base_url="http://testserver") as client:
        response = client.post(api.EXPORT_PATH, json={"challenge": challenge}, headers=admin)
        check("unencrypted_transport_denied", response.status_code == 400)
    text = log_capture.getvalue()
    check("audit_event_emitted", "recovery.export_created" in text)
    check("synthetic_secret_not_logged", SYNTHETIC not in text)
finally:
    main.app.dependency_overrides.clear()
    engine.dispose()
    main.engine.dispose()

faulthandler.cancel_dump_traceback_later()
print(
    json.dumps(
        {
            "base_commit": "23f1352ebcecdf21df18ab03b5aa5363862be045",
            "checks_passed": len(checks),
            "checks": checks,
            "database": "disposable in-memory SQLite",
            "credentials": "synthetic only",
            "external_network": "blocked",
            "production_changes": False,
            "platform": sys.platform,
            "python": sys.version.split()[0],
            "linux_python_311_confirmed": sys.platform == "linux"
            and sys.version_info[:2] == (3, 11),
            "packages": {
                name: importlib.metadata.version(name)
                for name in (
                    "cryptography",
                    "fastapi",
                    "starlette",
                    "PyJWT",
                    "pydantic",
                    "SQLAlchemy",
                )
            },
        },
        indent=2,
    )
)
