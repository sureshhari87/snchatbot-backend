"""Server-owned savings accounting and durable checkout models."""

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)

from database import Base


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SavingsScheme(Base):
    __tablename__ = "savings_schemes"
    __table_args__ = (
        UniqueConstraint("user_id", "request_key", name="uq_savings_enrollment_key"),
        CheckConstraint("kind IN ('monthly', 'digigold')", name="ck_savings_kind"),
        CheckConstraint("state IN ('active', 'redeemed')", name="ck_savings_state"),
        CheckConstraint(
            "(kind = 'monthly' AND monthly_paise IS NOT NULL AND monthly_paise >= 50000) OR (kind = 'digigold' AND monthly_paise IS NULL)",
            name="ck_savings_monthly_amount",
        ),
    )
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    request_key = Column(String(100), nullable=False)
    kind = Column(String(20), nullable=False)
    monthly_paise = Column(BigInteger, nullable=True)
    state = Column(String(20), nullable=False, default="active")
    started_at = Column(DateTime, nullable=False)
    matures_at = Column(DateTime, nullable=False)


class SavingsPayment(Base):
    __tablename__ = "savings_payments"
    __table_args__ = (
        UniqueConstraint("user_id", "request_key", name="uq_savings_payment_key"),
        UniqueConstraint("scheme_id", "installment", name="uq_savings_installment"),
        UniqueConstraint("mode", "verified_reference", name="uq_savings_verified_reference"),
        CheckConstraint("amount_paise > 0", name="ck_savings_payment_amount"),
        CheckConstraint("mode IN ('manual', 'razorpay')", name="ck_savings_payment_mode"),
        CheckConstraint(
            "state IN ('pending', 'posted', 'cancelled', 'rejected')",
            name="ck_savings_payment_state",
        ),
        CheckConstraint(
            "installment IS NULL OR (installment >= 1 AND installment <= 11)",
            name="ck_savings_installment",
        ),
    )
    id = Column(Integer, primary_key=True)
    scheme_id = Column(Integer, ForeignKey("savings_schemes.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    request_key = Column(String(100), nullable=False)
    amount_paise = Column(BigInteger, nullable=False)
    installment = Column(Integer, nullable=True)
    mode = Column(String(20), nullable=False)
    state = Column(String(20), nullable=False, default="pending")
    # Populated only by the trusted checkout creation/reconciliation adapter.
    provider_order_id = Column(String(100), nullable=True, unique=True)
    verified_reference = Column(String(100), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now)
    posted_at = Column(DateTime, nullable=True)


class SavingsGoldRate(Base):
    __tablename__ = "savings_gold_rates"
    __table_args__ = (
        CheckConstraint("paise_per_gram > 0", name="ck_savings_rate_amount"),
        CheckConstraint("valid_until > effective_at", name="ck_savings_rate_window"),
    )
    id = Column(Integer, primary_key=True)
    paise_per_gram = Column(BigInteger, nullable=False)
    purity = Column(String(20), nullable=False, default="24K 995")
    source = Column(String(200), nullable=False)
    effective_at = Column(DateTime, nullable=False)
    valid_until = Column(DateTime, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=utc_now)


class SavingsEntry(Base):
    __tablename__ = "savings_entries"
    __table_args__ = (
        CheckConstraint("kind IN ('credit', 'redemption')", name="ck_savings_entry_kind"),
        CheckConstraint("bonus_bps >= 0 AND bonus_bps <= 500", name="ck_savings_bonus_bps"),
        CheckConstraint(
            "(kind = 'credit' AND payment_id IS NOT NULL AND principal_paise > 0 AND gold_micrograms >= 0 AND bonus_micrograms >= 0 AND store_bonus_paise = 0) OR (kind = 'redemption' AND payment_id IS NULL AND principal_paise <= 0 AND gold_micrograms <= 0 AND bonus_micrograms <= 0 AND store_bonus_paise >= 0)",
            name="ck_savings_entry_signs",
        ),
    )
    id = Column(Integer, primary_key=True)
    scheme_id = Column(Integer, ForeignKey("savings_schemes.id"), nullable=False, index=True)
    event_key = Column(String(100), nullable=False, unique=True)
    payment_id = Column(Integer, ForeignKey("savings_payments.id"), nullable=True, unique=True)
    kind = Column(String(20), nullable=False)
    principal_paise = Column(BigInteger, nullable=False)
    gold_micrograms = Column(BigInteger, nullable=False, default=0)
    bonus_micrograms = Column(BigInteger, nullable=False, default=0)
    store_bonus_paise = Column(BigInteger, nullable=False, default=0)
    rate_id = Column(Integer, ForeignKey("savings_gold_rates.id"), nullable=True)
    bonus_bps = Column(Integer, nullable=False, default=0)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    evidence_reference = Column(String(100), nullable=False)
    created_at = Column(DateTime, nullable=False)


class SavingsAudit(Base):
    __tablename__ = "savings_audit"
    id = Column(Integer, primary_key=True)
    scheme_id = Column(Integer, ForeignKey("savings_schemes.id"), nullable=False, index=True)
    event_key = Column(String(100), nullable=False, unique=True)
    action = Column(String(30), nullable=False)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    evidence_reference = Column(String(100), nullable=False)
    created_at = Column(DateTime, nullable=False)


class SavingsCheckoutAttempt(Base):
    __tablename__ = "savings_checkout_attempts"
    __table_args__ = (
        CheckConstraint(
            "state IN ('creating', 'unknown', 'ready')", name="ck_savings_checkout_state"
        ),
    )
    payment_id = Column(Integer, ForeignKey("savings_payments.id"), primary_key=True)
    receipt = Column(String(40), nullable=False, unique=True)
    state = Column(String(20), nullable=False)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)


class SavingsPaymentResolution(Base):
    __tablename__ = "savings_payment_resolutions"
    __table_args__ = (
        CheckConstraint("kind IN ('cancelled', 'rejected')", name="ck_savings_resolution_kind"),
        CheckConstraint(
            "original_installment IS NULL OR (original_installment >= 1 AND original_installment <= 11)",
            name="ck_savings_resolution_installment",
        ),
    )
    payment_id = Column(Integer, ForeignKey("savings_payments.id"), primary_key=True)
    kind = Column(String(20), nullable=False)
    reason = Column(String(100), nullable=False)
    original_installment = Column(Integer, nullable=True)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False)


