"""Bound advance checkout, capture posting and audited review holds."""

import hashlib
import json
import re
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)

from database import Base
from models import utc_now


class CustomDesignCheckout(Base):
    __tablename__ = "custom_design_checkouts"
    __table_args__ = (
        CheckConstraint(
            "state IN ('creating','unknown','ready')", name="ck_custom_design_checkout_state"
        ),
    )
    quote_id = Column(Integer, ForeignKey("custom_design_quotes.id"), primary_key=True)
    design_id = Column(Integer, ForeignKey("custom_designs.id"), nullable=False, unique=True)
    receipt = Column(String(40), nullable=False, unique=True)
    provider_order_id = Column(String(100), nullable=True, unique=True)
    state = Column(String(20), nullable=False)
    created_at = Column(DateTime, nullable=False)


class CustomDesignAdvance(Base):
    __tablename__ = "custom_design_advances"
    __table_args__ = (
        CheckConstraint("amount_paise >= 100", name="ck_custom_design_advance_amount"),
    )
    id = Column(Integer, primary_key=True)
    design_id = Column(Integer, ForeignKey("custom_designs.id"), nullable=False, unique=True)
    quote_id = Column(Integer, ForeignKey("custom_design_quotes.id"), nullable=False, unique=True)
    amount_paise = Column(BigInteger, nullable=False)
    provider_order_id = Column(String(100), nullable=False, unique=True)
    provider_payment_id = Column(String(100), nullable=False, unique=True)
    created_at = Column(DateTime, nullable=False)


class CustomDesignHold(Base):
    __tablename__ = "custom_design_holds"
    __table_args__ = (
        UniqueConstraint("design_id", "reference", name="uq_custom_design_hold_reference"),
    )
    id = Column(Integer, primary_key=True)
    design_id = Column(Integer, ForeignKey("custom_designs.id"), nullable=False, index=True)
    reference = Column(String(100), nullable=False)
    reason = Column(String(200), nullable=False)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False)


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def held(db, design_id):
    return db.query(CustomDesignHold).filter_by(design_id=design_id).first() is not None


def hold(db, row, reference, reason, actor=None):
    from custom_design_api import audit

    old = db.query(CustomDesignHold).filter_by(design_id=row.id, reference=reference).first()
    if old:
        if old.reason != reason or old.actor_id != actor:
            raise HTTPException(409, "Hold reference reused for different evidence")
        return old
    item = CustomDesignHold(
        design_id=row.id, reference=reference, reason=reason, actor_id=actor, created_at=utc_now()
    )
    db.add(item)
    audit(db, row, "hold:" + reference, "review_required", actor, fingerprint([reference, reason]))
    db.flush()
    return item


def available(db, row):
    if held(db, row.id):
        raise HTTPException(409, "Custom design is held for audited review")


def accepted(db, row):
    from custom_design_api import latest_quote
    from custom_design_models import CustomDesignDecision

    available(db, row)
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
        raise HTTPException(409, "Accept the current quote before paying an advance")
    if q.advance_paise == 0:
        raise HTTPException(409, "This quote requires no advance")
    return q


def identifier(value, prefix):
    return (
        isinstance(value, str) and re.fullmatch(prefix + r"_[A-Za-z0-9]{1,90}", value) is not None
    )


def order_matches(order, q, attempt):
    return (
        isinstance(order, dict)
        and order.get("entity") == "order"
        and identifier(order.get("id"), "order")
        and type(order.get("amount")) is int
        and order["amount"] == q.advance_paise
        and order.get("currency") == "INR"
        and order.get("receipt") == attempt.receipt
        and order.get("status") in ("created", "attempted", "paid")
        and isinstance(order.get("notes"), dict)
        and order["notes"].get("purpose") == "sona_custom_design"
        and order["notes"].get("custom_design_quote_id") == str(q.id)
    )


def provider_get(provider, path):
    try:
        code, data = provider.call_razorpay("GET", path)
    except Exception:
        raise HTTPException(503, "Advance provider verification unavailable") from None
    if code != 200 or not isinstance(data, dict):
        raise HTTPException(503, "Advance provider verification unavailable")
    return data


