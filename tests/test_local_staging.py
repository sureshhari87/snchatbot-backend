"""Offline safety checks; never connect to Neon."""

import unittest
from unittest.mock import patch

from scripts.local_staging import clean_environment, validate_url

URL = "postgresql://staging_owner:dummy@ep-example.neon.tech/snchatbot_staging?sslmode=require"


class LocalStagingTests(unittest.TestCase):
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
