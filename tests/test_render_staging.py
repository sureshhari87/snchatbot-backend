"""Offline Render guard tests; no database connections or provider requests."""

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from scripts.render_staging import main, render_environment, verify_database

URL = "postgresql://staging_owner:dummy@ep-example.neon.tech/snchatbot_staging?sslmode=require"


class RenderStagingTests(unittest.TestCase):
    def source(self, **updates):
        return dict(
            APP_ENV="staging",
            DATABASE_URL=URL,
            STAGING_NEON_HOST="ep-example.neon.tech",
            RENDER_EXTERNAL_HOSTNAME="snchatbot-staging.onrender.com",
            SECRET_KEY="test-key-" * 5,
            **updates,
        )

    def test_forces_staging_safety_flags(self):
        env = render_environment(
            self.source(
                RUN_MIGRATIONS_ON_STARTUP="1",
                RUN_MIGRATIONS_BEFORE_START="1",
                TESTING="1",
                ADMIN_BOOTSTRAP_ENABLED="1",
                FIRESTORE_COMMERCE_ENABLED="1",
            )
        )
        for key in (
            "RUN_MIGRATIONS_ON_STARTUP",
            "RUN_MIGRATIONS_BEFORE_START",
            "TESTING",
            "ADMIN_BOOTSTRAP_ENABLED",
            "FIRESTORE_COMMERCE_ENABLED",
            "FIREBASE_AUTH_ENABLED",
        ):
            self.assertEqual(env[key], "0")
        self.assertEqual(env["TRUSTED_HOSTS"], "snchatbot-staging.onrender.com,127.0.0.1,localhost")
        self.assertEqual(env["WEB_CONCURRENCY"], "1")
        self.assertEqual(env["LLM_ENABLED"], "0")
        self.assertEqual(env["SMS_OTP_ENABLED"], "0")

    def test_rejects_wrong_targets_and_missing_identity(self):
        for key, value in (
            ("APP_ENV", "production"),
            ("STAGING_NEON_HOST", "other.neon.tech"),
            ("DATABASE_URL", URL.replace("snchatbot_staging", "neondb")),
            ("DATABASE_URL", URL.replace("staging_owner", "neondb_owner")),
            ("DATABASE_URL", URL.replace("ep-example.", "ep-example-pooler.")),
            ("RENDER_EXTERNAL_HOSTNAME", "*"),
            ("SECRET_KEY", "short"),
            ("PORT", "80"),
        ):
            env = self.source()
            env[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                render_environment(env)

    def test_rejects_live_payment_keys(self):
        with self.assertRaises(ValueError):
            render_environment(
                self.source(RAZORPAY_KEY_ID="rzp_live_dummy", RAZORPAY_KEY_SECRET="test-secret")
            )

    def test_admin_cors_is_explicit_and_defaults_closed(self):
        env = render_environment(self.source(CORS_ORIGINS="*"))
        self.assertEqual(env["CORS_ORIGINS"], "https://snchatbot-staging.onrender.com")
        for origin in ("http://localhost:7357", "http://127.0.0.1:7357"):
            env = render_environment(self.source(STAGING_ADMIN_ORIGIN=origin))
            self.assertEqual(
                env["CORS_ORIGINS"], "https://snchatbot-staging.onrender.com," + origin
            )

    def test_admin_cors_rejects_wildcards_and_other_origins(self):
        for origin in (
            "*",
            "null",
            "http://localhost:7357/",
            "http://localhost:7358",
            "https://other.example",
            "http://localhost:7357,https://other.example",
        ):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                render_environment(self.source(STAGING_ADMIN_ORIGIN=origin))

    def test_accepts_test_keys_and_webhook(self):
        env = render_environment(
            self.source(
                RAZORPAY_KEY_ID="rzp_test_dummy",
                RAZORPAY_KEY_SECRET="test-secret",
                RAZORPAY_WEBHOOK_SECRET="test-hook-" * 5,
            )
        )
        self.assertEqual(env["RAZORPAY_KEY_ID"], "rzp_test_dummy")

    def test_rejects_webhook_without_test_keys(self):
        with self.assertRaises(ValueError):
            render_environment(self.source(RAZORPAY_WEBHOOK_SECRET="test-hook-" * 5))

    def test_sms_requires_stable_secret_and_rotated_password(self):
        with self.assertRaises(ValueError):
            render_environment(self.source(SMS_OTP_ENABLED="1"))
        env = render_environment(
            self.source(
                SMS_OTP_ENABLED="1",
                ONHANDSMS_USERNAME="user",
                ONHANDSMS_PASSWORD="test-password",
                PHONE_AUTH_PEPPER="p" * 40,
            )
        )
        self.assertEqual(env["ONHANDSMS_METHOD"], "POST")
        self.assertEqual(env["ONHANDSMS_PAYLOAD_FORMAT"], "form")

    def test_empty_schema_is_not_migrated(self):
        with (
            patch("scripts.render_staging.preflight", return_value="empty"),
            self.assertRaises(ValueError),
        ):
            verify_database(URL)

    def test_empty_catalogue_is_rejected(self):
        with (
            patch("scripts.render_staging.preflight", return_value="ready"),
            patch("psycopg.connect") as connect,
        ):
            conn = connect.return_value.__enter__.return_value
            conn.execute.return_value.fetchone.return_value = (0,)
            with self.assertRaises(ValueError):
                verify_database(URL)
            self.assertEqual(conn.execute.call_args_list[0].args, ("SET TRANSACTION READ ONLY",))

    def test_existing_catalogue_check_is_read_only(self):
        with (
            patch("scripts.render_staging.preflight", return_value="ready"),
            patch("psycopg.connect") as connect,
        ):
            conn = connect.return_value.__enter__.return_value
            conn.execute.return_value.fetchone.return_value = (24,)
            verify_database(URL)
            self.assertEqual(
                [call.args[0] for call in conn.execute.call_args_list],
                ["SET TRANSACTION READ ONLY", "SELECT count(*) FROM products"],
            )

    def test_errors_never_print_credentials_or_start_server(self):
        output = io.StringIO()
        with (
            patch("scripts.render_staging.render_environment", side_effect=ValueError(URL)),
            patch("os.execv") as execute,
            redirect_stdout(output),
        ):
            self.assertEqual(main(), 1)
        execute.assert_not_called()
        self.assertNotIn(URL, output.getvalue())
        self.assertNotIn("dummy", output.getvalue())


if __name__ == "__main__":
    unittest.main()
