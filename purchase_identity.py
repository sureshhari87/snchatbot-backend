"""Default-off buyer verification. Raw PAN never enters order/provider snapshots."""

import json
import os
import re
import unicodedata
import uuid

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException

from models import User
from purchase_identity_models import PurchaseIdentity, PurchaseIdentityReview, PurchaseIdentityUse

THRESHOLD_PAISE = 20_000_000


def enabled():
    return os.getenv("PURCHASE_IDENTITY_ENABLED", "0") == "1"


def strict_runtime():
    return not (
        os.getenv("APP_ENV", "").strip().lower() == "test"
        and os.getenv("TESTING", "0").strip().lower() in {"1", "true", "yes", "on"}
    )


def verification_needed(value):
    needed = amount_requires_identity(value)
    if not needed:
        return False
    if not enabled():
        require(
            not strict_runtime(),
            "High-value purchases are unavailable until buyer verification is ready",
            503,
        )
        return False
    return True


def require(ok, detail, status=409):
    if not ok:
        raise HTTPException(status, detail, headers={"Cache-Control": "no-store"})


def amount_requires_identity(value):
    require(
        type(value) is int and 0 <= value <= 9_007_199_254_740_991,
        "Purchase amount requires review",
        422,
    )
    return value > THRESHOLD_PAISE


def text(value, *, maximum=200):
    require(isinstance(value, str), "Complete the required identity fields", 422)
    value = " ".join(value.split())
    require(
        0 < len(value) <= maximum
        and not any(unicodedata.category(c).startswith("C") for c in value),
        "Invalid identity field",
        422,
    )
    return value


def pan(value):
    require(isinstance(value, str), "Enter a valid PAN", 422)
    value = value.strip().upper()
    require(re.fullmatch(r"[A-Z]{5}[0-9]{4}[A-Z]", value) is not None, "Enter a valid PAN", 422)
    return value


def cipher():
    # Retain old keys during explicit rotation; never silently generate a production key.
    try:
        key = os.environ["PURCHASE_IDENTITY_ENCRYPTION_KEY"]
        return Fernet(key.encode("ascii"))
    except (KeyError, ValueError, UnicodeError):
        raise HTTPException(503, "Secure buyer identity storage is unavailable") from None


def facts(row):
    try:
        value = json.loads(cipher().decrypt(row.encrypted_facts.encode("ascii")).decode())
        require(
            value.get("purpose") == "sona_purchase_identity_v1"
            and value.get("identity_id") == row.id
            and value.get("user_id") == row.user_id,
            "Buyer identity requires secure review",
        )
        require(
            pan(value.get("pan"))[-4:] == row.pan_suffix, "Buyer identity requires secure review"
        )
        text(value.get("legal_name"))
        require(
            value.get("buyer_kind") in {"retail", "business"},
            "Buyer identity requires secure review",
        )
        return value
    except (InvalidToken, ValueError, TypeError, UnicodeError, AttributeError):
        raise HTTPException(503, "Buyer identity requires secure review") from None


def owner_lock(db, user_id):
    row = db.query(User).filter_by(id=user_id).populate_existing().with_for_update().first()
    require(row is not None, "Account unavailable", 404)


def latest(db, user_id):
    return (
        db.query(PurchaseIdentity)
        .filter_by(user_id=user_id)
        .order_by(PurchaseIdentity.version.desc())
        .first()
    )


def ensure_key_compatible(db):
    first = (
        db.query(PurchaseIdentity)
        .order_by(PurchaseIdentity.created_at, PurchaseIdentity.id)
        .first()
    )
    if first is not None:
        facts(first)


