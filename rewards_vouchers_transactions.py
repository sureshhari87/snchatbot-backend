"""Internal transaction primitives for trusted server quotes and provider reads.

No function commits, releases a hold/reservation, or creates a provider payment.
Future adapters must commit reservations before creating provider orders, reuse
their receipt, and call settle only with fresh authenticated provider reads.
Lock order: customer -> reservation -> reward account -> voucher.
"""

import hashlib
import json
import re
from dataclasses import asdict
from uuid import uuid4

from fastapi import HTTPException

from models import OrderSnapshot, User, utc_now
from rewards_vouchers_models import (
    GiftVoucher,
    GiftVoucherEntry,
    RewardAccount,
    RewardEntry,
    RewardVoucherHold,
)
from rewards_vouchers_reservation_models import FinancialReservation, FinancialReservationEvent
from rewards_vouchers_rules import calculate_checkout


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def require(ok, detail, status=409):
    if not ok:
        raise HTTPException(status, detail)


def locked_user(db, user_id):
    user = db.query(User).filter_by(id=user_id).populate_existing().with_for_update().first()
    require(user is not None, "Customer not found", 404)
    return user


def account(db, user_id):
    row = (
        db.query(RewardAccount)
        .filter_by(user_id=user_id)
        .populate_existing()
        .with_for_update()
        .first()
    )
    if row is None:
        row = RewardAccount(
            user_id=user_id, balance_points=0, reserved_points=0, created_at=utc_now()
        )
        db.add(row)
        db.flush()
    return row


def event(db, row, action, evidence):
    db.add(
        FinancialReservationEvent(
            reservation_id=row.id,
            event_key=f"reservation:{row.id}:{action}",
            action=action,
            evidence_sha256=digest(evidence),
            created_at=utc_now(),
        )
    )


def reserve(
    db,
    *,
    user_id,
    request_key,
    merchandise_paise,
    tax_paise=0,
    delivery_paise=0,
    coupon_discount_paise=0,
    reward_points_requested=0,
    voucher_code=None,
    now=None,
):
    """Amounts are from the trusted catalogue/coupon calculation, never client totals."""
    require(
        isinstance(request_key, str)
        and 1 <= len(request_key) <= 100
        and request_key.strip() == request_key,
        "Invalid reservation key",
        422,
    )
    if voucher_code is not None:
        require(
            isinstance(voucher_code, str)
            and re.fullmatch(r"[A-Za-z0-9-]{8,128}", voucher_code) is not None,
            "Voucher unavailable",
            404,
        )
    code_hash = hashlib.sha256(voucher_code.upper().encode()).hexdigest() if voucher_code else None
    # Validate types before reading balances or computing the request fingerprint.
    calculate_checkout(
        merchandise_paise=merchandise_paise,
        tax_paise=tax_paise,
        delivery_paise=delivery_paise,
        coupon_discount_paise=coupon_discount_paise,
        reward_points_requested=reward_points_requested,
    )
    sha = digest(
        dict(
            user_id=user_id,
            merchandise_paise=merchandise_paise,
            tax_paise=tax_paise,
            delivery_paise=delivery_paise,
            coupon_discount_paise=coupon_discount_paise,
            reward_points_requested=reward_points_requested,
            voucher_code_hash=code_hash,
        )
    )
    locked_user(db, user_id)
    old = (
        db.query(FinancialReservation)
        .filter_by(user_id=user_id, request_key=request_key)
        .populate_existing()
        .with_for_update()
        .first()
    )
    if old:
        require(old.user_id == user_id, "Reservation not found", 404)
        require(old.request_sha256 == sha, "Reservation key reused for different terms")
        return old
    points = account(db, user_id)
    if reward_points_requested:
        require(
            not db.query(RewardVoucherHold).filter_by(reward_user_id=user_id).first(),
            "Reward account held for audited review",
        )
    voucher = None
    if code_hash:
        voucher = (
            db.query(GiftVoucher)
            .filter_by(code_hash=code_hash)
            .populate_existing()
            .with_for_update()
            .first()
        )
        require(
            voucher is not None
            and voucher.state == "active"
            and voucher.expires_at is not None
            and voucher.expires_at > (now or utc_now())
            and voucher.assigned_user_id in (None, user_id),
            "Voucher unavailable",
            404,
        )
        require(
            not db.query(RewardVoucherHold).filter_by(voucher_id=voucher.id).first(),
            "Voucher held for audited review",
        )
    quote = calculate_checkout(
        merchandise_paise=merchandise_paise,
        tax_paise=tax_paise,
        delivery_paise=delivery_paise,
        coupon_discount_paise=coupon_discount_paise,
        reward_points_available=points.balance_points - points.reserved_points,
        reward_points_requested=reward_points_requested,
        voucher_available_paise=voucher.balance_paise - voucher.reserved_paise if voucher else 0,
    )
    points.reserved_points += quote.reward_points_used
    if voucher:
        voucher.reserved_paise += quote.voucher_redemption_paise
    row = FinancialReservation(
        user_id=user_id,
        request_key=request_key,
        request_sha256=sha,
        receipt="rvc_" + uuid4().hex,
        quote_json=json.dumps(asdict(quote), sort_keys=True),
        payable_paise=quote.payable_paise,
        reward_points=quote.reward_points_used,
        voucher_id=voucher.id if voucher else None,
        voucher_paise=quote.voucher_redemption_paise,
        state="reserved",
        created_at=now or utc_now(),
    )
    db.add(row)
    db.flush()
    event(db, row, "reserved", dict(request_sha256=sha))
    db.flush()
    return row


