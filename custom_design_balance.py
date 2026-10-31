"""Server-owned remaining-quote accounting and conservative checkout recovery."""

import os
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import inspect

from custom_design_api import audit, latest_quote, locked, quote_document, timestamp
from custom_design_balance_models import CustomDesignBalanceCheckout as Checkout
from custom_design_balance_models import CustomDesignBalanceEntry as Entry
from custom_design_balance_models import CustomDesignBalanceRequest as BalanceRequest
from custom_design_models import CustomDesignAudit, CustomDesignDecision
from custom_design_payments import (
    CustomDesignAdvance,
    CustomDesignCheckout,
    available,
    fingerprint,
    held,
    hold,
)
from financial_provider import capture_id, captured, get, identifier, order_matches
from models import CustomOrderRequest, utc_now

ENABLED = os.getenv("CUSTOM_DESIGN_BALANCE_ENABLED", "0").strip() == "1"
ONLINE = os.getenv("CUSTOM_DESIGN_BALANCE_RAZORPAY_ENABLED", "0").strip() == "1"
PURPOSE = "sona_custom_design_balance"


def storage_ready(db):
    key = "custom_balance_storage_ready"
    if key not in db.info:
        inspector = inspect(db.connection())
        db.info[key] = all(
            inspector.has_table(name)
            for name in [
                "custom_design_balance_requests",
                "custom_design_balance_checkouts",
                "custom_design_balance_entries",
            ]
        )
    return db.info[key]


def enabled():
    if not ENABLED:
        raise HTTPException(503, "Custom-design balance migration is not enabled")


def latest_checkout(db, request_id):
    return (
        db.query(Checkout)
        .filter_by(request_id=request_id)
        .order_by(Checkout.sequence.desc())
        .first()
    )


def accounting(db, row):
    quote = latest_quote(db, row.id)
    advance = db.query(CustomDesignAdvance).filter_by(design_id=row.id).first()
    request = db.query(BalanceRequest).filter_by(design_id=row.id).first()
    entry = db.query(Entry).filter_by(design_id=row.id).first()
    if not quote:
        if advance or request or entry:
            raise HTTPException(409, "Payment evidence exists without a quote")
        return quote, advance, request, entry, 0, None
    advance_audits = (
        db.query(CustomDesignAudit).filter_by(design_id=row.id, action="advance_posted").all()
    )
    if advance:
        expected = fingerprint(
            [advance.quote_id, advance.provider_payment_id, advance.amount_paise]
        )
        if (
            len(advance_audits) != 1
            or advance_audits[0].quote_id != advance.quote_id
            or advance_audits[0].actor_id is not None
            or advance_audits[0].payload_sha256 != expected
        ):
            raise HTTPException(409, "Advance posting audit requires reconciliation")
    elif advance_audits:
        raise HTTPException(409, "Advance audit exists without a posting")
    if advance and (advance.quote_id != quote.id or advance.amount_paise != quote.advance_paise):
        raise HTTPException(409, "Advance evidence differs from the current quote")
    advance_paise = advance.amount_paise if advance else 0
    if request and (
        request.quote_id != quote.id or request.amount_paise != quote.total_paise - advance_paise
    ):
        raise HTTPException(409, "Balance intent differs from the accepted quote")
    if entry and (
        not request
        or entry.request_id != request.id
        or entry.quote_id != quote.id
        or entry.mode != request.mode
        or entry.amount_paise != request.amount_paise
    ):
        raise HTTPException(409, "Balance posting differs from the frozen intent")
    posting = db.query(CustomDesignAudit).filter_by(design_id=row.id, action="balance_posted").all()
    if entry:
        if len(posting) != 1 or posting[0].quote_id != quote.id:
            raise HTTPException(409, "Balance posting audit requires reconciliation")
        event = posting[0]
        if entry.mode == "manual":
            expected = fingerprint(
                [request.id, entry.reference, event.detail, entry.actor_id, entry.amount_paise]
            )
            valid = event.actor_id == entry.actor_id
        else:
            attempt = db.get(Checkout, entry.checkout_id)
            valid = bool(
                attempt
                and attempt.request_id == request.id
                and attempt.provider_order_id == entry.provider_order_id
                and event.actor_id is None
            )
            expected = fingerprint(
                [request.id, entry.checkout_id, entry.provider_payment_id, entry.amount_paise]
            )
        if not valid or event.payload_sha256 != expected:
            raise HTTPException(409, "Balance evidence and audit differ")
    elif posting:
        raise HTTPException(409, "Balance audit exists without a posting")
    paid = advance_paise + (entry.amount_paise if entry else 0)
    if paid > quote.total_paise:
        raise HTTPException(409, "Custom-design payment evidence exceeds the quote")
    return quote, advance, request, entry, paid, quote.total_paise - paid


