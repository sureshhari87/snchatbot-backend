"""Authenticated savings API. Disabled until schema/cutover are authorized."""

import json
from datetime import timezone
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Query, Request
from fastapi import Path as ApiPath
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import get_bool
from models import User, utc_now
from savings import (
    SavingsConflict,
    active_holds,
    balance,
    confirm_manual,
    enroll,
    latest_hold_decision,
    payment_request,
    record_hold,
    redeem,
    resolve_pending,
    utc,
    verify_razorpay,
)
from savings_checkout import (
    CheckoutUnavailable,
    checkout,
    owned_payment,
    reconcile,
    refresh_checkout,
)
from savings_models import (
    SavingsAudit,
    SavingsCheckoutAttempt,
    SavingsEntry,
    SavingsGoldRate,
    SavingsHold,
    SavingsHoldDecision,
    SavingsPayment,
    SavingsPaymentResolution,
    SavingsScheme,
)

ENABLED = get_bool("SAVINGS_ENABLED", False)
MAX_MONEY = 9_223_372_036_854_775_807
DatabaseId = Annotated[int, ApiPath(gt=0, le=2_147_483_647)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EnrollmentInput(Input):
    request_key: str = Field(min_length=1, max_length=100)
    kind: Literal["monthly", "digigold"]
    monthly_paise: int | None = Field(default=None, strict=True, ge=50_000, le=MAX_MONEY)

    @model_validator(mode="after")
    def valid_amount(self):
        if (self.kind == "monthly") != (self.monthly_paise is not None):
            raise ValueError("Only monthly enrollment requires monthly_paise")
        return self


class PaymentInput(Input):
    request_key: str = Field(min_length=1, max_length=100)
    mode: Literal["manual", "razorpay"]
    amount_paise: int | None = Field(default=None, strict=True, gt=0, le=MAX_MONEY)


class ReferenceInput(Input):
    reference: str = Field(min_length=1, max_length=100)


class ResolutionInput(Input):
    reason: str = Field(min_length=1, max_length=100)


class RejectionInput(ResolutionInput):
    no_payment_received: bool = Field(strict=True)

    @model_validator(mode="after")
    def reviewed_absence(self):
        if not self.no_payment_received:
            raise ValueError("Verify no payment was received before rejecting")
        return self


class HoldInput(ResolutionInput):
    reference: str = Field(min_length=1, max_length=100)


class ReleaseInput(ResolutionInput):
    request_key: str = Field(min_length=1, max_length=80)


class ReconcileInput(Input):
    provider_order_id: str = Field(pattern=r"^order_[A-Za-z0-9_]{1,90}$")


class VerifyInput(Input):
    razorpay_order_id: str = Field(pattern=r"^order_[A-Za-z0-9_]{1,90}$")
    razorpay_payment_id: str = Field(pattern=r"^pay_[A-Za-z0-9_]{1,90}$")
    razorpay_signature: str = Field(pattern=r"^[a-fA-F0-9]{64}$")


class RateInput(Input):
    paise_per_gram: int = Field(gt=0, le=MAX_MONEY, strict=True)
    source: str = Field(min_length=1, max_length=200)
    valid_until: AwareDatetime


def enabled():
    if not ENABLED:
        raise HTTPException(503, "Savings migration is not enabled")


def timestamp(value):
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


def payment_document(row, db):
    attempt = db.get(SavingsCheckoutAttempt, row.id)
    resolution = db.get(SavingsPaymentResolution, row.id)
    held = active_holds(db, row.scheme_id).first() is not None
    return {
        "id": row.id,
        "scheme_id": row.scheme_id,
        "amount_paise": row.amount_paise,
        "installment": resolution.original_installment if resolution else row.installment,
        "mode": row.mode,
        "state": row.state,
        "created_at": timestamp(row.created_at),
        "posted_at": timestamp(row.posted_at),
        "checkout_state": attempt.state if attempt else None,
        "can_cancel": not held
        and row.state == "pending"
        and row.mode == "razorpay"
        and not attempt
        and not row.provider_order_id
        and not row.verified_reference,
        "can_reject": not held
        and row.state == "pending"
        and not attempt
        and not row.provider_order_id
        and not row.verified_reference,
        "can_checkout": not held
        and row.state == "pending"
        and row.mode == "razorpay"
        and (attempt is None or attempt.state == "ready"),
        "closed_at": timestamp(resolution.created_at) if resolution else None,
    }


def entry_document(row):
    return {
        "id": row.id,
        "kind": row.kind,
        "payment_id": row.payment_id,
        "principal_paise": row.principal_paise,
        "gold_micrograms": row.gold_micrograms,
        "bonus_micrograms": row.bonus_micrograms,
        "store_bonus_paise": row.store_bonus_paise,
        "rate_id": row.rate_id,
        "bonus_bps": row.bonus_bps,
        "created_at": timestamp(row.created_at),
    }


def scheme_document(row, db):
    principal, gold, bonus = balance(db, row.id)
    confirmed = db.query(SavingsPayment).filter_by(scheme_id=row.id, state="posted").count()
    pending = (
        db.query(SavingsPayment.id).filter_by(scheme_id=row.id, state="pending").first() is not None
    )
    matured = utc_now() >= row.matures_at
    held = active_holds(db, row.id).first() is not None
    return {
        "id": row.id,
        "kind": row.kind,
        "state": "held" if held and row.state == "active" else row.state,
        "review_required": held,
        "monthly_paise": row.monthly_paise,
        "started_at": timestamp(row.started_at),
        "matures_at": timestamp(row.matures_at),
        "principal_paise": principal,
        "gold_micrograms": gold,
        "bonus_micrograms": bonus,
        "confirmed_payments": confirmed,
        "pending_payment": pending,
        "matured": matured,
        "redeemable": not held
        and row.state == "active"
        and matured
        and not pending
        and principal > 0
        and (row.kind == "digigold" or confirmed == 11),
    }


def rate_document(row):
    return {
        "id": row.id,
        "paise_per_gram": row.paise_per_gram,
        "purity": row.purity,
        "effective_at": timestamp(row.effective_at),
        "valid_until": timestamp(row.valid_until),
    }


def mutate(db, operation):
    try:
        result = operation()
        db.commit()
        return result
    except HTTPException:
        db.rollback()
        raise
    except CheckoutUnavailable as exc:
        db.rollback()
        raise HTTPException(503, str(exc)) from None
    except SavingsConflict as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from None
    except PermissionError:
        db.rollback()
        raise HTTPException(403, "Savings administrator required") from None
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Savings write conflicts with an existing record") from None
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from None


def own_scheme(db, scheme_id, user_id):
    row = db.query(SavingsScheme).filter_by(id=scheme_id, user_id=user_id).first()
    if row is None:
        raise HTTPException(404, "Scheme unavailable")
    return row


def install(app, get_db, get_current_user, require_permission, provider):
    # Provider is the current backend module, supplied after its helpers exist.
    # Its secret values are never returned; only the SDK's public key identifier.
    admin_access = require_permission("savings:manage")
    protected = [Depends(enabled)]

    @app.get("/savings/schemes", dependencies=protected)
    def schemes(
        offset: int = Query(0, ge=0, le=10000),
        limit: int = Query(20, ge=1, le=50),
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        rows = (
            db.query(SavingsScheme)
            .filter_by(user_id=user.id)
            .order_by(SavingsScheme.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [scheme_document(row, db) for row in rows]

    @app.post("/savings/schemes", dependencies=protected)
    def create_scheme(
        payload: EnrollmentInput,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        return mutate(
            db,
            lambda: scheme_document(
                enroll(
                    db, user.id, payload.kind, payload.request_key, utc_now(), payload.monthly_paise
                ),
                db,
            ),
        )

    @app.get("/savings/schemes/{scheme_id}", dependencies=protected)
    def scheme_detail(
        scheme_id: DatabaseId, user: User = Depends(get_current_user), db: Session = Depends(get_db)
    ):
        return scheme_document(own_scheme(db, scheme_id, user.id), db)

    @app.get("/savings/schemes/{scheme_id}/payments", dependencies=protected)
    def payments(
        scheme_id: DatabaseId,
        offset: int = Query(0, ge=0, le=10000),
        limit: int = Query(20, ge=1, le=50),
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        own_scheme(db, scheme_id, user.id)
        rows = (
            db.query(SavingsPayment)
            .filter_by(scheme_id=scheme_id, user_id=user.id)
            .order_by(SavingsPayment.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [payment_document(row, db) for row in rows]

    @app.post("/savings/schemes/{scheme_id}/payments", dependencies=protected)
    def request_payment(
        scheme_id: DatabaseId,
        payload: PaymentInput,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        own_scheme(db, scheme_id, user.id)
        if payload.mode == "razorpay" and not provider.razorpay_checkout_is_configured():
            raise HTTPException(503, "Online savings payments are unavailable")
        return mutate(
            db,
            lambda: payment_document(
                payment_request(
                    db,
                    user.id,
                    scheme_id,
                    payload.request_key,
                    payload.mode,
                    utc_now(),
                    payload.amount_paise,
                ),
                db,
            ),
        )

    @app.get("/savings/schemes/{scheme_id}/ledger", dependencies=protected)
    def ledger(
        scheme_id: DatabaseId,
        offset: int = Query(0, ge=0, le=10000),
        limit: int = Query(20, ge=1, le=50),
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        own_scheme(db, scheme_id, user.id)
        rows = (
            db.query(SavingsEntry)
            .filter_by(scheme_id=scheme_id)
            .order_by(SavingsEntry.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [entry_document(row) for row in rows]

    @app.get("/savings/rate", dependencies=protected)
    def current_rate(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
        now = utc_now()
        row = (
            db.query(SavingsGoldRate)
            .filter(
                SavingsGoldRate.purity == "24K 995",
                SavingsGoldRate.effective_at <= now,
                SavingsGoldRate.valid_until > now,
            )
            .order_by(SavingsGoldRate.effective_at.desc(), SavingsGoldRate.id.desc())
            .first()
        )
        if row is None:
            raise HTTPException(503, "No current DigiGold rate is available")
        return rate_document(row)

    @app.post("/savings/payments/{payment_id}/checkout", dependencies=protected)
    def open_checkout(
        payment_id: DatabaseId,
        payload: Input | None = None,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        if not provider.razorpay_checkout_is_configured():
            raise HTTPException(503, "Online savings payments are unavailable")

        def operation():
            payment = checkout(db, payment_id, user.id, provider.call_razorpay, utc_now())
            return {
                "payment_id": payment.id,
                "key_id": provider.RAZORPAY_KEY_ID,
                "order_id": payment.provider_order_id,
                "amount_paise": payment.amount_paise,
                "currency": "INR",
            }

        return mutate(db, operation)

    @app.post("/savings/payments/{payment_id}/refresh", dependencies=protected)
    def refresh_payment(
        payment_id: DatabaseId,
        payload: Input | None = None,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        if not provider.razorpay_checkout_is_configured():
            raise HTTPException(503, "Online savings verification is unavailable")
        return mutate(
            db,
            lambda: payment_document(
                refresh_checkout(db, payment_id, user.id, provider.call_razorpay, utc_now()), db
            ),
        )

    @app.post("/savings/payments/{payment_id}/verify", dependencies=protected)
    def verify(
        payment_id: DatabaseId,
        payload: VerifyInput,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        if not provider.razorpay_checkout_is_configured():
            raise HTTPException(503, "Online savings verification is unavailable")

        def operation():
            payment = owned_payment(db, payment_id, user.id)
            if payment.provider_order_id != payload.razorpay_order_id:
                raise SavingsConflict("Checkout does not match this payment")
            if not provider.razorpay_checkout_signature_is_valid(
                payment.provider_order_id, payload.razorpay_payment_id, payload.razorpay_signature
            ):
                raise HTTPException(400, "Invalid checkout signature")
            try:
                entry = verify_razorpay(
                    db,
                    payment.id,
                    user.id,
                    payload.razorpay_payment_id,
                    lambda payment_id: safe_fetch(provider, payment_id),
                    utc_now,
                )
            except RuntimeError:
                raise CheckoutUnavailable("Provider verification unavailable") from None
            return entry_document(entry)

        return mutate(db, operation)

    @app.get("/admin/savings/schemes", dependencies=protected)
    def admin_schemes(
        offset: int = Query(0, ge=0, le=10000),
        limit: int = Query(20, ge=1, le=50),
        user_id: int | None = Query(None, gt=0, le=2_147_483_647),
        admin: User = Depends(admin_access),
        db: Session = Depends(get_db),
    ):
        query = db.query(SavingsScheme)
        if user_id is not None:
            query = query.filter_by(user_id=user_id)
        return [
            dict(scheme_document(row, db), user_id=row.user_id)
            for row in query.order_by(SavingsScheme.id.desc()).offset(offset).limit(limit).all()
        ]

    @app.get("/admin/savings/payments", dependencies=protected)
    def admin_payments(
        offset: int = Query(0, ge=0, le=10000),
        limit: int = Query(20, ge=1, le=50),
        state: Literal["pending", "posted", "cancelled", "rejected"] | None = None,
        admin: User = Depends(admin_access),
        db: Session = Depends(get_db),
    ):
        query = db.query(SavingsPayment)
        if state:
            query = query.filter_by(state=state)
        return [
            dict(payment_document(row, db), user_id=row.user_id)
            for row in query.order_by(SavingsPayment.id.desc()).offset(offset).limit(limit).all()
        ]

    @app.post("/savings/payments/{payment_id}/cancel", dependencies=protected)
    def cancel_request(
        payment_id: DatabaseId,
        payload: ResolutionInput,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        return mutate(
            db,
            lambda: payment_document(
                resolve_pending(db, payment_id, user, payload.reason, utc_now, kind="cancelled"), db
            ),
        )

    @app.post("/admin/savings/payments/{payment_id}/reject", dependencies=protected)
    def reject_request(
        payment_id: DatabaseId,
        payload: RejectionInput,
        admin: User = Depends(admin_access),
        db: Session = Depends(get_db),
    ):
        return mutate(
            db,
            lambda: payment_document(
                resolve_pending(
                    db, payment_id, admin, payload.reason, utc_now, kind="rejected", admin=True
                ),
                db,
            ),
        )

    @app.post("/admin/savings/payments/{payment_id}/confirm", dependencies=protected)
    def confirm(
        payment_id: DatabaseId,
        payload: ReferenceInput,
        admin: User = Depends(admin_access),
        db: Session = Depends(get_db),
    ):
        return mutate(
            db,
            lambda: entry_document(
                confirm_manual(db, payment_id, admin, payload.reference, utc_now)
            ),
        )

    @app.post("/admin/savings/schemes/{scheme_id}/redeem", dependencies=protected)
    def redeem_scheme(
        scheme_id: DatabaseId,
        payload: ReferenceInput,
        admin: User = Depends(admin_access),
        db: Session = Depends(get_db),
    ):
        return mutate(
            db, lambda: entry_document(redeem(db, scheme_id, admin, payload.reference, utc_now()))
        )

    @app.get("/admin/savings/schemes/{scheme_id}/reconciliation", dependencies=protected)
    def reconciliation_report(
        scheme_id: DatabaseId,
        admin: User = Depends(admin_access),
        db: Session = Depends(get_db),
    ):
        from savings_reconciliation import report

        return mutate(db, lambda: report(db, scheme_id))

    @app.get("/admin/savings/schemes/{scheme_id}/audit", dependencies=protected)
    def audits(
        scheme_id: DatabaseId,
        offset: int = Query(0, ge=0, le=10000),
        limit: int = Query(20, ge=1, le=50),
        admin: User = Depends(admin_access),
        db: Session = Depends(get_db),
    ):
        if db.get(SavingsScheme, scheme_id) is None:
            raise HTTPException(404, "Scheme unavailable")
        rows = (
            db.query(SavingsAudit)
            .filter_by(scheme_id=scheme_id)
            .order_by(SavingsAudit.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [
            {
                "id": row.id,
                "action": row.action,
                "actor_id": row.actor_id,
                "reference": row.evidence_reference,
                "created_at": timestamp(row.created_at),
            }
            for row in rows
        ]

    @app.post("/admin/savings/schemes/{scheme_id}/hold", dependencies=protected)
    def hold_scheme(
        scheme_id: DatabaseId,
        payload: HoldInput,
        admin: User = Depends(admin_access),
        db: Session = Depends(get_db),
    ):
        return mutate(
            db,
            lambda: hold_document(
                record_hold(
                    db,
                    scheme_id,
                    "admin_review",
                    payload.reference,
                    payload.reason,
                    utc_now,
                    actor_id=admin.id,
                ),
                db,
            ),
        )

    @app.get("/admin/savings/schemes/{scheme_id}/holds", dependencies=protected)
    def review_history(
        scheme_id: DatabaseId,
        offset: int = Query(0, ge=0, le=10000),
        limit: int = Query(20, ge=1, le=50),
        admin: User = Depends(admin_access),
        db: Session = Depends(get_db),
    ):
        if db.get(SavingsScheme, scheme_id) is None:
            raise HTTPException(404, "Scheme unavailable")
        rows = (
            db.query(SavingsHold)
            .filter_by(scheme_id=scheme_id)
            .order_by(SavingsHold.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [hold_document(row, db) for row in rows]

    @app.get("/admin/savings/holds/{hold_id}/decisions", dependencies=protected)
    def hold_decisions(
        hold_id: DatabaseId,
        offset: int = Query(0, ge=0, le=10000),
        limit: int = Query(20, ge=1, le=50),
        admin: User = Depends(admin_access),
        db: Session = Depends(get_db),
    ):
        if db.get(SavingsHold, hold_id) is None:
            raise HTTPException(404, "Review hold unavailable")
        rows = (
            db.query(SavingsHoldDecision)
            .filter_by(hold_id=hold_id)
            .order_by(SavingsHoldDecision.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [decision_document(row) for row in rows]

    @app.post("/admin/savings/holds/{hold_id}/release", dependencies=protected)
    def release_hold(
        hold_id: DatabaseId,
        payload: ReleaseInput,
        admin: User = Depends(admin_access),
        db: Session = Depends(get_db),
    ):
        from savings_hold_review import release

        if not provider.razorpay_checkout_is_configured():
            raise HTTPException(503, "Provider review verification unavailable")
        return mutate(
            db,
            lambda: decision_document(
                release(db, hold_id, admin, payload.reason, payload.request_key, provider, utc_now)
            ),
        )

    @app.post("/admin/savings/rates", dependencies=protected)
    def publish_rate(
        payload: RateInput, admin: User = Depends(admin_access), db: Session = Depends(get_db)
    ):
        now, until = utc_now(), utc(payload.valid_until)
        if until <= now:
            raise HTTPException(422, "Rate validity must end in the future")

        def operation():
            row = SavingsGoldRate(
                paise_per_gram=payload.paise_per_gram,
                purity="24K 995",
                source=payload.source,
                effective_at=now,
                valid_until=until,
                created_by=admin.id,
                created_at=now,
            )
            db.add(row)
            db.flush()
            return rate_document(row)

        return mutate(db, operation)

    @app.post("/admin/savings/payments/{payment_id}/reconcile-checkout", dependencies=protected)
    def reconcile_checkout(
        payment_id: DatabaseId,
        payload: ReconcileInput,
        admin: User = Depends(admin_access),
        db: Session = Depends(get_db),
    ):
        if not provider.razorpay_checkout_is_configured():
            raise HTTPException(503, "Provider reconciliation unavailable")
        return mutate(
            db,
            lambda: payment_document(
                reconcile(
                    db,
                    payment_id,
                    admin,
                    payload.provider_order_id,
                    provider.call_razorpay,
                    utc_now(),
                ),
                db,
            ),
        )

    @app.post("/savings/payments/razorpay/webhook", dependencies=protected)
    async def webhook(request: Request, db: Session = Depends(get_db)):
        from starlette.concurrency import run_in_threadpool

        if (
            not provider.razorpay_webhook_is_configured()
            or not provider.razorpay_checkout_is_configured()
        ):
            raise HTTPException(503, "Savings webhook verification is unavailable")
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 1_048_576:
                raise HTTPException(413, "Webhook is too large")
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
        return await run_in_threadpool(process_webhook, db, payload, provider)


def process_webhook(db, payload, provider):
    event = payload.get("event")
    if event in ("refund.created", "refund.processed") or (
        isinstance(event, str) and event.startswith("payment.dispute.")
    ):
        from savings_hold_events import verified_hold

        return mutate(db, lambda: verified_hold(db, payload, provider, safe_fetch))
    if event not in ("payment.captured", "order.paid"):
        return {"status": "ignored"}
    try:
        entity = payload["payload"]["payment"]["entity"]
        provider_id, order_id = entity["id"], entity["order_id"]
        if (
            not isinstance(provider_id, str)
            or not provider_id.startswith("pay_")
            or len(provider_id) > 100
        ):
            raise ValueError()
        if not isinstance(order_id, str) or len(order_id) > 100:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "Invalid captured payment event") from None
    payment = (
        db.query(SavingsPayment).filter_by(provider_order_id=order_id, mode="razorpay").first()
    )
    if payment is None:
        return {"status": "ignored"}

    def operation():
        try:
            entry = verify_razorpay(
                db,
                payment.id,
                payment.user_id,
                provider_id,
                lambda payment_id: safe_fetch(provider, payment_id),
                utc_now,
            )
        except RuntimeError:
            raise CheckoutUnavailable("Provider verification unavailable") from None
        return {"status": "posted", "entry_id": entry.id}

    return mutate(db, operation)


def safe_fetch(provider, payment_id):
    try:
        status, evidence = provider.fetch_razorpay_payment(payment_id)
    except Exception:
        raise CheckoutUnavailable("Provider verification unavailable") from None
    if status != 200:
        raise CheckoutUnavailable("Provider verification unavailable")
    return status, evidence


def decision_document(row):
    return {
        "id": row.id,
        "hold_id": row.hold_id,
        "action": row.action,
        "reason": row.reason,
        "actor_id": row.actor_id,
        "evidence_sha256": row.evidence_sha256,
        "created_at": timestamp(row.created_at),
    }


def hold_document(row, db=None):
    latest = latest_hold_decision(db, row.id) if db is not None else None
    return {
        "active": latest is None or latest.action != "released",
        "latest_decision": decision_document(latest) if latest else None,
        "can_request_release": (latest is None or latest.action != "released")
        and row.kind in ("refund", "dispute")
        and row.payment_id is not None,
        "id": row.id,
        "scheme_id": row.scheme_id,
        "payment_id": row.payment_id,
        "kind": row.kind,
        "reference": row.reference,
        "reason": row.reason,
        "actor_id": row.actor_id,
        "created_at": timestamp(row.created_at),
    }
