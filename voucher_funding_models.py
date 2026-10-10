"""Durable voucher funding and frozen jewellery checkout contexts."""

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


class VoucherFunding(Base):
    __tablename__ = "voucher_funding"
    __table_args__ = (
        CheckConstraint(
            "payment_mode IN ('manual','razorpay','complimentary')", name="ck_voucher_funding_mode"
        ),
        CheckConstraint(
            "state IN ('not_started','creating','unknown','ready','posted')",
            name="ck_voucher_funding_state",
        ),
        CheckConstraint(
            "(payment_mode IN ('manual','complimentary') AND state IN ('not_started','posted') AND provider_order_id IS NULL AND provider_payment_id IS NULL) OR (payment_mode = 'razorpay' AND ((state IN ('not_started','creating','unknown') AND provider_order_id IS NULL AND provider_payment_id IS NULL) OR (state = 'ready' AND provider_order_id IS NOT NULL AND provider_payment_id IS NULL) OR (state = 'posted' AND provider_order_id IS NOT NULL AND provider_payment_id IS NOT NULL)))",
            name="ck_voucher_funding_binding",
        ),
    )
    voucher_id = Column(Integer, ForeignKey("gift_vouchers.id"), primary_key=True)
    request_sha256 = Column(String(64), nullable=False)
    payment_mode = Column(String(20), nullable=False)
    code_ciphertext = Column(Text, nullable=False)
    receipt = Column(String(40), nullable=False, unique=True)
    state = Column(String(20), nullable=False)
    provider_order_id = Column(String(100), nullable=True, unique=True)
    provider_payment_id = Column(String(100), nullable=True, unique=True)
    created_at = Column(DateTime, nullable=False)


class VoucherFundingEvent(Base):
    __tablename__ = "voucher_funding_events"
    __table_args__ = (
        UniqueConstraint("voucher_id", "action", name="uq_voucher_funding_event_action"),
        CheckConstraint(
            "action IN ('request_created','checkout_started','checkout_unknown','checkout_bound','funding_posted')",
            name="ck_voucher_funding_event_action",
        ),
    )
    id = Column(Integer, primary_key=True)
    voucher_id = Column(Integer, ForeignKey("gift_vouchers.id"), nullable=False, index=True)
    action = Column(String(30), nullable=False)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    evidence_sha256 = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False)


class FinancialCheckoutAttempt(Base):
    __tablename__ = "financial_checkout_attempts"
    __table_args__ = (
        CheckConstraint(
            "state IN ('creating','unknown','ready')", name="ck_financial_checkout_attempt_state"
        ),
    )
    reservation_id = Column(Integer, ForeignKey("financial_reservations.id"), primary_key=True)
    client_request_sha256 = Column(String(64), nullable=False)
    context_json = Column(Text, nullable=False)
    state = Column(String(20), nullable=False)
    created_at = Column(DateTime, nullable=False)