def accepted(db, row):
    available(db, row)
    if not storage_ready(db):
        raise HTTPException(503, "Custom-design balance storage is not ready")
    quote, advance, request, entry, paid, due = accounting(db, row)
    decision = (
        (
            db.query(CustomDesignDecision)
            .filter_by(quote_id=quote.id)
            .order_by(CustomDesignDecision.id.desc())
            .first()
        )
        if quote
        else None
    )
    if not decision or decision.action != "approved":
        raise HTTPException(409, "Accept the current quote before collecting a balance")
    if quote.advance_paise and not advance:
        raise HTTPException(409, "Verify the required advance before collecting the balance")
    return quote, advance, request, entry, paid, due


def snapshot(db, row):
    completed = (
        db.query(CustomDesignAudit).filter_by(design_id=row.id, action="completed").first()
        is not None
    )
    base = dict(
        balance_feature_enabled=ENABLED,
        balance_online_enabled=ENABLED and ONLINE,
        balance_request=None,
        balance_due_paise=None,
        paid_total_paise=None,
        balance_request_available=False,
        balance_checkout_available=False,
        completion_available=False,
        completed=completed,
        payment_consistent=True,
        completion_requires_provider_verification=False,
    )
    if not storage_ready(db):
        if ENABLED:
            raise HTTPException(503, "Custom-design balance storage is not ready")
        return base
    try:
        quote, advance, request, entry, paid, due = accounting(db, row)
    except HTTPException:
        base["payment_consistent"] = False
        return base
    state = quote_document(quote, db)["state"] if quote else None
    accepted_now = state == "approved"
    review = held(db, row.id)
    required_advance_paid = bool(quote and (not quote.advance_paise or advance))
    started = (
        db.query(CustomDesignAudit)
        .filter_by(design_id=row.id, action="manufacturing_started")
        .first()
        is not None
    )
    attempt = latest_checkout(db, request.id) if request else None
    base.update(
        paid_total_paise=paid,
        balance_due_paise=due,
        completion_requires_provider_verification=bool(
            advance or entry and entry.mode == "razorpay"
        ),
        balance_request_available=bool(
            ENABLED
            and accepted_now
            and required_advance_paid
            and due
            and not request
            and not review
        ),
        balance_checkout_available=bool(
            ENABLED
            and ONLINE
            and request
            and request.mode == "razorpay"
            and not entry
            and not review
            and (not attempt or attempt.state in ("ready", "closed_failed"))
        ),
        completion_available=bool(
            ENABLED
            and quote
            and accepted_now
            and required_advance_paid
            and due == 0
            and started
            and not review
            and not completed
        ),
    )
    if request:
        base["balance_request"] = dict(
            id=request.id,
            quote_id=request.quote_id,
            mode=request.mode,
            amount_paise=request.amount_paise,
            state="held"
            if review
            else "posted"
            if entry
            else "awaiting_verification"
            if attempt and attempt.state == "paid_pending"
            else "pending",
            checkout_state=attempt.state if attempt else None,
            entry_id=entry.id if entry else None,
            created_at=timestamp(request.created_at),
        )
    return base


def freeze_guard(db, row):
    if storage_ready(db) and db.query(BalanceRequest).filter_by(design_id=row.id).first():
        raise HTTPException(
            409, "A frozen balance intent exists; audited review is required before changing terms"
        )


