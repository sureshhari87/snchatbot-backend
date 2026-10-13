"""Server-frozen balance intent, bound checkout attempts and immutable posting."""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)

from database import Base


class CustomDesignBalanceRequest(Base):
    __tablename__ = "custom_design_balance_requests"
    __table_args__ = (
        CheckConstraint("amount_paise > 0", name="ck_custom_balance_request_amount"),
        CheckConstraint("mode IN ('manual','razorpay')", name="ck_custom_balance_request_mode"),
        CheckConstraint("length(payload_sha256) = 64", name="ck_custom_balance_request_digest"),
    )
    id = Column(Integer, primary_key=True)
    design_id = Column(Integer, ForeignKey("custom_designs.id"), nullable=False, unique=True)
    quote_id = Column(Integer, ForeignKey("custom_design_quotes.id"), nullable=False, unique=True)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    request_key = Column(String(100), nullable=False)
    payload_sha256 = Column(String(64), nullable=False)
    amount_paise = Column(BigInteger, nullable=False)
    mode = Column(String(20), nullable=False)
    created_at = Column(DateTime, nullable=False)


class CustomDesignBalanceCheckout(Base):
    __tablename__ = "custom_design_balance_checkouts"
    __table_args__ = (
        UniqueConstraint("request_id", "sequence", name="uq_custom_balance_checkout_sequence"),
        CheckConstraint("sequence > 0", name="ck_custom_balance_checkout_sequence"),
        CheckConstraint(
            "state IN ('creating','unknown','ready','paid_pending','closed_failed')",
            name="ck_custom_balance_checkout_state",
        ),
        CheckConstraint(
            "state IN ('creating','unknown') OR provider_order_id IS NOT NULL",
            name="ck_custom_balance_checkout_binding",
        ),
        Index(
            "uq_custom_balance_active_checkout",
            "request_id",
            unique=True,
            postgresql_where=text("state != 'closed_failed'"),
            sqlite_where=text("state != 'closed_failed'"),
        ),
    )
    id = Column(Integer, primary_key=True)
    request_id = Column(
        Integer, ForeignKey("custom_design_balance_requests.id"), nullable=False, index=True
    )
    sequence = Column(Integer, nullable=False)
    receipt = Column(String(40), nullable=False, unique=True)
    provider_order_id = Column(String(100), nullable=True, unique=True)
    state = Column(String(20), nullable=False)
    created_at = Column(DateTime, nullable=False)


class CustomDesignBalanceEntry(Base):
    __tablename__ = "custom_design_balance_entries"
    __table_args__ = (
        CheckConstraint("amount_paise > 0", name="ck_custom_balance_entry_amount"),
        CheckConstraint("mode IN ('manual','razorpay')", name="ck_custom_balance_entry_mode"),
        CheckConstraint(
            "(mode = 'manual' AND actor_id IS NOT NULL AND checkout_id IS NULL AND provider_order_id IS NULL AND provider_payment_id IS NULL) OR (mode = 'razorpay' AND actor_id IS NULL AND checkout_id IS NOT NULL AND provider_order_id IS NOT NULL AND provider_payment_id IS NOT NULL)",
            name="ck_custom_balance_entry_evidence",
        ),
        UniqueConstraint("mode", "reference", name="uq_custom_balance_receipt"),
    )
    id = Column(Integer, primary_key=True)
    design_id = Column(Integer, ForeignKey("custom_designs.id"), nullable=False, unique=True)
    request_id = Column(
        Integer, ForeignKey("custom_design_balance_requests.id"), nullable=False, unique=True
    )
    quote_id = Column(Integer, ForeignKey("custom_design_quotes.id"), nullable=False, unique=True)
    checkout_id = Column(
        Integer, ForeignKey("custom_design_balance_checkouts.id"), nullable=True, unique=True
    )
    amount_paise = Column(BigInteger, nullable=False)
    mode = Column(String(20), nullable=False)
    reference = Column(String(100), nullable=False)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    provider_order_id = Column(String(100), nullable=True, unique=True)
    provider_payment_id = Column(String(100), nullable=True, unique=True)
    created_at = Column(DateTime, nullable=False)
