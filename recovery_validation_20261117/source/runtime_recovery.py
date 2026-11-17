"""Bounded encrypted runtime exports. No application or monitoring imports."""

import base64
import hashlib
import json
import os
import re
import sys
import time
from collections.abc import Mapping
from pathlib import Path

import cryptography
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

FORMAT = "sona-runtime-recovery-v1"
ALGORITHM = "RSA-OAEP-SHA256+A256GCM"
MAX_ENVELOPE_BYTES = 1024 * 1024
MAX_SETTINGS_BYTES = 256 * 1024
MAX_VALUE_BYTES = 64 * 1024
MAX_WINDOW_SECONDS = 1800
SOURCE_PATTERN = re.compile(r"[A-Za-z0-9_.-]{1,64}/[A-Za-z0-9_.-]{1,96}\Z")
WINDOW_PATTERN = re.compile(r"[0-9a-f]{32}\Z")
CHALLENGE_PATTERN = re.compile(r"[0-9a-f]{64}\Z")

# Deliberately audited names, not a prefix match or a dump of os.environ.
SETTING_NAMES = frozenset(
    """
    ACCESS_TOKEN_EXPIRE_MINUTES ADMIN_BOOTSTRAP_EMAIL ADMIN_BOOTSTRAP_ENABLED
    ADMIN_BOOTSTRAP_PASSWORD ADMIN_BOOTSTRAP_USERNAME AI_REQUESTS_PER_MINUTE
    ALERT_ERROR_THRESHOLD ALGORITHM ALLOWED_ORIGINS APP_DEBUG APP_ENV APP_MODULE
    BILLING_B2C_CONTROLS_ENABLED BILLING_DOCUMENTS_ENABLED BILLING_EINVOICE_APPLICABILITY
    BILLING_EINVOICE_REVIEW_REFERENCE BILLING_IRN_REQUIRED BILLING_ISSUER_JSON
    BILLING_REWARD_TAX_TREATMENT BILLING_SALES_ENABLED BILLING_SALES_SERIES_START_CONFIRMED
    BILLING_SIGNATORY_NAME BILLING_TAX_RULES_REVIEW_REFERENCE BILLING_VERIFICATION_BASE_URL
    BILLING_VERIFICATION_ENABLED BILLING_VERIFICATION_KEY BREVO_API_KEY BREVO_API_URL
    CONCEPT_REQUESTS_PER_MINUTE CORS_ALLOW_CREDENTIALS CORS_ORIGINS CUSTOMER_PROFILE_ENABLED
    CUSTOM_DESIGNS_ENABLED CUSTOM_DESIGN_BALANCE_ENABLED CUSTOM_DESIGN_BALANCE_RAZORPAY_ENABLED
    DATABASE_URL DELIVERY_DRAFTS_ENABLED EMAIL_FROM EMAIL_FROM_NAME EMAIL_HOST EMAIL_PASSWORD
    EMAIL_PORT EMAIL_PROVIDER EMAIL_TIMEOUT_SECONDS EMAIL_USERNAME EMAIL_USE_SSL EMAIL_USE_TLS
    EMAIL_VERIFICATION_EXPIRE_MINUTES ENVIRONMENT FINANCIAL_REPORTING_ENABLED
    FIREBASE_AUTH_ENABLED FIREBASE_CERTS_URL FIREBASE_PROJECT_ID FIREBASE_REQUIRE_EMAIL_VERIFIED
    FIREBASE_SERVICE_ACCOUNT_JSON FIREBASE_SERVICE_ACCOUNT_PATH FIRESTORE_COMMERCE_ENABLED
    FIRESTORE_COUPONS_COLLECTION FIRESTORE_GIFT_VOUCHERS_COLLECTION
    FIRESTORE_GOLD_RATES_COLLECTION FIRESTORE_GOLD_RATES_DOCUMENT FIRESTORE_ORDERS_COLLECTION
    FIRESTORE_PAYMENT_ATTEMPTS_COLLECTION FIRESTORE_PRODUCTS_COLLECTION FIRESTORE_USERS_COLLECTION
    FORWARDED_ALLOW_IPS FRONTEND_RESET_URL FRONTEND_VERIFY_URL GOOGLE_APPLICATION_CREDENTIALS
    HOST HTTPS_REDIRECT INBOX_BROADCAST_ENABLED JWT_ALGORITHM LLM_API_KEY LLM_BASE_URL
    LLM_ENABLED LLM_MAX_TOKENS LLM_MODEL LLM_TIMEOUT_SECONDS LOGIN_FAILURE_LIMIT
    LOGIN_LOCKOUT_MINUTES MAX_CATALOG_ITEMS MAX_REQUEST_BODY_BYTES
    MONITORING_WEBHOOK_TIMEOUT_SECONDS MONITORING_WEBHOOK_URL NOTIFICATION_RETENTION_ENABLED
    OMS_API_KEY OMS_BASE_URL OMS_ENABLED OMS_TIMEOUT_SECONDS ONHANDSMS_API_KEY ONHANDSMS_API_URL
    ONHANDSMS_MESSAGE_TEMPLATE ONHANDSMS_METHOD ONHANDSMS_PASSWORD ONHANDSMS_PAYLOAD_FORMAT
    ONHANDSMS_PAYLOAD_TEMPLATE ONHANDSMS_ROUTE ONHANDSMS_SENDER_ID ONHANDSMS_TEMPLATE_ID
    ONHANDSMS_USERNAME OPENAI_API_KEY OPENAI_BASE_URL OPENAI_IMAGE_MODEL
    OPENAI_MAX_OUTPUT_TOKENS OPENAI_MODEL OPENAI_REASONING_EFFORT ORDER_CANCELLATIONS_ENABLED
    ORDER_REFUND_EXECUTION_ENABLED OTP_EXPIRE_MINUTES OTP_MAX_ATTEMPTS
    OTP_RESEND_COOLDOWN_SECONDS PASSWORD_MIN_LENGTH PASSWORD_RESET_EXPIRE_MINUTES
    PHONE_AUTH_PEPPER PORT PROXY_HEADERS PURCHASE_IDENTITY_ENABLED PURCHASE_IDENTITY_ENCRYPTION_KEY
    PURCHASE_REWARD_RUPEES_PER_POINT PUSH_DELIVERY_ENABLED PUSH_DEVICE_REGISTRATION_ENABLED
    PUSH_FCM_PROJECT_ID PUSH_FCM_SERVICE_ACCOUNT_JSON PUSH_OUTBOX_ENABLED PUSH_STAGING_ALLOWED_USER_IDS
    RAZORPAY_KEY_ID RAZORPAY_KEY_SECRET RAZORPAY_TEST_NULL_DISPUTES_COMPAT RAZORPAY_WEBHOOK_SECRET
    REFERRALS_ENABLED REFERRAL_REWARD_POINTS REFRESH_TOKEN_EXPIRE_DAYS RESEND_API_KEY RESEND_API_URL
    RESEND_VERIFICATION_COOLDOWN_SECONDS REVIEW_PHOTOS_ENABLED REWARDS_VOUCHERS_ENABLED
    REWARD_POINT_VALUE_RUPEES RUN_MIGRATIONS_BEFORE_START RUN_MIGRATIONS_ON_STARTUP SAVINGS_ENABLED
    SECRET_KEY SENTRY_DSN SENTRY_PROFILES_SAMPLE_RATE SENTRY_RELEASE SENTRY_SEND_DEFAULT_PII
    SENTRY_TRACES_SAMPLE_RATE SMS_HTTP_USER_AGENT SMS_OTP_ENABLED SMS_PROVIDER SMS_TIMEOUT_SECONDS
    STAGING_NEON_HOST TESTING TRUSTED_HOSTS UVICORN_LOG_LEVEL VOUCHER_CODE_ENCRYPTION_KEY
    WEB_CONCURRENCY
    """.split()
)
HEADER_NAMES = frozenset(
    {"format", "algorithm", "recipient_sha256", "source_id", "window_id", "challenge", "created_at"}
)
ENVELOPE_NAMES = HEADER_NAMES | {"nonce", "wrapped_key", "ciphertext"}


