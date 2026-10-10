"""Financial jewellery checkout saga; feature-gated and SQL catalogue only."""

import hashlib
import json
import re
from dataclasses import asdict
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException

from config import get_bool
from financial_provider import capture_id, captured, get, order_matches
from models import OrderSnapshot, OrderSnapshotItem, utc_now
from rewards_vouchers_models import GiftVoucher, RewardAccount, RewardVoucherHold
from rewards_vouchers_reservation_models import FinancialReservation
from rewards_vouchers_rules import calculate_checkout
from rewards_vouchers_transactions import (
    bind,
    digest,
    locked_reservation,
    locked_user,
    place_hold,
    require,
    reserve,
    settle,
)
from voucher_funding_models import FinancialCheckoutAttempt

ENABLED = get_bool("REWARDS_VOUCHERS_ENABLED", False)


def enabled():
    require(ENABLED, "Rewards/vouchers migration is not enabled", 503)


def request_terms(payload):
    require(
        payload.commerce_source == "fastapi",
        "Use the FastAPI catalogue for financial checkout",
        422,
    )
    require(payload.currency == "INR", "Financial checkout supports INR only", 422)
    require(not payload.coupon_code, "SQL coupons require separate catalogue configuration")
    require(
        isinstance(payload.request_key, str)
        and 1 <= len(payload.request_key) <= 100
        and payload.request_key.strip() == payload.request_key,
        "A stable checkout request key is required",
        422,
    )
    code = payload.gift_voucher_code
    require(
        code is None or isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9-]{8,80}", code),
        "Voucher unavailable",
        404,
    )
    return dict(
        items=[item.model_dump(mode="json") for item in payload.items],
        amount=payload.amount,
        currency=payload.currency,
        gift_voucher_hash=hashlib.sha256(code.upper().encode()).hexdigest() if code else None,
        reward_points_requested=payload.reward_points_requested,
        delivery_address=payload.delivery_address,
        customer_name=payload.customer_name,
        customer_email=str(payload.customer_email) if payload.customer_email else None,
        customer_phone=payload.customer_phone,
    )


def base_quote(db, payload, user, provider):
    # Client notes, bearer codes and Firebase tokens never enter saved snapshots.
    clean = payload.model_copy(
        update=dict(
            amount=None,
            gift_voucher_code=None,
            reward_points_requested=0,
            notes={},
            firebase_id_token=None,
        )
    )
    result = provider.authoritative_checkout_calculation(db, clean, user)
    for item in clean.items:
        product = provider.lookup_authoritative_product(db, item)
        try:
            price = Decimal(str(product.price)) * 100
            valid = price.is_finite() and price >= 0 and price == price.to_integral_value()
        except (InvalidOperation, ValueError, AttributeError):
            valid = False
        require(valid, "Catalogue price requires minor-unit review")
    return result


def preview(db, payload, user, provider):
    sha = digest(request_terms(payload))
    existing = (
        db.query(FinancialReservation)
        .filter_by(user_id=user.id, request_key=payload.request_key)
        .first()
    )
    if existing is not None:
        attempt = db.get(FinancialCheckoutAttempt, existing.id)
        require(
            attempt is not None and attempt.client_request_sha256 == sha,
            "Checkout request key reused with different items or terms",
        )
        return json.loads(existing.quote_json)
    base = base_quote(db, payload, user, provider)
    account = db.get(RewardAccount, user.id)
    points = account.balance_points - account.reserved_points if account else 0
    if payload.reward_points_requested:
        require(
            not db.query(RewardVoucherHold).filter_by(reward_user_id=user.id).first(),
            "Reward account held for audited review",
        )
    voucher = None
    if payload.gift_voucher_code:
        sha = hashlib.sha256(payload.gift_voucher_code.upper().encode()).hexdigest()
        voucher = db.query(GiftVoucher).filter_by(code_hash=sha).first()
        require(
            voucher is not None
            and voucher.state == "active"
            and voucher.expires_at > utc_now()
            and voucher.assigned_user_id in (None, user.id),
            "Voucher unavailable",
            404,
        )
        require(
            not db.query(RewardVoucherHold).filter_by(voucher_id=voucher.id).first(),
            "Voucher held for audited review",
        )
    quote = calculate_checkout(
        merchandise_paise=base["amount"],
        reward_points_requested=payload.reward_points_requested,
        reward_points_available=points,
        voucher_available_paise=voucher.balance_paise - voucher.reserved_paise if voucher else 0,
    )
    return asdict(quote)


