"""Billing identity and number validation; never issues a document or allocates a number."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

IST = timezone(timedelta(hours=5, minutes=30))
ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
GSTIN_PATTERN = re.compile(r"[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]")
MAX_PAISE = 9223372036854775807


def gstin_checksum_valid(value: str) -> bool:
    """Syntax/checksum only: this is not a government registration lookup."""
    if not isinstance(value, str) or GSTIN_PATTERN.fullmatch(value) is None:
        return False
    total = 0
    factor = 2
    for character in reversed(value[:14]):
        product = ALPHABET.index(character) * factor
        total += product // 36 + product % 36
        factor = 1 if factor == 2 else 2
    return value[-1] == ALPHABET[(36 - total % 36) % 36]


def clean_text(value: str, *, limit: int) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or len(value) > limit
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("Billing identity text is missing or invalid")
    return value


@dataclass(frozen=True)
class BillingIssuer:
    legal_name: str
    billing_address: str
    state_code: str
    postal_code: str
    gstin: str

    def __post_init__(self):
        clean_text(self.legal_name, limit=200)
        clean_text(self.billing_address, limit=1000)
        if (
            not isinstance(self.state_code, str)
            or re.fullmatch(r"[0-9]{2}", self.state_code) is None
        ):
            raise ValueError("Billing state code is invalid")
        if (
            not isinstance(self.postal_code, str)
            or re.fullmatch(r"[1-9][0-9]{5}", self.postal_code) is None
        ):
            raise ValueError("Billing postal code is invalid")
        if not gstin_checksum_valid(self.gstin):
            raise ValueError("Billing GSTIN format or checksum is invalid")
        if self.gstin[:2] != self.state_code:
            raise ValueError("Billing GSTIN and issuer state differ")


def financial_year(issued_at: datetime) -> tuple[int, int]:
    if (
        not isinstance(issued_at, datetime)
        or issued_at.tzinfo is None
        or issued_at.utcoffset() is None
    ):
        raise ValueError("Invoice issue timestamp must include a timezone")
    local = issued_at.astimezone(IST)
    start = local.year if local.month >= 4 else local.year - 1
    return start, start + 1


def invoice_number(*, prefix: str, issued_at: datetime, sequence: int) -> str:
    """Render an already server-allocated number; this function never allocates one."""
    if not isinstance(prefix, str) or re.fullmatch(r"[A-Z][A-Z0-9]{0,7}", prefix) is None:
        raise ValueError("Invoice prefix is invalid")
    if type(sequence) is not int or not 1 <= sequence <= 9999999999999999:
        raise ValueError("Invoice sequence is invalid")
    start, end = financial_year(issued_at)
    number = f"{prefix}-{start % 100:02d}/{end % 100:02d}-{sequence}"
    if len(number) > 16:
        raise ValueError("Invoice number exceeds the 16-character limit")
    return number


def money_to_paise(value: str | Decimal | int) -> int:
    """Exact boundary conversion. Do not silently round or accept binary floats."""
    if isinstance(value, bool) or not isinstance(value, (str, Decimal, int)):
        raise ValueError("Money must use exact decimal units")
    if isinstance(value, str) and (
        len(value) > 40 or re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", value) is None
    ):
        raise ValueError("Money must have at most two decimal places")
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError):
        raise ValueError("Money is invalid") from None
    if not amount.is_finite() or amount < 0 or amount > Decimal("92233720368547758.07"):
        raise ValueError("Money is outside the supported range")
    # Bound precision before multiplying, without relying on a caller's context.
    digits = amount.as_tuple()
    if digits.exponent < -2:
        raise ValueError("Money must have at most two decimal places")
    if amount.is_zero():
        return 0
    coefficient = int("".join(str(digit) for digit in digits.digits))
    return coefficient * 10 ** (digits.exponent + 2)
