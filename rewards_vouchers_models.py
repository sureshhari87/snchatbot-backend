"""SQL reward/voucher ledger foundation; not exposed by customer endpoints yet."""

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


class RewardAccount(Base):
    __tablename__ = "reward_accounts"
    __table_args__ = (
        CheckConstraint("balance_points >= 0", name="ck_reward_balance"),
        CheckConstraint(
            "reserved_points >= 0 AND reserved_points <= balance_points", name="ck_reward_reserved"
        ),
    )
    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    balance_points = Column(BigInteger, nullable=False, default=0)
    reserved_points = Column(BigInteger, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False)


class RewardEntry(Base):
    __tablename__ = "reward_entries"
    __table_args__ = (
        CheckConstraint("kind IN ('purchase_credit','redemption')", name="ck_reward_entry_kind"),
        CheckConstraint(
            "(kind = 'purchase_credit' AND points_delta > 0) OR (kind = 'redemption' AND points_delta < 0)",
            name="ck_reward_entry_sign",
        ),
        UniqueConstraint("user_id", "order_id", "kind", name="uq_reward_order_posting"),
    )
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("reward_accounts.user_id"), nullable=False, index=True)
    order_id = Column(Integer, ForeignKey("order_snapshots.id"), nullable=False)
    event_key = Column(String(100), nullable=False, unique=True)
    kind = Column(String(30), nullable=False)
    points_delta = Column(BigInteger, nullable=False)
    evidence_reference = Column(String(100), nullable=False)
    evidence_sha256 = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False)


class GiftVoucher(Base):
    __tablename__ = "gift_vouchers"
    __table_args__ = (
        CheckConstraint("amount_paise >= 100", name="ck_gift_voucher_amount"),
        CheckConstraint("state IN ('pending','active')", name="ck_gift_voucher_state"),
        CheckConstraint("source IN ('purchased','complimentary')", name="ck_gift_voucher_source"),
        CheckConstraint(
            "balance_paise >= 0 AND balance_paise <= amount_paise AND reserved_paise >= 0 AND reserved_paise <= balance_paise",
            name="ck_gift_voucher_balance",
        ),
        CheckConstraint(
            "(state = 'pending' AND balance_paise = 0 AND activated_at IS NULL AND expires_at IS NULL) OR "
            "(state = 'active' AND activated_at IS NOT NULL AND expires_at IS NOT NULL AND expires_at > activated_at)",
            name="ck_gift_voucher_activation",
        ),
        UniqueConstraint("purchaser_id", "request_key", name="uq_gift_voucher_request"),
    )
    id = Column(Integer, primary_key=True)
    purchaser_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    assigned_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    request_key = Column(String(100), nullable=False)
    code_hash = Column(String(64), nullable=False, unique=True)
    source = Column(String(20), nullable=False)
    amount_paise = Column(BigInteger, nullable=False)
    balance_paise = Column(BigInteger, nullable=False, default=0)
    reserved_paise = Column(BigInteger, nullable=False, default=0)
    state = Column(String(20), nullable=False, default="pending")
    activated_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False)


class GiftVoucherEntry(Base):
    __tablename__ = "gift_voucher_entries"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('manual_funding','razorpay_funding','complimentary','redemption')",
            name="ck_gift_voucher_entry_kind",
        ),
        CheckConstraint(
            "(kind = 'redemption' AND amount_delta_paise < 0 AND order_id IS NOT NULL) OR "
            "(kind != 'redemption' AND amount_delta_paise > 0 AND order_id IS NULL)",
            name="ck_gift_voucher_entry_sign",
        ),
        CheckConstraint(
            "kind NOT IN ('manual_funding','complimentary') OR (actor_id IS NOT NULL AND reason IS NOT NULL AND length(trim(reason)) > 0)",
            name="ck_gift_voucher_manual_audit",
        ),
        UniqueConstraint(
            "voucher_id", "kind", "evidence_reference", name="uq_gift_voucher_evidence"
        ),
    )
    id = Column(Integer, primary_key=True)
    voucher_id = Column(Integer, ForeignKey("gift_vouchers.id"), nullable=False, index=True)
    order_id = Column(Integer, ForeignKey("order_snapshots.id"), nullable=True)
    event_key = Column(String(100), nullable=False, unique=True)
    kind = Column(String(30), nullable=False)
    amount_delta_paise = Column(BigInteger, nullable=False)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    reason = Column(String(200), nullable=True)
    evidence_reference = Column(String(100), nullable=False)
    evidence_sha256 = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False)


Index(
    "uq_gift_voucher_one_funding",
    GiftVoucherEntry.voucher_id,
    unique=True,
    postgresql_where=text("kind != 'redemption'"),
    sqlite_where=text("kind != 'redemption'"),
)


Index(
    "uq_gift_voucher_order_redemption",
    GiftVoucherEntry.voucher_id,
    GiftVoucherEntry.order_id,
    unique=True,
)


class RewardVoucherHold(Base):
    __tablename__ = "reward_voucher_holds"
    __table_args__ = (
        CheckConstraint(
            "(reward_user_id IS NOT NULL AND voucher_id IS NULL) OR (reward_user_id IS NULL AND voucher_id IS NOT NULL)",
            name="ck_reward_voucher_hold_target",
        ),
        CheckConstraint(
            "kind IN ('refund','dispute','admin_review')", name="ck_reward_voucher_hold_kind"
        ),
        CheckConstraint("length(trim(reason)) > 0", name="ck_reward_voucher_hold_reason"),
        UniqueConstraint("reward_user_id", "kind", "reference", name="uq_reward_hold_reference"),
        UniqueConstraint("voucher_id", "kind", "reference", name="uq_voucher_hold_reference"),
    )
    id = Column(Integer, primary_key=True)
    reward_user_id = Column(
        Integer, ForeignKey("reward_accounts.user_id"), nullable=True, index=True
    )
    voucher_id = Column(Integer, ForeignKey("gift_vouchers.id"), nullable=True, index=True)
    kind = Column(String(20), nullable=False)
    reference = Column(String(100), nullable=False)
    reason = Column(String(200), nullable=False)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False)
