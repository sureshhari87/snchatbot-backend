"""Validate generated briefs before they reach customer or saved design flows."""

from typing import Any


def validated_concept(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    result = {}
    for key, limit in (("name", 160), ("design_brief", 6000)):
        item = value.get(key)
        if not isinstance(item, str) or not item.strip() or len(item) > limit:
            return None
        result[key] = item.strip()
    for key in ("materials", "craft_notes"):
        items = value.get(key)
        if not isinstance(items, list) or len(items) > 20:
            return None
        if any(not isinstance(item, str) or not item.strip() or len(item) > 1000 for item in items):
            return None
        result[key] = [item.strip() for item in items]
    return result
