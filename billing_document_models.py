"""Immutable cross-service documents; sales numbering is transactionally serialized."""

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


class BillingDocument(Base):
    __tablename__ = "billing_documents"
    __table_args__ = (
        UniqueConstraint("source", "source_id", "kind", name="uq_billing_document_source_kind"),
        UniqueConstraint("number", name="uq_billing_document_number"),
        CheckConstraint("source_id > 0", name="ck_billing_document_source_id"),
        CheckConstraint("kind IN ('receipt','sales_invoice')", name="ck_billing_document_kind"),
        CheckConstraint(
            "source IN ('voucher','custom_advance','custom_balance','merchandise','custom_design')",
            name="ck_billing_document_source",
        ),
        CheckConstraint("length(snapshot_sha256) = 64", name="ck_billing_document_digest"),
    )
    id = Column(String(32), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    source = Column(String(30), nullable=False)
    source_id = Column(Integer, nullable=False)
    kind = Column(String(30), nullable=False)
    number = Column(String(16), nullable=True)
    request_sha256 = Column(String(64), nullable=False)
    snapshot_json = Column(Text, nullable=False)
    snapshot_sha256 = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False)


class BillingDocumentAudit(Base):
    __tablename__ = "billing_document_audit"
    id = Column(Integer, primary_key=True)
    document_id = Column(
        String(32), ForeignKey("billing_documents.id"), nullable=False, unique=True
    )
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    reason = Column(String(200), nullable=False)
    snapshot_sha256 = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False)


class BillingSeries(Base):
    __tablename__ = "billing_series"
    __table_args__ = (CheckConstraint("next_sequence > 0", name="ck_billing_series_sequence"),)
    key = Column(String(20), primary_key=True)
    next_sequence = Column(BigInteger, nullable=False)