def request_balance(db, design_id, user_id, payload):
    row = locked(db, design_id, user_id)
    quote, advance, old, entry, paid, due = accepted(db, row)
    if payload.quote_id != quote.id:
        raise HTTPException(409, "Quote changed; reload the accepted terms")
    if old:
        import purchase_identity

        purchase_identity.require_existing_payment(
            db, user_id, quote.total_paise, resource_kind="custom_quote", resource_key=str(quote.id)
        )
        if old.mode != payload.mode:
            raise HTTPException(409, "The frozen balance request uses a different payment mode")
        return old
    if not due:
        raise HTTPException(409, "Accepted quote is already fully paid")
    if payload.mode == "razorpay" and (not ONLINE or due < 100):
        raise HTTPException(
            409, "Online balance collection is unavailable; use the verified manual flow"
        )
    sha = fingerprint([quote.id, payload.mode, due])
    import purchase_identity

    buyer_identity = purchase_identity.require_verified(db, user_id, quote.total_paise)
    purchase_identity.bind(
        db,
        buyer_identity,
        resource_kind="custom_quote",
        resource_key=str(quote.id),
        amount_paise=quote.total_paise,
    )
    request = BalanceRequest(
        design_id=row.id,
        quote_id=quote.id,
        actor_id=user_id,
        request_key=payload.request_key,
        payload_sha256=sha,
        amount_paise=due,
        mode=payload.mode,
        created_at=utc_now(),
    )
    db.add(request)
    db.flush()
    audit(
        db, row, "balance-request:" + str(request.id), "balance_requested", user_id, sha, quote.id
    )
    db.flush()
    return request


def manual_post(db, design_id, admin, payload):
    row = locked(db, design_id)
    quote, advance, request, old, paid, due = accepted(db, row)
    if not request or request.id != payload.request_id or request.mode != "manual":
        raise HTTPException(409, "Read the existing manual balance request before confirmation")
    sha = fingerprint(
        [request.id, payload.reference, payload.reason, admin.id, request.amount_paise]
    )
    old_audit = (
        db.query(CustomDesignAudit)
        .filter_by(design_id=row.id, event_key="balance-posted:" + str(request.id))
        .first()
    )
    if old:
        if (
            old.mode != "manual"
            or old.reference != payload.reference
            or old.actor_id != admin.id
            or not old_audit
            or old_audit.payload_sha256 != sha
        ):
            raise HTTPException(409, "Manual balance is already posted against different evidence")
        return old
    if due != request.amount_paise:
        raise HTTPException(409, "Remaining amount differs from the frozen balance request")
    entry = Entry(
        design_id=row.id,
        request_id=request.id,
        quote_id=quote.id,
        amount_paise=request.amount_paise,
        mode="manual",
        reference=payload.reference,
        actor_id=admin.id,
        created_at=utc_now(),
    )
    db.add(entry)
    audit(
        db,
        row,
        "balance-posted:" + str(request.id),
        "balance_posted",
        admin.id,
        sha,
        quote.id,
        detail=payload.reason,
    )
    db.flush()
    return entry


def matches(order, request, attempt):
    return order_matches(
        order,
        order_id=attempt.provider_order_id,
        receipt=attempt.receipt,
        amount_paise=request.amount_paise,
        purpose=PURPOSE,
        reference_name="balance_request_id",
        reference=request.id,
    )


def provider_ready(provider):
    if not provider.razorpay_checkout_is_configured():
        raise HTTPException(503, "Balance payment provider unavailable")


def observe_paid(db, row, request, attempt, order):
    if (
        order.get("status") != "paid"
        or type(order.get("amount_paid")) is not int
        or order["amount_paid"] != request.amount_paise
        or type(order.get("amount_due")) is not int
        or order["amount_due"] != 0
    ):
        raise HTTPException(409, "Bound balance order is not fully paid")
    if attempt.state == "ready":
        attempt.state = "paid_pending"
        audit(
            db,
            row,
            "balance-paid-seen:" + str(attempt.id),
            "balance_verification_pending",
            None,
            fingerprint([request.id, attempt.id, attempt.provider_order_id]),
            request.quote_id,
        )
        db.commit()
        locked(db, row.id)


