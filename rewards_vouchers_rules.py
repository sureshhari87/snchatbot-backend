"""Approved reward/voucher arithmetic. Inputs must come from trusted server quotes.

This module does not accept payment evidence, move balances, or enable checkout.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

MAX_MINOR_UNITS = (1 << 63) - 1
POINT_VALUE_PAISE = 100
EARNING_PAISE_PER_POINT = 10000
MIN_PAYABLE_PAISE = 100
VOUCHER_VALIDITY_DAYS = 365
VOUCHER_DENOMINATIONS_PAISE = tuple(
    amount * 100 for amount in (500, 1000, 1500, 2000, 2500, 5000, 10000, 50000, 100000)
)


def nonnegative_integer(value, name):
    if type(value) is not int or not 0 <= value <= MAX_MINOR_UNITS:
        raise ValueError(f"{name} must be a nonnegative bounded integer")
    return value


@dataclass(frozen=True)
class DiscountCalculation:
    merchandise_paise: int
    tax_paise: int
    delivery_paise: int
    coupon_discount_paise: int
    voucher_redemption_paise: int
    reward_points_used: int
    reward_discount_paise: int
    eligible_paid_merchandise_paise: int
    payable_paise: int
    reward_points_earned: int


def calculate_checkout(
    *,
    merchandise_paise,
    tax_paise=0,
    delivery_paise=0,
    coupon_discount_paise=0,
    voucher_available_paise=0,
    reward_points_available=0,
    reward_points_requested=0,
):
    """Coupon -> voucher -> points, leaving tax/delivery intact and INR1 payable."""
    values = locals().copy()
    for name, value in values.items():
        nonnegative_integer(value, name)
    total = merchandise_paise + tax_paise + delivery_paise
    if not MIN_PAYABLE_PAISE <= total <= MAX_MINOR_UNITS:
        raise ValueError("Quoted total must fit integer paise and be at least INR1")
    limit = min(merchandise_paise, total - MIN_PAYABLE_PAISE)
    coupon = min(coupon_discount_paise, limit)
    remaining = merchandise_paise - coupon
    limit -= coupon
    voucher = min(voucher_available_paise, remaining, limit)
    remaining -= voucher
    limit -= voucher
    points = min(
        reward_points_requested,
        reward_points_available,
        remaining // POINT_VALUE_PAISE,
        limit // POINT_VALUE_PAISE,
    )
    reward_discount = points * POINT_VALUE_PAISE
    eligible_paid = remaining - reward_discount
    return DiscountCalculation(
        merchandise_paise=merchandise_paise,
        tax_paise=tax_paise,
        delivery_paise=delivery_paise,
        coupon_discount_paise=coupon,
        voucher_redemption_paise=voucher,
        reward_points_used=points,
        reward_discount_paise=reward_discount,
        eligible_paid_merchandise_paise=eligible_paid,
        payable_paise=eligible_paid + tax_paise + delivery_paise,
        reward_points_earned=eligible_paid // EARNING_PAISE_PER_POINT,
    )


def purchase_points(*, purpose, eligible_paid_merchandise_paise):
    nonnegative_integer(eligible_paid_merchandise_paise, "eligible_paid_merchandise_paise")
    if purpose == "jewellery":
        return eligible_paid_merchandise_paise // EARNING_PAISE_PER_POINT
    if purpose in {"savings", "voucher_purchase", "custom_design_advance"}:
        return 0
    raise ValueError("Unapproved reward earning purpose")


def voucher_expiry(activated_at):
    """365 elapsed days from verified activation, represented as naive UTC for SQL."""
    if not isinstance(activated_at, datetime):
        raise ValueError("Verified activation datetime required")
    if activated_at.tzinfo is not None:
        activated_at = activated_at.astimezone(timezone.utc).replace(tzinfo=None)
    return activated_at + timedelta(days=VOUCHER_VALIDITY_DAYS)
