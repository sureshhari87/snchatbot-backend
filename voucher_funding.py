"""Server-owned voucher issuance, audited funding and checkout recovery."""

import hashlib
import secrets
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException

from config import get_str
from financial_provider import capture_id, captured, get, order_matches
from models import User, utc_now
from rewards_vouchers_models import GiftVoucher, GiftVoucherEntry, RewardVoucherHold
from rewards_vouchers_rules import VOUCHER_DENOMINATIONS_PAISE, nonnegative_integer, voucher_expiry
from rewards_vouchers_transactions import digest, locked_user, place_hold, require
from voucher_funding_models import VoucherFunding, VoucherFundingEvent


def cipher():
    try:
        key = get_str("VOUCHER_CODE_ENCRYPTION_KEY")
        if not key:
            raise ValueError()
        return Fernet(key.encode("ascii"))
    except (ValueError, TypeError, UnicodeError):
        raise HTTPException(503, "Voucher code encryption unavailable") from None


def admin(db, actor_id):
    row = db.query(User).filter_by(id=actor_id).populate_existing().first()
    require(row is not None and row.is_admin, "Administrator required", 403)
    return row


def audit(db, row, action, evidence, actor_id=None):
    db.add(
        VoucherFundingEvent(
            voucher_id=row.id,
            action=action,
            actor_id=actor_id,
            evidence_sha256=digest(evidence),
            created_at=utc_now(),
        )
    )


def locked(db, voucher_id, user_id=None):
    query = db.query(GiftVoucher).filter_by(id=voucher_id)
    if user_id is not None:
        query = query.filter_by(purchaser_id=user_id)
    row = query.populate_existing().with_for_update().first()
    require(row is not None, "Voucher not found", 404)
    funding = (
        db.query(VoucherFunding)
        .filter_by(voucher_id=row.id)
        .populate_existing()
        .with_for_update()
        .first()
    )
    require(funding is not None, "Voucher funding record requires review")
    return row, funding


def held(db, row):
    return db.query(RewardVoucherHold).filter_by(voucher_id=row.id).first() is not None


def document(db, row, funding=None):
    funding = funding or db.get(VoucherFunding, row.id)
    review = held(db, row)
    expired = row.expires_at is not None and row.expires_at <= utc_now()
    return dict(
        id=row.id,
        source=row.source,
        state=row.state,
        payment_mode=funding.payment_mode if funding else None,
        checkout_state=funding.state if funding else None,
        amount_paise=row.amount_paise,
        balance_paise=row.balance_paise,
        reserved_paise=row.reserved_paise,
        available_paise=row.balance_paise - row.reserved_paise
        if row.state == "active" and not review and not expired
        else 0,
        assigned_user_id=row.assigned_user_id,
        review_required=review,
        expired=expired,
        activated_at=row.activated_at,
        expires_at=row.expires_at,
        created_at=row.created_at,
    )


def create(
    db,
    *,
    purchaser_id,
    request_key,
    amount_paise,
    payment_mode,
    assigned_user_id=None,
    actor_id=None,
    reason=None,
):
    nonnegative_integer(amount_paise, "amount_paise")
    require(amount_paise >= 100, "Voucher amount must be at least INR1", 422)
    require(
        isinstance(request_key, str)
        and 1 <= len(request_key) <= 100
        and request_key.strip() == request_key,
        "Invalid voucher request key",
        422,
    )
    require(
        payment_mode in ("manual", "razorpay", "complimentary"), "Invalid voucher payment mode", 422
    )
    if payment_mode == "complimentary":
        admin(db, actor_id)
        require(
            isinstance(reason, str) and 1 <= len(reason.strip()) <= 200,
            "Audited reason required",
            422,
        )
    else:
        require(actor_id is None and reason is None, "Purchased voucher terms differ", 422)
        require(
            amount_paise in VOUCHER_DENOMINATIONS_PAISE,
            "Choose an approved voucher denomination",
            422,
        )
    locked_user(db, purchaser_id)
    if assigned_user_id is not None:
        require(db.get(User, assigned_user_id) is not None, "Assigned customer not found", 404)
    sha = digest(
        dict(
            purchaser_id=purchaser_id,
            amount_paise=amount_paise,
            payment_mode=payment_mode,
            assigned_user_id=assigned_user_id,
            actor_id=actor_id,
            reason=reason.strip() if reason else None,
        )
    )
    old = (
        db.query(GiftVoucher)
        .filter_by(purchaser_id=purchaser_id, request_key=request_key)
        .populate_existing()
        .with_for_update()
        .first()
    )
    if old:
        funding = db.get(VoucherFunding, old.id)
        require(
            funding is not None and funding.request_sha256 == sha,
            "Voucher request key reused for different terms",
        )
        return old, funding
    code = "GV-" + secrets.token_hex(24).upper()
    encrypted = cipher().encrypt(code.encode()).decode("ascii")
    now = utc_now()
    row = GiftVoucher(
        purchaser_id=purchaser_id,
        assigned_user_id=assigned_user_id,
        request_key=request_key,
        code_hash=hashlib.sha256(code.encode()).hexdigest(),
        source="complimentary" if payment_mode == "complimentary" else "purchased",
        amount_paise=amount_paise,
        balance_paise=0,
        reserved_paise=0,
        state="pending",
        created_at=now,
    )
    db.add(row)
    db.flush()
    funding = VoucherFunding(
        voucher_id=row.id,
        request_sha256=sha,
        payment_mode=payment_mode,
        code_ciphertext=encrypted,
        receipt="gv_" + uuid4().hex,
        state="not_started",
        created_at=now,
    )
    db.add(funding)
    audit(
        db, row, "request_created", dict(request_sha256=sha), actor_id if actor_id else purchaser_id
    )
    db.flush()
    if payment_mode == "complimentary":
        fund_manual(
            db,
            voucher_id=row.id,
            actor_id=actor_id,
            confirmed_paise=amount_paise,
            reference="complimentary:" + str(row.id),
            reason=reason,
        )
    return row, funding


