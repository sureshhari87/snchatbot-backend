from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from rewards_vouchers_rules import (
    MAX_MINOR_UNITS,
    calculate_checkout,
    purchase_points,
    voucher_expiry,
)


def test_stacking_and_tax_delivery_exclusion():
    result = calculate_checkout(
        merchandise_paise=100000,
        tax_paise=3000,
        delivery_paise=500,
        coupon_discount_paise=10000,
        voucher_available_paise=25000,
        reward_points_available=200,
        reward_points_requested=100,
    )
    assert result.coupon_discount_paise == 10000
    assert result.voucher_redemption_paise == 25000
    assert result.reward_points_used == 100
    assert result.eligible_paid_merchandise_paise == 55000
    assert result.payable_paise == 58500
    assert result.reward_points_earned == 5


@pytest.mark.parametrize("available", [10000, 1000000])
def test_voucher_preserves_one_rupee_payable(available):
    result = calculate_checkout(merchandise_paise=10000, voucher_available_paise=available)
    assert result.voucher_redemption_paise == 9900
    assert result.payable_paise == 100
    assert result.reward_points_earned == 0


def test_rewards_never_consume_tax_or_shipping():
    result = calculate_checkout(
        merchandise_paise=10000,
        tax_paise=3000,
        delivery_paise=1000,
        reward_points_available=1000,
        reward_points_requested=1000,
    )
    assert result.reward_points_used == 100
    assert result.payable_paise == 4000
    assert result.reward_points_earned == 0


def test_coupon_can_not_remove_minimum_or_tax():
    result = calculate_checkout(
        merchandise_paise=10000,
        tax_paise=50,
        coupon_discount_paise=20000,
        voucher_available_paise=20000,
        reward_points_requested=200,
        reward_points_available=200,
    )
    assert result.coupon_discount_paise == 9950
    assert result.voucher_redemption_paise == 0
    assert result.reward_points_used == 0
    assert result.payable_paise == 100


def test_fractional_rupees_remain_integer_and_points_round_down():
    result = calculate_checkout(
        merchandise_paise=10051,
        reward_points_available=999,
        reward_points_requested=999,
    )
    assert result.reward_points_used == 99
    assert result.payable_paise == 151


def test_requested_points_are_capped_by_available_balance():
    result = calculate_checkout(
        merchandise_paise=100000,
        reward_points_available=3,
        reward_points_requested=100,
    )
    assert result.reward_points_used == 3


@pytest.mark.parametrize("bad", [-1, True, False, 1.25, "100", None, MAX_MINOR_UNITS + 1])
@pytest.mark.parametrize(
    "field",
    [
        "merchandise_paise",
        "tax_paise",
        "delivery_paise",
        "coupon_discount_paise",
        "voucher_available_paise",
        "reward_points_available",
        "reward_points_requested",
    ],
)
def test_money_and_points_are_strict_bounded_integers(field, bad):
    args = {"merchandise_paise": 10000, field: bad}
    with pytest.raises(ValueError):
        calculate_checkout(**args)


@pytest.mark.parametrize("amount", [0, 1, 99])
def test_no_zero_or_subminimum_payment_checkout(amount):
    with pytest.raises(ValueError):
        calculate_checkout(merchandise_paise=amount)


def test_aggregate_bigint_overflow_rejected():
    with pytest.raises(ValueError):
        calculate_checkout(merchandise_paise=MAX_MINOR_UNITS, tax_paise=1)


def test_result_is_immutable():
    result = calculate_checkout(merchandise_paise=10000)
    with pytest.raises(FrozenInstanceError):
        result.payable_paise = 0


@pytest.mark.parametrize("purpose", ["savings", "voucher_purchase", "custom_design_advance"])
def test_excluded_payment_types_do_not_earn(purpose):
    assert purchase_points(purpose=purpose, eligible_paid_merchandise_paise=99999999) == 0


@pytest.mark.parametrize("amount,points", [(9999, 0), (10000, 1), (19999, 1), (20000, 2)])
def test_full_hundred_rupee_earning_threshold(amount, points):
    assert purchase_points(purpose="jewellery", eligible_paid_merchandise_paise=amount) == points


def test_unknown_purpose_fails_closed():
    with pytest.raises(ValueError):
        purchase_points(purpose="referral", eligible_paid_merchandise_paise=10000)


def test_activation_expiry_uses_elapsed_days_and_utc():
    activated = datetime(2026, 10, 10, 10, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    assert voucher_expiry(activated) == datetime(2027, 10, 10, 4, 30)


def test_leap_year_is_365_days_not_calendar_anniversary():
    assert voucher_expiry(datetime(2024, 2, 29)) == datetime(2025, 2, 28)


def test_activation_required():
    with pytest.raises(ValueError):
        voucher_expiry(None)


def test_exhaustive_small_amounts_preserve_invariants():
    for merchandise in (100, 101, 250, 9999, 10000, 10001):
        for charges in (0, 50, 100, 1000):
            for coupon in (0, 100, 100000):
                for voucher in (0, 99, 100000):
                    result = calculate_checkout(
                        merchandise_paise=merchandise,
                        tax_paise=charges,
                        coupon_discount_paise=coupon,
                        voucher_available_paise=voucher,
                        reward_points_available=1000,
                        reward_points_requested=1000,
                    )
                    reductions = (
                        result.coupon_discount_paise
                        + result.voucher_redemption_paise
                        + result.reward_discount_paise
                    )
                    assert reductions <= merchandise
                    assert result.payable_paise >= 100
                    assert result.payable_paise == merchandise + charges - reductions
                    assert result.payable_paise >= charges
                    assert (
                        result.reward_points_earned
                        == result.eligible_paid_merchandise_paise // 10000
                    )