def failed_attempt(db, row, request, attempt, order, provider):
    if (
        order.get("status") != "attempted"
        or type(order.get("attempts")) is not int
        or order["attempts"] < 1
        or type(order.get("amount_paid")) is not int
        or order["amount_paid"] != 0
        or type(order.get("amount_due")) is not int
        or order["amount_due"] != request.amount_paise
    ):
        raise HTTPException(
            409, "Existing balance attempt requires verification; no new order created"
        )
    evidence = get(provider, "orders/" + attempt.provider_order_id + "/payments")
    items = evidence.get("items")
    if (
        evidence.get("entity") != "collection"
        or not isinstance(items, list)
        or type(evidence.get("count")) is not int
        or evidence["count"] != len(items)
        or not 0 < len(items) < 100
        or len(items) != order["attempts"]
    ):
        raise HTTPException(503, "Failed balance attempt evidence is incomplete")
    seen = set()
    for payment in items:
        if (
            not isinstance(payment, dict)
            or payment.get("entity") != "payment"
            or not identifier(payment.get("id"), "pay")
            or payment["id"] in seen
            or payment.get("order_id") != attempt.provider_order_id
            or payment.get("currency") != "INR"
            or type(payment.get("amount")) is not int
            or payment["amount"] != request.amount_paise
            or payment.get("status") != "failed"
            or payment.get("captured") is not False
        ):
            raise HTTPException(409, "Existing balance payment is not a verified terminal failure")
        seen.add(payment["id"])
    attempt.state = "closed_failed"
    audit(
        db,
        row,
        "balance-failed:" + str(attempt.id),
        "balance_attempt_failed",
        None,
        fingerprint([attempt.id, sorted(seen)]),
        request.quote_id,
    )
    db.flush()


def checkout(db, design_id, user_id, provider):
    if not ONLINE:
        raise HTTPException(503, "Online custom balance collection is not enabled")
    provider_ready(provider)
    row = locked(db, design_id, user_id)
    quote, advance, request, entry, paid, due = accepted(db, row)
    if not request or request.mode != "razorpay":
        raise HTTPException(409, "Request the online balance against the accepted quote first")
    if entry:
        raise HTTPException(409, "Balance already posted; reload the request")
    attempt = latest_checkout(db, request.id)
    if attempt and attempt.state not in ("closed_failed",):
        if attempt.state in ("creating", "unknown"):
            raise HTTPException(
                409, "Checkout outcome is uncertain; reconcile the existing attempt"
            )
        order = get(provider, "orders/" + attempt.provider_order_id)
        if not matches(order, request, attempt):
            raise HTTPException(409, "Provider order differs from the frozen balance checkout")
        if order["status"] == "paid":
            observe_paid(db, row, request, attempt, order)
            raise HTTPException(
                409, "Balance payment awaits verification; check the existing payment"
            )
        if attempt.state == "paid_pending":
            raise HTTPException(409, "Observed balance capture requires store verification")
        if (
            order["status"] == "created"
            and type(order.get("attempts")) is int
            and order["attempts"] == 0
        ):
            return attempt, request
        failed_attempt(db, row, request, attempt, order, provider)
    sequence = attempt.sequence + 1 if attempt else 1
    attempt = Checkout(
        request_id=request.id,
        sequence=sequence,
        receipt="cdb_" + uuid4().hex,
        state="creating",
        created_at=utc_now(),
    )
    db.add(attempt)
    db.flush()
    audit(
        db,
        row,
        "balance-checkout:" + str(attempt.id),
        "balance_checkout_started",
        user_id,
        fingerprint([request.id, attempt.id, attempt.receipt]),
        quote.id,
    )
    db.commit()
    payload = dict(
        amount=request.amount_paise,
        currency="INR",
        receipt=attempt.receipt,
        partial_payment=False,
        notes=dict(purpose=PURPOSE, balance_request_id=str(request.id)),
    )
    try:
        code, order = provider.call_razorpay("POST", "orders", payload)
        valid = code in (200, 201) and matches(order, request, attempt)
    except Exception:
        valid = False
    row = locked(db, design_id, user_id)
    db.refresh(attempt)
    if attempt.state != "creating":
        raise HTTPException(409, "Checkout state changed; read before retrying")
    if not valid:
        attempt.state = "unknown"
        audit(
            db,
            row,
            "balance-checkout-unknown:" + str(attempt.id),
            "balance_checkout_unknown",
            None,
            fingerprint([request.id, attempt.id]),
            request.quote_id,
        )
        db.commit()
        raise HTTPException(503, "Checkout outcome is unknown; store reconciliation is required")
    attempt.provider_order_id = order["id"]
    attempt.state = "ready"
    audit(
        db,
        row,
        "balance-checkout-bound:" + str(attempt.id),
        "balance_checkout_bound",
        user_id,
        fingerprint([attempt.id, order["id"]]),
        request.quote_id,
    )
    db.flush()
    return attempt, request


