"""Structured custom-design requests and immutable quote/decision history."""

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


class CustomDesign(Base):
    __tablename__ = "custom_designs"
    __table_args__ = (
        UniqueConstraint("user_id", "request_key", name="uq_custom_design_request_key"),
    )
    id = Column(Integer, primary_key=True)
    custom_order_id = Column(
        Integer, ForeignKey("custom_order_requests.id"), nullable=False, unique=True
    )
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    request_key = Column(String(100), nullable=False)
    payload_sha256 = Column(String(64), nullable=False)
    customer_notes = Column(Text, nullable=False)
    reference_image_url = Column(String(2048), nullable=True)
    purity = Column(String(40), nullable=False)
    budget_range = Column(String(100), nullable=False)
    created_at = Column(DateTime, nullable=False)


class CustomDesignQuote(Base):
    __tablename__ = "custom_design_quotes"
    __table_args__ = (
        UniqueConstraint("design_id", "version", name="uq_custom_design_quote_version"),
        UniqueConstraint("design_id", "request_key", name="uq_custom_design_quote_key"),
        CheckConstraint("version > 0", name="ck_custom_design_quote_version"),
        CheckConstraint(
            "total_paise > 0 AND advance_paise >= 0 AND advance_paise <= total_paise",
            name="ck_custom_design_quote_money",
        ),
        CheckConstraint(
            "advance_paise = 0 OR advance_paise >= 100", name="ck_custom_design_advance_minimum"
        ),
    )
    id = Column(Integer, primary_key=True)
    design_id = Column(Integer, ForeignKey("custom_designs.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    request_key = Column(String(100), nullable=False)
    payload_sha256 = Column(String(64), nullable=False)
    total_paise = Column(BigInteger, nullable=False)
    advance_paise = Column(BigInteger, nullable=False)
    notes = Column(Text, nullable=False)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False)


class CustomDesignDecision(Base):
    __tablename__ = "custom_design_decisions"
    __table_args__ = (
        UniqueConstraint("design_id", "request_key", name="uq_custom_design_decision_key"),
        CheckConstraint("action IN ('approved', 'rejected')", name="ck_custom_design_decision"),
    )
    id = Column(Integer, primary_key=True)
    design_id = Column(Integer, ForeignKey("custom_designs.id"), nullable=False, index=True)
    quote_id = Column(Integer, ForeignKey("custom_design_quotes.id"), nullable=False)
    request_key = Column(String(100), nullable=False)
    payload_sha256 = Column(String(64), nullable=False)
    action = Column(String(20), nullable=False)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False)


class CustomDesignAudit(Base):
    __tablename__ = "custom_design_audit"
    __table_args__ = (
        UniqueConstraint("design_id", "event_key", name="uq_custom_design_audit_event"),
    )
    id = Column(Integer, primary_key=True)
    design_id = Column(Integer, ForeignKey("custom_designs.id"), nullable=False, index=True)
    event_key = Column(String(140), nullable=False)
    action = Column(String(40), nullable=False)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    quote_id = Column(Integer, ForeignKey("custom_design_quotes.id"), nullable=True)
    detail = Column(Text, nullable=True)
    payload_sha256 = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False)
