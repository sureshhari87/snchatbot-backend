"""Opt-in, session-owned device registry. No push sender is enabled here."""

import hashlib
import os

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, SecretStr, field_validator
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.exc import IntegrityError

from database import Base
from models import RefreshToken, User, utc_now


def enabled():
    return os.environ.get("PUSH_DEVICE_REGISTRATION_ENABLED", "0") == "1"


def lock_owner(db, user_id):
    # Do not refresh this object: password reset can have pending User changes.
    return db.query(User).filter_by(id=user_id).with_for_update().first()


class PushDevice(Base):
    __tablename__ = "push_devices"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    family_id = Column(String(100), nullable=False, unique=True)
    token_digest = Column(String(64), nullable=False, unique=True)
    push_token = Column(String(4096), nullable=False)
    updated_at = Column(DateTime, nullable=False)


class RegistrationRejected(ValueError):
    """Never include authentication or device tokens in error messages."""


def register_device(db, user_id, refresh_token, push_token):
    """user_id must come from backend authentication. Caller owns transaction.

    Refuse token conflicts rather than transferring ownership implicitly.
    Lock the session row; eventual logout integration must use the same lock.
    """
    if (
        not isinstance(push_token, str)
        or not 20 <= len(push_token) <= 4096
        or any(c.isspace() for c in push_token)
    ):
        raise RegistrationRejected("Invalid registration")
    if lock_owner(db, user_id) is None:
        raise RegistrationRejected("Active session required")
    session = (
        db.query(RefreshToken)
        .filter_by(user_id=user_id, token=refresh_token)
        .populate_existing()
        .with_for_update()
        .first()
    )
    if (
        session is None
        or session.is_revoked
        or session.expires_at <= utc_now()
        or not session.family_id
    ):
        raise RegistrationRejected("Active session required")
    try:
        with db.begin_nested():
            row = (
                db.query(PushDevice)
                .filter_by(family_id=session.family_id)
                .populate_existing()
                .with_for_update()
                .first()
            )
            if row is not None and row.user_id != user_id:
                raise RegistrationRejected("Registration conflict")
            if row is None:
                row = PushDevice(user_id=user_id, family_id=session.family_id)
                db.add(row)
            row.token_digest = hashlib.sha256(push_token.encode()).hexdigest()
            row.push_token = push_token
            row.updated_at = utc_now()
            db.flush()
    except IntegrityError:
        raise RegistrationRejected("Registration conflict") from None
    return row.id


def detach_family(db, user_id, family_id):
    lock_owner(db, user_id)
    return (
        db.query(PushDevice)
        .filter_by(user_id=user_id, family_id=family_id)
        .delete(synchronize_session=False)
    )


def detach_all(db, user_id):
    lock_owner(db, user_id)
    return db.query(PushDevice).filter_by(user_id=user_id).delete(synchronize_session=False)


def eligible_devices(db, user_id):
    """Candidate lookup only; sending also needs preferences and race safeguards."""
    active = (
        db.query(RefreshToken.id)
        .filter(
            RefreshToken.user_id == PushDevice.user_id,
            RefreshToken.family_id == PushDevice.family_id,
            RefreshToken.is_revoked.is_(False),
            RefreshToken.expires_at > utc_now(),
        )
        .exists()
    )
    return db.query(PushDevice).filter(PushDevice.user_id == user_id, active).all()


class SessionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh_token: SecretStr

    @field_validator("refresh_token")
    @classmethod
    def validate_refresh(cls, value):
        if not 1 <= len(value.get_secret_value()) <= 8192:
            raise ValueError("Invalid session credential")
        return value


class DeviceInput(SessionInput):
    push_token: SecretStr

    @field_validator("push_token")
    @classmethod
    def validate_push(cls, value):
        token = value.get_secret_value()
        if not 20 <= len(token) <= 4096 or any(c.isspace() for c in token):
            raise ValueError("Invalid device token")
        return value


def install(app, get_db, get_current_user, limiter):
    def permitted(user=Depends(get_current_user)):
        if not enabled():
            raise HTTPException(503, "Device registration is disabled")
        if not user.is_verified:
            raise HTTPException(403, "Verified account required")
        return user

    @app.put("/users/me/push-device")
    @limiter.limit("20/minute")
    def register(
        request: Request, payload: DeviceInput, user=Depends(permitted), db=Depends(get_db)
    ):
        try:
            register_device(
                db,
                user.id,
                payload.refresh_token.get_secret_value(),
                payload.push_token.get_secret_value(),
            )
            db.commit()
        except RegistrationRejected:
            db.rollback()
            raise HTTPException(409, "Device registration rejected") from None
        return {"registered": True}

    @app.post("/users/me/push-device/detach")
    @limiter.limit("20/minute")
    def detach(
        request: Request, payload: SessionInput, user=Depends(permitted), db=Depends(get_db)
    ):
        lock_owner(db, user.id)
        session = (
            db.query(RefreshToken)
            .filter_by(user_id=user.id, token=payload.refresh_token.get_secret_value())
            .first()
        )
        if session is not None and session.family_id:
            detach_family(db, user.id, session.family_id)
        db.commit()
        return {"detached": True}
