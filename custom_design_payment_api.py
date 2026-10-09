"""Advance and manufacturing routes; default-disabled with the custom-design API."""

import json
from typing import Annotated

from fastapi import Depends, HTTPException, Query, Request
from fastapi import Path as ApiPath
from pydantic import Field
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from custom_design_api import (
    Input,
    audit,
    document,
    enabled,
    latest_quote,
    locked,
    mutate,
    timestamp,
)
from custom_design_models import CustomDesignAudit, CustomDesignDecision, CustomDesignQuote
from custom_design_payments import (
    CustomDesignAdvance,
    CustomDesignCheckout,
    available,
    checkout,
    fingerprint,
    hold,
    identifier,
    post_capture,
    provider_get,
    refresh,
)
from models import User

Identity = Annotated[int, ApiPath(gt=0, le=2_147_483_647)]


class ProofInput(Input):
    razorpay_order_id: str = Field(pattern=r"^order_[A-Za-z0-9]{1,90}$")
    razorpay_payment_id: str = Field(pattern=r"^pay_[A-Za-z0-9]{1,90}$")
    razorpay_signature: str = Field(pattern=r"^[a-fA-F0-9]{64}$")


class ReconcileInput(Input):
    provider_order_id: str = Field(pattern=r"^order_[A-Za-z0-9]{1,90}$")


class ReviewInput(Input):
    reference: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=200)


class StartInput(Input):
    request_key: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=200)


def provider_ready(provider):
    if not provider.razorpay_checkout_is_configured():
        raise HTTPException(503, "Custom advance provider unavailable")


def verify(db, design_id, user_id, payload, provider):
    row = locked(db, design_id, user_id)
    attempt = db.query(CustomDesignCheckout).filter_by(design_id=row.id).first()
    if (
        not attempt
        or attempt.state != "ready"
        or attempt.provider_order_id != payload.razorpay_order_id
    ):
        raise HTTPException(409, "Advance proof differs from the bound checkout")
    if not provider.razorpay_checkout_signature_is_valid(
        payload.razorpay_order_id, payload.razorpay_payment_id, payload.razorpay_signature
    ):
        raise HTTPException(400, "Invalid advance payment signature")
    q = db.get(CustomDesignQuote, attempt.quote_id)
    post_capture(db, row, q, attempt, payload.razorpay_payment_id, provider)
    return document(row, db)


def manufacturing_start(db, design_id, admin, payload):
    row = locked(db, design_id)
    available(db, row)
    sha = fingerprint(payload.model_dump())
    event_key = "manufacture:" + payload.request_key
    old = db.query(CustomDesignAudit).filter_by(design_id=row.id, event_key=event_key).first()
    if old:
        if (
            old.payload_sha256 != sha
            or old.actor_id != admin.id
            or old.action != "manufacturing_started"
        ):
            raise HTTPException(409, "Manufacturing key reused for different terms")
        return document(row, db)
    if (
        db.query(CustomDesignAudit)
        .filter_by(design_id=row.id, action="manufacturing_started")
        .first()
    ):
        raise HTTPException(409, "Manufacturing already started")
    q = latest_quote(db, row.id)
    decision = (
        db.query(CustomDesignDecision)
        .filter_by(quote_id=q.id)
        .order_by(CustomDesignDecision.id.desc())
        .first()
        if q
        else None
    )
    if not decision or decision.action != "approved":
        raise HTTPException(409, "Current quote must be accepted before manufacturing")
    credit = db.query(CustomDesignAdvance).filter_by(design_id=row.id).first()
    if q.advance_paise and (
        not credit or credit.quote_id != q.id or credit.amount_paise != q.advance_paise
    ):
        raise HTTPException(409, "Required advance must be verified before manufacturing")
    audit(db, row, event_key, "manufacturing_started", admin.id, sha, q.id, detail=payload.reason)
    db.flush()
    return document(row, db)


