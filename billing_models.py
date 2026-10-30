"""Immutable customer payment acknowledgements; no tax-invoice number allocation."""

from sqlalchemy import (
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


class BillingReceipt(Base):
    __tablename__ = "billing_receipts"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_billing_receipt_source"),
        CheckConstraint("source = 'savings' AND source_id > 0", name="ck_billing_receipt_source"),
        CheckConstraint("length(snapshot_sha256) = 64", name="ck_billing_receipt_digest"),
    )
    id = Column(String(32), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    source = Column(String(30), nullable=False)
    source_id = Column(Integer, ForeignKey("savings_payments.id"), nullable=False)
    snapshot_json = Column(Text, nullable=False)
    snapshot_sha256 = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False)


class BillingReceiptAudit(Base):
    __tablename__ = "billing_receipt_audit"
    id = Column(Integer, primary_key=True)
    receipt_id = Column(String(32), ForeignKey("billing_receipts.id"), nullable=False, unique=True)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    action = Column(String(30), nullable=False)
    snapshot_sha256 = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False)
    __table_args__ = (CheckConstraint("action = 'issued'", name="ck_billing_receipt_audit_action"),)
