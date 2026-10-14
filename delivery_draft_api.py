"""Previewed inactive drafts. Never writes live delivery settings."""

import hashlib
import json
import os
import time
from datetime import date
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Path, Query
from jwt import PyJWTError
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import config
from delivery_draft_models import DeliveryDraft, DeliveryDraftAudit
from delivery_rules import calendar
from models import User, utc_now

ENABLED = os.getenv("DELIVERY_DRAFTS_ENABLED", "0").strip() == "1"
AUDIENCE = "sona-inactive-delivery-draft-preview"
KEY_PATTERN = r"^[A-Za-z0-9._:-]{8,100}$"
Positive = Annotated[int, Field(strict=True, gt=0)]
DayCount = Annotated[int, Field(strict=True, ge=0, le=365)]


class DraftRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    active: StrictBool
    pin_prefix: str = Field(pattern=r"^[1-9][0-9]{0,5}$")
    method: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    handling_days: DayCount
    minimum_transit_days: DayCount
    maximum_transit_days: DayCount
    working_weekdays: list[Annotated[int, Field(strict=True, ge=0, le=6)]] = Field(
        min_length=1, max_length=7
    )
    holidays: list[str] = Field(max_length=3660)
    cutoff: str = Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    fulfilment_group: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_inactive(self):
        if self.active is not False:
            raise ValueError("Activation is not approved")
        if self.minimum_transit_days > self.maximum_transit_days:
            raise ValueError("Invalid transit range")
        if len(set(self.working_weekdays)) != len(self.working_weekdays):
            raise ValueError("Duplicate working weekdays")
        for value in self.holidays:
            if date.fromisoformat(value).isoformat() != value:
                raise ValueError("Use ISO holiday dates")
        if len(set(self.holidays)) != len(self.holidays) or not self.fulfilment_group.isprintable():
            raise ValueError("Invalid calendar or fulfilment group")
        calendar(self.model_dump())
        return self


class DraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_key: str = Field(pattern=KEY_PATTERN)
    draft_id: Positive | None = None
    expected_version: Annotated[int, Field(strict=True, ge=0)]
    name: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=3, max_length=500)
    route: DraftRoute

    @model_validator(mode="after")
    def validate_version(self):
        if (self.draft_id is None) != (self.expected_version == 0):
            raise ValueError(
                "New drafts need version zero; saved drafts need their current version"
            )
        return self


class SaveInput(DraftInput):
    preview_token: str = Field(min_length=20, max_length=4096)


def enabled():
    if not ENABLED:
        raise HTTPException(503, "Inactive delivery draft editing is not enabled")
    if config.APP_ENV != "staging":
        raise HTTPException(403, "Only staging inactive delivery drafts are approved")


def canonical(payload):
    value = payload.model_dump(exclude={"preview_token"})
    value["route"]["working_weekdays"].sort()
    value["route"]["holidays"].sort()
    return value


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def snapshot(row):
    return {
        "id": row.id,
        "name": row.name,
        "version": row.version,
        "route": row.route,
        "activation_allowed": False,
        "affects_checkout": False,
    }


def receipt(row):
    return {
        "request_key": row.request_key,
        "draft_id": row.draft_id,
        "version": row.version,
        "payload_sha256": row.payload_sha256,
        "saved": row.after,
        "activation_allowed": False,
        "affects_checkout": False,
    }


def replay(row, admin, sha):
    if row.actor_id != admin.id or row.payload_sha256 != sha:
        raise HTTPException(409, "Draft key belongs to a different saved intent")
    return receipt(row)


def existing(db, value):
    if value["draft_id"] is None:
        return None
    row = db.get(DeliveryDraft, value["draft_id"])
    if row is None:
        raise HTTPException(404, "Delivery draft not found")
    if row.version != value["expected_version"]:
        raise HTTPException(409, "Draft changed. Reload before editing")
    return row


