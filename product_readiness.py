"""Strict server-owned product readiness for delivery promises."""

import re
from datetime import date, datetime, timedelta, timezone

IST = timezone(timedelta(minutes=330))


def readiness(data, selected_size=""):
    """Return handling, manufacturing/adjustment days and earliest IST date.

    Positive adjustment time requires an explicit list of affected sizes.
    Missing metadata never means immediate readiness.
    """
    if not isinstance(data, dict):
        raise ValueError("Missing product readiness")
    ready, made = data.get("readyToShip"), data.get("madeToOrder")
    if type(ready) is not bool or type(made) is not bool or ready == made:
        raise ValueError("Conflicting product readiness")
    values = []
    for key in ("handlingDays", "manufacturingDays", "sizeAdjustmentDays"):
        value = data.get(key)
        if type(value) is not int or not 0 <= value <= 365:
            raise ValueError("Invalid product readiness days")
        values.append(value)
    handling, manufacturing, adjustment = values
    if not made and manufacturing:
        raise ValueError("Manufacturing time conflicts with ready stock")
    if adjustment:
        sizes = data.get("sizeAdjustmentSizes")
        choices = data.get("sizeOptions") or data.get("sizes")
        if (
            not isinstance(sizes, list)
            or not sizes
            or not isinstance(choices, list)
            or any(not isinstance(x, str) or not x or x != x.strip() for x in sizes)
            or len(set(sizes)) != len(sizes)
            or any(x not in choices for x in sizes)
            or not selected_size
            or selected_size not in choices
        ):
            raise ValueError("Size adjustment requires review")
        adjustment = adjustment if selected_size in sizes else 0
    earliest = data.get("dispatchNotBefore")
    if earliest is not None:
        if not isinstance(earliest, str):
            raise ValueError("Invalid earliest dispatch")
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", earliest):
            earliest = date.fromisoformat(earliest)
        else:
            instant = datetime.fromisoformat(earliest.replace("Z", "+00:00"))
            if instant.tzinfo is None:
                raise ValueError("Earliest dispatch timezone required")
            earliest = instant.astimezone(IST).date()
    return handling, manufacturing + adjustment, earliest, made
