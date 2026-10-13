"""Default-disabled verified balance and full-payment completion endpoints."""

from typing import Literal

from fastapi import Depends, HTTPException
from pydantic import Field
from sqlalchemy.orm import Session

import custom_design_balance as balance
from custom_design_api import Input, document, locked, mutate
from custom_design_api import enabled as custom_enabled
from custom_design_balance_models import CustomDesignBalanceCheckout as Checkout
from custom_design_balance_models import CustomDesignBalanceEntry as Entry
from custom_design_balance_models import CustomDesignBalanceRequest as BalanceRequest
from custom_design_models import CustomDesignAudit
from custom_design_payment_api import Identity, ProofInput
from custom_design_payments import fingerprint, hold
from financial_provider import get, identifier
from models import User


class BalanceInput(Input):
    request_key: str = Field(min_length=1, max_length=100)
    quote_id: int = Field(gt=0, le=2_147_483_647, strict=True)
    mode: Literal["manual", "razorpay"]


class ManualInput(Input):
    request_id: int = Field(gt=0, le=2_147_483_647, strict=True)
    reference: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=200)


class ReconcileInput(Input):
    provider_order_id: str = Field(pattern=r"^order_[A-Za-z0-9]{1,90}$")
    reason: str = Field(min_length=1, max_length=200)


class CompleteInput(Input):
    request_key: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=200)


def verify(db, design_id, user_id, payload, provider):
    row = locked(db, design_id, user_id)
    request = db.query(BalanceRequest).filter_by(design_id=row.id, mode="razorpay").first()
    attempt = (
        db.query(Checkout)
        .filter_by(request_id=request.id, provider_order_id=payload.razorpay_order_id)
        .first()
        if request
        else None
    )
    if not attempt or not provider.razorpay_checkout_signature_is_valid(
        payload.razorpay_order_id, payload.razorpay_payment_id, payload.razorpay_signature
    ):
        raise HTTPException(409, "Balance proof differs from the bound checkout")
    balance.capture_post(db, row.id, attempt.id, payload.razorpay_payment_id, provider)
    return document(row, db)


def event_operation(db, payload, provider):
    if not balance.ENABLED or not balance.storage_ready(db):
        return {"status": "ignored"}
    event = payload.get("event")
    if event in ("payment.captured", "order.paid"):
        try:
            payment = payload["payload"]["payment"]["entity"]
            order_id, payment_id = payment["order_id"], payment["id"]
        except (KeyError, TypeError):
            raise HTTPException(400, "Invalid custom balance event") from None
        if not identifier(order_id, "order") or not identifier(payment_id, "pay"):
            raise HTTPException(400, "Invalid custom balance event")
        attempt = db.query(Checkout).filter_by(provider_order_id=order_id).first()
        if not attempt:
            return {"status": "ignored"}
        request = db.get(BalanceRequest, attempt.request_id)
        credit = balance.capture_post(db, request.design_id, attempt.id, payment_id, provider)
        return {"status": "posted", "entry_id": credit.id} if credit else {"status": "held"}
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
            raise HTTPException(400, "Invalid custom balance review event") from None
        if not identifier(reference, prefix):
            raise HTTPException(400, "Invalid custom balance review reference")
        evidence = get(provider, ("refunds/" if kind == "refund" else "disputes/") + reference)
        payment_id = evidence.get("payment_id")
        if (
            evidence.get("id") != reference
            or evidence.get("entity") != kind
            or not identifier(payment_id, "pay")
        ):
            raise HTTPException(409, "Custom balance review evidence does not match")
        credit = db.query(Entry).filter_by(provider_payment_id=payment_id).first()
        request = db.get(BalanceRequest, credit.request_id) if credit else None
        if request is None:
            payment = get(provider, "payments/" + payment_id)
            if (
                payment.get("id") != payment_id
                or payment.get("entity") != "payment"
                or not identifier(payment.get("order_id"), "order")
            ):
                raise HTTPException(409, "Balance review payment evidence does not match")
            attempt = db.query(Checkout).filter_by(provider_order_id=payment["order_id"]).first()
            if not attempt:
                return {"status": "ignored"}
            request = db.get(BalanceRequest, attempt.request_id)
            order = get(provider, "orders/" + attempt.provider_order_id)
            if (
                not balance.matches(order, request, attempt)
                or payment.get("currency") != "INR"
                or type(payment.get("amount")) is not int
                or payment["amount"] != request.amount_paise
            ):
                raise HTTPException(409, "Balance review differs from the bound checkout")
        if (
            evidence.get("currency") != "INR"
            or type(evidence.get("amount")) is not int
            or not 0 < evidence["amount"] <= request.amount_paise
        ):
            raise HTTPException(409, "Balance review amount does not match")
        row = locked(db, request.design_id)
        item = hold(
            db,
            row,
            "balance:" + fingerprint([kind, reference]),
            "Provider " + kind + " requires audited review",
        )
        return {"status": "held", "hold_id": item.id}
    return {"status": "ignored"}


