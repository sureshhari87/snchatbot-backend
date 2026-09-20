"""Offline configuration tests: no provider requests or SMS sends."""

import json
import unittest

from scripts.local_staging import clean_environment, sms_staging_environment


class OnhandStagingTests(unittest.TestCase):
    def test_https_post_form_mapping(self):
        env = sms_staging_environment("dummy-user", "dummy-password", "x" * 40)
        self.assertEqual(env["SMS_OTP_ENABLED"], "1")
        self.assertEqual(env["SMS_PROVIDER"], "onhand")
        self.assertEqual(env["ONHANDSMS_API_URL"], "https://api.onhandsms.com/api/v2/sendsms")
        self.assertEqual(env["ONHANDSMS_METHOD"], "POST")
        self.assertEqual(env["ONHANDSMS_PAYLOAD_FORMAT"], "form")
        payload = json.loads(env["ONHANDSMS_PAYLOAD_TEMPLATE"])
        self.assertEqual(
            set(payload),
            {
                "username",
                "password",
                "senderid",
                "number",
                "istamil",
                "dlttemplateid",
                "message",
            },
        )
        self.assertEqual(payload["number"], "{phone_local}")
        self.assertEqual(payload["password"], "{password}")
        self.assertEqual(env["ONHANDSMS_SENDER_ID"], "SONAJS")
        self.assertEqual(env["ONHANDSMS_TEMPLATE_ID"], "1707173372695978586")

    def test_approved_message(self):
        env = sms_staging_environment("dummy-user", "dummy-password", "x" * 40)
        self.assertEqual(
            env["ONHANDSMS_MESSAGE_TEMPLATE"].splitlines(),
            [
                "Dear User,",
                "Your mobile verification code is {otp}",
                "Please don't share this.",
                "Thanks,",
                "SONA JEWELLERS",
            ],
        )

    def test_reject_missing_credentials_reused_password_and_weak_pepper(self):
        for values in [
            ("", "password", "x" * 40),
            ("user", "", "x" * 40),
            ("same", "same", "x" * 40),
            ("user", "password", "short"),
        ]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                sms_staging_environment(*values)

    def test_phone_identity_secret_survives_launcher_jwt_rotation(self):
        url = "postgresql://staging_owner:dummy@ep-example.neon.tech/snchatbot_staging?sslmode=require"
        first, second = clean_environment(url), clean_environment(url)
        for env in [first, second]:
            env.update(sms_staging_environment("dummy-user", "dummy-password", "x" * 40))
        self.assertNotEqual(first["SECRET_KEY"], second["SECRET_KEY"])
        self.assertEqual(first["PHONE_AUTH_PEPPER"], second["PHONE_AUTH_PEPPER"])
        self.assertEqual(first["TESTING"], "0")


if __name__ == "__main__":
    unittest.main()
