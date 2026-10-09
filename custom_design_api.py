"""Feature-gated custom-design request, immutable quote and decision API."""

import hashlib
import json
from typing import Annotated, Literal
from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Query
from fastapi import Path as ApiPath
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import get_bool
from custom_design_models import (
    CustomDesign,
    CustomDesignAudit,
    CustomDesignDecision,
    CustomDesignQuote,
)
from models import CustomOrderRequest, Product, User, utc_now

ENABLED = get_bool("CUSTOM_DESIGNS_ENABLED", False)
MAX_MONEY = 9_223_372_036_854_775_807
Identity = Annotated[int, ApiPath(gt=0, le=2_147_483_647)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CreateInput(Input):
    request_key: str = Field(min_length=1, max_length=100)
    product_id: int | None = Field(default=None, strict=True, gt=0, le=2_147_483_647)
    description: str = Field(min_length=1, max_length=4000)
    customer_notes: str = Field(default="", max_length=4000)
    metal: str = Field(min_length=1, max_length=40)
    purity: str = Field(min_length=1, max_length=40)
    category: str | None = Field(default=None, max_length=100)
    budget_range: str = Field(min_length=1, max_length=100)
    reference_image_url: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def reference_url(self):
        if self.reference_image_url:
            parsed = urlparse(self.reference_image_url)
            if (
                parsed.scheme != "https"
                or parsed.hostname != "res.cloudinary.com"
                or parsed.username
                or parsed.password
                or parsed.port not in (None, 443)
            ):
                raise ValueError("Use a public HTTPS Cloudinary reference image")
        return self


class QuoteInput(Input):
    request_key: str = Field(min_length=1, max_length=100)
    total_paise: int = Field(strict=True, gt=0, le=MAX_MONEY)
    advance_paise: int = Field(strict=True, ge=0, le=MAX_MONEY)
    notes: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def amount(self):
        if self.advance_paise > self.total_paise or 0 < self.advance_paise < 100:
            raise ValueError("Advance must be zero or at least INR1, and no greater than the quote")
        return self


class DecisionInput(Input):
    request_key: str = Field(min_length=1, max_length=100)
    quote_id: int = Field(strict=True, gt=0, le=2_147_483_647)
    action: Literal["approved", "rejected"]


def enabled():
    if not ENABLED:
        raise HTTPException(503, "Custom-design migration is not enabled")


def digest(payload):
    return hashlib.sha256(
        json.dumps(
            payload.model_dump(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
    ).hexdigest()


def timestamp(value):
    from datetime import timezone

    return value.replace(tzinfo=timezone.utc).isoformat()


def locked(db, design_id, user_id=None):
    query = db.query(CustomDesign).filter_by(id=design_id)
    if user_id is not None:
        query = query.filter_by(user_id=user_id)
    row = query.with_for_update().populate_existing().first()
    if row is None:
        raise HTTPException(404, "Custom design unavailable")
    return row


def audit(db, row, key, action, actor, sha, quote_id=None, detail=None):
    db.add(
        CustomDesignAudit(
            design_id=row.id,
            event_key=key,
            detail=detail,
            action=action,
            actor_id=actor,
            quote_id=quote_id,
            payload_sha256=sha,
            created_at=utc_now(),
        )
    )


def latest_quote(db, design_id):
    return (
        db.query(CustomDesignQuote)
        .filter_by(design_id=design_id)
        .order_by(CustomDesignQuote.version.desc())
        .first()
    )


def quote_document(row, db):
    decision = (
        db.query(CustomDesignDecision)
        .filter_by(quote_id=row.id)
        .order_by(CustomDesignDecision.id.desc())
        .first()
    )
    return dict(
        id=row.id,
        version=row.version,
        total_paise=row.total_paise,
        advance_paise=row.advance_paise,
        notes=row.notes,
        state=decision.action if decision else "sent",
        created_at=timestamp(row.created_at),
    )


def document(row, db):
    parent = db.get(CustomOrderRequest, row.custom_order_id)
    quote = latest_quote(db, row.id)
    from custom_design_payments import CustomDesignAdvance, CustomDesignCheckout, held

    credit = db.query(CustomDesignAdvance).filter_by(design_id=row.id).first()
    attempt = db.query(CustomDesignCheckout).filter_by(design_id=row.id).first()
    review_required = held(db, row.id)
    quote_state = quote_document(quote, db) if quote else None
    started = (
        db.query(CustomDesignAudit)
        .filter_by(design_id=row.id, action="manufacturing_started")
        .first()
        is not None
    )
    accepted = bool(quote_state and quote_state["state"] == "approved")
    advance_paid = bool(
        credit
        and quote
        and credit.quote_id == quote.id
        and credit.amount_paise == quote.advance_paise
    )
    return dict(
        id=row.id,
        custom_order_id=row.custom_order_id,
        user_id=row.user_id,
        product_id=parent.product_id,
        description=parent.description,
        customer_notes=row.customer_notes,
        metal=parent.metal,
        category=parent.category,
        purity=row.purity,
        budget_range=row.budget_range,
        reference_image_url=row.reference_image_url,
        created_at=timestamp(row.created_at),
        quote=quote_document(quote, db) if quote else None,
        advance_checkout_available=bool(
            accepted
            and quote.advance_paise
            and not credit
            and not review_required
            and (not attempt or attempt.state == "ready")
        ),
        advance=dict(
            id=credit.id,
            amount_paise=credit.amount_paise,
            quote_id=credit.quote_id,
            state="posted",
            created_at=timestamp(credit.created_at),
        )
        if credit
        else None,
        checkout_state=attempt.state if attempt else None,
        review_required=review_required,
        manufacturing_started=started,
        manufacturing_allowed=bool(
            accepted
            and (not quote.advance_paise or advance_paid)
            and not review_required
            and not started
        ),
        completion_available=False,
    )


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
        raise HTTPException(
            409, "Concurrent custom-design change; reload before retrying"
        ) from None


def replay(row, sha, actor=None):
    if row.payload_sha256 != sha or (actor is not None and row.actor_id != actor):
        raise HTTPException(409, "Request key was already used for different terms")


def create(db, user, payload, provider):
    sha = digest(payload)
    old = db.query(CustomDesign).filter_by(user_id=user.id, request_key=payload.request_key).first()
    if old:
        replay(old, sha)
        return document(old, db)
    if payload.product_id is not None and db.get(Product, payload.product_id) is None:
        raise HTTPException(404, "Product unavailable")
    parent = CustomOrderRequest(
        user_id=user.id,
        product_id=payload.product_id,
        description=payload.description,
        metal=payload.metal,
        category=payload.category,
        status="requested",
    )
    db.add(parent)
    db.flush()
    row = CustomDesign(
        custom_order_id=parent.id,
        user_id=user.id,
        request_key=payload.request_key,
        payload_sha256=sha,
        customer_notes=payload.customer_notes,
        reference_image_url=payload.reference_image_url,
        purity=payload.purity,
        budget_range=payload.budget_range,
        created_at=utc_now(),
    )
    db.add(row)
    db.flush()
    provider.create_customer_action_lead(
        db,
        user,
        source="custom_order",
        intent="custom_order",
        message=payload.description,
        session_id=None,
    )
    audit(db, row, "request:" + payload.request_key, "request_submitted", user.id, sha)
    db.flush()
    return document(row, db)


def publish(db, design_id, admin, payload):
    row = locked(db, design_id)
    sha = digest(payload)
    old = (
        db.query(CustomDesignQuote)
        .filter_by(design_id=row.id, request_key=payload.request_key)
        .first()
    )
    if old:
        replay(old, sha, admin.id)
        return quote_document(old, db)
    financial_change_guard(db, row)
    previous = latest_quote(db, row.id)
    quote = CustomDesignQuote(
        design_id=row.id,
        version=previous.version + 1 if previous else 1,
        request_key=payload.request_key,
        payload_sha256=sha,
        total_paise=payload.total_paise,
        advance_paise=payload.advance_paise,
        notes=payload.notes,
        actor_id=admin.id,
        created_at=utc_now(),
    )
    db.add(quote)
    db.flush()
    audit(db, row, "quote:" + payload.request_key, "quote_published", admin.id, sha, quote.id)
    db.flush()
    return quote_document(quote, db)


def decide(db, design_id, user, payload):
    row = locked(db, design_id, user.id)
    sha = digest(payload)
    old = (
        db.query(CustomDesignDecision)
        .filter_by(design_id=row.id, request_key=payload.request_key)
        .first()
    )
    if old:
        replay(old, sha, user.id)
        return document(row, db)
    financial_change_guard(db, row)
    quote = latest_quote(db, row.id)
    if quote is None or quote.id != payload.quote_id:
        raise HTTPException(409, "Quote changed; review the current version")
    db.add(
        CustomDesignDecision(
            design_id=row.id,
            quote_id=quote.id,
            request_key=payload.request_key,
            payload_sha256=sha,
            action=payload.action,
            actor_id=user.id,
            created_at=utc_now(),
        )
    )
    audit(
        db,
        row,
        "decision:" + payload.request_key,
        "quote_" + payload.action,
        user.id,
        sha,
        quote.id,
    )
    db.flush()
    return document(row, db)


def financial_change_guard(db, row):
    from custom_design_payments import CustomDesignCheckout, available

    available(db, row)
    if (
        db.query(CustomDesignCheckout).filter_by(design_id=row.id).first()
        or db.query(CustomDesignAudit)
        .filter_by(design_id=row.id, action="manufacturing_started")
        .first()
    ):
        raise HTTPException(
            409,
            "Checkout or manufacturing has begun; audited review is required before changing terms",
        )


def reject_legacy_status_update(db, request_id):
    if ENABLED and db.query(CustomDesign).filter_by(custom_order_id=request_id).first() is not None:
        raise HTTPException(409, "Use the structured custom-design workflow")


def install(app, get_db, get_current_user, require_permission, provider):
    protected = [Depends(enabled)]
    admin_access = require_permission("support:manage")

    @app.post("/custom-designs", dependencies=protected)
    def create_design(
        payload: CreateInput, user: User = Depends(get_current_user), db: Session = Depends(get_db)
    ):
        return mutate(db, lambda: create(db, user, payload, provider))

    @app.get("/custom-designs/my", dependencies=protected)
    def own_designs(
        offset: int = Query(0, ge=0, le=10000),
        limit: int = Query(20, ge=1, le=50),
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        rows = (
            db.query(CustomDesign)
            .filter_by(user_id=user.id)
            .order_by(CustomDesign.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [document(row, db) for row in rows]

    @app.get("/custom-designs/{design_id}", dependencies=protected)
    def own_design(
        design_id: Identity, user: User = Depends(get_current_user), db: Session = Depends(get_db)
    ):
        return document(locked(db, design_id, user.id), db)

    @app.post("/custom-designs/{design_id}/decision", dependencies=protected)
    def decision(
        design_id: Identity,
        payload: DecisionInput,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        return mutate(db, lambda: decide(db, design_id, user, payload))

    @app.get("/admin/custom-designs", dependencies=protected)
    def designs(
        offset: int = Query(0, ge=0, le=10000),
        limit: int = Query(20, ge=1, le=50),
        admin: User = Depends(admin_access),
        db: Session = Depends(get_db),
    ):
        rows = (
            db.query(CustomDesign)
            .order_by(CustomDesign.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [document(row, db) for row in rows]

    @app.post("/admin/custom-designs/{design_id}/quotes", dependencies=protected)
    def quote(
        design_id: Identity,
        payload: QuoteInput,
        admin: User = Depends(admin_access),
        db: Session = Depends(get_db),
    ):
        return mutate(db, lambda: publish(db, design_id, admin, payload))

    @app.get("/admin/custom-designs/{design_id}/quotes", dependencies=protected)
    def quotes(
        design_id: Identity,
        offset: int = Query(0, ge=0, le=10000),
        limit: int = Query(20, ge=1, le=50),
        admin: User = Depends(admin_access),
        db: Session = Depends(get_db),
    ):
        locked(db, design_id)
        rows = (
            db.query(CustomDesignQuote)
            .filter_by(design_id=design_id)
            .order_by(CustomDesignQuote.version.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [quote_document(row, db) for row in rows]

    @app.get("/admin/custom-designs/{design_id}/audit", dependencies=protected)
    def history(
        design_id: Identity,
        offset: int = Query(0, ge=0, le=10000),
        limit: int = Query(20, ge=1, le=50),
        admin: User = Depends(admin_access),
        db: Session = Depends(get_db),
    ):
        locked(db, design_id)
        rows = (
            db.query(CustomDesignAudit)
            .filter_by(design_id=design_id)
            .order_by(CustomDesignAudit.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [
            dict(
                id=row.id,
                action=row.action,
                actor_id=row.actor_id,
                quote_id=row.quote_id,
                created_at=timestamp(row.created_at),
            )
            for row in rows
        ]