def event_operation(db, payload, provider):
    event = payload.get("event")
    if event in ("payment.captured", "order.paid"):
        try:
            entity = payload["payload"]["payment"]["entity"]
            payment_id, order_id = entity["id"], entity["order_id"]
        except (KeyError, TypeError):
            raise HTTPException(400, "Invalid custom advance event") from None
        if not identifier(payment_id, "pay") or not identifier(order_id, "order"):
            raise HTTPException(400, "Invalid custom advance event")
        attempt = db.query(CustomDesignCheckout).filter_by(provider_order_id=order_id).first()
        if not attempt:
            return {"status": "ignored"}
        row = locked(db, attempt.design_id)
        q = db.get(CustomDesignQuote, attempt.quote_id)
        credit = post_capture(db, row, q, attempt, payment_id, provider)
        return {"status": "posted", "entry_id": credit.id}
    if (
        event in ("refund.created", "refund.processed")
        or isinstance(event, str)
        and event.startswith("payment.dispute.")
    ):
        kind = "refund" if event.startswith("refund.") else "dispute"
        prefix = "rfnd" if kind == "refund" else "disp"
        try:
            reference = payload["payload"][kind]["entity"]["id"]
        except (KeyError, TypeError):
            raise HTTPException(400, "Invalid custom review event") from None
        if not identifier(reference, prefix):
            raise HTTPException(400, "Invalid custom review reference")
        evidence = provider_get(
            provider, ("refunds/" if kind == "refund" else "disputes/") + reference
        )
        payment_id = evidence.get("payment_id")
        if (
            not identifier(payment_id, "pay")
            or evidence.get("id") != reference
            or evidence.get("entity") != kind
        ):
            raise HTTPException(409, "Custom review provider evidence does not match")
        credit = db.query(CustomDesignAdvance).filter_by(provider_payment_id=payment_id).first()
        if not credit:
            return {"status": "ignored"}
        if (
            evidence.get("currency") != "INR"
            or type(evidence.get("amount")) is not int
            or not 0 < evidence["amount"] <= credit.amount_paise
        ):
            raise HTTPException(409, "Custom review provider amount does not match")
        row = locked(db, credit.design_id)
        item = hold(db, row, reference, "Provider " + kind + " requires audited review")
        return {"status": "held", "hold_id": item.id}
    return {"status": "ignored"}


class CustomerReviewInput(Input):
    request_key: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=200)


