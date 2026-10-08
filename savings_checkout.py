"""Durable savings checkout creation and verified recovery.

An attempt is committed before POSTing to the provider. A missing/malformed
response leaves an unknown outcome, never automatic re-creation. Recovery
fetches a candidate order and checks its receipt, amount and server notes.
"""

from urllib.parse import quote
from uuid import uuid4

from models import utc_now
from savings import SavingsConflict, audit, locked_scheme, verify_razorpay
from savings_models import SavingsCheckoutAttempt, SavingsPayment


class CheckoutUnavailable(SavingsConflict):
    pass


def owned_payment(db, payment_id, user_id):
    payment = db.query(SavingsPayment).filter_by(id=payment_id, user_id=user_id).first()
    if payment is None:
        raise SavingsConflict("Payment unavailable")
    return payment


def order_matches(order, payment, attempt):
    notes = order.get("notes") if isinstance(order, dict) else None
    return (
        isinstance(order, dict)
        and isinstance(order.get("id"), str)
        and order["id"].startswith("order_")
        and len(order["id"]) <= 100
        and order.get("entity") == "order"
        and order.get("currency") == "INR"
        and type(order.get("amount")) is int
        and order["amount"] == payment.amount_paise
        and order.get("receipt") == attempt.receipt
        and order.get("status") in ("created", "attempted", "paid")
        and isinstance(notes, dict)
        and notes.get("savings_payment_id") == str(payment.id)
        and notes.get("purpose") == "sona_savings"
    )


def checkout(db, payment_id, user_id, call_provider, now):
    payment = owned_payment(db, payment_id, user_id)
    scheme = locked_scheme(db, payment.scheme_id, user_id)
    db.refresh(payment)
    if payment.mode != "razorpay" or payment.state != "pending" or scheme.state != "active":
        raise SavingsConflict("Payment cannot open checkout")
    attempt = db.get(SavingsCheckoutAttempt, payment.id)
    if attempt:
        if attempt.state == "ready" and payment.provider_order_id:
            return payment
        raise SavingsConflict("Checkout outcome requires reconciliation; no new order was created")
    attempt = SavingsCheckoutAttempt(
        payment_id=payment.id,
        receipt="ss_" + uuid4().hex,
        state="creating",
        created_at=now,
        updated_at=now,
    )
    db.add(attempt)
    audit(
        db,
        scheme,
        f"checkout-start:{payment.id}",
        "checkout_started",
        user_id,
        attempt.receipt,
        now,
    )
    # Durable intent first; crashes and timeouts are recoverable without duplicate POSTs.
    db.commit()
    payload = {
        "amount": payment.amount_paise,
        "currency": "INR",
        "receipt": attempt.receipt,
        "partial_payment": False,
        "notes": {"purpose": "sona_savings", "savings_payment_id": str(payment.id)},
    }
    try:
        status, order = call_provider("POST", "orders", payload)
        valid = status in (200, 201) and order_matches(order, payment, attempt)
    except Exception:
        # Never include provider exception text, response or credentials in errors.
        valid, order = False, None
    scheme = locked_scheme(db, payment.scheme_id, user_id)
    db.refresh(payment)
    db.refresh(attempt)
    if attempt.state == "ready" and payment.provider_order_id:
        if valid and payment.provider_order_id != order["id"]:
            raise SavingsConflict("Checkout binding changed during creation")
        return payment
    if not valid:
        attempt.state, attempt.updated_at = "unknown", now
        audit(
            db,
            scheme,
            f"checkout-unknown:{payment.id}",
            "checkout_unknown",
            None,
            attempt.receipt,
            now,
        )
        db.commit()
        raise CheckoutUnavailable(
            "Checkout could not be confirmed; contact the store for reconciliation"
        )
    bind(db, payment, attempt, order, user_id, now)
    db.commit()
    return payment


def bind(db, payment, attempt, order, actor_id, now):
    if not order_matches(order, payment, attempt):
        raise SavingsConflict("Provider order does not match the savings request")
    if payment.provider_order_id and payment.provider_order_id != order["id"]:
        raise SavingsConflict("Payment already has another provider order")
    payment.provider_order_id = order["id"]
    attempt.state, attempt.updated_at = "ready", now
    scheme = locked_scheme(db, payment.scheme_id)
    audit(db, scheme, f"checkout-ready:{payment.id}", "checkout_bound", actor_id, order["id"], now)
    db.flush()


def reconcile(db, payment_id, admin, provider_order_id, call_provider, now):
    if not admin.is_admin:
        raise PermissionError("Admin reconciliation required")
    payment = db.get(SavingsPayment, payment_id)
    if payment is None or payment.mode != "razorpay":
        raise SavingsConflict("Savings checkout unavailable")
    locked_scheme(db, payment.scheme_id)
    db.refresh(payment)
    attempt = db.get(SavingsCheckoutAttempt, payment.id)
    if attempt is None:
        raise SavingsConflict("No checkout attempt exists")
    if attempt.state == "ready":
        if payment.provider_order_id != provider_order_id:
            raise SavingsConflict("Checkout is already bound to another order")
        return payment
    try:
        status, order = call_provider("GET", "orders/" + provider_order_id)
    except Exception:
        raise CheckoutUnavailable("Provider reconciliation unavailable") from None
    if status != 200:
        raise CheckoutUnavailable("Provider reconciliation unavailable")
    bind(db, payment, attempt, order, admin.id, now)
    if order.get("status") == "paid":
        try:
            code, collection = call_provider("GET", "orders/" + provider_order_id + "/payments")
        except Exception:
            raise CheckoutUnavailable("Provider payment reconciliation unavailable") from None
        if (
            code != 200
            or not isinstance(collection, dict)
            or not isinstance(collection.get("items"), list)
        ):
            raise CheckoutUnavailable("Provider payment reconciliation unavailable")
        captures = [
            item
            for item in collection["items"]
            if isinstance(item, dict) and item.get("status") == "captured"
        ]
        if len(captures) != 1 or not isinstance(captures[0].get("id"), str):
            raise SavingsConflict("Paid order requires captured-payment review")
        provider_id = captures[0]["id"]
        if not provider_id.startswith("pay_") or len(provider_id) > 100:
            raise SavingsConflict("Invalid provider payment reference")

        def fetch(payment_id):
            try:
                code, evidence = call_provider("GET", "payments/" + quote(payment_id, safe=""))
            except Exception:
                raise CheckoutUnavailable("Provider payment reconciliation unavailable") from None
            if code != 200:
                raise CheckoutUnavailable("Provider payment reconciliation unavailable")
            return code, evidence

        verify_razorpay(db, payment.id, payment.user_id, provider_id, fetch, utc_now)
    db.flush()
    return payment
