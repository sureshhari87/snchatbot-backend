"""Recipient-owned inbox; no SMS/FCM delivery or global broadcasts."""

from datetime import timezone
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import CustomerNotification, User, utc_now


class NotificationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    user_id: int = Field(gt=0, strict=True)
    deduplication_key: str = Field(min_length=8, max_length=100)
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=2000)
    target: Literal["", "/cart", "/voucher", "/schemes", "Gold", "Diamond", "Silver", "Men", "Women", "Kids"] = ""


class SeenInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[Annotated[int, Field(gt=0, strict=True)]] = Field(max_length=100)


def document(row):
    return {
        "id": row.id, "title": row.title, "body": row.body, "target": row.target,
        "createdAt": row.created_at.replace(tzinfo=timezone.utc).isoformat(),
        "read": row.read_at is not None,
    }


def install(app, get_db, get_current_user, require_permission, log_admin_action):
    @app.get("/notifications/my")
    def inbox(offset: int = Query(0, ge=0, le=10000), limit: int = Query(50, ge=1, le=100),
              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
        rows = db.query(CustomerNotification).filter_by(user_id=user.id)\
            .order_by(CustomerNotification.id.desc()).offset(offset).limit(limit).all()
        return [document(row) for row in rows]

    @app.post("/notifications/seen")
    def seen(payload: SeenInput, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
        # Only mark rows actually fetched by the caller; never consume future messages.
        db.query(CustomerNotification).filter(
            CustomerNotification.user_id == user.id,
            CustomerNotification.id.in_(payload.ids), CustomerNotification.read_at.is_(None),
        ).update({"read_at": utc_now()}, synchronize_session=False)
        db.commit()
        return {"ok": True}

    @app.post("/admin/notifications", status_code=201)
    def create(payload: NotificationCreate, request: Request,
               admin: User = Depends(require_permission("support:manage")),
               db: Session = Depends(get_db)):
        if db.get(User, payload.user_id) is None:
            raise HTTPException(404, "Recipient not found")
        values = payload.model_dump()
        row = CustomerNotification(created_by=admin.id, **values)
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            row = db.query(CustomerNotification).filter_by(
                user_id=payload.user_id, deduplication_key=payload.deduplication_key
            ).first()
            if row is None or any(getattr(row, name) != value for name, value in values.items()):
                raise HTTPException(409, "Notification key already used or recipient changed") from None
            return document(row)
        db.refresh(row)
        log_admin_action(request, admin, "create", "notification", row.id)
        return document(row)
