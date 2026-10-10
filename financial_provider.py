"""Fail-closed authenticated Razorpay reads. Never logs provider bodies."""

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


def collection(provider, path, *, paginated=True):
    seen = set()
    result = []
    for page in range(10 if paginated else 1):
        endpoint = (
            path + ("&" if "?" in path else "?") + f"count=100&skip={page * 100}"
            if paginated
            else path
        )
        data = get(provider, endpoint)
        items = data.get("items")
        if (
            data.get("entity") != "collection"
            or not isinstance(items, list)
            or type(data.get("count")) is not int
            or data["count"] != len(items)
            or len(items) > 100
            or any(not isinstance(item, dict) for item in items)
        ):
            raise HTTPException(503, "Financial provider collection unavailable")
        for item in items:
            key = item.get("id")
            if not isinstance(key, str) or key in seen:
                raise HTTPException(503, "Financial provider collection unavailable")
            seen.add(key)
            result.append(item)
        if len(items) < 100:
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