def locked_reservation(db, reservation_id, user_id):
    locked_user(db, user_id)
    row = (
        db.query(FinancialReservation)
        .filter_by(id=reservation_id, user_id=user_id)
        .populate_existing()
        .with_for_update()
        .first()
    )
    require(row is not None, "Reservation not found", 404)
    return row


def verify_order(order, row):
    require(
        isinstance(order, dict)
        and order.get("entity") == "order"
        and isinstance(order.get("id"), str)
        and re.fullmatch(r"order_[A-Za-z0-9]{1,90}", order["id"])
        and order.get("receipt") == row.receipt
        and order.get("currency") == "INR"
        and type(order.get("amount")) is int
        and order["amount"] == row.payable_paise
        and isinstance(order.get("notes"), dict)
        and order["notes"].get("purpose") == "sona_rewards_checkout"
        and order["notes"].get("reservation_id") == str(row.id),
        "Provider order differs from reserved checkout",
    )


def bind(db, *, reservation_id, user_id, order_id, provider_order):
    row = locked_reservation(db, reservation_id, user_id)
    verify_order(provider_order, row)
    order = db.get(OrderSnapshot, order_id)
    require(
        order is not None
        and order.user_id == user_id
        and order.order_reference == provider_order["id"]
        and order.currency == "INR",
        "Local order differs from reservation",
    )
    if row.state != "reserved":
        require(
            row.order_id == order_id and row.provider_order_id == provider_order["id"],
            "Reservation already bound to another order",
        )
        return row
    row.order_id = order_id
    row.provider_order_id = provider_order["id"]
    row.state = "ready"
    event(
        db, row, "checkout_bound", dict(order_id=order_id, provider_order_id=row.provider_order_id)
    )
    db.flush()
    return row


