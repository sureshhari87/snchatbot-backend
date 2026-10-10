"""Authenticated reward/voucher APIs. Disabled by default; no release/reset API."""

import hashlib
import json
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Query, Request, Response
from fastapi import Path as ApiPath
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

import financial_checkout
import voucher_funding
from models import User, utc_now
from rewards_vouchers_models import (
    GiftVoucher,
    GiftVoucherEntry,
    RewardAccount,
    RewardEntry,
    RewardVoucherHold,
)
from rewards_vouchers_reservation_models import FinancialReservation
from rewards_vouchers_transactions import place_hold, require
from schemas import RazorpayOrderCreate
from voucher_funding_models import VoucherFunding, VoucherFundingEvent

Identity = Annotated[int, ApiPath(gt=0, le=2147483647)]
Money = Annotated[int, Field(strict=True, ge=100, le=9223372036854775807)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PurchaseInput(Input):
    request_key: str = Field(min_length=1, max_length=100)
    amount_paise: Money
    payment_mode: Literal["manual", "razorpay"]
    assigned_user_id: int | None = Field(default=None, strict=True, gt=0, le=2147483647)


class ComplimentaryInput(Input):
    request_key: str = Field(min_length=1, max_length=70)
    purchaser_id: int = Field(strict=True, gt=0, le=2147483647)
    assigned_user_id: int | None = Field(default=None, strict=True, gt=0, le=2147483647)
    amount_paise: Money
    reason: str = Field(min_length=1, max_length=200)


class ManualInput(Input):
    confirmed_paise: Money
    reference: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=200)


class HoldInput(Input):
    reference: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=200)


class ProofInput(Input):
    razorpay_order_id: str = Field(pattern=r"^order_[A-Za-z0-9]{1,90}$")
    razorpay_payment_id: str = Field(pattern=r"^pay_[A-Za-z0-9]{1,90}$")
    razorpay_signature: str = Field(pattern=r"^[a-fA-F0-9]{64}$")


class ReconcileInput(Input):
    provider_order_id: str = Field(pattern=r"^order_[A-Za-z0-9]{1,90}$")


class CodeInput(Input):
    code: str = Field(pattern=r"^[A-Za-z0-9-]{8,80}$")


def mutate(db, operation):
    try:
        result = operation()
        db.commit()
        return result
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Concurrent financial change; reload before retrying") from None
    except ValueError:
        db.rollback()
        raise HTTPException(422, "Invalid financial input") from None
    except Exception:
        db.rollback()
        raise


def provider_ready(provider):
    require(
        provider.razorpay_checkout_is_configured(), "Financial payment provider unavailable", 503
    )


def private(response: Response):
    response.headers["Cache-Control"] = "no-store"


def owned(db, voucher_id, user_id):
    row = (
        db.query(GiftVoucher)
        .filter(
            GiftVoucher.id == voucher_id,
            or_(GiftVoucher.purchaser_id == user_id, GiftVoucher.assigned_user_id == user_id),
        )
        .first()
    )
    require(row is not None, "Voucher not found", 404)
    return row


def voucher_reconciliation(db, row):
    entries = db.query(GiftVoucherEntry).filter_by(voucher_id=row.id).all()
    ledger_balance = sum(entry.amount_delta_paise for entry in entries)
    reserved = sum(
        item.voucher_paise
        for item in db.query(FinancialReservation)
        .filter(FinancialReservation.voucher_id == row.id, FinancialReservation.state != "posted")
        .all()
    )
    funding = db.get(VoucherFunding, row.id)
    credits = [entry for entry in entries if entry.kind != "redemption"]
    issues = []
    if ledger_balance != row.balance_paise:
        issues.append("ledger_balance_mismatch")
    if reserved != row.reserved_paise:
        issues.append("reservation_balance_mismatch")
    if len(credits) != (1 if row.state == "active" else 0):
        issues.append("funding_count_mismatch")
    if not funding or (funding.state == "posted") != (row.state == "active"):
        issues.append("funding_state_mismatch")
    return dict(
        voucher=voucher_funding.document(db, row, funding),
        consistent=not issues,
        issues=issues,
        funding_entry_count=len(credits),
        ledger_entry_count=len(entries),
        ledger_balance_paise=ledger_balance,
        posting_audit_count=db.query(VoucherFundingEvent)
        .filter_by(voucher_id=row.id, action="funding_posted")
        .count(),
        hold_count=db.query(RewardVoucherHold).filter_by(voucher_id=row.id).count(),
        provider_order_id=funding.provider_order_id if funding else None,
        receipt=funding.receipt if funding else None,
        provider_verified=False,
        release_authorized=False,
    )