def worker_command() -> list[str]:
    # Explicitly load only the trusted dependency directory, without .pth/site hooks.
    bootstrap = (
        "import runpy\nimport sys\n"
        "sys.path.insert(0, sys.argv.pop(1))\n"
        "runpy.run_path(sys.argv.pop(1), run_name='__main__')"
    )
    return [
        sys.executable,
        "-I",
        "-S",
        "-B",
        "-c",
        bootstrap,
        str(Path(cryptography.__file__).resolve().parents[1]),
        str(Path(__file__).resolve()),
        "--worker",
    ]


class RecoveryError(ValueError):
    def __init__(self):
        super().__init__("Recovery data could not be validated")


def encode_json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RecoveryError()
        result[key] = value
    return result


def _invalid_constant(value):
    raise RecoveryError()


def decode_json(data: bytes, limit: int = MAX_ENVELOPE_BYTES):
    if not isinstance(data, bytes) or len(data) > limit:
        raise RecoveryError()
    try:
        return json.loads(data, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise RecoveryError() from None


def public_key(pem: bytes) -> rsa.RSAPublicKey:
    if not isinstance(pem, bytes) or len(pem) > 8192:
        raise RecoveryError()
    try:
        key = serialization.load_pem_public_key(pem)
        if not isinstance(key, rsa.RSAPublicKey) or key.key_size not in {3072, 4096}:
            raise RecoveryError()
        if key.public_numbers().e != 65537:
            raise RecoveryError()
        return key
    except (ValueError, TypeError):
        raise RecoveryError() from None


def recipient_fingerprint(pem: bytes) -> str:
    key = public_key(pem)
    der = key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return hashlib.sha256(der).hexdigest()


def _oaep():
    return padding.OAEP(
        mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=FORMAT.encode()
    )


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(value) -> bytes:
    if not isinstance(value, str):
        raise RecoveryError()
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, UnicodeError):
        raise RecoveryError() from None


