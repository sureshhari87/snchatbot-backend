"""Private, bounded review media. No customer filenames or raw metadata."""

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import deferred

from database import Base


class ReviewPhoto(Base):
    __tablename__ = "review_photos"
    __table_args__ = (
        UniqueConstraint("owner_id", "upload_key", name="uq_review_photo_upload"),
        CheckConstraint(
            "stored_bytes >= 0 AND stored_bytes <= 393216", name="ck_review_photo_size"
        ),
        CheckConstraint(
            "(removed_at IS NULL AND content IS NOT NULL AND stored_bytes > 0 AND length(content) = stored_bytes) OR (removed_at IS NOT NULL AND content IS NULL AND stored_bytes = 0 AND review_id IS NULL)",
            name="ck_review_photo_content",
        ),
    )
    id = Column(String(32), primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    review_id = Column(Integer, ForeignKey("product_reviews.id"), nullable=True, index=True)
    upload_key = Column(String(100), nullable=False)
    input_sha256 = Column(String(64), nullable=False)
    content = deferred(Column(LargeBinary, nullable=True))
    stored_bytes = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False)
    removed_at = Column(DateTime, nullable=True)