def submit(db, user_id, data):
    require(enabled(), "Buyer verification is not enabled", 503)
    require(
        isinstance(data, dict)
        and set(data)
        == {"request_key", "pan", "legal_name", "buyer_kind", "declaration_confirmed"},
        "Invalid buyer verification request",
        422,
    )
    require(
        data["declaration_confirmed"] is True, "Confirm the buyer details and compliance use", 422
    )
    number = pan(data["pan"])
    name = text(data["legal_name"])
    kind = data["buyer_kind"]
    require(kind in {"retail", "business"}, "Select the buyer type", 422)
    request_key = text(data["request_key"], maximum=80)
    require(
        re.search(r"[A-Z]{5}[0-9]{4}[A-Z]", request_key.upper()) is None,
        "Keep PAN out of request references",
        422,
    )
    codec = cipher()
    ensure_key_compatible(db)
    owner_lock(db, user_id)
    old = db.query(PurchaseIdentity).filter_by(user_id=user_id, request_key=request_key).first()
    if old:
        saved = facts(old)
        require(
            (saved["pan"], saved["legal_name"], saved["buyer_kind"]) == (number, name, kind),
            "Request key already used for different identity details",
        )
        return old
    previous = latest(db, user_id)
    identity = uuid.uuid4().hex
    value = dict(
        purpose="sona_purchase_identity_v1",
        identity_id=identity,
        user_id=user_id,
        pan=number,
        legal_name=name,
        buyer_kind=kind,
    )
    row = PurchaseIdentity(
        id=identity,
        user_id=user_id,
        version=previous.version + 1 if previous else 1,
        request_key=request_key,
        encrypted_facts=codec.encrypt(json.dumps(value, ensure_ascii=False).encode()).decode(
            "ascii"
        ),
        pan_suffix=number[-4:],
    )
    db.add(row)
    db.flush()
    return row


def document(db, row, *, include_name=False):
    if row is None:
        return {"state": "not_submitted"}
    review = db.query(PurchaseIdentityReview).filter_by(identity_id=row.id).first()
    result = dict(
        id=row.id,
        version=row.version,
        state=review.decision if review else "pending_review",
        masked_pan="******" + row.pan_suffix,
        verification_method=review.method if review else None,
    )
    if include_name:
        value = facts(row)
        result.update(legal_name=value["legal_name"], buyer_kind=value["buyer_kind"])
    return result


def review(db, identity_id, admin, data):
    require(enabled(), "Buyer verification is not enabled", 503)
    require(getattr(admin, "is_admin", False) is True, "Administrator access required", 403)
    require(
        isinstance(data, dict)
        and set(data)
        == {"decision", "verified_pan", "verified_legal_name", "evidence_reference", "reason"},
        "Invalid verification review",
        422,
    )
    row = db.query(PurchaseIdentity).filter_by(id=identity_id).first()
    require(row is not None, "Buyer verification unavailable", 404)
    require(row.user_id != admin.id, "An administrator cannot verify their own buyer identity", 403)
    owner_lock(db, row.user_id)
    require(latest(db, row.user_id).id == row.id, "A newer submission needs review")
    decision = data["decision"]
    require(decision in {"approved", "rejected"}, "Select a verification decision", 422)
    reference = text(data["evidence_reference"], maximum=160)
    reason = text(data["reason"], maximum=500)
    require(
        re.search(r"[A-Z]{5}[0-9]{4}[A-Z]", (reference + " " + reason).upper()) is None,
        "Keep PAN out of audit references and reasons",
        422,
    )
    if decision == "approved":
        value = facts(row)
        require(
            pan(data["verified_pan"]) == value["pan"]
            and text(data["verified_legal_name"]).casefold() == value["legal_name"].casefold(),
            "Verified PAN and legal name do not match the submission",
        )
    old = db.query(PurchaseIdentityReview).filter_by(identity_id=row.id).first()
    if old:
        require(
            (old.actor_id, old.decision, old.evidence_reference, old.reason)
            == (admin.id, decision, reference, reason),
            "Saved verification decision cannot be changed",
        )
        return old
    item = PurchaseIdentityReview(
        id=uuid.uuid4().hex,
        identity_id=row.id,
        actor_id=admin.id,
        decision=decision,
        method="manual_original_pan" if decision == "approved" else "manual_rejection",
        evidence_reference=reference,
        reason=reason,
    )
    db.add(item)
    db.flush()
    return item


def require_verified(db, user_id, amount_paise, *, legal_name=None, buyer_kind=None):
    if not verification_needed(amount_paise):
        return None
    owner_lock(db, user_id)
    row = latest(db, user_id)
    require(row is not None, "PAN and full buyer name are required above INR 2,00,000")
    decision = (
        db.query(PurchaseIdentityReview).filter_by(identity_id=row.id, decision="approved").first()
    )
    require(decision is not None, "Buyer identity verification is pending; payment is unavailable")
    value = facts(row)
    if legal_name is not None:
        require(
            text(legal_name).casefold() == value["legal_name"].casefold(),
            "Use the full verified buyer name on the purchase",
        )
    if buyer_kind is not None:
        require(buyer_kind == value["buyer_kind"], "Buyer type differs from verified identity")
    return row


