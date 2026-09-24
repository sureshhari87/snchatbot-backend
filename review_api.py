"""First-stage text review API. Legacy photo/vote/purchase migration stays gated."""

from datetime import datetime
from typing import Literal

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import Product, ProductReview, ReviewHelpfulVote, User, utc_now


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    rating: int = Field(ge=1, le=5, strict=True)
    title: str = Field(default="", max_length=120)
    body: str = Field(min_length=10, max_length=2000)
    fit: Literal["notApplicable", "runsSmall", "trueToSize", "runsLarge"] = "notApplicable"
    size_feedback: Literal["notApplicable", "comfortable", "tight", "loose", "adjustable"] = "notApplicable"


class ModerationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    status: Literal["approved", "rejected", "hidden"]
    expected_updated_at: datetime
    reason: str | None = Field(default=None, max_length=1000)
    seller_response: str | None = Field(default=None, max_length=1000)


class HelpfulInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    helpful: bool = Field(strict=True)


def document(row, helpful_count=0):
    # Never publish user IDs, email addresses, or admin identifiers.
    return {
        "id": str(row.id),
        "productDocumentId": str(row.product_id),
        "customerDisplayName": row.display_name,
        "rating": row.rating,
        "title": row.title,
        "body": row.body,
        "moderationStatus": row.status,
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
        "verifiedPurchase": False,
        "photoUrls": [],
        "helpfulCount": helpful_count,
        "fit": row.fit,
        "sizeFeedback": row.size_feedback,
        "sellerResponse": row.seller_response,
    }


