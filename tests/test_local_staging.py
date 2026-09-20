"""Offline safety checks; never connect to Neon."""

import unittest
from unittest.mock import patch

from scripts.local_staging import (
    clean_environment,
    payment_test_environment,
    staging_auth_environment,
    validate_url,
)

URL = "postgresql://staging_owner:dummy@ep-example.neon.tech/snchatbot_staging?sslmode=require"


class LocalStagingTests(unittest.TestCase):
    def test_auth_configuration_has_no_admin_or_service_credentials(self):
        self.assertEqual(
            staging_auth_environment(),
            {
                "FIREBASE_PROJECT_ID": "sona-jewellery-app",
                "FIREBASE_AUTH_ENABLED": "1",
            },
        )

    def test_payment_test_keys(self):
        env = payment_test_environment("rzp_test_dummy123", "dummy-secret")
        self.assertEqual(env["RAZORPAY_KEY_ID"], "rzp_test_dummy123")
        self.assertEqual(env["RAZORPAY_KEY_SECRET"], "dummy-secret")
        self.assertEqual(len(env), 2)

    def test_payment_rejects_live_or_invalid_keys(self):
        for key in ["rzp_live_dummy123", "", "rzp_test_", "rzp_test_bad key"]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                payment_test_environment(key, "dummy-secret")

    def test_payment_rejects_empty_or_multiline_secret(self):
        for value in ["", "bad secret", "secret\n", "secret\x00"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                payment_test_environment("rzp_test_dummy123", value)

    def test_direct_staging_url(self):
        self.assertEqual(validate_url(URL), URL)

    def test_reject_other_targets(self):
        for value in [
            URL.replace("staging_owner", "neondb_owner"),
            URL.replace("snchatbot_staging", "neondb"),
            URL.replace("ep-example.", "ep-example-pooler."),
            URL.replace("neon.tech", "example.org"),
        ]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_url(value)

    def test_reject_unsafe_connection_options(self):
        for value in [
            URL.replace("require", "disable"),
            URL + "&host=other",
            URL + "&sslmode=disable",
            URL + "#fragment",
            URL.replace(":dummy@", "@"),
        ]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_url(value)

    def test_environment_does_not_copy_secrets(self):
        with patch.dict(
            "os.environ",
            {
                "DATABASE_URL": "production",
                "RAZORPAY_KEY_SECRET": "must-not-copy",
                "GOOGLE_APPLICATION_CREDENTIALS": "must-not-copy",
                "SECRET_KEY": "production",
                "PATH": "test-path",
            },
            clear=True,
        ):
            env = clean_environment(URL)
        self.assertEqual(env["DATABASE_URL"], URL)
        self.assertEqual(env["PATH"], "test-path")
        self.assertNotEqual(env["SECRET_KEY"], "production")
        self.assertNotIn("RAZORPAY_KEY_SECRET", env)
        self.assertNotIn("GOOGLE_APPLICATION_CREDENTIALS", env)
        self.assertEqual(env["RUN_MIGRATIONS_ON_STARTUP"], "0")


if __name__ == "__main__":
    unittest.main()
