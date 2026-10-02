from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from push_delivery_policy import INDIA, evaluate_delivery


def decide(*, clock="2026-10-01T12:00:00", kind="verified_payment", enabled=True, **fields):
    prefs = SimpleNamespace(
        user_id=3,
        push_enabled=True,
        order_updates_enabled=True,
        quiet_hours_start=None,
        quiet_hours_end=None,
    )
    for key, value in fields.items():
        setattr(prefs, key, value)
    return evaluate_delivery(
        kind=kind,
        recipient_id=3,
        preferences=prefs,
        now=datetime.fromisoformat(clock).replace(tzinfo=INDIA),
        enabled=enabled,
    )


@pytest.mark.parametrize("kind", ["verified_payment", "order_status"])
def test_only_transactional_kinds_eligible(kind):
    assert decide(kind=kind).allowed


@pytest.mark.parametrize(
    "kind", ["marketing", "admin", "payment.failed", "", "payment-verified:11"]
)
def test_other_kinds_cannot_send(kind):
    assert decide(kind=kind).reason == "unsupported_kind"


@pytest.mark.parametrize("field", ["push_enabled", "order_updates_enabled"])
def test_opt_out(field):
    assert decide(**{field: False}).reason == "opted_out"


def test_default_disabled_and_missing_or_foreign_preferences():
    args = dict(
        kind="order_status", recipient_id=3, preferences=None, now=datetime.now(timezone.utc)
    )
    assert evaluate_delivery(**args).reason == "disabled"
    assert evaluate_delivery(**args, enabled=True).reason == "missing_or_foreign_preferences"
    assert decide(user_id=5).reason == "missing_or_foreign_preferences"


@pytest.mark.parametrize(
    "start,end",
    [
        (None, "07:00"),
        ("22:00", None),
        ("", ""),
        ("22:00", "22:00"),
        ("24:00", "07:00"),
        ("9:00", "10:00"),
        ("22:60", "07:00"),
        (" 22:00", "07:00"),
    ],
)
def test_invalid_quiet_hours_block(start, end):
    result = decide(quiet_hours_start=start, quiet_hours_end=end)
    assert not result.allowed
    assert result.reason == "invalid_quiet_hours"
    assert result.retry_at is None


@pytest.mark.parametrize(
    "clock,allowed,resume",
    [
        ("2026-10-01T21:59:59", True, None),
        ("2026-10-01T22:00:00", False, "2026-10-02T01:30:00+00:00"),
        ("2026-10-02T00:00:00", False, "2026-10-02T01:30:00+00:00"),
        ("2026-10-02T06:59:59", False, "2026-10-02T01:30:00+00:00"),
        ("2026-10-02T07:00:00", True, None),
    ],
)
def test_overnight_boundaries(clock, allowed, resume):
    result = decide(clock=clock, quiet_hours_start="22:00", quiet_hours_end="07:00")
    assert result.allowed is allowed
    assert (result.retry_at.isoformat() if result.retry_at else None) == resume


def test_daytime_window_and_recheck_opt_out():
    result = decide(quiet_hours_start="12:00", quiet_hours_end="14:00")
    assert result.retry_at.isoformat() == "2026-10-01T08:30:00+00:00"
    assert decide(
        clock="2026-10-01T14:00:00", quiet_hours_start="12:00", quiet_hours_end="14:00"
    ).allowed
    assert not decide(clock="2026-10-01T14:00:00", push_enabled=False).allowed


def test_naive_clock_rejected():
    prefs = SimpleNamespace(user_id=3, push_enabled=True, order_updates_enabled=True)
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate_delivery(
            kind="order_status",
            recipient_id=3,
            preferences=prefs,
            now=datetime(2026, 10, 1),
            enabled=True,
        )
