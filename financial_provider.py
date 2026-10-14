"""Fail-closed authenticated Razorpay reads. Never logs provider bodies."""

import os
import re

from fastapi import HTTPException


def identifier(value, prefix):
    return (
        isinstance(value, str) and re.fullmatch(prefix + r"_[A-Za-z0-9]{1,90}", value) is not None
    )


def get(provider, path):
    try:
        status, data = provider.call_razorpay("GET", path)
    except Exception:
        raise HTTPException(503, "Financial payment verification unavailable") from None
    if status != 200 or not isinstance(data, dict):
        raise HTTPException(503, "Financial payment verification unavailable")
    return data


def collection_kind(path):
    route = path.partition("?")[0]
    if route == "disputes":
        return "disputes"
    if re.fullmatch(r"orders/[^/]+/payments", route):
        return "order_payments"
    if re.fullmatch(r"payments/[^/]+/refunds", route):
        return "payment_refunds"
    return "unknown"


def invalid_collection(path, reason):
    # Return only a bounded route category and validation reason. Provider
    # payloads and identifiers remain private while operators can identify
    # the incompatible envelope safely from an API response or request log.
    raise HTTPException(
        503,
        f"Financial provider collection unavailable [{collection_kind(path)}:{reason}]",
    )


def allow_null_test_disputes(provider):
    return (
        os.getenv("RAZORPAY_TEST_NULL_DISPUTES_COMPAT") == "1"
        and os.getenv("APP_ENV") == "staging"
        and str(getattr(provider, "RAZORPAY_KEY_ID", "")).startswith("rzp_test_")
    )


def collection(provider, path, *, paginated=True):
    seen = set()
    result = []
    advertised_total = None
    for page in range(10 if paginated else 1):
        endpoint = (
            path + ("&" if "?" in path else "?") + f"count=100&skip={page * 100}"
            if paginated
            else path
        )
        data = get(provider, endpoint)
        items = data.get("items")
        response_count = data.get("count")
        if data.get("entity") != "collection":
            invalid_collection(path, "entity")
        # Razorpay TEST may return both collection fields as null when there
        # are no disputes. This compatibility shape is never accepted for a
        # live key or for payment/refund collections.
        if (
            items is None
            and response_count is None
            and collection_kind(path) == "disputes"
            and allow_null_test_disputes(provider)
        ):
            items = []
            response_count = 0
        if type(response_count) is not int:
            invalid_collection(path, "count_type")
        # Preserve the existing empty-disputes compatibility while requiring
        # every advertised paginated record before evidence is complete.
        if items is None and response_count == 0 and collection_kind(path) == "disputes":
            items = []
        if not isinstance(items, list):
            invalid_collection(path, "items")
        if response_count < len(items):
            invalid_collection(path, "count_range")
        if len(items) > 100:
            invalid_collection(path, "page_size")
        if any(not isinstance(item, dict) for item in items):
            invalid_collection(path, "item_type")
        for item in items:
            key = item.get("id")
            if not isinstance(key, str):
                invalid_collection(path, "item_id")
            if key in seen:
                invalid_collection(path, "duplicate_id")
            seen.add(key)
            result.append(item)
        if paginated and response_count > len(items):
            if advertised_total is not None and advertised_total != response_count:
                invalid_collection(path, "total_changed")
            advertised_total = response_count
        if paginated and advertised_total is not None and len(result) > advertised_total:
            invalid_collection(path, "count_range")
        if len(items) < 100:
            if paginated and advertised_total is not None and len(result) != advertised_total:
                invalid_collection(path, "incomplete_collection")
            return result
        if paginated and advertised_total is not None and len(result) == advertised_total:
            return result
        if not paginated:
            break
    raise HTTPException(503, "Financial provider collection requires reconciliation")


def order_matches(
    order, *, order_id=None, receipt, amount_paise, purpose, reference_name, reference
):
    return (
        isinstance(order, dict)
        and order.get("entity") == "order"
        and identifier(order.get("id"), "order")
        and (order_id is None or order["id"] == order_id)
        and order.get("receipt") == receipt
        and order.get("currency") == "INR"
        and type(order.get("amount")) is int
        and order["amount"] == amount_paise
        and order.get("status") in ("created", "attempted", "paid")
        and isinstance(order.get("notes"), dict)
        and order["notes"].get("purpose") == purpose
        and order["notes"].get(reference_name) == str(reference)
    )


def captured(provider, *, order, order_id, payment_id, amount_paise):
    """Return fresh bound capture and adverse evidence; no balance mutation."""
    if not identifier(payment_id, "pay"):
        raise HTTPException(422, "Invalid payment reference")
    payment = get(provider, "payments/" + payment_id)
    if (
        payment.get("entity") != "payment"
        or payment.get("id") != payment_id
        or payment.get("order_id") != order_id
        or payment.get("currency") != "INR"
        or type(payment.get("amount")) is not int
        or payment["amount"] != amount_paise
        or payment.get("status") != "captured"
        or payment.get("captured") is not True
        or type(payment.get("amount_refunded")) is not int
        or not 0 <= payment["amount_refunded"] <= amount_paise
    ):
        raise HTTPException(409, "Captured payment differs from bound financial checkout")
    if (
        order.get("status") != "paid"
        or type(order.get("amount_paid")) is not int
        or order["amount_paid"] != amount_paise
        or type(order.get("amount_due")) is not int
        or order["amount_due"] != 0
    ):
        raise HTTPException(409, "Financial provider order is not fully paid")
    adverse = []
    if payment["amount_refunded"] or payment.get("refund_status") is not None:
        return payment, [
            ("refund", "payment:" + payment_id, "Provider refund requires audited review")
        ]
    refunds = collection(provider, "payments/" + payment_id + "/refunds")
    for item in refunds:
        if (
            item.get("entity") != "refund"
            or not identifier(item.get("id"), "rfnd")
            or item.get("payment_id") != payment_id
            or item.get("currency") != "INR"
            or type(item.get("amount")) is not int
            or not 0 < item["amount"] <= amount_paise
        ):
            raise HTTPException(503, "Financial refund evidence unavailable")
        adverse.append(("refund", item["id"], "Provider refund requires audited review"))
    if adverse:
        return payment, adverse
    # Disputes API has no documented payment-id filter; paginate and bind locally.
    disputes = collection(provider, "disputes")
    for item in disputes:
        if (
            item.get("entity") != "dispute"
            or not identifier(item.get("id"), "disp")
            or not identifier(item.get("payment_id"), "pay")
        ):
            raise HTTPException(503, "Financial dispute evidence unavailable")
        if item["payment_id"] == payment_id:
            if (
                item.get("currency") != "INR"
                or type(item.get("amount")) is not int
                or not 0 < item["amount"] <= amount_paise
            ):
                raise HTTPException(503, "Financial dispute evidence unavailable")
            # Even a resolved dispute cannot automatically release a reviewed hold.
            adverse.append(("dispute", item["id"], "Provider dispute requires audited review"))
    return payment, adverse


def capture_id(provider, order_id):
    items = collection(provider, "orders/" + order_id + "/payments", paginated=False)
    for item in items:
        if (
            item.get("entity") != "payment"
            or not identifier(item.get("id"), "pay")
            or item.get("order_id") != order_id
        ):
            raise HTTPException(503, "Financial payment collection unavailable")
    ids = [item["id"] for item in items if item.get("status") == "captured"]
    if len(ids) != 1:
        raise HTTPException(409, "Financial capture requires reconciliation")
    return ids[0]
