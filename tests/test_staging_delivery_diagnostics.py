"""Offline read-only diagnostics tests; never connect to Neon."""

import json
import unittest
from unittest.mock import patch

import psycopg

from scripts.check_staging_delivery import diagnose, error_category
from scripts.configure_staging_delivery import approved_routes
from scripts.local_staging import REVISION

URL = "postgresql://staging_owner:test-password@ep-example.neon.tech/snchatbot_staging?sslmode=require"
HOST = "ep-example.neon.tech"


class DeliveryDiagnosticTests(unittest.TestCase):
    def test_error_is_redacted(self):
        with patch(
            "psycopg.connect",
            side_effect=psycopg.OperationalError("password authentication failed: " + URL),
        ):
            result = diagnose(URL, HOST)
        self.assertEqual(result["phase"], "CONNECT")
        self.assertEqual(result["category"], "AUTHENTICATION")
        self.assertNotIn(URL, json.dumps(result))
        self.assertNotIn("test-password", json.dumps(result))
        self.assertFalse(result["writes_performed"])

    def test_invalid_targets_never_connect(self):
        with patch("psycopg.connect") as connect:
            self.assertEqual(
                diagnose(URL.replace("snchatbot_staging", "neondb"), HOST)["category"],
                "INVALID_STAGING_URL",
            )
            self.assertEqual(diagnose(URL, "other.neon.tech")["category"], "HOST_MISMATCH")
        connect.assert_not_called()

    def test_fixed_error_categories(self):
        for message, expected in (
            ("could not translate host name", "DNS"),
            ("connection timeout expired", "CONNECTION_TIMEOUT"),
            ("connection refused", "CONNECTION_REFUSED"),
            ("certificate verify failed", "TLS"),
            ("permission denied", "PERMISSION"),
            ("server closed the connection", "DISCONNECTED"),
            ("canceling statement due to lock timeout", "LOCK_TIMEOUT"),
            (URL, "UNCLASSIFIED"),
        ):
            with self.subTest(expected=expected):
                self.assertEqual(error_category(psycopg.OperationalError(message)), expected)

    def test_config_reads_without_locks_or_writes(self):
        for row, expected in (
            (None, "not_configured"),
            ((json.dumps(approved_routes()), False), "already_configured"),
            (('[{"pin_prefix":"625"}]', False), "needs_review"),
            (("[]", True), "needs_review"),
        ):
            with self.subTest(expected=expected), patch("psycopg.connect") as connect:
                conn = connect.return_value.__enter__.return_value
                conn.execute.return_value.fetchone.side_effect = [
                    ("snchatbot_staging", "staging_owner"),
                    row,
                    (True, True),
                ]
                conn.execute.return_value.fetchall.return_value = [(REVISION,)]
                result = diagnose(URL, HOST)
                self.assertEqual(result["delivery"], expected)
                sql = [call.args[0] for call in conn.execute.call_args_list]
                self.assertEqual(sql[0], "SET TRANSACTION READ ONLY")
                self.assertTrue(all(query.startswith("SELECT") for query in sql[1:]))
                self.assertTrue(
                    all("FOR UPDATE" not in query and "advisory" not in query for query in sql)
                )
                self.assertFalse(result["writes_performed"])

    def test_wrong_identity_stops_before_config_read(self):
        with patch("psycopg.connect") as connect:
            conn = connect.return_value.__enter__.return_value
            conn.execute.return_value.fetchone.return_value = ("neondb", "neondb_owner")
            result = diagnose(URL, HOST)
            self.assertEqual(result["category"], "IDENTITY_MISMATCH")
            self.assertEqual(conn.execute.call_count, 2)

    def test_permission_failure_reports_step(self):
        with patch("psycopg.connect") as connect:
            conn = connect.return_value.__enter__.return_value
            conn.execute.return_value.fetchone.side_effect = [
                ("snchatbot_staging", "staging_owner"),
                psycopg.errors.InsufficientPrivilege("private error " + URL),
            ]
            conn.execute.return_value.fetchall.return_value = [(REVISION,)]
            result = diagnose(URL, HOST)
        self.assertEqual(result["phase"], "DELIVERY_READ")
        self.assertEqual(result["category"], "PERMISSION")
        self.assertNotIn("private error", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
