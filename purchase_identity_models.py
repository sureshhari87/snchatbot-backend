"""Encrypted buyer submissions and append-only verification/binding evidence."""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from database import Base
from models import utc_now


class PurchaseIdentity(Base):
    __tablename__ = "purchase_identities"
    __table_args__ = (
        UniqueConstraint("user_id", "version", name="uq_purchase_identity_version"),
        UniqueConstraint("user_id", "request_key", name="uq_purchase_identity_request"),
        CheckConstraint("version > 0", name="ck_purchase_identity_version"),
    )
    id = Column(String(32), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    request_key = Column(String(80), nullable=False)
    encrypted_facts = Column(Text, nullable=False)
    pan_suffix = Column(String(4), nullable=False)
    created_at = Column(DateTime, nullable=False, default=utc_now)


class PurchaseIdentityReview(Base):
    __tablename__ = "purchase_identity_reviews"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('approved','rejected')", name="ck_purchase_identity_decision"
        ),
    )
    id = Column(String(32), primary_key=True)
    identity_id = Column(
        String(32), ForeignKey("purchase_identities.id"), nullable=False, unique=True
    )
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    decision = Column(String(12), nullable=False)
    method = Column(String(40), nullable=False)
    evidence_reference = Column(String(160), nullable=False)
    reason = Column(String(500), nullable=False)
    created_at = Column(DateTime, nullable=False, default=utc_now)


class PurchaseIdentityUse(Base):
    __tablename__ = "purchase_identity_uses"
    __table_args__ = (
        UniqueConstraint("resource_kind", "resource_key", name="uq_purchase_identity_use"),
        CheckConstraint("amount_paise > 20000000", name="ck_purchase_identity_amount"),
    )
    id = Column(String(32), primary_key=True)
    identity_id = Column(String(32), ForeignKey("purchase_identities.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    resource_kind = Column(String(30), nullable=False)
    resource_key = Column(String(100), nullable=False)
    amount_paise = Column(BigInteger, nullable=False)
    created_at = Column(DateTime, nullable=False, default=utc_now)
