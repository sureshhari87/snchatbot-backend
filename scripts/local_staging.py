"""Operator-run local staging. No production credentials, settings or data are used."""

import getpass
import json
import os
import secrets
import socket
import subprocess  # nosec B404 - local executables, shell=False, output suppressed
import sys
import tempfile
import time
import warnings
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import urlopen

REPO = Path(__file__).resolve().parents[1]
DATABASE = "snchatbot_staging"
ROLE = "staging_owner"
REVISION = "0014_catalogue_source"


def validate_url(value):
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("Use a direct TLS PostgreSQL connection URL")
    host = parsed.hostname or ""
    if not host.endswith(".neon.tech") or "-pooler" in host or parsed.port not in {None, 5432}:
        raise ValueError("Use the direct, non-pooled Neon endpoint")
    if unquote(parsed.username or "") != ROLE or unquote(parsed.path) != "/" + DATABASE:
        raise ValueError("Only staging_owner / snchatbot_staging is accepted")
    if not parsed.password or parsed.fragment:
        raise ValueError("The staging connection URL is incomplete")
    query = parse_qs(parsed.query, keep_blank_values=True)
    if set(query) - {"sslmode", "channel_binding"} or any(len(v) != 1 for v in query.values()):
        raise ValueError("Unexpected connection options")
    if query.get("sslmode", [""])[0] not in {"require", "verify-ca", "verify-full"}:
        raise ValueError("TLS is required")
    return value.strip()


def clean_environment(url):
    allowed = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "APPDATA",
        "LOCALAPPDATA",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env.update(
        DATABASE_URL=validate_url(url),
        APP_ENV="staging",
        TESTING="0",
        HTTPS_REDIRECT="0",
        CORS_ORIGINS="http://127.0.0.1:8001",
        RUN_MIGRATIONS_ON_STARTUP="0",
        FIRESTORE_COMMERCE_ENABLED="0",
        FIREBASE_AUTH_ENABLED="0",
        LLM_ENABLED="0",
        ADMIN_BOOTSTRAP_ENABLED="0",
        PYTHONUNBUFFERED="1",
    )
    env["SECRET_KEY"] = secrets.token_urlsafe(48)
    return env


def preflight(url):
    import psycopg

    with psycopg.connect(validate_url(url), connect_timeout=10) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        db, user = conn.execute("SELECT current_database(), current_user").fetchone()
        if (db, user) != (DATABASE, ROLE):
            raise ValueError("Unexpected database identity")
        tables = {
            row[0]
            for row in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        }
        if not tables:
            return "empty"
        if "alembic_version" not in tables:
            raise ValueError("Existing database needs manual schema review")
        versions = conn.execute("SELECT version_num FROM alembic_version").fetchall()
        if versions != [(REVISION,)]:
            raise ValueError("Unexpected schema revision; no automatic stamp or migration allowed")
        return "ready"


def migrate_child():
    validate_url(os.environ.get("DATABASE_URL", ""))
    sys.path.insert(0, str(REPO))
    from alembic.config import Config

    from alembic import command

    config = Config(str(REPO / "alembic.ini"))
    config.set_main_option("script_location", str(REPO / "alembic"))
    config.set_main_option("prepend_sys_path", str(REPO))
    command.upgrade(config, "head")


def payment_test_environment(key_id, key_secret):
    if not key_id.startswith("rzp_test_") or not key_id[len("rzp_test_") :].isalnum():
        raise ValueError("Only a Razorpay test key ID is accepted")
    if not key_secret or any(c.isspace() or not c.isprintable() for c in key_secret):
        raise ValueError("A nonempty Razorpay test key secret is required")
    return {"RAZORPAY_KEY_ID": key_id, "RAZORPAY_KEY_SECRET": key_secret}


def staging_auth_environment():
    # Public project identifier only; users and permissions remain in staging SQL.
    return {"FIREBASE_PROJECT_ID": "sona-jewellery-app", "FIREBASE_AUTH_ENABLED": "1"}


def sms_staging_environment(username, password, pepper):
    if not username or not password or any(not c.isprintable() for c in username + password):
        raise ValueError("SMS credentials must be nonempty single-line values")
    if username == password:
        raise ValueError("Replace the exposed SMS password before using staging")
    if len(pepper) < 32 or any(c.isspace() or not c.isprintable() for c in pepper):
        raise ValueError("Use a reusable random staging phone secret of at least 32 characters")
    return {
        "SMS_OTP_ENABLED": "1",
        "SMS_PROVIDER": "onhand",
        "ONHANDSMS_API_URL": "https://api.onhandsms.com/api/v2/sendsms",
        "ONHANDSMS_USERNAME": username,
        "ONHANDSMS_PASSWORD": password,
        "ONHANDSMS_SENDER_ID": "SONAJS",
        "ONHANDSMS_TEMPLATE_ID": "1707173372695978586",
        "ONHANDSMS_METHOD": "POST",
        "ONHANDSMS_PAYLOAD_FORMAT": "form",
        "ONHANDSMS_PAYLOAD_TEMPLATE": json.dumps(
            {
                "username": "{username}",
                "password": "{password}",
                "senderid": "{sender_id}",
                "number": "{phone_local}",
                "istamil": "0",
                "dlttemplateid": "{template_id}",
                "message": "{message}",
            }
        ),
        "ONHANDSMS_MESSAGE_TEMPLATE": (
            "Dear User,\nYour mobile verification code is {otp}\n"
            "Please don't share this.\nThanks,\nSONA JEWELLERS"
        ),
        "PHONE_AUTH_PEPPER": pepper,
    }