def save(db, admin, payload):
    value = canonical(payload)
    sha = digest(value)
    old = db.query(DeliveryDraftAudit).filter_by(request_key=value["request_key"]).first()
    if old:
        return replay(old, admin, sha)
    try:
        claims = jwt.decode(
            payload.preview_token,
            config.SECRET_KEY,
            algorithms=[config.ALGORITHM],
            audience=AUDIENCE,
            options={"require": ["exp", "aud", "actor_id", "payload_sha256", "environment"]},
        )
        if (
            claims["actor_id"] != admin.id
            or claims["payload_sha256"] != sha
            or claims["environment"] != config.APP_ENV
        ):
            raise ValueError()
    except (PyJWTError, ValueError, TypeError):
        raise HTTPException(409, "Preview expired or changed. Preview this draft again") from None
    try:
        row = existing(db, value)
    except HTTPException as error:
        if error.status_code != 409:
            raise
        db.rollback()
        old = db.query(DeliveryDraftAudit).filter_by(request_key=value["request_key"]).first()
        if old:
            return replay(old, admin, sha)
        raise
    before = snapshot(row) if row else None
    try:
        if row is None:
            row = DeliveryDraft(name=value["name"], route=value["route"], version=1)
            db.add(row)
            db.flush()
            after = snapshot(row)
        else:
            version = value["expected_version"] + 1
            count = (
                db.query(DeliveryDraft)
                .filter_by(id=row.id, version=value["expected_version"])
                .update(
                    {
                        "name": value["name"],
                        "route": value["route"],
                        "version": version,
                        "updated_at": utc_now(),
                    },
                    synchronize_session=False,
                )
            )
            if count != 1:
                db.rollback()
                old = (
                    db.query(DeliveryDraftAudit).filter_by(request_key=value["request_key"]).first()
                )
                if old:
                    return replay(old, admin, sha)
                raise HTTPException(409, "Draft changed. Reload before editing")
            after = {**before, "name": value["name"], "route": value["route"], "version": version}
        audit = DeliveryDraftAudit(
            draft_id=after["id"],
            version=after["version"],
            actor_id=admin.id,
            request_key=value["request_key"],
            payload_sha256=sha,
            reason=value["reason"],
            before=before,
            after=after,
        )
        db.add(audit)
        db.commit()
        return receipt(audit)
    except IntegrityError:
        db.rollback()
        old = db.query(DeliveryDraftAudit).filter_by(request_key=value["request_key"]).first()
        if old:
            return replay(old, admin, sha)
        raise HTTPException(
            409, "Draft save conflicted. Read saved state before retrying"
        ) from None


def install(app, get_db, require_permission):
    protected = [Depends(enabled)]
    access = require_permission("config:manage")

    @app.get("/admin/delivery-drafts", dependencies=protected)
    def listing(
        after: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=100),
        admin: User = Depends(access),
        db: Session = Depends(get_db),
    ):
        rows = (
            db.query(DeliveryDraft)
            .filter(DeliveryDraft.id > after)
            .order_by(DeliveryDraft.id)
            .limit(limit + 1)
            .all()
        )
        return {
            "items": [snapshot(row) for row in rows[:limit]],
            "next_after": rows[limit - 1].id if len(rows) > limit else None,
            "activation_allowed": False,
            "affects_checkout": False,
        }

    @app.post("/admin/delivery-drafts/preview", dependencies=protected)
    def preview(payload: DraftInput, admin: User = Depends(access), db: Session = Depends(get_db)):
        value = canonical(payload)
        row = existing(db, value)
        sha = digest(value)
        expires = int(time.time()) + 600
        token = jwt.encode(
            {
                "aud": AUDIENCE,
                "exp": expires,
                "actor_id": admin.id,
                "payload_sha256": sha,
                "environment": config.APP_ENV,
            },
            config.SECRET_KEY,
            algorithm=config.ALGORITHM,
        )
        return {
            "draft": value,
            "before": snapshot(row) if row else None,
            "payload_sha256": sha,
            "preview_token": token,
            "expires_at": expires,
            "activation_allowed": False,
            "affects_checkout": False,
        }

    @app.post("/admin/delivery-drafts/save", dependencies=protected)
    def confirm(payload: SaveInput, admin: User = Depends(access), db: Session = Depends(get_db)):
        return save(db, admin, payload)

    @app.get("/admin/delivery-drafts/key/{request_key}", dependencies=protected)
    def read(
        request_key: str = Path(pattern=KEY_PATTERN),
        admin: User = Depends(access),
        db: Session = Depends(get_db),
    ):
        row = (
            db.query(DeliveryDraftAudit)
            .filter_by(request_key=request_key, actor_id=admin.id)
            .first()
        )
        if row is None:
            raise HTTPException(404, "Saved draft intent not found")
        return receipt(row)
