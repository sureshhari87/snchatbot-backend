"""Validate existing delivery configuration without creating service coverage."""

import math
import re
from datetime import date


def route_header(value):
    if not isinstance(value, dict) or type(value.get("active", True)) is not bool:
        raise ValueError("Invalid route state")
    if value.get("active", True) is False:
        return None
    prefix = value.get("pin_prefix")
    if type(prefix) is int:
        prefix = str(prefix)
    method = value.get("method", "standard")
    if not isinstance(prefix, str) or not re.fullmatch(r"[0-9]{1,6}", prefix):
        raise ValueError("Invalid PIN prefix")
    if not isinstance(method, str) or not method or len(method) > 40:
        raise ValueError("Invalid delivery method")
    return prefix, method


def days(value):
    if type(value) is int:
        result = value
    elif isinstance(value, str) and re.fullmatch(r"[0-9]{1,3}", value):
        result = int(value)
    elif type(value) is float and math.isfinite(value) and value.is_integer():
        result = int(value)
    else:
        raise ValueError("Invalid business days")
    if not 0 <= result <= 365:
        raise ValueError("Invalid business days")
    return result


def calendar(value):
    handling, minimum, maximum = (
        days(value[key])
        for key in ["handling_days", "minimum_transit_days", "maximum_transit_days"]
    )
    if minimum > maximum:
        raise ValueError("Invalid transit range")
    weekdays = value.get("working_weekdays", [0, 1, 2, 3, 4, 5])
    if (
        not isinstance(weekdays, list)
        or not weekdays
        or len(weekdays) > 7
        or any(type(day) is not int or not 0 <= day <= 6 for day in weekdays)
    ):
        raise ValueError("Invalid working calendar")
    holidays = value.get("holidays", [])
    if not isinstance(holidays, list) or len(holidays) > 3660:
        raise ValueError("Invalid holiday calendar")
    for day in holidays:
        if not isinstance(day, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", day):
            raise ValueError("Invalid holiday date")
        date.fromisoformat(day)
    cutoff = value.get("cutoff", "16:00")
    if not isinstance(cutoff, str) or not re.fullmatch(r"[0-9]{1,2}:[0-9]{1,2}", cutoff):
        raise ValueError("Invalid cutoff")
    hour, minute = map(int, cutoff.split(":"))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("Invalid cutoff")
    group = value.get("fulfilment_group", "store")
    if not isinstance(group, str) or not group or len(group) > 100 or not group.isprintable():
        raise ValueError("Invalid fulfilment group")
    return {
        "handling_days": handling,
        "minimum_transit_days": minimum,
        "maximum_transit_days": maximum,
        "working_weekdays": sorted(set(weekdays)),
        "holidays": sorted(set(holidays)),
        "cutoff": f"{hour:02d}:{minute:02d}",
        "fulfilment_group": group,
    }


def project(value):
    header = route_header(value)
    # Inactive drafts may be incomplete; no inactive route grants coverage.
    if header is None:
        return {"active": False}
    prefix, method = header
    return {"active": True, "pin_prefix": prefix, "method": method, **calendar(value)}
