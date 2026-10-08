"""Fresh provider evidence for the approved hold-for-review policy."""

from fastapi import HTTPException

from models import utc_now
from savings import SavingsConflict, latest_hold_decision, record_hold
from savings_checkout import CheckoutUnavailable, order_matches
from savings_models import SavingsCheckoutAttempt, SavingsPayment


def verified_hold(db, payload, provider, fetch_payment):
    event = payload.get("event")
    kind = "refund" if event in ("refund.created", "refund.processed") else "dispute"
    try:
        entity = payload["payload"][kind]["entity"]
        reference, provider_id = entity["id"], entity["payment_id"]
        prefix = "rfnd_" if kind == "refund" else "disp_"
        if (
            not isinstance(reference, str)
            or not reference.startswith(prefix)
            or len(reference) > 100
            or not reference.isascii()
            or not reference.replace("_", "").isalnum()
        ):
            raise ValueError()
        if (
            not isinstance(provider_id, str)
            or not provider_id.startswith("pay_")
            or len(provider_id) > 100
            or not provider_id.isascii()
            or not provider_id.replace("_", "").isalnum()
        ):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "Invalid savings review event") from None
    try:
        status, current = provider.call_razorpay(
            "GET", ("refunds/" if kind == "refund" else "disputes/") + reference
        )
    except Exception:
        raise CheckoutUnavailable("Provider review verification unavailable") from None
    if status != 200:
        raise CheckoutUnavailable("Provider review verification unavailable")
    if (
        not isinstance(current, dict)
        or current.get("id") != reference
        or current.get("entity") != kind
        or current.get("payment_id") != provider_id
        or current.get("currency") != "INR"
        or type(current.get("amount")) is not int
        or current["amount"] <= 0
    ):
        raise SavingsConflict("Provider review does not match savings evidence")
    if kind == "refund" and current.get("status") not in ("pending", "processed"):
        return {"status": "ignored"}
    _, evidence = fetch_payment(provider, provider_id)
    if not isinstance(evidence, dict) or evidence.get("id") != provider_id:
        raise SavingsConflict("Payment evidence unavailable for review")
    payment = (
        db.query(SavingsPayment)
        .filter_by(mode="razorpay", provider_order_id=evidence.get("order_id"))
        .first()
        if isinstance(evidence.get("order_id"), str)
        else None
    )
    if payment is None:
        order_id = evidence.get("order_id")
        if (
            not isinstance(order_id, str)
            or not order_id.startswith("order_")
            or len(order_id) > 100
            or not order_id.replace("_", "").isalnum()
        ):
            raise SavingsConflict("Invalid provider order for review")
        try:
            code, order = provider.call_razorpay("GET", "orders/" + order_id)
        except Exception:
            raise CheckoutUnavailable("Provider review verification unavailable") from None
        if code != 200:
            raise CheckoutUnavailable("Provider review verification unavailable")
        notes = order.get("notes") if isinstance(order, dict) else None
        if not isinstance(notes, dict) or notes.get("purpose") != "sona_savings":
            return {"status": "ignored"}
        local_id = notes.get("savings_payment_id")
        if (
            not isinstance(local_id, str)
            or not local_id.isascii()
            or not local_id.isdigit()
            or len(local_id) > 10
        ):
            raise SavingsConflict("Invalid savings order review")
        if not 0 < int(local_id) <= 2_147_483_647:
            raise SavingsConflict("Invalid savings order review")
        payment = db.query(SavingsPayment).filter_by(id=int(local_id), mode="razorpay").first()
        attempt = db.get(SavingsCheckoutAttempt, payment.id) if payment else None
        if (
            not payment
            or not attempt
            or order.get("id") != order_id
            or not order_matches(order, payment, attempt)
        ):
            raise SavingsConflict("Review order does not match trusted checkout intent")
        if payment.provider_order_id and payment.provider_order_id != order_id:
            raise SavingsConflict("Review order differs from bound checkout")
        # The receipt and notes identify the plan. Do not bind or credit an uncertain checkout.
    if (
        evidence.get("currency") != "INR"
        or evidence.get("status") not in ("captured", "refunded")
        or type(evidence.get("amount")) is not int
        or evidence["amount"] != payment.amount_paise
        or current["amount"] > payment.amount_paise
    ):
        raise SavingsConflict("Review payment does not match savings checkout")
    hold = record_hold(
        db,
        payment.scheme_id,
        kind,
        reference,
        "Verified provider " + kind,
        utc_now,
        payment_id=payment.id,
        reopen_released=kind == "refund" or current.get("status") != "won",
    )
    latest = latest_hold_decision(db, hold.id)
    return {
        "status": "released" if latest and latest.action == "released" else "held",
        "hold_id": hold.id,
    }
