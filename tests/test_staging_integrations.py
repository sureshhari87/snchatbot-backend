"""Offline validation only; never contact payment or AI providers."""

import unittest
from unittest.mock import patch

from scripts.local_staging import (
    ai_staging_environment,
    clean_environment,
    main,
    webhook_test_environment,
)


class StagingIntegrationTests(unittest.TestCase):
    def test_webhook_secret(self):
        self.assertEqual(webhook_test_environment("x" * 40), {"RAZORPAY_WEBHOOK_SECRET": "x" * 40})

    def test_reject_invalid_webhook_secrets(self):
        for value in ("", "short", "x" * 40 + "\n", "x" * 40 + " "):
            with self.subTest(value=value), self.assertRaises(ValueError):
                webhook_test_environment(value)

    def test_webhook_requires_test_payments_before_any_prompt(self):
        with patch("builtins.input") as prompt, self.assertRaises(ValueError):
            main(test_webhook=True)
        prompt.assert_not_called()

    def test_ai_configuration(self):
        env = ai_staging_environment("https://provider.example/v1", "test-model", "dummy-key")
        self.assertEqual(env["LLM_ENABLED"], "1")
        self.assertEqual(env["LLM_MODEL"], "test-model")
        self.assertEqual(env["LLM_API_KEY"], "dummy-key")

    def test_reject_unsafe_ai_configuration(self):
        for url in (
            "http://provider.example",
            "https://user:pass@provider.example",
            "https://provider.example?key=secret",
            "https://provider.example/#secret",
            "",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                ai_staging_environment(url, "model", "key")
        for model, key in (("", "key"), ("model", ""), ("model", "key\n")):
            with self.subTest(model=model), self.assertRaises(ValueError):
                ai_staging_environment("https://provider.example/v1", model, key)

    def test_no_inherited_ai_or_webhook_credentials(self):
        with patch.dict(
            "os.environ",
            {
                "LLM_API_KEY": "production",
                "OPENAI_API_KEY": "production",
                "RAZORPAY_WEBHOOK_SECRET": "production",
            },
        ):
            env = clean_environment(
                "postgresql://staging_owner:dummy@ep-example.neon.tech/snchatbot_staging?sslmode=require"
            )
        self.assertEqual(env["LLM_ENABLED"], "0")
        for key in ("LLM_API_KEY", "OPENAI_API_KEY", "RAZORPAY_WEBHOOK_SECRET"):
            self.assertNotIn(key, env)


if __name__ == "__main__":
    unittest.main()
