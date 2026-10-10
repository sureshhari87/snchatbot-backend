"""Signed financial event dispatch with fresh provider binding and durable holds."""

import financial_checkout
import voucher_funding
from financial_provider import get, identifier
from rewards_vouchers_reservation_models import FinancialReservation
from rewards_vouchers_transactions import locked_reservation, place_hold, require
from voucher_funding_models import VoucherFunding


def entity(payload, kind):
    envelope = payload.get("payload")
    if not isinstance(envelope, dict):
        return {}
    item = envelope.get(kind)
    return (
        item.get("entity", {})
        if isinstance(item, dict) and isinstance(item.get("entity"), dict)
        else {}
    )


def scope(db, order_id, provider):
    voucher = db.query(VoucherFunding).filter_by(provider_order_id=order_id).first()
    reservation = db.query(FinancialReservation).filter_by(provider_order_id=order_id).first()
    if voucher:
        return "voucher", voucher.voucher_id, None
    if reservation:
        return "checkout", reservation.id, None
    order = get(provider, "orders/" + order_id)
    receipt = order.get("receipt")
    if not isinstance(receipt, str):
        return None, None, None
    voucher = db.query(VoucherFunding).filter_by(receipt=receipt).first()
    reservation = db.query(FinancialReservation).filter_by(receipt=receipt).first()
    if voucher:
        return "voucher", voucher.voucher_id, order
    if reservation:
        return "checkout", reservation.id, order
    return None, None, None


def handle(db, payload, provider):
    event = payload.get("event")
    if not isinstance(event, str):
        return None
    if event not in (
        "payment.captured",
        "order.paid",
        "refund.created",
        "refund.processed",
        "refund.failed",
    ) and not event.startswith("payment.dispute."):
        return None
    adverse = event.startswith("refund.") or event.startswith("payment.dispute.")
    if adverse:
        kind = "refund" if event.startswith("refund.") else "dispute"
        reference = entity(payload, kind).get("id")
        require(
            identifier(reference, "rfnd" if kind == "refund" else "disp"),
            "Invalid financial review event",
            400,
        )
        proof = get(provider, ("refunds/" if kind == "refund" else "disputes/") + reference)
        payment_id = proof.get("payment_id")
        require(
            proof.get("id") == reference
            and proof.get("entity") == kind
            and identifier(payment_id, "pay"),
            "Financial review provider evidence differs",
        )
        payment = get(provider, "payments/" + payment_id)
        order_id = payment.get("order_id")
        require(
            payment.get("id") == payment_id
            and payment.get("entity") == "payment"
            and identifier(order_id, "order"),
            "Financial review payment evidence differs",
        )
    else:
        payment = entity(payload, "payment")
        payment_id = payment.get("id")
        order_id = payment.get("order_id") or entity(payload, "order").get("id")
        require(
            identifier(order_id, "order") and (payment_id is None or identifier(payment_id, "pay")),
            "Invalid financial payment event",
            400,
        )
    target, identity, order = scope(db, order_id, provider)
    if target is None:
        return None
    if target == "voucher":
        row, funding = voucher_funding.locked(db, identity)
        order = order or get(provider, "orders/" + order_id)
        require(
            voucher_funding.matches(order, row, funding, order_id),
            "Financial event order differs from voucher",
        )
        voucher_funding.bind(db, row, funding, order)
        amount = row.amount_paise
    else:
        saved = db.get(FinancialReservation, identity)
        row = locked_reservation(db, identity, saved.user_id)
        attempt = financial_checkout.saved_attempt(db, row)
        order = order or get(provider, "orders/" + order_id)
        require(
            financial_checkout.matches(order, row, order_id),
            "Financial event differs from reserved checkout",
        )
        local = financial_checkout.bind_checkout(db, row, attempt, order)
        amount = row.payable_paise
    if adverse:
        require(
            payment.get("order_id") == order_id
            and payment.get("currency") == "INR"
            and type(payment.get("amount")) is int
            and payment["amount"] == amount,
            "Financial review payment amount or currency differs",
        )
        require(
            proof.get("currency") == "INR"
            and type(proof.get("amount")) is int
            and 0 < proof["amount"] <= amount,
            "Financial review amount or currency differs",
        )
        reason = "Provider " + kind + " requires audited review"
        if target == "voucher":
            place_hold(db, kind=kind, reference=reference, reason=reason, voucher_id=row.id)
        else:
            financial_checkout.hold_targets(db, row, [(kind, reference, reason)])
        result = "held"
    elif target == "voucher":
        if payment_id is None:
            from financial_provider import capture_id

            payment_id = capture_id(provider, order_id)
        result = voucher_funding.post_online(db, row, funding, payment_id, provider, order)
        result = "held" if result["review_required"] else "posted"
    else:
        if payment_id is None:
            from financial_provider import capture_id

            payment_id = capture_id(provider, order_id)
        result = provider.finalize_razorpay_payment(db, local, payment_id, "financial_webhook")[
            "status"
        ]
    return dict(event=event, status=result, order_reference=order_id, payment_id=payment_id)