def install(app, get_db, get_current_user, require_permission, provider):
    protected = [Depends(custom_enabled), Depends(balance.enabled)]
    admin_access = require_permission("support:manage")

    @app.post("/custom-designs/{design_id}/balance/request", dependencies=protected)
    def request_balance(
        design_id: Identity,
        payload: BalanceInput,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        def operation():
            balance.request_balance(db, design_id, user.id, payload)
            return document(locked(db, design_id, user.id), db)

        return mutate(db, operation)

    @app.post("/custom-designs/{design_id}/balance/checkout", dependencies=protected)
    def checkout(
        design_id: Identity, user: User = Depends(get_current_user), db: Session = Depends(get_db)
    ):
        def operation():
            attempt, request = balance.checkout(db, design_id, user.id, provider)
            return dict(
                design_id=design_id,
                purpose="custom_design_balance",
                request_id=request.id,
                quote_id=request.quote_id,
                checkout_id=attempt.id,
                key_id=provider.RAZORPAY_KEY_ID,
                order_id=attempt.provider_order_id,
                amount_paise=request.amount_paise,
                currency="INR",
            )

        return mutate(db, operation)

    @app.post("/custom-designs/{design_id}/balance/verify", dependencies=protected)
    def verify_balance(
        design_id: Identity,
        payload: ProofInput,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        balance.provider_ready(provider)
        return mutate(db, lambda: verify(db, design_id, user.id, payload, provider))

    @app.post("/custom-designs/{design_id}/balance/refresh", dependencies=protected)
    def refresh(
        design_id: Identity, user: User = Depends(get_current_user), db: Session = Depends(get_db)
    ):
        def operation():
            balance.refresh(db, design_id, user.id, provider)
            return document(locked(db, design_id, user.id), db)

        return mutate(db, operation)

    @app.post("/admin/custom-designs/{design_id}/balance/confirm-manual", dependencies=protected)
    def confirm_manual(
        design_id: Identity,
        payload: ManualInput,
        admin: User = Depends(admin_access),
        db: Session = Depends(get_db),
    ):
        def operation():
            balance.manual_post(db, design_id, admin, payload)
            return document(locked(db, design_id), db)

        return mutate(db, operation)

    @app.post(
        "/admin/custom-designs/{design_id}/balance/reconcile-checkout", dependencies=protected
    )
    def reconcile(
        design_id: Identity,
        payload: ReconcileInput,
        admin: User = Depends(admin_access),
        db: Session = Depends(get_db),
    ):
        def operation():
            balance.refresh(
                db,
                design_id,
                admin.id,
                provider,
                order_id=payload.provider_order_id,
                admin=True,
                reason=payload.reason,
            )
            return document(locked(db, design_id), db)

        return mutate(db, operation)

    @app.post("/admin/custom-designs/{design_id}/complete", dependencies=protected)
    def complete(
        design_id: Identity,
        payload: CompleteInput,
        admin: User = Depends(admin_access),
        db: Session = Depends(get_db),
    ):
        def operation():
            balance.complete(db, design_id, admin, payload, provider)
            return document(locked(db, design_id), db)

        return mutate(db, operation)

    @app.get("/admin/custom-designs/{design_id}/balance/reconciliation", dependencies=protected)
    def reconciliation(
        design_id: Identity, admin: User = Depends(admin_access), db: Session = Depends(get_db)
    ):
        row = locked(db, design_id)
        result = balance.snapshot(db, row)
        count = db.query(Entry).filter_by(design_id=row.id).count()
        audit_count = (
            db.query(CustomDesignAudit).filter_by(design_id=row.id, action="balance_posted").count()
        )
        complete_count = (
            db.query(CustomDesignAudit).filter_by(design_id=row.id, action="completed").count()
        )
        return dict(
            design_id=row.id,
            consistent=result["payment_consistent"],
            issues=[] if result["payment_consistent"] else ["balance_evidence_mismatch"],
            paid_total_paise=result["paid_total_paise"],
            balance_due_paise=result["balance_due_paise"],
            balance_entry_count=count,
            balance_posting_audit_count=audit_count,
            completion_audit_count=complete_count,
            completed=result["completed"],
            completion_available=result["completion_available"],
            provider_verified=False,
            release_authorized=False,
        )
