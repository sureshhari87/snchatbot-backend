"""Immutable referral ownership, frozen terms, outcomes and credits."""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
)

from database import Base


class ReferralCode(Base):
    __tablename__ = "referral_codes"
    __table_args__ = (UniqueConstraint("owner_id", "code", name="uq_referral_code_owner"),)
    owner_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    code = Column(String(20), nullable=False, unique=True)
    created_at = Column(DateTime, nullable=False)


class ReferralApplication(Base):
    __tablename__ = "referral_applications"
    __table_args__ = (
        ForeignKeyConstraint(
            ["referrer_id", "code"],
            ["referral_codes.owner_id", "referral_codes.code"],
            name="fk_referral_code_owner",
        ),
        CheckConstraint("referred_id != referrer_id", name="ck_referral_not_self"),
        CheckConstraint(
            "terms_version = 1 AND referrer_points = 100 AND customer_points = 50 AND minimum_merchandise_paise = 100000",
            name="ck_referral_approved_terms",
        ),
    )
    id = Column(Integer, primary_key=True)
    referred_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    referrer_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    code = Column(String(20), ForeignKey("referral_codes.code"), nullable=False)
    terms_version = Column(Integer, nullable=False)
    referrer_points = Column(BigInteger, nullable=False)
    customer_points = Column(BigInteger, nullable=False)
    minimum_merchandise_paise = Column(BigInteger, nullable=False)
    created_at = Column(DateTime, nullable=False)


class ReferralOutcome(Base):
    __tablename__ = "referral_outcomes"
    __table_args__ = (
        CheckConstraint(
            "state IN ('rewarded','not_qualified','held')", name="ck_referral_outcome_state"
        ),
        CheckConstraint("merchandise_after_coupon_paise >= 0", name="ck_referral_outcome_amount"),
    )
    id = Column(Integer, primary_key=True)
    application_id = Column(
        Integer, ForeignKey("referral_applications.id"), nullable=False, unique=True
    )
    order_id = Column(Integer, ForeignKey("order_snapshots.id"), nullable=False, unique=True)
    state = Column(String(20), nullable=False)
    reason = Column(String(100), nullable=False)
    merchandise_after_coupon_paise = Column(BigInteger, nullable=False)
    evidence_reference = Column(String(100), nullable=False)
    evidence_sha256 = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False)


class ReferralCredit(Base):
    __tablename__ = "referral_credits"
    __table_args__ = (
        CheckConstraint(
            "role IN ('referrer','new_customer') AND points_delta > 0", name="ck_referral_credit"
        ),
        UniqueConstraint("outcome_id", "role", name="uq_referral_credit_role"),
        UniqueConstraint("outcome_id", "user_id", name="uq_referral_credit_user"),
    )
    id = Column(Integer, primary_key=True)
    outcome_id = Column(Integer, ForeignKey("referral_outcomes.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("reward_accounts.user_id"), nullable=False, index=True)
    order_id = Column(Integer, ForeignKey("order_snapshots.id"), nullable=False)
    role = Column(String(20), nullable=False)
    points_delta = Column(BigInteger, nullable=False)
    event_key = Column(String(100), nullable=False, unique=True)
    evidence_reference = Column(String(100), nullable=False)
    evidence_sha256 = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False)