def main(test_payments=False, firebase_auth=False, onhand_sms=False):
    if not sys.stdin.isatty():
        raise ValueError("Run in an interactive terminal for hidden input")
    print("Select Neon branch staging, database snchatbot_staging, role staging_owner.")
    print("Use its DIRECT URL with pooling OFF. Production must not be selected.")
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        url = validate_url(getpass.getpass("Paste staging connection URL (hidden): "))
    print("Validated staging database and role. Branch selection must be confirmed in Neon.")
    print("This will migrate an empty staging database and run a loopback-only backend.")
    print("Startup may create demo products; these are not your real inventory.")
    if input("Type START_STAGING to continue: ").strip() != "START_STAGING":
        print("Cancelled. No database changes made.")
        return
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 8001))
    state = preflight(url)
    env = clean_environment(url)
    # Empty working directory prevents config.py from loading the repository .env.
    # No credentials or subprocess logs are written to disk.
    with tempfile.TemporaryDirectory(prefix="snchatbot-staging-") as working:
        if state == "empty":
            print("Migrating the dedicated staging database...")
            subprocess.run(  # nosec B603 - fixed local script, no shell
                [sys.executable, str(Path(__file__).resolve()), "--migrate-child"],
                cwd=working,
                env=env,
                check=True,
                timeout=180,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        if preflight(url) != "ready":
            raise RuntimeError("Staging schema verification failed")
        print("Staging schema verified: " + REVISION)
        if firebase_auth:
            env.update(staging_auth_environment())
            print("Firebase login enabled for sona-jewellery-app; no admin grants are added.")
        if test_payments:
            print("Enter TEST keys only. Neither value is saved or displayed.")
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                key_id = getpass.getpass("Razorpay TEST Key ID (hidden): ")
                key_secret = getpass.getpass("Razorpay TEST Key Secret (hidden): ")
            env.update(payment_test_environment(key_id, key_secret))
        if onhand_sms:
            print("OnhandSMS sends REAL SMS and may use paid credits, even in staging.")
            print("Use the rotated password. Store one random 32+ character phone secret")
            print("in your password manager and reuse it on EVERY staging restart.")
            if input("Type ENABLE_SMS to enable sending from the app: ").strip() != "ENABLE_SMS":
                print("SMS setup cancelled; backend was not started.")
                return
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                username = getpass.getpass("OnhandSMS username (hidden): ")
                password = getpass.getpass("New OnhandSMS password (hidden): ")
                pepper = getpass.getpass("Reusable staging phone secret (hidden): ")
            env.update(sms_staging_environment(username, password, pepper))
        process = subprocess.Popen(  # nosec B603 - fixed local module, no shell
            [
                sys.executable,
                "-m",
                "uvicorn",
                "main:app",
                "--app-dir",
                str(REPO),
                "--host",
                "127.0.0.1",
                "--port",
                "8001",
                "--workers",
                "1",
                "--no-proxy-headers",
            ],
            cwd=working,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(60):
                if process.poll() is not None:
                    raise RuntimeError("Local backend exited before readiness")
                try:
                    with urlopen("http://127.0.0.1:8001/ready", timeout=2) as response:  # nosec B310 - fixed loopback URL
                        payload = json.load(response)
                        database = payload.get("dependencies", {}).get("database", {})
                        if (
                            response.status == 200
                            and payload.get("status") == "ok"
                            and database.get("status") == "ok"
                        ):
                            break
                except (OSError, ValueError):
                    pass
                time.sleep(1)
            else:
                raise RuntimeError("Local staging readiness timed out")
            if onhand_sms:
                sms = payload.get("dependencies", {}).get("sms_otp", {})
                if sms.get("status") != "configured" or sms.get("provider") != "onhand":
                    raise RuntimeError("Backend did not load OnhandSMS settings")
                print("ONHANDSMS CONFIGURED: HTTPS POST. SMS delivery not yet verified.")
            print("STAGING READY: http://127.0.0.1:8001/ready")
            if firebase_auth:
                print(
                    "Customer Firebase login configured; actual sign-in still needs verification."
                )
            print("Keep this terminal open. Ctrl+C stops only this local backend.")
            if test_payments:
                payment = payload.get("dependencies", {}).get("razorpay", {})
                if not payment.get("checkout_api_configured"):
                    raise RuntimeError("Backend did not load test payment configuration")
                print("RAZORPAY TEST KEYS LOADED. Provider authentication is not yet verified.")
                print("Webhook, login and delivery checks remain separate prerequisites.")
            else:
                print("Payment credentials have not been configured.")
            print("No production changes.")
            process.wait()
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["--migrate-child"]:
            migrate_child()
        elif sys.argv[1:] and set(sys.argv[1:]) <= {
            "--test-payments",
            "--firebase-auth",
            "--onhand-sms",
        }:
            main(
                test_payments="--test-payments" in sys.argv,
                firebase_auth="--firebase-auth" in sys.argv,
                onhand_sms="--onhand-sms" in sys.argv,
            )
        elif sys.argv[1:]:
            raise ValueError("No command-line credentials accepted")
        else:
            main()
    except KeyboardInterrupt:
        print("Local staging stopped.")
    except Exception as exc:
        print("Staging setup stopped (" + type(exc).__name__ + "). No production changes.")
        print("Do not share the URL. Report which step stopped; staging may be partially migrated.")
        sys.exit(1)
