"""Owner-approved finished-jewellery GST; no blanket rule for other goods."""

from decimal import Decimal, InvalidOperation

JEWELLERY_TYPES = frozenset(
    "necklace necklaces malai chain chains ring rings bracelet bracelets earring earrings drops haram bangles bangle sets anklets pendant pendants mangalsutra jewellery jewelry".split()
) | {
    "baby bangle",
    "baby bangles",
    "leg chains",
    "nose ring",
    "nose rings",
    "nose pin",
    "nose pins",
    "jewellery set",
}


def finished_jewellery(data):
    category = str(data.get("category") or "").strip().lower()
    kind = str(data.get("productType") or data.get("subCategory") or "")
    kind = " ".join(kind.lower().replace("_", " ").replace("-", " ").split())
    return category in {"gold", "diamond", "silver", "platinum"} and kind in JEWELLERY_TYPES


def apply_jewellery_gst(data):
    result = dict(data)
    if not finished_jewellery(result):
        return result
    for key in ("gstPercent", "gst_percent"):
        value = result.get(key)
        if value is None or value == "":
            continue
        try:
            valid = not isinstance(value, bool) and Decimal(str(value)) == Decimal(3)
        except (InvalidOperation, ValueError):
            valid = False
        if not valid:
            raise ValueError("Finished jewellery GST is fixed at 3%")
    result.pop("gst_percent", None)
    result["gstPercent"] = 3
    return result