class SavingsHold(Base):
    __tablename__ = "savings_holds"
    __table_args__ = (
        UniqueConstraint("scheme_id", "kind", "reference", name="uq_savings_hold_reference"),
        CheckConstraint(
            "kind IN ('refund', 'dispute', 'admin_review')", name="ck_savings_hold_kind"
        ),
    )
    id = Column(Integer, primary_key=True)
    scheme_id = Column(Integer, ForeignKey("savings_schemes.id"), nullable=False, index=True)
    payment_id = Column(Integer, ForeignKey("savings_payments.id"), nullable=True)
    kind = Column(String(20), nullable=False)
    reference = Column(String(100), nullable=False)
    reason = Column(String(100), nullable=False)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False)


class SavingsHoldDecision(Base):
    __tablename__ = "savings_hold_decisions"
    __table_args__ = (
        CheckConstraint(
            "action IN ('released', 'reopened')", name="ck_savings_hold_decision_action"
        ),
        CheckConstraint(
            "action != 'released' OR actor_id IS NOT NULL", name="ck_savings_release_actor"
        ),
    )
    id = Column(Integer, primary_key=True)
    hold_id = Column(Integer, ForeignKey("savings_holds.id"), nullable=False, index=True)
    action = Column(String(20), nullable=False)
    event_key = Column(String(100), nullable=False, unique=True)
    reason = Column(String(100), nullable=False)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    evidence_sha256 = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False)