def activate(db, row, funding, *, kind, reference, evidence, actor_id=None, reason=None):
    require(
        row.state == "pending" and row.balance_paise == 0 and row.reserved_paise == 0,
        "Voucher is already funded",
    )
    require(not held(db, row), "Voucher held for audited review")
    now = utc_now()
    row.state = "active"
    row.balance_paise = row.amount_paise
    row.activated_at = now
    row.expires_at = voucher_expiry(now)
    funding.state = "posted"
    entry = GiftVoucherEntry(
        voucher_id=row.id,
        event_key=f"voucher-fund:{row.id}",
        kind=kind,
        amount_delta_paise=row.amount_paise,
        actor_id=actor_id,
        reason=reason,
        evidence_reference=reference,
        evidence_sha256=digest(evidence),
        created_at=now,
    )
    db.add(entry)
    audit(db, row, "funding_posted", evidence, actor_id)
    db.flush()
    return entry


def fund_manual(db, *, voucher_id, actor_id, confirmed_paise, reference, reason):
    admin(db, actor_id)
    nonnegative_integer(confirmed_paise, "confirmed_paise")
    require(
        isinstance(reference, str)
        and 1 <= len(reference) <= 100
        and reference.strip() == reference,
        "Payment receipt reference required",
        422,
    )
    require(
        isinstance(reason, str) and 1 <= len(reason.strip()) <= 200,
        "Audited confirmation reason required",
        422,
    )
    row, funding = locked(db, voucher_id)
    kind = "complimentary" if funding.payment_mode == "complimentary" else "manual_funding"
    require(
        funding.payment_mode in ("manual", "complimentary"),
        "Online voucher cannot be manually funded",
    )
    require(
        confirmed_paise == row.amount_paise,
        "Confirmed payment must equal the requested voucher amount",
    )
    old = (
        db.query(GiftVoucherEntry)
        .filter(GiftVoucherEntry.voucher_id == row.id, GiftVoucherEntry.kind != "redemption")
        .first()
    )
    if old:
        require(
            old.kind == kind
            and old.evidence_reference == reference
            and old.actor_id == actor_id
            and old.reason == reason.strip(),
            "Voucher is funded against different audit evidence",
        )
        return document(db, row, funding)
    activate(
        db,
        row,
        funding,
        kind=kind,
        reference=reference,
        actor_id=actor_id,
        reason=reason.strip(),
        evidence=dict(
            voucher_id=row.id,
            amount_paise=row.amount_paise,
            actor_id=actor_id,
            reference=reference,
            reason=reason.strip(),
            kind=kind,
        ),
    )
    return document(db, row, funding)


def reveal(db, voucher_id, user_id):
    row = db.get(GiftVoucher, voucher_id)
    require(
        row is not None and user_id in (row.purchaser_id, row.assigned_user_id),
        "Voucher not found",
        404,
    )
    require(
        row.state == "active" and row.expires_at > utc_now() and not held(db, row),
        "Voucher unavailable",
    )
    funding = db.get(VoucherFunding, row.id)
    require(funding is not None, "Voucher code requires review")
    try:
        code = cipher().decrypt(funding.code_ciphertext.encode()).decode("ascii")
    except (InvalidToken, UnicodeError, ValueError):
        raise HTTPException(503, "Voucher code unavailable") from None
    require(
        hashlib.sha256(code.encode()).hexdigest() == row.code_hash, "Voucher code requires review"
    )
    return dict(voucher_id=row.id, code=code)


def matches(order, row, funding, order_id=None):
    return order_matches(
        order,
        order_id=order_id,
        receipt=funding.receipt,
        amount_paise=row.amount_paise,
        purpose="sona_voucher_funding",
        reference_name="voucher_id",
        reference=row.id,
    )


