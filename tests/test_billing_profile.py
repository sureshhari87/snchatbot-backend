"""Offline billing tests: no application import, database, provider or email."""

import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from decimal import Decimal, localcontext

from billing_profile import (
    BillingIssuer,
    financial_year,
    gstin_checksum_valid,
    invoice_number,
    money_to_paise,
)


class BillingProfileTests(unittest.TestCase):
    def issuer(self, **overrides):
        values = {
            "legal_name": "SONA JEWELLERS",
            "billing_address": "NO 32, KANSA METTU STREET, MADURAI-625001",
            "state_code": "33",
            "postal_code": "625001",
            "gstin": "33AEYFS4414C1ZZ",
        }
        return BillingIssuer(**(values | overrides))

    def test_approved_issuer_and_checksum(self):
        issuer = self.issuer()
        self.assertTrue(gstin_checksum_valid(issuer.gstin))
        with self.assertRaises(FrozenInstanceError):
            issuer.legal_name = "Changed"

    def test_typo_and_missing_gstin_are_rejected(self):
        for value in ["33AEYFS4414C1ZA", "", "33aeyfs4414c1zz", None]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.issuer(gstin=value)

    def test_state_mismatch_and_bad_header_are_rejected(self):
        for overrides in [
            {"state_code": "32"},
            {"legal_name": ""},
            {"billing_address": "x\nInjected"},
            {"postal_code": "123"},
        ]:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.issuer(**overrides)

    def test_financial_year_changes_at_indian_midnight(self):
        before = datetime(2027, 3, 31, 18, 29, 59, tzinfo=timezone.utc)
        after = datetime(2027, 3, 31, 18, 30, tzinfo=timezone.utc)
        self.assertEqual(financial_year(before), (2026, 2027))
        self.assertEqual(financial_year(after), (2027, 2028))
        self.assertEqual(invoice_number(prefix="SNJ", issued_at=before, sequence=1), "SNJ-26/27-1")
        self.assertEqual(invoice_number(prefix="SNJ", issued_at=after, sequence=1), "SNJ-27/28-1")

    def test_naive_timestamp_and_invalid_sequence_are_rejected(self):
        date = datetime(2026, 10, 28, tzinfo=timezone.utc)
        with self.assertRaises(ValueError):
            invoice_number(prefix="SNJ", issued_at=date.replace(tzinfo=None), sequence=1)
        for sequence in [0, -1, True, 1.0, "1"]:
            with self.subTest(sequence=sequence), self.assertRaises(ValueError):
                invoice_number(prefix="SNJ", issued_at=date, sequence=sequence)

    def test_number_length_limit_does_not_wrap_or_reuse(self):
        date = datetime(2026, 10, 28, tzinfo=timezone.utc)
        self.assertEqual(
            invoice_number(prefix="SNJ", issued_at=date, sequence=999999), "SNJ-26/27-999999"
        )
        with self.assertRaises(ValueError):
            invoice_number(prefix="SNJ", issued_at=date, sequence=1000000)
        for prefix in ["SNJ-", "sona", "", "SNJ\n"]:
            with self.subTest(prefix=prefix), self.assertRaises(ValueError):
                invoice_number(prefix=prefix, issued_at=date, sequence=1)

    def test_paise_are_preserved_for_the_approved_bangle_examples(self):
        self.assertEqual(money_to_paise("205984.04"), 20598404)
        self.assertEqual(money_to_paise("204290.20"), 20429020)
        self.assertEqual(money_to_paise(Decimal("0.01")), 1)
        self.assertEqual(money_to_paise(0), 0)

    def test_fractional_paise_float_and_overflow_are_rejected(self):
        for value in [
            "0.001",
            "-1",
            "NaN",
            "1e3",
            "1,000",
            " 1",
            True,
            1.25,
            Decimal("Infinity"),
            Decimal("1.001"),
            "92233720368547758.08",
        ]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                money_to_paise(value)
        self.assertEqual(money_to_paise("92233720368547758.07"), 9223372036854775807)

    def test_money_conversion_is_independent_of_decimal_context(self):
        with localcontext() as context:
            context.prec = 6
            self.assertEqual(money_to_paise("205984.04"), 20598404)


if __name__ == "__main__":
    unittest.main()