def install(app, get_db, get_current_user, require_permission):
    admin_permission = require_permission("support:manage")

    def helpful_count(db, review_id):
        return db.query(ReviewHelpfulVote).filter_by(review_id=review_id).count()

    def product_exists(db, product_id):
        if db.get(Product, product_id) is None:
            raise HTTPException(404, "Product not found")

    @app.get("/products/{product_id}/reviews/summary")
    def summary(product_id: int, db: Session = Depends(get_db)):
        product_exists(db, product_id)
        counts = dict(
            db.query(ProductReview.rating, func.count(ProductReview.id))
            .filter_by(product_id=product_id, status="approved")
            .group_by(ProductReview.rating)
            .all()
        )
        total = sum(counts.values())
        return {
            "approvedCount": total,
            "photoReviewCount": 0,
            "averageRating": sum(k * v for k, v in counts.items()) / total if total else 0,
            "starCounts": {str(k): counts.get(k, 0) for k in range(1, 6)},
        }

    @app.get("/products/{product_id}/reviews")
    def public_reviews(
        product_id: int,
        offset: int = Query(0, ge=0, le=10000),
        limit: int = Query(10, ge=1, le=50),
        sort: Literal["newest", "highest", "lowest", "mostHelpful"] = "newest",
        stars: int | None = Query(None, ge=1, le=5),
        db: Session = Depends(get_db),
    ):
        product_exists(db, product_id)
        query = db.query(ProductReview).filter_by(product_id=product_id, status="approved")
        if stars is not None:
            query = query.filter_by(rating=stars)
        if sort == "mostHelpful":
            votes = (
                db.query(func.count(ReviewHelpfulVote.user_id))
                .filter(ReviewHelpfulVote.review_id == ProductReview.id)
                .correlate(ProductReview)
                .scalar_subquery()
            )
            query = query.order_by(votes.desc())
        elif sort != "newest":
            query = query.order_by(
                ProductReview.rating.desc() if sort == "highest" else ProductReview.rating.asc()
            )
        rows = (
            query.order_by(ProductReview.created_at.desc(), ProductReview.id.desc())
            .offset(offset)
            .limit(limit + 1)
            .all()
        )
        page = rows[:limit]
        vote_counts = (
            dict(
                db.query(ReviewHelpfulVote.review_id, func.count(ReviewHelpfulVote.user_id))
                .filter(ReviewHelpfulVote.review_id.in_([row.id for row in page]))
                .group_by(ReviewHelpfulVote.review_id)
                .all()
            )
            if page
            else {}
        )
        return {
            "items": [document(row, vote_counts.get(row.id, 0)) for row in page],
            "next_offset": offset + limit if len(rows) > limit else None,
        }

    @app.get("/products/{product_id}/reviews/me")
    def own_review(
        product_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
    ):
        row = db.query(ProductReview).filter_by(product_id=product_id, user_id=user.id).first()
        if row is None:
            raise HTTPException(404, "Review not found")
        return document(row)

    @app.post("/products/{product_id}/reviews", status_code=201)
    def create_review(
        product_id: int,
        payload: ReviewInput,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        product_exists(db, product_id)
        row = ProductReview(product_id=product_id, user_id=user.id, **payload.model_dump())
        # An explicit public display-name workflow is not migrated yet.
        row.display_name = "Sona customer"
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "Review already exists; edit the existing review") from None
        db.refresh(row)
        return document(row)

    def owned_review(db, review_id, user):
        row = (
            db.query(ProductReview)
            .filter_by(id=review_id, user_id=user.id)
            .with_for_update()
            .first()
        )
        if row is None:
            raise HTTPException(404, "Review not found")
        return row

    @app.patch("/reviews/{review_id}")
    def edit_review(
        review_id: int,
        payload: ReviewInput,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        row = owned_review(db, review_id, user)
        for name, value in payload.model_dump().items():
            setattr(row, name, value)
        row.status = "pending"
        row.updated_at = utc_now()
        row.moderated_by = None
        row.moderated_at = None
        row.moderation_reason = None
        row.seller_response = None
        db.commit()
        db.refresh(row)
        return document(row)

    @app.delete("/reviews/{review_id}")
    def delete_review(
        review_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
    ):
        row = owned_review(db, review_id, user)
        db.query(ReviewHelpfulVote).filter_by(review_id=row.id).delete()
        db.delete(row)
        db.commit()
        return {"deleted": True}

    @app.get("/reviews/{review_id}/helpful")
    def own_vote(
        review_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
    ):
        row = db.query(ProductReview).filter_by(id=review_id, status="approved").first()
        if row is None:
            raise HTTPException(404, "Review not found")
        return {
            "helpful": db.get(ReviewHelpfulVote, (review_id, user.id)) is not None,
            "helpfulCount": helpful_count(db, review_id),
        }

    @app.put("/reviews/{review_id}/helpful")
    def set_helpful(
        review_id: int,
        payload: HelpfulInput,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        row = db.query(ProductReview).filter_by(id=review_id).with_for_update().first()
        if row is None or row.status != "approved":
            raise HTTPException(404, "Review not found")
        if row.user_id == user.id:
            raise HTTPException(409, "Cannot vote on your own review")
        vote = db.get(ReviewHelpfulVote, (review_id, user.id))
        if payload.helpful and vote is None:
            db.add(ReviewHelpfulVote(review_id=review_id, user_id=user.id))
        elif not payload.helpful and vote is not None:
            db.delete(vote)
        db.commit()
        return {"helpful": payload.helpful, "helpfulCount": helpful_count(db, review_id)}

    @app.get("/admin/reviews")
    def moderation_queue(
        status: Literal["pending", "approved", "rejected", "hidden"] = "pending",
        offset: int = Query(0, ge=0, le=10000),
        limit: int = Query(20, ge=1, le=50),
        admin: User = Depends(admin_permission),
        db: Session = Depends(get_db),
    ):
        rows = (
            db.query(ProductReview)
            .filter_by(status=status)
            .order_by(ProductReview.id)
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [document(row) for row in rows]

    @app.patch("/admin/reviews/{review_id}/moderation")
    def moderate(
        review_id: int,
        payload: ModerationInput,
        admin: User = Depends(admin_permission),
        db: Session = Depends(get_db),
    ):
        row = db.query(ProductReview).filter_by(id=review_id).with_for_update().first()
        if row is None:
            raise HTTPException(404, "Review not found")
        if payload.expected_updated_at != row.updated_at:
            raise HTTPException(409, "Review changed; reload before moderating")
        if payload.status == "rejected" and not payload.reason:
            raise HTTPException(422, "A rejection reason is required")
        row.status = payload.status
        row.moderation_reason = payload.reason if payload.status == "rejected" else None
        if "seller_response" in payload.model_fields_set:
            row.seller_response = payload.seller_response
        row.moderated_by = admin.id
        row.moderated_at = utc_now()
        row.updated_at = row.moderated_at
        db.commit()
        db.refresh(row)
        return document(row)