def capture_post(db, design_id, attempt_id, payment_id, provider):
    row = locked(db, design_id)
    if held(db, row.id):
        return None
    quote, advance, request, old, paid, due = accepted(db, row)
    attempt = db.get(Checkout, attempt_id)
    if (
        not request
        or not attempt
        or attempt.request_id != request.id
        or request.mode != "razorpay"
        or not attempt.provider_order_id
    ):
        raise HTTPException(409, "No matching frozen balance checkout")
    order = get(provider, "orders/" + attempt.provider_order_id)
    if not matches(order, request, attempt):
        raise HTTPException(409, "Provider order differs from the balance checkout")
    observe_paid(db, row, request, attempt, order)
    row = locked(db, design_id)
    if held(db, row.id):
        return None
    quote, advance, request, old, paid, due = accepted(db, row)
    payment, adverse = captured(
        provider,
        order=order,
        order_id=attempt.provider_order_id,
        payment_id=payment_id,
        amount_paise=request.amount_paise,
    )
    if (
        adverse
        or attempt.state == "closed_failed"
        or old
        and (old.checkout_id != attempt.id or old.provider_payment_id != payment_id)
    ):
        reasons = adverse or [
            ("uncertain", payment_id, "Additional or late balance capture requires audited review")
        ]
        for kind, reference, reason in reasons:
            hold(db, row, "balance:" + fingerprint([kind, reference]), reason)
        db.commit()
        return None
    available(db, row)
    if old:
        return old
    if due != request.amount_paise:
        raise HTTPException(409, "Balance evidence differs from the remaining quote amount")
    entry = Entry(
        design_id=row.id,
        request_id=request.id,
        quote_id=request.quote_id,
        checkout_id=attempt.id,
        amount_paise=request.amount_paise,
        mode="razorpay",
        reference=payment_id,
        provider_order_id=attempt.provider_order_id,
        provider_payment_id=payment_id,
        created_at=utc_now(),
    )
    db.add(entry)
    audit(
        db,
        row,
        "balance-posted:" + str(request.id),
        "balance_posted",
        None,
        fingerprint([request.id, attempt.id, payment_id, request.amount_paise]),
        request.quote_id,
    )
    db.flush()
    return entry


def refresh(db, design_id, user_id, provider, order_id=None, admin=False, reason=None):
    provider_ready(provider)
    row = locked(db, design_id, None if admin else user_id)
    if held(db, row.id):
        return None
    quote, advance, request, entry, paid, due = accepted(db, row)
    if not request or request.mode != "razorpay":
        raise HTTPException(409, "No existing online balance request")
    attempt = latest_checkout(db, request.id)
    if not attempt:
        raise HTTPException(409, "No existing balance checkout")
    target = attempt.provider_order_id or (order_id if admin else None)
    if not target or not identifier(target, "order") or order_id and order_id != target:
        raise HTTPException(409, "Existing checkout requires store reconciliation")
    order = get(provider, "orders/" + target)
    if not matches(order, request, attempt):
        raise HTTPException(409, "Provider order does not match the saved balance receipt")
    if not attempt.provider_order_id:
        attempt.provider_order_id = target
        attempt.state = "ready"
        audit(
            db,
            row,
            "balance-checkout-bound:" + str(attempt.id),
            "balance_checkout_bound",
            user_id,
            fingerprint([attempt.id, target]),
            request.quote_id,
            detail=reason,
        )
        db.flush()
    if order["status"] != "paid":
        return None
    return capture_post(db, row.id, attempt.id, capture_id(provider, target), provider)


