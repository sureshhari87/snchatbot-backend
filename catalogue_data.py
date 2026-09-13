"""Validated catalogue mapping; no remote reads and no implicit commits."""

import json
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from models import AppConfigEntry, Product

RATES_KEY = "commerce.metal_rates"


def number(value, field):
    if isinstance(value, bool):
        raise ValueError(f"Invalid {field}")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"Invalid {field}") from None
    if not result.is_finite() or result < 0 or result > Decimal("1000000000000"):
        raise ValueError(f"Invalid {field}")
    return result


def first(data, *keys, default=None):
    return next(
        (data[k] for k in keys if data.get(k) is not None and str(data[k]).strip()), default
    )


def dynamic(data):
    flag = first(data, "useDynamicPricing", "use_dynamic_pricing")
    metal = str(first(data, "metalType", "metal_type", "metal", "category", default="")).lower()
    weight = first(
        data, "metalWeight", "metal_weight", "netWeight", "net_weight", "weight", default=0
    )
    enabled = flag is None or flag is True or str(flag).lower() in {"true", "1"}
    return enabled and bool(re.search(r"\b(gold|silver)\b", metal)) and number(weight, "weight") > 0


def price(data, rates):
    if not dynamic(data):
        if first(data, "useDynamicPricing", "use_dynamic_pricing") is True:
            raise ValueError("Dynamic product requires a supported metal and positive weight")
        result = number(
            first(data, "price", "sellingPrice", "selling_price", "salePrice", default=0), "price"
        )
    else:
        metal = str(first(data, "metalType", "metal_type", "metal", "category", default="")).lower()
        purity = (
            str(first(data, "purity", "metalPurity", "metal_purity", default="22K"))
            .upper()
            .replace("KT", "K")
        )
        key = (
            "silver"
            if re.search(r"\bsilver\b", metal)
            else {"18K": "gold18k", "24K": "gold24k"}.get(purity, "gold22k")
        )
        rate = number(rates.get(key, 0), key)
        if not rate and key == "gold18k":
            gold24 = number(rates.get("gold24k", 0), "gold24k")
            gold22 = number(rates.get("gold22k", 0), "gold22k")
            rate = gold24 * 18 / 24 if gold24 else gold22 * 18 / 22
        if rate <= 0:
            raise ValueError(f"Missing positive metal rate: {key}")
        weight = number(
            first(data, "metalWeight", "metal_weight", "netWeight", "net_weight", "weight"),
            "weight",
        )
        metal_value = rate * weight
        wastage = number(first(data, "wastagePercent", "wastage_percent", default=0), "wastage")
        subtotal = metal_value * (1 + wastage / 100)
        for camel, snake in [
            ("makingCharge", "making_charge"),
            ("stoneCharge", "stone_charge"),
            ("diamondCharge", "diamond_charge"),
            ("shippingCharge", "shipping_charge"),
        ]:
            subtotal += number(first(data, camel, snake, default=0), camel)
        gst = number(first(data, "gstPercent", "gst_percent", default=3), "gst") or Decimal(3)
        result = subtotal * (1 + gst / 100)
    if result <= 0:
        raise ValueError("A positive selling price is required")
    return float(result.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def product_values(data, rates):
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("Product name is required")
    stock = number(
        first(data, "stock_quantity", "stockQuantity", "stock", "quantity", default=0), "stock"
    )
    if stock != stock.to_integral_value():
        raise ValueError("Stock must be a whole number")
    return dict(
        name=name,
        description=data.get("description"),
        sku=data.get("sku") or None,
        category=data.get("category"),
        metal=first(data, "metalType", "metal", "category"),
        price=price(data, rates),
        image=data.get("image"),
        stock_quantity=int(stock),
        in_stock=stock > 0
        and data.get("active", True) is not False
        and data.get("deleted") is not True,
        product_type=first(data, "productType", "subCategory"),
        audience=first(data, "audience", "gender", "forWhom"),
        purity=data.get("purity"),
        weight=float(number(first(data, "metalWeight", "weight", default=0), "weight")),
        source_data=dict(data),
    )


def read_rates(db, lock=False):
    query = db.query(AppConfigEntry).filter_by(key=RATES_KEY)
    entry = query.with_for_update().first() if lock else query.first()
    return json.loads(entry.value) if entry else {}


def decode_value(value):
    if "mapValue" in value:
        return {k: decode_value(v) for k, v in value["mapValue"].get("fields", {}).items()}
    if "arrayValue" in value:
        return [decode_value(v) for v in value["arrayValue"].get("values", [])]
    if "integerValue" in value:
        return int(value["integerValue"])
    if "nullValue" in value:
        return None
    for key in ("doubleValue", "booleanValue", "stringValue", "timestampValue"):
        if key in value:
            return value[key]
    raise ValueError("Unsupported Firestore field type; preserve snapshot and review")


def decode_document(document):
    return {key: decode_value(value) for key, value in document.get("fields", {}).items()}


def import_snapshot(db, snapshot):
    """Insert-only, caller-owned transaction. Never reset an existing product's stock."""
    if snapshot.get("project") != "sona-jewellery-app" or snapshot.get("database") != "(default)":
        raise ValueError("Unexpected source database")
    collections = snapshot["collections"]
    prefix = "projects/sona-jewellery-app/databases/(default)/documents/"
    rates_doc = next(
        (
            d
            for d in collections.get("gold_rates", [])
            if d.get("name") == prefix + "gold_rates/today"
        ),
        None,
    )
    if rates_doc is None:
        raise ValueError("Snapshot must include gold_rates/today")
    rates = decode_document(rates_doc)
    existing_rates = db.query(AppConfigEntry).filter_by(key=RATES_KEY).with_for_update().first()
    if existing_rates:
        rates = json.loads(existing_rates.value)
    else:
        db.add(AppConfigEntry(key=RATES_KEY, value=json.dumps(rates), is_public=False))
    report = {
        "inserted": 0,
        "already_imported": 0,
        "empty_documents": 0,
        "unavailable": 0,
        "mapping": {},
    }
    # Validate the entire snapshot before adding any products.
    planned = []
    seen = set()
    for document in collections["products"]:
        path = document.get("name", "")
        if not path.startswith(prefix + "products/"):
            raise ValueError("Unexpected product source path")
        source_id = path[len(prefix + "products/") :]
        if not source_id or "/" in source_id or source_id in seen:
            raise ValueError("Invalid or duplicate product source identity")
        seen.add(source_id)
        data = decode_document(document)
        if not data:
            report["empty_documents"] += 1
            continue
        existing = db.query(Product).filter_by(source_id=source_id).first()
        if existing:
            report["already_imported"] += 1
            report["mapping"][source_id] = existing.id
            continue
        planned.append(Product(source_id=source_id, **product_values(data, rates)))
    for product in planned:
        db.add(product)
        db.flush()
        report["inserted"] += 1
        report["unavailable"] += not product.in_stock
        report["mapping"][product.source_id] = product.id
    return report