def context(base, payload, row):
    quote = json.loads(row.quote_json)
    metadata = base["metadata"]
    metadata.update(
        financial_reservation_id=row.id,
        financial_quote=quote,
        payable_amount=row.payable_paise,
        payable_total=row.payable_paise / 100,
    )
    metadata["rewards"] = dict(
        requested_points=payload.reward_points_requested,
        consumed_points=row.reward_points,
        status="reserved",
    )
    metadata["gift_voucher"] = dict(
        voucher_id=row.voucher_id,
        redeemed_paise=row.voucher_paise,
        status="reserved" if row.voucher_id else "not_requested",
    )
    return dict(
        items=[item.model_dump(mode="json") for item in base["items"]],
        metadata=metadata,
        customer_name=payload.customer_name,
        customer_email=str(payload.customer_email) if payload.customer_email else None,
        customer_phone=payload.customer_phone,
        delivery_address=payload.delivery_address,
    )


def matches(order, row, order_id=None):
    return order_matches(
        order,
        order_id=order_id,
        receipt=row.receipt,
        amount_paise=row.payable_paise,
        purpose="sona_rewards_checkout",
        reference_name="reservation_id",
        reference=row.id,
    )


def saved_attempt(db, row):
    attempt = (
        db.query(FinancialCheckoutAttempt)
        .filter_by(reservation_id=row.id)
        .populate_existing()
        .with_for_update()
        .first()
    )
    require(attempt is not None, "Financial checkout attempt requires review")
    return attempt


def bind_checkout(db, row, attempt, order):
    require(matches(order, row), "Provider order differs from reserved checkout")
    if row.order_id is not None:
        require(
            row.provider_order_id == order["id"], "Financial checkout bound to a different order"
        )
        return db.get(OrderSnapshot, row.order_id)
    require(
        row.state == "reserved" and attempt.state in ("creating", "unknown"),
        "Existing reserved checkout required",
    )
    saved = json.loads(attempt.context_json)
    now = utc_now()
    local = OrderSnapshot(
        user_id=row.user_id,
        order_reference=order["id"],
        status="payment_pending",
        total=row.payable_paise / 100,
        currency="INR",
        customer_name=saved["customer_name"],
        customer_email=saved["customer_email"],
        customer_phone=saved["customer_phone"],
        delivery_address=json.dumps(saved["delivery_address"]),
        payment_status="pending",
        source="razorpay_checkout",
        raw_payload=json.dumps(dict(metadata=saved["metadata"])),
        created_at=now,
        updated_at=now,
    )
    db.add(local)
    db.flush()
    for item in saved["items"]:
        db.add(
            OrderSnapshotItem(
                order_id=local.id,
                product_reference=item["product_id"],
                backend_product_id=item["backend_product_id"],
                name=item["name"],
                qty=item["qty"],
                unit_price=item["price"],
                image=item["image"],
                created_at=now,
            )
        )
    bind(db, reservation_id=row.id, user_id=row.user_id, order_id=local.id, provider_order=order)
    attempt.state = "ready"
    db.flush()
    return local


def response(row, provider):
    quote = json.loads(row.quote_json)
    return dict(
        key_id=provider.RAZORPAY_KEY_ID,
        keyId=provider.RAZORPAY_KEY_ID,
        order_id=row.provider_order_id,
        razorpayOrderId=row.provider_order_id,
        order_reference=row.provider_order_id,
        local_order_id=row.order_id,
        amount=row.payable_paise,
        currency="INR",
        receipt=row.receipt,
        status="paid" if row.state == "posted" else "created",
        payable_total=row.payable_paise / 100,
        payableTotal=row.payable_paise / 100,
        coupon_discount=0,
        couponDiscount=0,
        reward_points_used=row.reward_points,
        rewardPointsUsed=row.reward_points,
        server_calculated=True,
        voucher_redemption_paise=row.voucher_paise,
        reservation_id=row.id,
        financial_quote=quote,
    )