def provider_history(db, row, quote, advance, entry, provider):
    online = []
    if advance:
        checkout_row = db.query(CustomDesignCheckout).filter_by(design_id=row.id).first()
        if (
            not checkout_row
            or checkout_row.quote_id != quote.id
            or checkout_row.provider_order_id != advance.provider_order_id
        ):
            raise HTTPException(409, "Advance checkout evidence requires reconciliation")
        online.append(
            (
                advance,
                checkout_row.receipt,
                "sona_custom_design",
                "custom_design_quote_id",
                quote.id,
            )
        )
    if entry and entry.mode == "razorpay":
        checkout_row = db.get(Checkout, entry.checkout_id)
        if not checkout_row:
            raise HTTPException(409, "Balance checkout evidence requires reconciliation")
        online.append(
            (entry, checkout_row.receipt, PURPOSE, "balance_request_id", entry.request_id)
        )
    if online:
        provider_ready(provider)
    for credit, receipt, purpose, reference_name, reference in online:
        order = get(provider, "orders/" + credit.provider_order_id)
        if not order_matches(
            order,
            order_id=credit.provider_order_id,
            receipt=receipt,
            amount_paise=credit.amount_paise,
            purpose=purpose,
            reference_name=reference_name,
            reference=reference,
        ):
            raise HTTPException(409, "Paid quote evidence requires store reconciliation")
        payment, adverse = captured(
            provider,
            order=order,
            order_id=credit.provider_order_id,
            payment_id=credit.provider_payment_id,
            amount_paise=credit.amount_paise,
        )
        if adverse:
            for kind, reference, reason in adverse:
                hold(db, row, "balance:" + fingerprint([kind, reference]), reason)
            db.commit()
            raise HTTPException(
                409, "Custom design requires audited payment review before completion"
            )


def complete(db, design_id, admin, payload, provider):
    row = locked(db, design_id)
    quote, advance, request, entry, paid, due = accepted(db, row)
    key = "complete:" + payload.request_key
    sha = fingerprint([payload.model_dump(), admin.id, quote.id, paid])
    previous = db.query(CustomDesignAudit).filter_by(design_id=row.id, event_key=key).first()
    if previous:
        if (
            previous.action != "completed"
            or previous.actor_id != admin.id
            or previous.payload_sha256 != sha
        ):
            raise HTTPException(409, "Completion key was used for a different intent")
        return
    if db.query(CustomDesignAudit).filter_by(design_id=row.id, action="completed").first():
        raise HTTPException(409, "Custom design is already completed")
    if (
        due != 0
        or not db.query(CustomDesignAudit)
        .filter_by(design_id=row.id, action="manufacturing_started")
        .first()
    ):
        raise HTTPException(409, "Manufacturing and full verified quote payment are required")
    provider_history(db, row, quote, advance, entry, provider)
    available(db, row)
    parent = db.get(CustomOrderRequest, row.custom_order_id)
    parent.status = "completed"
    audit(db, row, key, "completed", admin.id, sha, quote.id, detail=payload.reason)
    db.flush()


def paid_or_observed(db, row):
    if not storage_ready(db):
        return False
    if db.query(Entry).filter_by(design_id=row.id).first():
        return True
    request = db.query(BalanceRequest).filter_by(design_id=row.id).first()
    return bool(
        request
        and db.query(Checkout).filter_by(request_id=request.id, state="paid_pending").first()
    )
