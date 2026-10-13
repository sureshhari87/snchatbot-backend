"""Staging-only, explicitly previewed inbox records; never creates push jobs."""

import hashlib
import json
import os
import time
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Path, Request
from jose import JWTError, jwt
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import config
from inbox_broadcast_models import InboxBroadcast
from models import CustomerNotification, User, utc_now

ENABLED = os.getenv("INBOX_BROADCAST_ENABLED", "0").strip() == "1"
AUDIENCE = "sona-inbox-broadcast-preview"


class BroadcastInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_key: str = Field(pattern=r"^[A-Za-z0-9._:-]{8,100}$")
    recipient_ids: list[Annotated[int, Field(gt=0, strict=True)]] = Field(
        min_length=1, max_length=100
    )
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=2000)
    target: Literal[
        "", "/cart", "/voucher", "/schemes", "Gold", "Diamond", "Silver", "Men", "Women", "Kids"
    ] = ""


class ConfirmInput(BroadcastInput):
    preview_token: str = Field(min_length=20, max_length=4096)


def enabled():
    if not ENABLED:
        raise HTTPException(503, "Inbox broadcasts are not enabled")
    if config.APP_ENV != "staging":
        raise HTTPException(403, "Production inbox broadcasts are not approved")


def canonical(payload):
    value = payload.model_dump(exclude={"preview_token"})
    value["recipient_ids"] = sorted(set(value["recipient_ids"]))
    return value


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def recipients(db, value, lock=False):
    ids = value["recipient_ids"]
    query = (
        db.query(User.id)
        .filter(User.id.in_(ids), User.is_admin.is_(False), User.is_verified.is_(True))
        .order_by(User.id)
    )
    if lock:
        query = query.with_for_update()
    found = {user_id for (user_id,) in query.all()}
    if found != set(ids):
        raise HTTPException(409, "One or more recipients are unavailable; verify the explicit list")


def document(row):
    return {
        "id": row.id,
        "request_key": row.request_key,
        "state": "saved",
        "recipient_ids": row.recipient_ids,
        "notification_count": row.recipient_count,
        "payload_sha256": row.payload_sha256,
        "channel": "inbox",
        "push_created": False,
        "sms_created": False,
        "production_sending": False,
    }


def replay(row, admin, sha):
    if row.actor_id != admin.id or row.payload_sha256 != sha:
        raise HTTPException(409, "Broadcast key already belongs to a different intent")
    return document(row)


def confirm(db, admin, payload):
    value = canonical(payload)
    sha = digest(value)
    old = db.query(InboxBroadcast).filter_by(request_key=value["request_key"]).first()
    if old:
        return replay(old, admin, sha), False
    try:
        claims = jwt.decode(
            payload.preview_token,
            config.SECRET_KEY,
            algorithms=[config.ALGORITHM],
            audience=AUDIENCE,
        )
        if (
            claims.get("actor_id") != admin.id
            or claims.get("payload_sha256") != sha
            or claims.get("environment") != config.APP_ENV
        ):
            raise ValueError()
    except (JWTError, ValueError, TypeError):
        raise HTTPException(
            409, "Preview expired or differs from this intent; preview the same draft again"
        ) from None
    recipients(db, value, lock=True)
    notifications = []
    for user_id in value["recipient_ids"]:
        item = CustomerNotification(
            user_id=user_id,
            created_by=admin.id,
            deduplication_key="broadcast:"
            + digest([value["request_key"], admin.id])
            + ":"
            + str(user_id),
            title=value["title"],
            body=value["body"],
            target=value["target"],
            created_at=utc_now(),
        )
        db.add(item)
        notifications.append(item)
    try:
        db.flush()
        row = InboxBroadcast(
            request_key=value["request_key"],
            actor_id=admin.id,
            payload_sha256=sha,
            channel="inbox",
            recipient_count=len(notifications),
            recipient_ids=value["recipient_ids"],
            notification_ids=[item.id for item in notifications],
            title=value["title"],
            body=value["body"],
            target=value["target"],
            created_at=utc_now(),
        )
        db.add(row)
        db.commit()
    except IntegrityError:
        db.rollback()
        old = db.query(InboxBroadcast).filter_by(request_key=value["request_key"]).first()
        if old:
            return replay(old, admin, sha), False
        raise HTTPException(
            409, "Broadcast could not be saved atomically; read before retrying"
        ) from None
    return document(row), True


def install(app, get_db, require_permission, log_admin_action):
    protected = [Depends(enabled)]
    admin_access = require_permission("support:manage")

    @app.post("/admin/broadcasts/preview", dependencies=protected)
    def preview(
        payload: BroadcastInput, admin: User = Depends(admin_access), db: Session = Depends(get_db)
    ):
        value = canonical(payload)
        recipients(db, value)
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
            "recipient_ids": value["recipient_ids"],
            "recipient_count": len(value["recipient_ids"]),
            "payload_sha256": sha,
            "preview_token": token,
            "expires_at": expires,
            "channel": "inbox",
            "push_created": False,
            "sms_created": False,
            "production_sending": False,
        }

    @app.post("/admin/broadcasts/confirm", dependencies=protected)
    def send(
        payload: ConfirmInput,
        request: Request,
        admin: User = Depends(admin_access),
        db: Session = Depends(get_db),
    ):
        result, created = confirm(db, admin, payload)
        # The immutable broadcast row itself is the transactional audit.
        # Additional operational logs are deliberately payload/token-free.
        if created:
            log_admin_action(request, admin, "broadcast_saved", "inbox_broadcast", result["id"])
        return result

    @app.get("/admin/broadcasts/key/{request_key}", dependencies=protected)
    def read(
        request_key: str = Path(pattern=r"^[A-Za-z0-9._:-]{8,100}$"),
        admin: User = Depends(admin_access),
        db: Session = Depends(get_db),
    ):
        row = db.query(InboxBroadcast).filter_by(request_key=request_key, actor_id=admin.id).first()
        if row is None:
            raise HTTPException(404, "Saved broadcast not found")
        return document(row)
