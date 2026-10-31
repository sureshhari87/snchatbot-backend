"""Owner-scoped contact details, separate from authentication and immutable receipts."""

import hashlib
import hmac
import os
import re
import secrets
import unicodedata
from datetime import timedelta
from uuid import uuid4

from email_validator import EmailNotValidError, validate_email
from fastapi import HTTPException
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint, text

from database import Base
from models import User, utc_now


class CustomerProfile(Base):
    __tablename__ = "customer_profiles"
    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    version = Column(Integer, nullable=False, default=1)
    display_name = Column(String(120), nullable=False, default="")
    contact_email = Column(String(254), nullable=True, unique=True)
    email_verified_at = Column(DateTime, nullable=True)


class ContactChallenge(Base):
    __tablename__ = "customer_contact_challenges"
    __table_args__ = (
        UniqueConstraint("user_id", "request_key", name="uq_contact_challenge_request"),
    )
    id = Column(String(32), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    email = Column(String(254), nullable=False, index=True)
    request_key = Column(String(80), nullable=False)
    code_hash = Column(String(64), nullable=False)
    state = Column(String(20), nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=utc_now)
    expires_at = Column(DateTime, nullable=False)


def enabled():
    return os.getenv("CUSTOMER_PROFILE_ENABLED", "0") == "1"


def require(ok, message, status=409):
    if not ok:
        raise HTTPException(status, message, headers={"Cache-Control": "no-store"})


def lock(db, uid):
    require(
        db.query(User).filter_by(id=uid).with_for_update().first() is not None,
        "Account unavailable",
        401,
    )


def name(value):
    require(isinstance(value, str), "Enter your name", 422)
    require(
        not any(unicodedata.category(c).startswith("C") for c in value),
        "Enter a name without control characters",
        422,
    )
    value = " ".join(value.split())
    require(
        2 <= len(value) <= 120 and any(c.isalpha() for c in value),
        "Enter your full name, up to 120 characters",
        422,
    )
    return value


def real_email(value):
    return (
        isinstance(value, str)
        and "@" in value
        and not value.lower().endswith("@phone.sona.invalid")
    )


def receipt_customer(db, user):
    row = db.get(CustomerProfile, user.id) if enabled() else None
    phone = not real_email(user.email)
    label = (
        row.display_name
        if row and row.display_name
        else ("Not provided" if phone else user.username)
    )
    email = (
        row.contact_email
        if row and row.email_verified_at
        else (user.email if real_email(user.email) and user.is_verified else "Not provided")
    )
    return dict(account_name=label, email=email)


def read(db, user):
    row = db.get(CustomerProfile, user.id)
    values = receipt_customer(db, user)
    pending = (
        db.query(ContactChallenge)
        .filter_by(user_id=user.id)
        .order_by(ContactChallenge.created_at.desc())
        .first()
    )
    active = (
        pending is not None
        and pending.state != "verified"
        and pending.expires_at > utc_now()
        and pending.attempts < 5
    )
    return dict(
        version=row.version if row else 0,
        display_name=row.display_name if row else "",
        email=None if values["email"] == "Not provided" else values["email"],
        email_verified=values["email"] != "Not provided",
        login_method="phone" if not real_email(user.email) else "email",
        pending_challenge_id=pending.id if active else None,
    )


def save_name(db, user, value):
    require(
        set(value) == {"display_name", "version"} and type(value["version"]) is int,
        "Invalid profile update",
        422,
    )
    label = name(value["display_name"])
    lock(db, user.id)
    row = db.get(CustomerProfile, user.id)
    require(
        value["version"] == (row.version if row else 0), "Profile changed. Reload before saving"
    )
    if row is None:
        row = CustomerProfile(user_id=user.id, display_name=label, version=1)
        db.add(row)
    elif row.display_name != label:
        row.display_name = label
        row.version += 1
    db.flush()
    return read(db, user)


def code_digest(challenge, code):
    key = os.getenv("SECRET_KEY", "")
    require(len(key) >= 16, "Email verification is not configured", 503)
    return hmac.new(
        key.encode(),
        (
            "sona-contact-v1:"
            + challenge.id
            + ":"
            + str(challenge.user_id)
            + ":"
            + challenge.email
            + ":"
            + code
        ).encode(),
        hashlib.sha256,
    ).hexdigest()


def request_email(db, user, value):
    require(set(value) == {"email", "request_key"}, "Invalid email request", 422)
    try:
        email = validate_email(value["email"], check_deliverability=False).normalized.lower()
    except (EmailNotValidError, TypeError, AttributeError):
        raise HTTPException(422, "Enter a valid email address") from None
    require(real_email(email) and len(email) <= 254, "Enter a real email address", 422)
    key = value["request_key"]
    require(
        isinstance(key, str) and re.fullmatch(r"[a-f0-9]{32}", key),
        "Invalid request reference",
        422,
    )
    lock(db, user.id)
    previous = db.query(ContactChallenge).filter_by(user_id=user.id, request_key=key).first()
    if previous:
        require(previous.email == email, "Request reference belongs to another email")
        return previous, None
    if db.get_bind().dialect.name == "postgresql":
        # Serialize recipient quotas across different accounts, without a plaintext email lock key.
        target_lock = int.from_bytes(
            hashlib.sha256(("sona-contact-quota:" + email).encode()).digest()[:8],
            "big",
            signed=True,
        )
        db.execute(text("SELECT pg_advisory_xact_lock(:target)"), {"target": target_lock})
    now = utc_now()
    recent = db.query(ContactChallenge).filter(
        ContactChallenge.created_at > now - timedelta(hours=1)
    )
    require(
        recent.filter_by(user_id=user.id).count() < 5
        and recent.filter_by(email=email).count() < 10,
        "Email verification limit reached. Try later",
        429,
    )
    latest = (
        db.query(ContactChallenge)
        .filter_by(user_id=user.id)
        .order_by(ContactChallenge.created_at.desc())
        .first()
    )
    require(
        not latest or latest.created_at <= now - timedelta(seconds=60),
        "Wait before requesting another code",
        429,
    )
    row = ContactChallenge(
        id=uuid4().hex,
        user_id=user.id,
        email=email,
        request_key=key,
        state="delivery_unknown",
        attempts=0,
        created_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    code = f"{secrets.randbelow(1000000):06d}"
    row.code_hash = code_digest(row, code)
    db.add(row)
    db.flush()
    return row, code


def verify_email(db, user, value):
    require(set(value) == {"challenge_id", "code"}, "Invalid verification request", 422)
    require(
        isinstance(value["challenge_id"], str)
        and re.fullmatch(r"[a-f0-9]{32}", value["challenge_id"])
        and isinstance(value["code"], str)
        and re.fullmatch(r"[0-9]{6}", value["code"]),
        "Enter the six-digit code",
        422,
    )
    lock(db, user.id)
    row = (
        db.query(ContactChallenge)
        .filter_by(id=value["challenge_id"], user_id=user.id)
        .with_for_update()
        .first()
    )
    require(row is not None, "Verification unavailable", 404)
    if row.state == "verified":
        profile = db.get(CustomerProfile, user.id)
        require(
            profile is not None and profile.contact_email == row.email,
            "This code is no longer current",
        )
        return read(db, user), True
    latest = (
        db.query(ContactChallenge)
        .filter_by(user_id=user.id)
        .order_by(ContactChallenge.created_at.desc())
        .first()
    )
    require(
        latest.id == row.id and row.expires_at > utc_now() and row.attempts < 5,
        "Code expired or replaced. Request a new code",
    )
    row.attempts += 1
    if not hmac.compare_digest(row.code_hash, code_digest(row, value["code"])):
        db.flush()
        return {"message": "Code incorrect. Check the email and try again."}, False
    require(
        db.query(CustomerProfile)
        .filter(CustomerProfile.contact_email == row.email, CustomerProfile.user_id != user.id)
        .first()
        is None,
        "This email is already linked. Contact support",
    )
    profile = db.get(CustomerProfile, user.id)
    if profile is None:
        profile = CustomerProfile(user_id=user.id, version=1, display_name="")
        db.add(profile)
    else:
        profile.version += 1
    profile.contact_email = row.email
    profile.email_verified_at = utc_now()
    row.state = "verified"
    db.flush()
    return read(db, user), True
