"""Append-only tax decisions, credit notes, cancellations and refund evidence."""

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


class BillingSupplyEvent(Base):
    __tablename__ = "billing_supply_events"
    id = Column(String(32), primary_key=True)
    source = Column(String(30), nullable=False)
    source_id = Column(Integer, nullable=False)
    snapshot_json = Column(Text, nullable=False)
    snapshot_sha256 = Column(String(64), nullable=False)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False)


class TaxApproval(Base):
    __tablename__ = "billing_tax_approvals"
    __table_args__ = (
        UniqueConstraint("scope", "version", name="uq_billing_tax_version"),
        CheckConstraint("status IN ('pending','approved','revoked')", name="ck_billing_tax_status"),
    )
    id = Column(String(32), primary_key=True)
    scope = Column(String(120), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    status = Column(String(10), nullable=False)
    snapshot_json = Column(Text, nullable=False)
    snapshot_sha256 = Column(String(64), nullable=False)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False)


class InvoiceAdjustment(Base):
    __tablename__ = "billing_invoice_adjustments"
    __table_args__ = (
        UniqueConstraint("invoice_id", "request_key", name="uq_billing_adjustment_request"),
        UniqueConstraint("number", name="uq_billing_adjustment_number"),
        UniqueConstraint("provider_reference", name="uq_billing_adjustment_refund"),
        CheckConstraint(
            "kind IN ('credit_note','cancellation','refund')", name="ck_billing_adjustment_kind"
        ),
    )
    id = Column(String(32), primary_key=True)
    invoice_id = Column(String(32), ForeignKey("billing_documents.id"), nullable=False, index=True)
    request_key = Column(String(100), nullable=False)
    kind = Column(String(20), nullable=False)
    number = Column(String(16))
    provider_reference = Column(String(100))
    request_sha256 = Column(String(64), nullable=False)
    snapshot_json = Column(Text, nullable=False)
    snapshot_sha256 = Column(String(64), nullable=False)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False)