def bind(db, row, *, resource_kind, resource_key, amount_paise):
    if row is None:
        return None
    require(amount_requires_identity(amount_paise), "High-value purchase binding required")
    require(
        resource_kind in {"merchandise", "custom_quote", "sales_invoice"},
        "Invalid purchase identity binding",
        422,
    )
    resource_key = text(resource_key, maximum=100)
    require(
        db.query(PurchaseIdentityReview).filter_by(identity_id=row.id, decision="approved").first()
        is not None,
        "Verified buyer identity is required",
    )
    require(
        re.search(r"[A-Z]{5}[0-9]{4}[A-Z]", resource_key.upper()) is None,
        "Keep PAN out of purchase references",
        422,
    )
    old = (
        db.query(PurchaseIdentityUse)
        .filter_by(resource_kind=resource_kind, resource_key=resource_key)
        .first()
    )
    if old:
        require(
            (old.identity_id, old.user_id, old.amount_paise) == (row.id, row.user_id, amount_paise),
            "Frozen purchase identity differs; audited review required",
        )
        return old
    item = PurchaseIdentityUse(
        id=uuid.uuid4().hex,
        identity_id=row.id,
        user_id=row.user_id,
        resource_kind=resource_kind,
        resource_key=resource_key,
        amount_paise=amount_paise,
    )
    db.add(item)
    db.flush()
    return item


def require_bound(
    db, user_id, amount_paise, *, resource_kind, resource_key, legal_name, buyer_kind, gstin=None
):
    """An invoice must retain the purchaser verified for the original transaction."""
    if not verification_needed(amount_paise):
        return None
    usage = (
        db.query(PurchaseIdentityUse)
        .filter_by(resource_kind=resource_kind, resource_key=resource_key)
        .first()
    )
    require(
        usage is not None and usage.user_id == user_id and usage.amount_paise == amount_paise,
        "Original purchase identity binding needs audited review",
    )
    row = db.get(PurchaseIdentity, usage.identity_id)
    require(
        row is not None and row.user_id == user_id, "Original buyer identity needs audited review"
    )
    review = (
        db.query(PurchaseIdentityReview).filter_by(identity_id=row.id, decision="approved").first()
    )
    require(review is not None, "Original buyer verification needs audited review")
    value = facts(row)
    require(
        text(legal_name).casefold() == value["legal_name"].casefold()
        and (buyer_kind is None or buyer_kind == value["buyer_kind"]),
        "Invoice buyer must match the original verified purchaser",
    )
    if buyer_kind == "business":
        require(
            isinstance(gstin, str) and len(gstin) == 15 and gstin[2:12].upper() == value["pan"],
            "Business GSTIN must belong to the verified purchaser",
        )
    return row


def require_order_fulfilment(db, order):
    if not enabled() and not strict_runtime():
        return None
    import billing_sources
    from rewards_vouchers_reservation_models import FinancialReservation

    require(
        not billing_sources.review(db, "merchandise", order),
        "Financial review must be resolved before fulfilment",
    )
    evidence = billing_sources.evidence(db, "merchandise", order)
    reservation = db.query(FinancialReservation).filter_by(order_id=order.id).first()
    reference = "reservation:" + str(reservation.id) if reservation else "order:" + str(order.id)
    return require_bound(
        db,
        order.user_id,
        evidence["gross_paise"],
        resource_kind="merchandise",
        resource_key=reference,
        legal_name=order.customer_name or "",
        buyer_kind=None,
    )


def require_existing_payment(
    db, user_id, amount_paise, *, resource_kind, resource_key, legal_name=None
):
    """Never attach a new purchaser automatically to an existing payment intent."""
    if not verification_needed(amount_paise):
        return None
    usage = (
        db.query(PurchaseIdentityUse)
        .filter_by(resource_kind=resource_kind, resource_key=resource_key)
        .first()
    )
    require(
        usage is not None and usage.user_id == user_id,
        "Existing payment needs audited buyer identity review",
    )
    row = db.get(PurchaseIdentity, usage.identity_id)
    require(
        row is not None and row.user_id == user_id, "Existing buyer identity needs audited review"
    )
    original = facts(row)
    return require_bound(
        db,
        user_id,
        amount_paise,
        resource_kind=resource_kind,
        resource_key=resource_key,
        legal_name=legal_name if legal_name is not None else original["legal_name"],
        buyer_kind=None,
    )