def install(app, get_db, get_current_user, require_permission, provider):
    protected = [Depends(financial_checkout.enabled), Depends(private)]
    voucher_admin = require_permission("vouchers:manage")
    reward_admin = require_permission("rewards:manage")

    @app.get("/rewards", dependencies=protected)
    def reward_account(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
        row = db.get(RewardAccount, user.id)
        review = db.query(RewardVoucherHold).filter_by(reward_user_id=user.id).first() is not None
        return dict(
            balance_points=row.balance_points if row else 0,
            reserved_points=row.reserved_points if row else 0,
            available_points=row.balance_points - row.reserved_points if row and not review else 0,
            review_required=review,
            point_value_paise=100,
        )

    @app.get("/rewards/history", dependencies=protected)
    def reward_history(
        offset: int = Query(0, ge=0, le=10000),
        limit: int = Query(20, ge=1, le=50),
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        return [
            dict(
                id=row.id,
                kind=row.kind,
                points_delta=row.points_delta,
                order_id=row.order_id,
                created_at=row.created_at,
            )
            for row in db.query(RewardEntry)
            .filter_by(user_id=user.id)
            .order_by(RewardEntry.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        ]

    @app.post("/vouchers", dependencies=protected)
    def purchase(
        payload: PurchaseInput,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        def operation():
            row, funding = voucher_funding.create(db, purchaser_id=user.id, **payload.model_dump())
            return voucher_funding.document(db, row, funding)

        return mutate(db, operation)

    @app.get("/vouchers/my", dependencies=protected)
    def vouchers(
        offset: int = Query(0, ge=0, le=10000),
        limit: int = Query(20, ge=1, le=50),
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        rows = (
            db.query(GiftVoucher)
            .filter(
                or_(GiftVoucher.purchaser_id == user.id, GiftVoucher.assigned_user_id == user.id)
            )
            .order_by(GiftVoucher.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [voucher_funding.document(db, row) for row in rows]

    @app.post("/vouchers/validate", dependencies=protected)
    def validate(
        payload: CodeInput, user: User = Depends(get_current_user), db: Session = Depends(get_db)
    ):
        row = (
            db.query(GiftVoucher)
            .filter_by(code_hash=hashlib.sha256(payload.code.upper().encode()).hexdigest())
            .first()
        )
        require(
            row is not None
            and row.state == "active"
            and row.expires_at > utc_now()
            and row.assigned_user_id in (None, user.id)
            and not voucher_funding.held(db, row),
            "Voucher unavailable",
            404,
        )
        return dict(
            available_paise=row.balance_paise - row.reserved_paise, expires_at=row.expires_at
        )

    @app.get("/vouchers/{voucher_id}", dependencies=protected)
    def read(
        voucher_id: Identity, user: User = Depends(get_current_user), db: Session = Depends(get_db)
    ):
        return voucher_funding.document(db, owned(db, voucher_id, user.id))

    @app.get("/vouchers/{voucher_id}/history", dependencies=protected)
    def voucher_history(
        voucher_id: Identity,
        offset: int = Query(0, ge=0, le=10000),
        limit: int = Query(20, ge=1, le=50),
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        owned(db, voucher_id, user.id)
        return [
            dict(
                id=row.id,
                kind=row.kind,
                amount_delta_paise=row.amount_delta_paise,
                created_at=row.created_at,
            )
            for row in db.query(GiftVoucherEntry)
            .filter_by(voucher_id=voucher_id)
            .order_by(GiftVoucherEntry.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        ]

    @app.post("/vouchers/{voucher_id}/code", dependencies=protected)
    def code(
        voucher_id: Identity, user: User = Depends(get_current_user), db: Session = Depends(get_db)
    ):
        return voucher_funding.reveal(db, voucher_id, user.id)

    @app.post("/vouchers/{voucher_id}/checkout", dependencies=protected)
    def checkout(
        voucher_id: Identity, user: User = Depends(get_current_user), db: Session = Depends(get_db)
    ):
        provider_ready(provider)

        def operation():
            row, funding = voucher_funding.checkout(db, voucher_id, user.id, provider)
            return dict(
                voucher_id=row.id,
                key_id=provider.RAZORPAY_KEY_ID,
                order_id=funding.provider_order_id,
                amount_paise=row.amount_paise,
                currency="INR",
                checkout_available=funding.state == "ready" and row.state == "pending",
                voucher=voucher_funding.document(db, row, funding),
            )

        return mutate(db, operation)

    @app.post("/vouchers/{voucher_id}/verify", dependencies=protected)
    def verify(
        voucher_id: Identity,
        payload: ProofInput,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        provider_ready(provider)

        def operation():
            row, funding = voucher_funding.locked(db, voucher_id, user.id)
            require(
                funding.provider_order_id == payload.razorpay_order_id,
                "Proof differs from bound voucher checkout",
            )
            require(
                provider.razorpay_checkout_signature_is_valid(
                    payload.razorpay_order_id,
                    payload.razorpay_payment_id,
                    payload.razorpay_signature,
                ),
                "Invalid payment signature",
                400,
            )
            return voucher_funding.post_online(
                db, row, funding, payload.razorpay_payment_id, provider
            )

        return mutate(db, operation)

    @app.post("/vouchers/{voucher_id}/refresh", dependencies=protected)
    def refresh(
        voucher_id: Identity, user: User = Depends(get_current_user), db: Session = Depends(get_db)
    ):
        provider_ready(provider)
        return mutate(db, lambda: voucher_funding.refresh(db, voucher_id, user.id, provider))

    @app.post("/admin/vouchers/complimentary", dependencies=protected)
    def complimentary(
        payload: ComplimentaryInput,
        actor: User = Depends(voucher_admin),
        db: Session = Depends(get_db),
    ):
        def operation():
            terms = payload.model_dump()
            terms["request_key"] = f"comp:{actor.id}:" + terms["request_key"]
            row, funding = voucher_funding.create(
                db, **terms, payment_mode="complimentary", actor_id=actor.id
            )
            return voucher_funding.document(db, row, funding)

        return mutate(db, operation)

    @app.get("/admin/vouchers", dependencies=protected)
    def admin_list(
        offset: int = Query(0, ge=0, le=10000),
        limit: int = Query(20, ge=1, le=50),
        actor: User = Depends(voucher_admin),
        db: Session = Depends(get_db),
    ):
        return [
            voucher_funding.document(db, row)
            for row in db.query(GiftVoucher)
            .order_by(GiftVoucher.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        ]

    @app.post("/admin/vouchers/{voucher_id}/confirm-manual", dependencies=protected)
    def confirm(
        voucher_id: Identity,
        payload: ManualInput,
        actor: User = Depends(voucher_admin),
        db: Session = Depends(get_db),
    ):
        return mutate(
            db,
            lambda: voucher_funding.fund_manual(
                db, voucher_id=voucher_id, actor_id=actor.id, **payload.model_dump()
            ),
        )

    @app.post("/admin/vouchers/{voucher_id}/hold", dependencies=protected)
    def hold(
        voucher_id: Identity,
        payload: HoldInput,
        actor: User = Depends(voucher_admin),
        db: Session = Depends(get_db),
    ):
        def operation():
            row, _ = voucher_funding.locked(db, voucher_id)
            place_hold(
                db,
                kind="admin_review",
                voucher_id=row.id,
                actor_id=actor.id,
                **payload.model_dump(),
            )
            return voucher_funding.document(db, row)

        return mutate(db, operation)

    @app.get("/admin/vouchers/{voucher_id}/reconciliation", dependencies=protected)
    def reconciliation(
        voucher_id: Identity, actor: User = Depends(voucher_admin), db: Session = Depends(get_db)
    ):
        row, _ = voucher_funding.locked(db, voucher_id)
        return voucher_reconciliation(db, row)

    @app.post("/admin/vouchers/{voucher_id}/reconcile-checkout", dependencies=protected)
    def reconcile(
        voucher_id: Identity,
        payload: ReconcileInput,
        actor: User = Depends(voucher_admin),
        db: Session = Depends(get_db),
    ):
        provider_ready(provider)
        return mutate(
            db,
            lambda: voucher_funding.refresh(
                db,
                voucher_id,
                actor.id,
                provider,
                admin_access=True,
                provider_order_id=payload.provider_order_id,
            ),
        )

    @app.post("/admin/rewards/{customer_id}/hold", dependencies=protected)
    def reward_hold(
        customer_id: Identity,
        payload: HoldInput,
        actor: User = Depends(reward_admin),
        db: Session = Depends(get_db),
    ):
        def operation():
            row = place_hold(
                db,
                kind="admin_review",
                reward_user_id=customer_id,
                actor_id=actor.id,
                **payload.model_dump(),
            )
            return dict(hold_id=row.id, review_required=True, release_authorized=False)

        return mutate(db, operation)

    @app.post("/financial/checkout/quote", dependencies=protected)
    def quote(
        payload: RazorpayOrderCreate,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        return financial_checkout.preview(db, payload, user, provider)

    @app.post("/financial/checkouts/{reservation_id}/refresh", dependencies=protected)
    def checkout_refresh(
        reservation_id: Identity,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        provider_ready(provider)
        return mutate(db, lambda: financial_checkout.refresh(db, reservation_id, user.id, provider))

    @app.post("/admin/financial/checkouts/{reservation_id}/reconcile", dependencies=protected)
    def checkout_reconcile(
        reservation_id: Identity,
        payload: ReconcileInput,
        actor: User = Depends(reward_admin),
        db: Session = Depends(get_db),
    ):
        provider_ready(provider)
        return mutate(
            db,
            lambda: financial_checkout.refresh(
                db,
                reservation_id,
                actor.id,
                provider,
                admin_access=True,
                provider_order_id=payload.provider_order_id,
            ),
        )

    @app.post("/financial/payments/razorpay/webhook", dependencies=protected)
    async def webhook(request: Request, db: Session = Depends(get_db)):
        provider_ready(provider)
        require(provider.razorpay_webhook_is_configured(), "Financial webhook unavailable", 503)
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            require(len(raw) <= 1048576, "Webhook too large", 413)
        require(
            provider.razorpay_signature_is_valid(
                bytes(raw), request.headers.get("x-razorpay-signature")
            ),
            "Invalid webhook signature",
            400,
        )
        try:
            payload = json.loads(raw)
            require(isinstance(payload, dict), "Invalid webhook body", 400)
        except (ValueError, TypeError):
            raise HTTPException(400, "Invalid webhook body") from None
        from financial_events import handle

        return await run_in_threadpool(
            mutate, db, lambda: handle(db, payload, provider) or dict(status="ignored")
        )
