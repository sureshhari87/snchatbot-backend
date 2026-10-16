"""Offline delivery reconciliation; no database, credentials, network or import writes."""

import hashlib
import json
import re
from datetime import date

from delivery_rules import calendar, project
from product_readiness import readiness


def require(value):
    if not value:
        raise ValueError("Invalid audit input")


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def text(value, maximum=500):
    require(isinstance(value, str) and 0 < len(value) <= maximum and value.isprintable())
    require(value == value.strip())
    return value


def integer(value, maximum=365):
    require(type(value) is int and 0 <= value <= maximum)
    return value


def records(rows):
    require(isinstance(rows, list) and len(rows) <= 10000)
    result = {}
    for row in rows:
        require(isinstance(row, dict) and set(row) == {"id", "data"})
        identity = text(row["id"])
        require(identity not in result and isinstance(row["data"], dict))
        result[identity] = row["data"]
    return result


def state(data):
    require(type(data.get("active")) is bool)
    integer(data.get("priority"), 2147483647)


def location(data):
    state(data)
    text(data.get("safeDisplayName"))
    pin = data.get("originPin")
    require(isinstance(pin, str) and re.fullmatch(r"[1-9][0-9]{5}", pin) is not None)
    integer(data.get("defaultHandlingDays"))
    weekdays, holidays = data.get("workingWeekdays"), data.get("holidays")
    require(isinstance(weekdays, list) and 0 < len(weekdays) <= 7)
    require(all(type(day) is int and 1 <= day <= 7 for day in weekdays))
    require(len(set(weekdays)) == len(weekdays))
    require(isinstance(holidays, list) and len(holidays) <= 3660)
    require(all(isinstance(day, str) for day in holidays))
    require(len(set(holidays)) == len(holidays))
    for day in holidays:
        require(date.fromisoformat(day).isoformat() == day)
    cutoff = data.get("dispatchCutoffTime")
    require(isinstance(cutoff, str))
    require(re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", cutoff) is not None)
    return {
        "handling_days": data["defaultHandlingDays"],
        "working_weekdays": sorted(day - 1 for day in weekdays),
        "holidays": sorted(holidays),
        "cutoff": cutoff,
    }


def sla(data):
    state(data)
    prefix = data.get("destinationPinPrefix")
    require(isinstance(prefix, str) and re.fullmatch(r"[1-9][0-9]{0,5}", prefix) is not None)
    require(data.get("deliveryMethod") in ("standard", "express"))
    minimum = integer(data.get("minimumTransitBusinessDays"))
    maximum = integer(data.get("maximumTransitBusinessDays"))
    require(minimum <= maximum)
    return {
        "pin_prefix": prefix,
        "method": data["deliveryMethod"],
        "minimum_transit_days": minimum,
        "maximum_transit_days": maximum,
    }


def product_ready(data):
    require(isinstance(data, dict))
    choices = data.get("sizeOptions") or data.get("sizes")
    if data.get("sizeAdjustmentDays"):
        require(isinstance(choices, list) and 0 < len(choices) <= 1000)
        require(all(isinstance(size, str) and size and size == size.strip() for size in choices))
        require(len(set(choices)) == len(choices))
        for size in choices:
            readiness(data, size)
    else:
        readiness(data)


def overlaps(first, second):
    return first["method"] == second["method"] and (
        first["pin_prefix"].startswith(second["pin_prefix"])
        or second["pin_prefix"].startswith(first["pin_prefix"])
    )


def strict_backend_route(route):
    require(isinstance(route, dict) and type(route.get("active")) is bool)
    if route["active"] is False:
        return {"active": False}
    prefix = route.get("pin_prefix")
    require(isinstance(prefix, str) and re.fullmatch(r"[1-9][0-9]{0,5}", prefix) is not None)
    method = route.get("method")
    require(isinstance(method, str) and re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", method) is not None)
    for field in ["handling_days", "minimum_transit_days", "maximum_transit_days"]:
        integer(route.get(field))
    weekdays, holidays = route.get("working_weekdays"), route.get("holidays")
    require(isinstance(weekdays, list) and len(set(weekdays)) == len(weekdays))
    require(isinstance(holidays, list) and all(isinstance(day, str) for day in holidays))
    require(len(set(holidays)) == len(holidays))
    text(route.get("fulfilment_group"), 100)
    normalized = project(route)
    require(route.get("cutoff") == normalized["cutoff"])
    return normalized


def envelope(value, fields):
    require(isinstance(value, dict) and set(value) == {"schema_version", *fields})
    require(type(value["schema_version"]) is int and value["schema_version"] == 1)


def audit(legacy, backend, bindings=None):
    """Hash full source records, project only reviewed fields, never infer joins."""
    envelope(legacy, ["inventory_locations", "delivery_sla_rules", "products"])
    envelope(backend, ["routes", "products"])
    locations = records(legacy["inventory_locations"])
    rules = records(legacy["delivery_sla_rules"])
    products = records(legacy["products"])
    require(len(rules) <= 1000)
    require(isinstance(backend["routes"], list) and len(backend["routes"]) <= 1000)
    require(isinstance(backend["products"], list) and len(backend["products"]) <= 10000)
    if bindings is None:
        bindings = {"schema_version": 1, "bindings": []}
    envelope(bindings, ["bindings"])
    require(isinstance(bindings["bindings"], list) and len(bindings["bindings"]) <= 1000)
    report = {
        "schema_version": 1,
        "mode": "offline_review_only",
        "input_sha256": {
            "legacy": digest(legacy),
            "backend": digest(backend),
            "bindings": digest(bindings),
        },
        "identities": {},
        "issues": [],
        "proposals": [],
        "activation_allowed": False,
        "import_authorized": False,
        "writes_performed": False,
    }

    def issue(code, collection, identity, **extra):
        report["issues"].append({"code": code, "collection": collection, "id": identity, **extra})

    valid = {}
    for collection, rows, validate in [
        ("inventory_locations", locations, location),
        ("delivery_sla_rules", rules, sla),
        ("products", products, product_ready),
    ]:
        valid[collection] = {}
        report["identities"][collection] = []
        for identity, data in sorted(rows.items()):
            report["identities"][collection].append({"id": identity, "sha256": digest(data)})
            try:
                valid[collection][identity] = validate(data)
            except (ValueError, TypeError, KeyError, OverflowError):
                issue(
                    "invalid_readiness" if collection == "products" else "invalid_legacy_record",
                    collection,
                    identity,
                )
    pairs = sorted(valid["delivery_sla_rules"].items())
    for position, (identity, route) in enumerate(pairs):
        for other, candidate in pairs[position + 1 :]:
            if overlaps(route, candidate):
                code = (
                    "duplicate_coverage"
                    if route["pin_prefix"] == candidate["pin_prefix"]
                    else "overlapping_coverage"
                )
                issue(code, "delivery_sla_rules", identity, other_id=other)
                if rules[identity]["priority"] != rules[other]["priority"]:
                    issue(
                        "priority_semantics_require_review",
                        "delivery_sla_rules",
                        identity,
                        other_id=other,
                    )

    active_routes = []
    report["identities"]["backend_routes"] = []
    for index, route in enumerate(backend["routes"]):
        report["identities"]["backend_routes"].append({"index": index, "sha256": digest(route)})
        try:
            normalized = strict_backend_route(route)
            if normalized["active"]:
                active_routes.append((index, normalized))
        except (ValueError, TypeError, KeyError, OverflowError):
            issue("invalid_backend_route", "backend_routes", index)
    for position, (index, route) in enumerate(active_routes):
        for other, candidate in active_routes[position + 1 :]:
            if overlaps(route, candidate):
                issue("backend_coverage_overlap", "backend_routes", index, other_id=other)

    backend_ids, source_ids = set(), {}
    report["identities"]["backend_products"] = []
    for row in backend["products"]:
        require(isinstance(row, dict) and set(row) == {"id", "source_id", "source_data"})
        require(type(row["id"]) is int and row["id"] > 0 and row["id"] not in backend_ids)
        backend_ids.add(row["id"])
        require(row["source_id"] is None or isinstance(row["source_id"], str))
        require(row["source_data"] is None or isinstance(row["source_data"], dict))
        report["identities"]["backend_products"].append(
            {"id": row["id"], "source_id": row["source_id"], "sha256": digest(row["source_data"])}
        )
        source = row["source_id"]
        if source is not None:
            text(source)
            source_ids.setdefault(source, []).append(row["id"])
            if source not in products:
                issue("backend_source_missing_from_export", "backend_products", row["id"])
            elif digest(products[source]) != digest(row["source_data"]):
                issue("source_payload_differs", "backend_products", row["id"])
        try:
            product_ready(row["source_data"] or {})
        except (ValueError, TypeError, KeyError, OverflowError):
            issue("invalid_readiness", "backend_products", row["id"])
    for source, identities in sorted(source_ids.items()):
        if len(identities) > 1:
            issue("duplicate_backend_source_identity", "products", source, backend_ids=identities)
    for identity in sorted(set(products) - source_ids.keys()):
        issue("unmapped_product_identity", "products", identity)

    seen, used_locations = set(), set()
    for binding in bindings["bindings"]:
        require(
            isinstance(binding, dict)
            and set(binding) == {"sla_id", "location_id", "fulfilment_group", "reason"}
        )
        identity, origin = text(binding["sla_id"]), text(binding["location_id"])
        require(identity not in seen)
        seen.add(identity)
        group = text(binding["fulfilment_group"], 100)
        require(len(text(binding["reason"])) >= 3)
        if identity not in rules or origin not in locations:
            issue("unknown_explicit_binding", "delivery_sla_rules", identity)
            continue
        used_locations.add(origin)
        if (
            identity not in valid["delivery_sla_rules"]
            or origin not in valid["inventory_locations"]
        ):
            issue("invalid_binding_source", "delivery_sla_rules", identity)
            continue
        route = {
            "active": False,
            **valid["delivery_sla_rules"][identity],
            **valid["inventory_locations"][origin],
            "fulfilment_group": group,
        }
        calendar(route)
        matching = [index for index, saved in active_routes if overlaps(route, saved)]
        if matching:
            issue(
                "proposal_overlaps_live_coverage",
                "delivery_sla_rules",
                identity,
                backend_route_indexes=matching,
            )
        issue("origin_and_priority_require_review", "delivery_sla_rules", identity)
        report["proposals"].append(
            {
                "source_sla_id": identity,
                "source_location_id": origin,
                "source_sla_sha256": digest(rules[identity]),
                "source_location_sha256": digest(locations[origin]),
                "binding_sha256": digest(binding),
                "route": route,
                "review_required": True,
                "activation_allowed": False,
                "source_was_active": {
                    "location": locations[origin]["active"],
                    "sla": rules[identity]["active"],
                },
            }
        )
    for identity in sorted(set(rules) - seen):
        issue("explicit_location_binding_required", "delivery_sla_rules", identity)
    for identity in sorted(set(locations) - used_locations):
        issue("unbound_inventory_location", "inventory_locations", identity)
    report["summary"] = {
        "legacy_locations": len(locations),
        "legacy_rules": len(rules),
        "legacy_products": len(products),
        "backend_products": len(backend_ids),
        "backend_routes": len(backend["routes"]),
        "inactive_proposals": len(report["proposals"]),
        "review_issues": len(report["issues"]),
        "ready_for_import": False,
    }
    return report
