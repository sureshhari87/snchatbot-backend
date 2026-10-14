"""Inactive delivery drafts are isolated from checkout configuration."""

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint

from models import Base, utc_now


class DeliveryDraft(Base):
    __tablename__ = "delivery_drafts"
    id = Column(Integer, primary_key=True)
    name = Column(String(80), nullable=False)
    route = Column(JSON, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False, default=utc_now)
    updated_at = Column(DateTime, nullable=False, default=utc_now)


class DeliveryDraftAudit(Base):
    __tablename__ = "delivery_draft_audits"
    __table_args__ = (UniqueConstraint("draft_id", "version", name="uq_delivery_draft_version"),)
    id = Column(Integer, primary_key=True)
    draft_id = Column(Integer, ForeignKey("delivery_drafts.id"), nullable=False)
    version = Column(Integer, nullable=False)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    request_key = Column(String(100), nullable=False, unique=True)
    payload_sha256 = Column(String(64), nullable=False)
    reason = Column(String(500), nullable=False)
    before = Column(JSON, nullable=True)
    after = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, default=utc_now)