def checkout(db, design_id, user_id, provider):
    from custom_design_api import audit, locked

    row = locked(db, design_id, user_id)
    q = accepted(db, row)
    if db.query(CustomDesignAdvance).filter_by(design_id=row.id).first():
        raise HTTPException(409, "Advance already posted; reload the request")
    attempt = db.get(CustomDesignCheckout, q.id)
    if attempt:
        if attempt.state == "ready" and attempt.provider_order_id:
            return attempt, q
        raise HTTPException(
            409, "Checkout outcome requires store reconciliation; no new order created"
        )
    attempt = CustomDesignCheckout(
        quote_id=q.id,
        design_id=row.id,
        receipt="cd_" + uuid4().hex,
        state="creating",
        created_at=utc_now(),
    )
    db.add(attempt)
    audit(
        db,
        row,
        "checkout:" + str(q.id),
        "checkout_started",
        user_id,
        fingerprint([q.id, attempt.receipt]),
        q.id,
    )
    db.commit()
    payload = dict(
        amount=q.advance_paise,
        currency="INR",
        receipt=attempt.receipt,
        partial_payment=False,
        notes=dict(purpose="sona_custom_design", custom_design_quote_id=str(q.id)),
    )
    try:
        status, order = provider.call_razorpay("POST", "orders", payload)
        valid = status in (200, 201) and order_matches(order, q, attempt)
    except Exception:
        valid, order = False, None
    row = locked(db, design_id, user_id)
    db.refresh(attempt)
    if attempt.state == "ready":
        if valid and attempt.provider_order_id != order["id"]:
            raise HTTPException(409, "Checkout binding changed")
        return attempt, q
    if not valid:
        attempt.state = "unknown"
        audit(
            db,
            row,
            "checkout-unknown:" + str(q.id),
            "checkout_unknown",
            None,
            fingerprint([q.id, attempt.receipt]),
            q.id,
        )
        db.commit()
        raise HTTPException(503, "Checkout outcome unknown; store reconciliation required")
    attempt.state, attempt.provider_order_id = "ready", order["id"]
    audit(
        db,
        row,
        "checkout-bound:" + str(q.id),
        "checkout_bound",
        user_id,
        fingerprint([q.id, order["id"]]),
        q.id,
    )
    db.flush()
    return attempt, q


def post_capture(db, row, q, attempt, payment_id, provider):
    from custom_design_api import audit

    evidence = provider_get(provider, "payments/" + payment_id)
    if (
        not identifier(payment_id, "pay")
        or evidence.get("id") != payment_id
        or evidence.get("entity") != "payment"
        or evidence.get("order_id") != attempt.provider_order_id
        or evidence.get("currency") != "INR"
        or type(evidence.get("amount")) is not int
        or evidence["amount"] != q.advance_paise
        or evidence.get("status") != "captured"
        or evidence.get("captured") is not True
        or type(evidence.get("amount_refunded")) is not int
    ):
        raise HTTPException(409, "Captured advance does not match the accepted quote")
    old = db.query(CustomDesignAdvance).filter_by(design_id=row.id).first()
    if old and (
        old.quote_id != q.id
        or old.provider_order_id != attempt.provider_order_id
        or old.provider_payment_id != payment_id
        or old.amount_paise != q.advance_paise
    ):
        raise HTTPException(409, "Advance already posted against different evidence")
    adverse = evidence["amount_refunded"] != 0 or evidence.get("refund_status") is not None
    if adverse:
        hold(db, row, "payment:" + payment_id, "Refunded or uncertain captured advance")
        db.commit()
        raise HTTPException(409, "Advance requires audited refund review")
    if old:
        return old
    credit = CustomDesignAdvance(
        design_id=row.id,
        quote_id=q.id,
        amount_paise=q.advance_paise,
        provider_order_id=attempt.provider_order_id,
        provider_payment_id=payment_id,
        created_at=utc_now(),
    )
    db.add(credit)
    audit(
        db,
        row,
        "advance:" + str(q.id),
        "advance_posted",
        None,
        fingerprint([q.id, payment_id, q.advance_paise]),
        q.id,
    )
    db.flush()
    return credit


def refresh(db, design_id, user_id, provider, order_id=None, admin=False):
    from custom_design_api import audit, locked
    from custom_design_models import CustomDesignQuote

    row = locked(db, design_id, None if admin else user_id)
    attempt = db.query(CustomDesignCheckout).filter_by(design_id=row.id).first()
    if not attempt:
        raise HTTPException(409, "No existing advance checkout")
    q = db.get(CustomDesignQuote, attempt.quote_id)
    target = attempt.provider_order_id
    if admin and not target:
        target = order_id
    if not target or not identifier(target, "order") or (order_id and order_id != target):
        raise HTTPException(409, "Advance order requires store reconciliation")
    order = provider_get(provider, "orders/" + target)
    if not order_matches(order, q, attempt):
        raise HTTPException(409, "Provider order does not match the saved checkout")
    if not attempt.provider_order_id:
        attempt.state, attempt.provider_order_id = "ready", target
        audit(
            db,
            row,
            "checkout-bound:" + str(q.id),
            "checkout_bound",
            user_id,
            fingerprint([q.id, target]),
            q.id,
        )
    if order.get("status") != "paid":
        return None
    collection = provider_get(provider, "orders/" + target + "/payments")
    items = collection.get("items")
    if (
        collection.get("entity") != "collection"
        or not isinstance(items, list)
        or type(collection.get("count")) is not int
        or collection["count"] != len(items)
        or len(items) >= 100
        or any(not isinstance(item, dict) for item in items)
    ):
        raise HTTPException(503, "Advance payment collection unavailable")
    captures = [item for item in items if item.get("status") == "captured"]
    if len(captures) != 1 or not identifier(captures[0].get("id"), "pay"):
        raise HTTPException(409, "Paid advance requires store reconciliation")
    return post_capture(db, row, q, attempt, captures[0]["id"], provider)
