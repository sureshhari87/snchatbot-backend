"""Owned photo references and normalized JPEGs, never arbitrary external URLs."""

import base64
import hashlib
import io
import uuid
import warnings
from datetime import timedelta
from threading import BoundedSemaphore

from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import func

from config import get_bool
from models import ProductReview, User, utc_now
from review_photo_models import ReviewPhoto

ENABLED = get_bool("REVIEW_PHOTOS_ENABLED", False)
MAX_INPUT = 512 * 1024
MAX_STORED = 384 * 1024
MAX_PIXELS = 16_000_000
MAX_USER_BYTES = 20 * 1024 * 1024
MAX_DAILY_UPLOADS = 20
_DECODER = BoundedSemaphore(1)


def lock_owner(db, owner_id):
    db.query(User).filter_by(id=owner_id).with_for_update().one()


def _normalize(raw, mime):
    if not raw or len(raw) > MAX_INPUT:
        raise HTTPException(413, "Choose a photo no larger than 512 KB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw), formats=["JPEG", "PNG", "WEBP"]) as image:
                expected = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}[
                    image.format
                ]
                if expected != mime or getattr(image, "n_frames", 1) != 1:
                    raise ValueError("Unsupported image")
                if image.width * image.height > MAX_PIXELS:
                    raise ValueError("Too many pixels")
                image.load()
                oriented = ImageOps.exif_transpose(image)
                oriented.thumbnail((1280, 1280))
                rgba = oriented.convert("RGBA")
                clean = Image.new("RGB", rgba.size, "white")
                clean.paste(rgba, mask=rgba.getchannel("A"))
                # A fresh image removes EXIF, GPS, comments, profiles and trailing data.
                output = io.BytesIO()
                clean.save(output, format="JPEG", quality=80, optimize=True)
                result = output.getvalue()
                if len(result) > MAX_STORED:
                    raise HTTPException(413, "Choose a smaller photo")
                return result
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        raise HTTPException(422, "Choose a valid, non-animated JPEG, PNG or WebP photo") from None


def normalize(raw, mime):
    # Bound peak decoder memory per worker; never wait while holding a DB lock.
    if not _DECODER.acquire(blocking=False):
        raise HTTPException(503, "Photo processing is busy; retry this upload")
    try:
        return _normalize(raw, mime)
    finally:
        _DECODER.release()


def upload(db, owner_id, product_id, key, encoded, mime):
    if not ENABLED:
        raise HTTPException(503, "Review photos are not enabled")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        raise HTTPException(422, "Invalid photo data") from None
    digest = hashlib.sha256(raw).hexdigest()
    lock_owner(db, owner_id)
    old = db.query(ReviewPhoto).filter_by(owner_id=owner_id, upload_key=key).first()
    if old is not None:
        if old.removed_at is not None or old.product_id != product_id or old.input_sha256 != digest:
            raise HTTPException(409, "Photo upload key changed or was removed")
        return old
    daily = (
        db.query(ReviewPhoto.id)
        .filter(
            ReviewPhoto.owner_id == owner_id,
            ReviewPhoto.created_at >= utc_now() - timedelta(days=1),
        )
        .count()
    )
    if daily >= MAX_DAILY_UPLOADS:
        raise HTTPException(429, "Daily review photo limit reached")
    content = normalize(raw, mime)
    total = (
        db.query(func.coalesce(func.sum(ReviewPhoto.stored_bytes), 0))
        .filter_by(owner_id=owner_id)
        .scalar()
    )
    if total + len(content) > MAX_USER_BYTES:
        raise HTTPException(413, "Review photo storage limit reached")
    row = ReviewPhoto(
        id=uuid.uuid4().hex,
        owner_id=owner_id,
        product_id=product_id,
        upload_key=key,
        input_sha256=digest,
        content=content,
        stored_bytes=len(content),
        created_at=utc_now(),
    )
    db.add(row)
    db.flush()
    return row


def remove(row):
    row.content = None
    row.stored_bytes = 0
    row.review_id = None
    row.removed_at = utc_now()


def bind(db, review, ids):
    if len(ids) > 5 or len(set(ids)) != len(ids):
        raise HTTPException(422, "Choose at most five distinct review photos")
    existing = db.query(ReviewPhoto).filter_by(review_id=review.id, removed_at=None).all()
    current = {row.id for row in existing}
    if not ENABLED and set(ids) - current:
        raise HTTPException(503, "Review photos are not enabled")
    selected = db.query(ReviewPhoto).filter(ReviewPhoto.id.in_(ids)).all() if ids else []
    if len(selected) != len(ids) or any(
        row.owner_id != review.user_id
        or row.product_id != review.product_id
        or row.removed_at is not None
        or row.review_id not in (None, review.id)
        for row in selected
    ):
        raise HTTPException(422, "Review photo is unavailable")
    for row in existing:
        if row.id not in ids:
            remove(row)
    for row in selected:
        row.review_id = review.id
    db.flush()


def ids_for(db, review_id):
    return [
        row[0]
        for row in db.query(ReviewPhoto.id)
        .filter_by(review_id=review_id, removed_at=None)
        .order_by(ReviewPhoto.created_at, ReviewPhoto.id)
        .all()
    ]


def exists_for(db, review_model):
    return (
        db.query(ReviewPhoto.id)
        .filter(
            ReviewPhoto.review_id == review_model.id,
            ReviewPhoto.owner_id == review_model.user_id,
            ReviewPhoto.product_id == review_model.product_id,
            ReviewPhoto.removed_at.is_(None),
        )
        .correlate(review_model)
        .exists()
    )


def review_count(db, product_id):
    if not ENABLED:
        return 0
    return (
        db.query(ProductReview.id)
        .filter_by(product_id=product_id, status="approved")
        .filter(exists_for(db, ProductReview))
        .count()
    )


def documents(db, rows, request):
    ids = [row.id for row in rows]
    mapping = {key: [] for key in ids}
    if ids:
        for photo_id, review_id in (
            db.query(ReviewPhoto.id, ReviewPhoto.review_id)
            .filter(ReviewPhoto.review_id.in_(ids), ReviewPhoto.removed_at.is_(None))
            .order_by(ReviewPhoto.created_at, ReviewPhoto.id)
        ):
            mapping[review_id].append(photo_id)
    return {
        row.id: {
            "photoIds": mapping[row.id],
            "photoUrls": [
                str(request.url_for("public_review_photo", photo_id=key)) for key in mapping[row.id]
            ]
            if ENABLED and row.status == "approved"
            else [],
        }
        for row in rows
    }


def public_bytes(db, photo_id):
    if not ENABLED:
        raise HTTPException(404, "Photo not found")
    photo = (
        db.query(ReviewPhoto)
        .join(ProductReview, ProductReview.id == ReviewPhoto.review_id)
        .filter(
            ReviewPhoto.id == photo_id,
            ReviewPhoto.owner_id == ProductReview.user_id,
            ReviewPhoto.product_id == ProductReview.product_id,
            ReviewPhoto.removed_at.is_(None),
            ProductReview.status == "approved",
        )
        .first()
    )
    if photo is None:
        raise HTTPException(404, "Photo not found")
    return photo.content
