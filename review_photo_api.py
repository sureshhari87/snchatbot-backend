"""Bounded authenticated upload, private previews and revocable approved media."""

import base64
from typing import Literal

from fastapi import Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

import review_photos as photos
from models import Product, ProductReview, User
from review_photo_models import ReviewPhoto


class PhotoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    upload_key: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    content_type: Literal["image/jpeg", "image/png", "image/webp"]
    data_base64: str = Field(min_length=1, max_length=699052)


HEADERS = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}


def install(app, get_db, get_current_user, require_permission, limiter):
    admin = require_permission("support:manage")

    @app.post("/products/{product_id}/review-photos", status_code=201)
    @limiter.limit("10/minute")
    def upload(
        request: Request,
        response: Response,
        product_id: int,
        payload: PhotoInput,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        if db.get(Product, product_id) is None:
            raise HTTPException(404, "Product not found")
        row = photos.upload(
            db, user.id, product_id, payload.upload_key, payload.data_base64, payload.content_type
        )
        db.commit()
        response.headers.update(HEADERS)
        return {"id": row.id, "content_type": "image/jpeg"}

    @app.get("/products/{product_id}/review-photos/drafts")
    def drafts(
        product_id: int,
        response: Response,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        rows = (
            db.query(ReviewPhoto.id)
            .filter_by(owner_id=user.id, product_id=product_id, review_id=None, removed_at=None)
            .order_by(ReviewPhoto.created_at.desc(), ReviewPhoto.id)
            .limit(50)
            .all()
        )
        response.headers.update(HEADERS)
        return {"items": [row[0] for row in rows]}

    @app.get("/products/{product_id}/review-photos/by-key/{upload_key}")
    def lookup(
        product_id: int,
        upload_key: str,
        response: Response,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        row = (
            db.query(ReviewPhoto)
            .filter_by(owner_id=user.id, product_id=product_id, upload_key=upload_key)
            .first()
        )
        if row is None:
            raise HTTPException(404, "Photo not found")
        response.headers.update(HEADERS)
        return {"id": row.id, "removed": row.removed_at is not None}

    def private(db, photo_id, owner_id=None):
        row = db.get(ReviewPhoto, photo_id)
        if (
            row is None
            or row.removed_at is not None
            or (owner_id is not None and row.owner_id != owner_id)
        ):
            raise HTTPException(404, "Photo not found")
        if owner_id is None:
            review = db.get(ProductReview, row.review_id) if row.review_id is not None else None
            if (
                review is None
                or review.user_id != row.owner_id
                or review.product_id != row.product_id
            ):
                raise HTTPException(404, "Photo not found")
        return {
            "id": row.id,
            "content_type": "image/jpeg",
            "data_base64": base64.b64encode(row.content).decode("ascii"),
        }

    @app.get("/review-photos/{photo_id}/private")
    def own(
        photo_id: str,
        response: Response,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        response.headers.update(HEADERS)
        return private(db, photo_id, user.id)

    @app.get("/admin/review-photos/{photo_id}")
    def moderation(
        photo_id: str,
        response: Response,
        user: User = Depends(admin),
        db: Session = Depends(get_db),
    ):
        response.headers.update(HEADERS)
        return private(db, photo_id)

    @app.get("/review-photos/{photo_id}", name="public_review_photo")
    def public(photo_id: str, db: Session = Depends(get_db)):
        return Response(photos.public_bytes(db, photo_id), media_type="image/jpeg", headers=HEADERS)

    @app.delete("/review-photos/{photo_id}")
    def remove(
        photo_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
    ):
        photos.lock_owner(db, user.id)
        row = db.get(ReviewPhoto, photo_id)
        if row is None or row.owner_id != user.id:
            raise HTTPException(404, "Photo not found")
        if row.review_id is not None:
            raise HTTPException(409, "Remove attached photos by editing the review")
        photos.remove(row)
        db.commit()
        return {"removed": True}
