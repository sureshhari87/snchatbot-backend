"""Durable redemption reservations. No public API is enabled by this module."""

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


class FinancialReservation(Base):
    __tablename__ = "financial_reservations"
    __table_args__ = (
        UniqueConstraint("user_id", "request_key", name="uq_financial_reservation_request"),
        CheckConstraint(
            "state IN ('reserved','ready','posted')", name="ck_financial_reservation_state"
        ),
        CheckConstraint(
            "payable_paise >= 100 AND reward_points >= 0 AND voucher_paise >= 0",
            name="ck_financial_reservation_amounts",
        ),
        CheckConstraint(
            "(voucher_id IS NULL AND voucher_paise = 0) OR (voucher_id IS NOT NULL AND voucher_paise >= 0)",
            name="ck_financial_reservation_voucher",
        ),
        CheckConstraint(
            "(state = 'reserved' AND provider_order_id IS NULL AND order_id IS NULL AND provider_payment_id IS NULL) OR "
            "(state = 'ready' AND provider_order_id IS NOT NULL AND order_id IS NOT NULL AND provider_payment_id IS NULL) OR "
            "(state = 'posted' AND provider_order_id IS NOT NULL AND order_id IS NOT NULL AND provider_payment_id IS NOT NULL)",
            name="ck_financial_reservation_binding",
        ),
    )
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    request_key = Column(String(100), nullable=False)
    request_sha256 = Column(String(64), nullable=False)
    receipt = Column(String(40), nullable=False, unique=True)
    quote_json = Column(Text, nullable=False)
    payable_paise = Column(BigInteger, nullable=False)
    reward_points = Column(BigInteger, nullable=False)
    voucher_id = Column(Integer, ForeignKey("gift_vouchers.id"), nullable=True)
    voucher_paise = Column(BigInteger, nullable=False)
    state = Column(String(20), nullable=False)
    order_id = Column(Integer, ForeignKey("order_snapshots.id"), nullable=True, unique=True)
    provider_order_id = Column(String(100), nullable=True, unique=True)
    provider_payment_id = Column(String(100), nullable=True, unique=True)
    created_at = Column(DateTime, nullable=False)


class FinancialReservationEvent(Base):
    __tablename__ = "financial_reservation_events"
    __table_args__ = (
        UniqueConstraint("reservation_id", "action", name="uq_financial_reservation_action"),
        CheckConstraint(
            "action IN ('reserved','checkout_bound','payment_posted')",
            name="ck_financial_reservation_event_action",
        ),
    )
    id = Column(Integer, primary_key=True)
    reservation_id = Column(
        Integer, ForeignKey("financial_reservations.id"), nullable=False, index=True
    )
    event_key = Column(String(100), nullable=False, unique=True)
    action = Column(String(30), nullable=False)
    evidence_sha256 = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False)