def validate_envelope(data: bytes, *, recipient=None, source=None, window=None, challenge=None):
    value = decode_json(data)
    if not isinstance(value, dict) or set(value) != ENVELOPE_NAMES:
        raise RecoveryError()
    if value["format"] != FORMAT or value["algorithm"] != ALGORITHM:
        raise RecoveryError()
    for name, pattern in (
        ("recipient_sha256", CHALLENGE_PATTERN),
        ("source_id", SOURCE_PATTERN),
        ("window_id", WINDOW_PATTERN),
        ("challenge", CHALLENGE_PATTERN),
    ):
        if not isinstance(value[name], str) or not pattern.fullmatch(value[name]):
            raise RecoveryError()
    if type(value["created_at"]) is not int or not 0 < value["created_at"] < 2**53:
        raise RecoveryError()
    for name, expected in (
        ("recipient_sha256", recipient),
        ("source_id", source),
        ("window_id", window),
        ("challenge", challenge),
    ):
        if expected is not None and value[name] != expected:
            raise RecoveryError()
    if len(_unb64(value["nonce"])) != 12 or len(_unb64(value["wrapped_key"])) not in {384, 512}:
        raise RecoveryError()
    if not 16 <= len(_unb64(value["ciphertext"])) <= MAX_SETTINGS_BYTES * 2:
        raise RecoveryError()
    return value


def capture_settings(environment: Mapping[str, str]) -> dict[str, str]:
    settings = {}
    total = 0
    for name in sorted(SETTING_NAMES):
        if name not in environment:
            continue
        value = environment[name]
        if not isinstance(value, str):
            raise RecoveryError()
        size = len(value.encode("utf-8"))
        total += size
        if size > MAX_VALUE_BYTES or total > MAX_SETTINGS_BYTES:
            raise RecoveryError()
        settings[name] = value
    return settings


def create_export(environment, pem: bytes, *, source: str, window: str, challenge: str) -> bytes:
    key = public_key(pem)
    header = {
        "format": FORMAT,
        "algorithm": ALGORITHM,
        "recipient_sha256": recipient_fingerprint(pem),
        "source_id": source,
        "window_id": window,
        "challenge": challenge,
        "created_at": int(time.time()),
    }
    settings = capture_settings(environment)
    payload = encode_json(
        {
            "format": "sona-runtime-settings-v1",
            "settings": settings,
            "absent_names": sorted(SETTING_NAMES - settings.keys()),
        }
    )
    if len(payload) > MAX_SETTINGS_BYTES * 2:
        raise RecoveryError()
    secret = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    envelope = {
        **header,
        "nonce": _b64(nonce),
        "wrapped_key": _b64(key.encrypt(secret, _oaep())),
        "ciphertext": _b64(AESGCM(secret).encrypt(nonce, payload, encode_json(header))),
    }
    result = encode_json(envelope)
    validate_envelope(result)
    return result


def decrypt_export(data: bytes, key: rsa.RSAPrivateKey, **expected):
    try:
        pem = key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        envelope = validate_envelope(data, recipient=recipient_fingerprint(pem), **expected)
        header = {name: envelope[name] for name in HEADER_NAMES}
        secret = key.decrypt(_unb64(envelope["wrapped_key"]), _oaep())
        if len(secret) != 32:
            raise RecoveryError()
        payload = decode_json(
            AESGCM(secret).decrypt(
                _unb64(envelope["nonce"]), _unb64(envelope["ciphertext"]), encode_json(header)
            )
        )
        if not isinstance(payload, dict) or set(payload) != {"format", "settings", "absent_names"}:
            raise RecoveryError()
        if payload["format"] != "sona-runtime-settings-v1" or not isinstance(
            payload["settings"], dict
        ):
            raise RecoveryError()
        settings = payload["settings"]
        absent = payload["absent_names"]
        if not set(settings) <= SETTING_NAMES or capture_settings(settings) != settings:
            raise RecoveryError()
        if not isinstance(absent, list) or absent != sorted(SETTING_NAMES - settings.keys()):
            raise RecoveryError()
        return header, settings
    except Exception:
        raise RecoveryError() from None


def worker_main() -> int:
    # This process never imports the app, SDK monitoring, a database or an HTTP client.
    try:
        if os.name == "posix":
            import resource

            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        control = decode_json(sys.stdin.buffer.read(16 * 1024 + 1), 16 * 1024)
        if not isinstance(control, dict) or set(control) != {
            "public_key",
            "recipient",
            "source",
            "window",
            "challenge",
        }:
            return 78
        pem = control["public_key"].encode()
        if recipient_fingerprint(pem) != control["recipient"]:
            return 78
        if os.environ.get("SPACE_ID", control["source"]) != control["source"]:
            return 78
        result = create_export(
            os.environ,
            pem,
            source=control["source"],
            window=control["window"],
            challenge=control["challenge"],
        )
        sys.stdout.buffer.write(result)
        return 0
    except Exception:
        return 78


if __name__ == "__main__":
    raise SystemExit(worker_main() if sys.argv[1:] == ["--worker"] else 2)