def install(app, get_db, get_current_user, require_permission, provider):
    protected = [Depends(enabled)]
    admin_access = require_permission("support:manage")

    @app.post("/custom-designs/{design_id}/checkout", dependencies=protected)
    def open_checkout(
        design_id: Identity, user: User = Depends(get_current_user), db: Session = Depends(get_db)
    ):
        provider_ready(provider)

        def operation():
            attempt, q = checkout(db, design_id, user.id, provider)
            return dict(
                design_id=design_id,
                quote_id=q.id,
                key_id=provider.RAZORPAY_KEY_ID,
                order_id=attempt.provider_order_id,
                amount_paise=q.advance_paise,
                currency="INR",
            )

        return mutate(db, operation)

    @app.post("/custom-designs/{design_id}/verify", dependencies=protected)
    def verify_advance(
        design_id: Identity,
        payload: ProofInput,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        provider_ready(provider)
        return mutate(db, lambda: verify(db, design_id, user.id, payload, provider))

    @app.post("/custom-designs/{design_id}/refresh", dependencies=protected)
    def refresh_advance(
        design_id: Identity, user: User = Depends(get_current_user), db: Session = Depends(get_db)
    ):
        provider_ready(provider)

        def operation():
            refresh(db, design_id, user.id, provider)
            return document(locked(db, design_id, user.id), db)

        return mutate(db, operation)

    @app.post("/admin/custom-designs/{design_id}/reconcile-checkout", dependencies=protected)
    def reconcile(
        design_id: Identity,
        payload: ReconcileInput,
        admin: User = Depends(admin_access),
        db: Session = Depends(get_db),
    ):
        provider_ready(provider)

        def operation():
            refresh(
                db, design_id, admin.id, provider, order_id=payload.provider_order_id, admin=True
            )
            return document(locked(db, design_id), db)

        return mutate(db, operation)

    @app.post("/admin/custom-designs/{design_id}/hold", dependencies=protected)
    def review(
        design_id: Identity,
        payload: ReviewInput,
        admin: User = Depends(admin_access),
        db: Session = Depends(get_db),
    ):
        def operation():
            row = locked(db, design_id)
            item = hold(db, row, payload.reference, payload.reason, admin.id)
            return dict(
                id=item.id,
                design_id=row.id,
                reason=item.reason,
                reference=item.reference,
                created_at=timestamp(item.created_at),
            )

        return mutate(db, operation)

    @app.post("/admin/custom-designs/{design_id}/manufacturing/start", dependencies=protected)
    def start(
        design_id: Identity,
        payload: StartInput,
        admin: User = Depends(admin_access),
        db: Session = Depends(get_db),
    ):
        return mutate(db, lambda: manufacturing_start(db, design_id, admin, payload))

    @app.post("/custom-designs/payments/razorpay/webhook", dependencies=protected)
    async def webhook(request: Request, db: Session = Depends(get_db)):
        provider_ready(provider)
        if not provider.razorpay_webhook_is_configured():
            raise HTTPException(503, "Custom advance webhook unavailable")
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 1_048_576:
                raise HTTPException(413, "Webhook too large")
        if not provider.razorpay_signature_is_valid(
            bytes(raw), request.headers.get("x-razorpay-signature")
        ):
            raise HTTPException(400, "Invalid webhook signature")
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError()
        except (ValueError, TypeError):
            raise HTTPException(400, "Invalid webhook body") from None
        return await run_in_threadpool(mutate, db, lambda: event_operation(db, payload, provider))

    @app.get("/admin/custom-designs/{design_id}/reconciliation", dependencies=protected)
    def reconciliation(
        design_id: Identity, admin: User = Depends(admin_access), db: Session = Depends(get_db)
    ):
        row = locked(db, design_id)
        attempt = db.query(CustomDesignCheckout).filter_by(design_id=row.id).first()
        credit = db.query(CustomDesignAdvance).filter_by(design_id=row.id).first()
        issues = []
        if credit:
            q = db.get(CustomDesignQuote, credit.quote_id)
            if not q or q.design_id != row.id or credit.amount_paise != q.advance_paise:
                issues.append("advance_quote_mismatch")
            if (
                not attempt
                or attempt.state != "ready"
                or attempt.quote_id != credit.quote_id
                or attempt.provider_order_id != credit.provider_order_id
            ):
                issues.append("advance_checkout_mismatch")
            count = (
                db.query(CustomDesignAudit)
                .filter_by(design_id=row.id, action="advance_posted")
                .count()
            )
            if count != 1:
                issues.append("advance_audit_mismatch")
        elif (
            db.query(CustomDesignAudit).filter_by(design_id=row.id, action="advance_posted").first()
        ):
            issues.append("posting_audit_without_advance")
        from custom_design_payments import CustomDesignHold

        holds = db.query(CustomDesignHold).filter_by(design_id=row.id).count()
        return dict(
            design_id=row.id,
            consistent=not issues,
            issues=issues,
            advance_paise=credit.amount_paise if credit else 0,
            advance_entry_count=1 if credit else 0,
            review_required=holds > 0,
            hold_count=holds,
            provider_verified=False,
            release_authorized=False,
            completion_available=False,
        )

    @app.get("/admin/custom-designs/{design_id}/holds", dependencies=protected)
    def holds(
        design_id: Identity,
        offset: int = Query(0, ge=0, le=10000),
        limit: int = Query(20, ge=1, le=50),
        admin: User = Depends(admin_access),
        db: Session = Depends(get_db),
    ):
        from custom_design_payments import CustomDesignHold

        locked(db, design_id)
        rows = (
            db.query(CustomDesignHold)
            .filter_by(design_id=design_id)
            .order_by(CustomDesignHold.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [
            dict(
                id=row.id,
                reference=row.reference,
                reason=row.reason,
                created_at=timestamp(row.created_at),
            )
            for row in rows
        ]

    @app.post("/custom-designs/{design_id}/cancellation-review", dependencies=protected)
    def cancellation(
        design_id: Identity,
        payload: CustomerReviewInput,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        def operation():
            row = locked(db, design_id, user.id)
            if not db.query(CustomDesignAdvance).filter_by(design_id=row.id).first():
                raise HTTPException(409, "Paid cancellation review requires a posted advance")
            hold(db, row, "customer-cancel:" + payload.request_key, payload.reason, user.id)
            return document(row, db)

        return mutate(db, operation)
