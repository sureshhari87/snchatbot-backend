"""Negotiated cancellations and append-only decision history."""

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


class OrderCancellation(Base):
    __tablename__ = "order_cancellations"
    __table_args__ = (
        CheckConstraint("version > 0", name="ck_cancellation_version"),
        CheckConstraint(
            "cash_paise >= 0 AND refund_paise >= 0 AND refund_paise <= cash_paise",
            name="ck_cancellation_money",
        ),
        CheckConstraint(
            "state IN ('requested','return_review','proposed','accepted','disputed','refund_pending','refund_review','refunded','cancelled')",
            name="ck_cancellation_state",
        ),
    )
    id = Column(String(32), primary_key=True)
    order_id = Column(Integer, ForeignKey("order_snapshots.id"), nullable=False, unique=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    state = Column(String(24), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    reason = Column(String(1000), nullable=False)
    cash_paise = Column(BigInteger, nullable=False, default=0)
    refund_paise = Column(BigInteger, nullable=False, default=0)
    proposal_json = Column(Text, nullable=True)
    payment_id = Column(String(100), nullable=True)
    refund_id = Column(String(100), nullable=True, unique=True)
    created_at = Column(DateTime, nullable=False, default=utc_now)


class OrderCancellationEvent(Base):
    __tablename__ = "order_cancellation_events"
    __table_args__ = (UniqueConstraint("case_id", "version", name="uq_cancellation_event_version"),)
    id = Column(Integer, primary_key=True)
    case_id = Column(String(32), ForeignKey("order_cancellations.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    action = Column(String(40), nullable=False)
    detail_json = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=utc_now)