def checkout(db, payload, user, provider):
    enabled()
    sha = digest(request_terms(payload))
    locked_user(db, user.id)
    old = (
        db.query(FinancialReservation)
        .filter_by(user_id=user.id, request_key=payload.request_key)
        .populate_existing()
        .with_for_update()
        .first()
    )
    if old:
        attempt = saved_attempt(db, old)
        require(
            attempt.client_request_sha256 == sha,
            "Checkout request key reused with different items or terms",
        )
        require(
            attempt.state == "ready" and old.provider_order_id is not None,
            "Checkout outcome requires reconciliation; no new order created",
        )
        order = get(provider, "orders/" + old.provider_order_id)
        require(
            matches(order, old, old.provider_order_id),
            "Provider order differs from reserved checkout",
        )
        if order["status"] == "paid":
            result = provider.finalize_razorpay_payment(
                db,
                db.get(OrderSnapshot, old.order_id),
                capture_id(provider, old.provider_order_id),
                "financial_retry_recovery",
            )
            if result["status"] == "held":
                db.commit()
                raise HTTPException(409, "Financial payment requires audited review")
        else:
            require(
                order["status"] == "created"
                and type(order.get("attempts")) is int
                and order["attempts"] == 0,
                "Attempted financial checkout requires reconciliation; no new order created",
            )
        return response(old, provider)
    base = base_quote(db, payload, user, provider)
    row = reserve(
        db,
        user_id=user.id,
        request_key=payload.request_key,
        merchandise_paise=base["amount"],
        reward_points_requested=payload.reward_points_requested,
        voucher_code=payload.gift_voucher_code,
    )
    require(
        payload.amount is None or payload.amount == row.payable_paise,
        "Checkout amount changed; refresh the quote",
    )
    attempt = FinancialCheckoutAttempt(
        reservation_id=row.id,
        client_request_sha256=sha,
        context_json=json.dumps(context(base, payload, row), sort_keys=True),
        state="creating",
        created_at=utc_now(),
    )
    db.add(attempt)
    db.commit()
    request = dict(
        amount=row.payable_paise,
        currency="INR",
        receipt=row.receipt,
        partial_payment=False,
        notes=dict(purpose="sona_rewards_checkout", reservation_id=str(row.id)),
    )
    try:
        status, order = provider.call_razorpay("POST", "orders", request)
        valid = status in (200, 201) and matches(order, row)
    except Exception:
        valid = False
        order = None
    row = locked_reservation(db, row.id, user.id)
    attempt = saved_attempt(db, row)
    if attempt.state == "ready":
        require(not valid or row.provider_order_id == order["id"], "Checkout binding changed")
        return response(row, provider)
    if not valid:
        attempt.state = "unknown"
        db.commit()
        raise HTTPException(
            503, "Financial checkout outcome unknown; store reconciliation required"
        )
    bind_checkout(db, row, attempt, order)
    return response(row, provider)


def for_order(db, order):
    try:
        raw = json.loads(order.raw_payload or "{}")
    except (ValueError, TypeError):
        raw = {}
    metadata = raw.get("metadata") if isinstance(raw, dict) else None
    metadata = metadata if isinstance(metadata, dict) else {}
    if not ENABLED:
        require(
            not metadata.get("financial_reservation_id"), "Financial settlement is disabled", 503
        )
        return None
    row = db.query(FinancialReservation).filter_by(order_id=order.id).first()
    require(
        row is not None or not metadata.get("financial_reservation_id"),
        "Financial order binding requires review",
    )
    if row is not None:
        require(
            row.user_id == order.user_id and row.provider_order_id == order.order_reference,
            "Financial order ownership or binding differs",
        )
        locked_user(db, row.user_id)
    return row


def hold_targets(db, row, adverse):
    for kind, reference, reason in adverse:
        place_hold(db, kind=kind, reference=reference, reason=reason, reward_user_id=row.user_id)
    if row.voucher_id is not None:
        for kind, reference, reason in adverse:
            place_hold(db, kind=kind, reference=reference, reason=reason, voucher_id=row.voucher_id)
    db.flush()


def evidence(db, row, payment_id, provider):
    row = locked_reservation(db, row.id, row.user_id)
    order = get(provider, "orders/" + row.provider_order_id)
    require(
        matches(order, row, row.provider_order_id), "Provider order differs from reserved checkout"
    )
    payment, adverse = captured(
        provider,
        order=order,
        order_id=row.provider_order_id,
        payment_id=payment_id,
        amount_paise=row.payable_paise,
    )
    if adverse:
        hold_targets(db, row, adverse)
    return order, payment, adverse


def post(db, row, order, payment):
    settle(
        db,
        reservation_id=row.id,
        user_id=row.user_id,
        provider_order=order,
        provider_payment=payment,
    )
    quote = json.loads(row.quote_json)
    return dict(
        reward_points_consumed=row.reward_points,
        purchase_rewards_awarded=quote["reward_points_earned"],
        referral_rewards_awarded=0,
        gift_voucher_redeemed=bool(row.voucher_paise),
        voucher_redemption_paise=row.voucher_paise,
    )