def settle(db, *, reservation_id, user_id, provider_order, provider_payment):
    """Fresh provider reads only; signed webhook payloads are insufficient evidence."""
    row = locked_reservation(db, reservation_id, user_id)
    require(row.state in ("ready", "posted"), "Existing bound checkout required")
    verify_order(provider_order, row)
    payment = provider_payment
    require(
        provider_order.get("id") == row.provider_order_id
        and provider_order.get("status") == "paid"
        and type(provider_order.get("amount_paid")) is int
        and provider_order["amount_paid"] == row.payable_paise
        and type(provider_order.get("amount_due")) is int
        and provider_order["amount_due"] == 0,
        "Provider order is not fully paid",
    )
    require(
        isinstance(payment, dict)
        and payment.get("entity") == "payment"
        and isinstance(payment.get("id"), str)
        and re.fullmatch(r"pay_[A-Za-z0-9]{1,90}", payment["id"])
        and payment.get("order_id") == row.provider_order_id
        and payment.get("currency") == "INR"
        and type(payment.get("amount")) is int
        and payment["amount"] == row.payable_paise
        and payment.get("status") == "captured"
        and payment.get("captured") is True
        and type(payment.get("amount_refunded")) is int
        and payment["amount_refunded"] == 0
        and payment.get("refund_status") is None,
        "Provider payment differs or requires refund review",
    )
    if row.state == "posted":
        require(
            row.provider_payment_id == payment["id"],
            "Reservation posted against a different payment",
        )
        return row
    points = account(db, user_id)
    require(
        not db.query(RewardVoucherHold).filter_by(reward_user_id=user_id).first(),
        "Reward account held for audited review",
    )
    require(
        points.reserved_points >= row.reward_points and points.balance_points >= row.reward_points,
        "Reserved reward balance requires audited review",
    )
    voucher = None
    if row.voucher_id:
        voucher = (
            db.query(GiftVoucher)
            .filter_by(id=row.voucher_id)
            .populate_existing()
            .with_for_update()
            .one()
        )
        require(
            not db.query(RewardVoucherHold).filter_by(voucher_id=voucher.id).first(),
            "Voucher held for audited review",
        )
        require(
            voucher.reserved_paise >= row.voucher_paise
            and voucher.balance_paise >= row.voucher_paise,
            "Reserved voucher balance requires audited review",
        )
    quote = json.loads(row.quote_json)
    # Recompute from frozen server terms; never use a standalone stored credit value.
    recalculated = calculate_checkout(
        merchandise_paise=quote["merchandise_paise"],
        tax_paise=quote["tax_paise"],
        delivery_paise=quote["delivery_paise"],
        coupon_discount_paise=quote["coupon_discount_paise"],
        voucher_available_paise=row.voucher_paise,
        reward_points_available=row.reward_points,
        reward_points_requested=row.reward_points,
    )
    require(
        asdict(recalculated) == quote
        and recalculated.payable_paise == row.payable_paise
        and recalculated.voucher_redemption_paise == row.voucher_paise
        and recalculated.reward_points_used == row.reward_points,
        "Reserved quote requires audited review",
    )
    evidence = digest(
        dict(reservation_id=row.id, order_id=row.order_id, payment_id=payment["id"], quote=quote)
    )
    now = utc_now()
    if row.reward_points:
        db.add(
            RewardEntry(
                user_id=user_id,
                order_id=row.order_id,
                event_key=f"reward-spend:{row.id}",
                kind="redemption",
                points_delta=-row.reward_points,
                evidence_reference=payment["id"],
                evidence_sha256=evidence,
                created_at=now,
            )
        )
    earned = quote["reward_points_earned"]
    if earned:
        db.add(
            RewardEntry(
                user_id=user_id,
                order_id=row.order_id,
                event_key=f"reward-earn:{row.id}",
                kind="purchase_credit",
                points_delta=earned,
                evidence_reference=payment["id"],
                evidence_sha256=evidence,
                created_at=now,
            )
        )
    points.balance_points = points.balance_points - row.reward_points + earned
    points.reserved_points -= row.reward_points
    if voucher and row.voucher_paise:
        db.add(
            GiftVoucherEntry(
                voucher_id=voucher.id,
                order_id=row.order_id,
                event_key=f"voucher-spend:{row.id}",
                kind="redemption",
                amount_delta_paise=-row.voucher_paise,
                evidence_reference=payment["id"],
                evidence_sha256=evidence,
                created_at=now,
            )
        )
        voucher.balance_paise -= row.voucher_paise
        voucher.reserved_paise -= row.voucher_paise
    row.provider_payment_id = payment["id"]
    row.state = "posted"
    event(db, row, "payment_posted", dict(payment_id=payment["id"], evidence_sha256=evidence))
    db.flush()
    return row


def place_hold(db, *, kind, reference, reason, reward_user_id=None, voucher_id=None, actor_id=None):
    """Internal audited hold for trusted adverse evidence or authenticated admin review.

    Holds never release reservations or adjust balances. Call reward-target holds
    before voucher-target holds when applying both in one transaction. Provider
    adapters must authenticate and bind refund/dispute evidence before calling.
    """
    require(kind in ("refund", "dispute", "admin_review"), "Invalid hold kind", 422)
    require(
        isinstance(reference, str)
        and 1 <= len(reference) <= 100
        and reference.strip() == reference,
        "Invalid hold reference",
        422,
    )
    require(
        isinstance(reason, str) and 1 <= len(reason.strip()) <= 200,
        "Audited hold reason required",
        422,
    )
    require(
        (reward_user_id is None) != (voucher_id is None), "Exactly one hold target required", 422
    )
    require(kind != "admin_review" or actor_id is not None, "Administrator required", 403)
    if actor_id is not None:
        actor = db.get(User, actor_id)
        require(actor is not None and actor.is_admin, "Administrator required", 403)
    if reward_user_id is not None:
        locked_user(db, reward_user_id)
        account(db, reward_user_id)
    else:
        voucher = (
            db.query(GiftVoucher)
            .filter_by(id=voucher_id)
            .populate_existing()
            .with_for_update()
            .first()
        )
        require(voucher is not None, "Voucher not found", 404)
    old = (
        db.query(RewardVoucherHold)
        .filter_by(
            reward_user_id=reward_user_id, voucher_id=voucher_id, kind=kind, reference=reference
        )
        .first()
    )
    if old:
        require(
            old.reason == reason.strip() and old.actor_id == actor_id,
            "Hold reference reused with different audit terms",
        )
        return old
    row = RewardVoucherHold(
        reward_user_id=reward_user_id,
        voucher_id=voucher_id,
        kind=kind,
        reference=reference,
        reason=reason.strip(),
        actor_id=actor_id,
        created_at=utc_now(),
    )
    db.add(row)
    db.flush()
    return row