def bind(db, row, funding, order, actor_id=None):
    require(matches(order, row, funding), "Provider order differs from voucher funding")
    if funding.provider_order_id:
        require(
            funding.provider_order_id == order["id"],
            "Voucher checkout already bound to a different order",
        )
        return
    require(
        funding.payment_mode == "razorpay" and funding.state in ("creating", "unknown"),
        "Existing online funding attempt required",
    )
    funding.provider_order_id = order["id"]
    funding.state = "ready"
    audit(
        db,
        row,
        "checkout_bound",
        dict(provider_order_id=order["id"], receipt=funding.receipt),
        actor_id,
    )
    db.flush()


def checkout(db, voucher_id, user_id, provider):
    row, funding = locked(db, voucher_id, user_id)
    require(funding.payment_mode == "razorpay", "This voucher uses manual funding")
    require(row.state == "pending" and not held(db, row), "Voucher is funded or held for review")
    if funding.state == "ready":
        order = get(provider, "orders/" + funding.provider_order_id)
        require(
            matches(order, row, funding, funding.provider_order_id),
            "Provider order differs from voucher funding",
        )
        if order["status"] == "paid":
            result = post_online(
                db, row, funding, capture_id(provider, funding.provider_order_id), provider, order
            )
            if result["review_required"]:
                db.commit()
                raise HTTPException(409, "Voucher funding requires audited review")
        else:
            require(
                order["status"] == "created"
                and type(order.get("attempts")) is int
                and order["attempts"] == 0,
                "Attempted voucher checkout requires reconciliation; no new order created",
            )
        return row, funding
    require(
        funding.state == "not_started",
        "Voucher checkout outcome requires reconciliation; no new order created",
    )
    funding.state = "creating"
    audit(
        db,
        row,
        "checkout_started",
        dict(receipt=funding.receipt, amount_paise=row.amount_paise),
        user_id,
    )
    db.commit()
    payload = dict(
        amount=row.amount_paise,
        currency="INR",
        receipt=funding.receipt,
        partial_payment=False,
        notes=dict(purpose="sona_voucher_funding", voucher_id=str(row.id)),
    )
    try:
        status, order = provider.call_razorpay("POST", "orders", payload)
        valid = status in (200, 201) and matches(order, row, funding)
    except Exception:
        valid = False
        order = None
    row, funding = locked(db, voucher_id, user_id)
    if funding.state in ("ready", "posted"):
        require(
            not valid or funding.provider_order_id == order["id"],
            "Voucher checkout binding changed",
        )
        return row, funding
    if not valid:
        if funding.state == "creating":
            funding.state = "unknown"
            audit(db, row, "checkout_unknown", dict(receipt=funding.receipt))
        db.commit()
        raise HTTPException(503, "Voucher checkout outcome unknown; store reconciliation required")
    bind(db, row, funding, order, user_id)
    return row, funding


def post_online(db, row, funding, payment_id, provider, order=None):
    require(
        funding.payment_mode == "razorpay" and funding.provider_order_id is not None,
        "Bound online voucher checkout required",
    )
    order = order or get(provider, "orders/" + funding.provider_order_id)
    require(
        matches(order, row, funding, funding.provider_order_id),
        "Provider order differs from voucher funding",
    )
    payment, adverse = captured(
        provider,
        order=order,
        order_id=funding.provider_order_id,
        payment_id=payment_id,
        amount_paise=row.amount_paise,
    )
    if adverse:
        for kind, reference, reason in adverse:
            place_hold(db, kind=kind, reference=reference, reason=reason, voucher_id=row.id)
        db.flush()
        return document(db, row, funding)
    old = (
        db.query(GiftVoucherEntry)
        .filter(GiftVoucherEntry.voucher_id == row.id, GiftVoucherEntry.kind != "redemption")
        .first()
    )
    if old:
        require(
            old.kind == "razorpay_funding"
            and funding.provider_payment_id == payment_id
            and old.evidence_reference == payment_id,
            "Voucher funded by different payment evidence",
        )
        return document(db, row, funding)
    require(funding.state == "ready", "Online funding attempt requires review")
    funding.provider_payment_id = payment_id
    activate(
        db,
        row,
        funding,
        kind="razorpay_funding",
        reference=payment_id,
        evidence=dict(
            voucher_id=row.id,
            payment_id=payment_id,
            provider_order_id=funding.provider_order_id,
            amount_paise=row.amount_paise,
        ),
    )
    return document(db, row, funding)


def refresh(db, voucher_id, user_id, provider, *, admin_access=False, provider_order_id=None):
    row, funding = locked(db, voucher_id, None if admin_access else user_id)
    require(funding.payment_mode == "razorpay", "Voucher uses manual funding")
    target = funding.provider_order_id or (provider_order_id if admin_access else None)
    require(
        target is not None and (provider_order_id is None or provider_order_id == target),
        "Voucher order requires store reconciliation",
    )
    from financial_provider import identifier

    require(identifier(target, "order"), "Invalid order reference", 422)
    order = get(provider, "orders/" + target)
    require(matches(order, row, funding, target), "Provider order differs from voucher funding")
    bind(db, row, funding, order, user_id if admin_access else None)
    if order.get("status") == "paid":
        return post_online(db, row, funding, capture_id(provider, target), provider, order)
    return document(db, row, funding)
