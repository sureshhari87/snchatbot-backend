"""Pure delivery policy; not wired to a sender or enabled by this module."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

INDIA = timezone(timedelta(hours=5, minutes=30), name="Asia/Kolkata")
ALLOWED_KINDS = frozenset({"verified_payment", "order_status"})


@dataclass(frozen=True)
class DeliveryDecision:
    allowed: bool
    reason: str
    retry_at: datetime | None = None


def _minute(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
        raise ValueError("Invalid quiet hours")
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def evaluate_delivery(*, kind, recipient_id, preferences, now, enabled=False):
    """Use persisted preferences and a trusted producer's kind, not admin text.

    The future worker must separately revalidate device ownership and active
    sessions immediately before sending. Missing preferences fail closed.
    """
    if not enabled:
        return DeliveryDecision(False, "disabled")
    if kind not in ALLOWED_KINDS:
        return DeliveryDecision(False, "unsupported_kind")
    if preferences is None or preferences.user_id != recipient_id:
        return DeliveryDecision(False, "missing_or_foreign_preferences")
    if preferences.push_enabled is not True or preferences.order_updates_enabled is not True:
        return DeliveryDecision(False, "opted_out")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Delivery clock must be timezone-aware")
    start, end = preferences.quiet_hours_start, preferences.quiet_hours_end
    if start is None and end is None:
        return DeliveryDecision(True, "eligible")
    try:
        start_min, end_min = _minute(start), _minute(end)
    except ValueError:
        return DeliveryDecision(False, "invalid_quiet_hours")
    if start_min == end_min:
        return DeliveryDecision(False, "invalid_quiet_hours")
    local = now.astimezone(INDIA)
    minute = local.hour * 60 + local.minute
    quiet = (
        start_min <= minute < end_min
        if start_min < end_min
        else minute >= start_min or minute < end_min
    )
    if not quiet:
        return DeliveryDecision(True, "eligible")
    resume = local.replace(hour=end_min // 60, minute=end_min % 60, second=0, microsecond=0)
    if resume <= local:
        resume += timedelta(days=1)
    return DeliveryDecision(False, "quiet_hours", resume.astimezone(timezone.utc))