def refresh(db, reservation_id, user_id, provider, *, admin_access=False, provider_order_id=None):
    row = db.get(FinancialReservation, reservation_id)
    require(row is not None and (admin_access or row.user_id == user_id), "Checkout not found", 404)
    row = locked_reservation(db, reservation_id, row.user_id)
    attempt = saved_attempt(db, row)
    target = row.provider_order_id or (provider_order_id if admin_access else None)
    from financial_provider import identifier

    require(
        identifier(target, "order") and (provider_order_id is None or provider_order_id == target),
        "Checkout requires store reconciliation",
    )
    order = get(provider, "orders/" + target)
    require(matches(order, row, target), "Provider order differs from reserved checkout")
    local = bind_checkout(db, row, attempt, order)
    if order.get("status") == "paid":
        result = provider.finalize_razorpay_payment(
            db, local, capture_id(provider, target), "financial_refresh"
        )
        return dict(
            status=result["status"],
            reservation_id=row.id,
            order=provider.serialize_order_snapshot(db, result["order"]),
        )
    return dict(
        status="pending", reservation_id=row.id, order=provider.serialize_order_snapshot(db, local)
    )


def finalize(db, row, local, payment_id, source, provider, event_id=None):
    """Own financial path: posting, stock, cart, order and notifications are atomic."""
    local = (
        db.query(OrderSnapshot).filter_by(id=local.id).populate_existing().with_for_update().one()
    )
    row = locked_reservation(db, row.id, row.user_id)
    require(
        row.order_id == local.id
        and row.provider_order_id == local.order_reference
        and row.user_id == local.user_id
        and local.currency == "INR",
        "Financial order binding requires review",
    )
    attempt = saved_attempt(db, row)
    saved = json.loads(attempt.context_json)
    actual = [
        dict(
            product_id=item.product_reference,
            backend_product_id=item.backend_product_id,
            name=item.name,
            qty=item.qty,
            price=item.unit_price,
            image=item.image,
        )
        for item in db.query(OrderSnapshotItem)
        .filter_by(order_id=local.id)
        .order_by(OrderSnapshotItem.id)
        .all()
    ]
    require(actual == saved["items"], "Financial order items differ from the frozen checkout")
    provider_order, payment, adverse = evidence(db, row, payment_id, provider)
    held_account = db.query(RewardVoucherHold).filter_by(reward_user_id=row.user_id).first()
    held_voucher = (
        db.query(RewardVoucherHold).filter_by(voucher_id=row.voucher_id).first()
        if row.voucher_id
        else None
    )
    if adverse or held_account or held_voucher:
        return dict(status="held", order=local)
    if row.state == "posted":
        require(
            row.provider_payment_id == payment_id
            and provider.order_already_finalized(local, payment_id),
            "Financial posting/order state requires review",
        )
        return dict(status="idempotent", order=local)
    require(
        not provider.order_already_finalized(local, payment_id)
        and local.payment_status not in ("verified", "paid"),
        "Financial order already finalized against different evidence",
    )
    try:
        with db.begin_nested():
            financial = post(db, row, provider_order, payment)
            inventory = provider.decrement_inventory_for_order(db, local)
            from commerce import clean_paid_cart

            cleanup = clean_paid_cart(db, local)
            now = utc_now()
            local.payment_status = "verified"
            local.payment_reference = payment_id
            if local.status in ("", "created", "payment_pending", "pending_payment"):
                local.status = "placed"
            local.updated_at = now
            raw = provider.order_raw_payload(local)
            raw["razorpay_finalization"] = dict(
                status="finalized",
                payment_id=payment_id,
                source=source,
                event_id=event_id,
                amount=row.payable_paise,
                currency="INR",
                finalized_at=now.isoformat(),
                inventory_decremented=True,
                inventory_updates=inventory,
                **financial,
                **cleanup,
            )
            local.raw_payload = json.dumps(raw)
            from order_notifications import enqueue_verified_payment

            enqueue_verified_payment(db, local)
            provider.record_integration_event(
                db,
                service="razorpay",
                action="finalize_payment",
                status_value="finalized",
                user_id=row.user_id,
                reference=row.provider_order_id,
                request_payload=dict(payment_id=payment_id, source=source, event_id=event_id),
                response_payload=raw["razorpay_finalization"],
            )
            db.flush()
    except HTTPException:
        provider.mark_razorpay_finalization_failure(
            db, local, payment_id, "financial_finalization_failed", source, {}, event_id
        )
        raise
    return dict(status="finalized", order=local)
