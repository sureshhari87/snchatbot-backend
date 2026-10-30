"""Posted-ledger acknowledgements. No provider reads, invoice allocation or payment mutation."""

import hashlib
import json
import os
from dataclasses import asdict
from datetime import timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

import config
from billing_models import BillingReceipt, BillingReceiptAudit
from billing_profile import MAX_PAISE, BillingIssuer
from models import User, utc_now
from savings_models import (
    SavingsAudit,
    SavingsEntry,
    SavingsGoldRate,
    SavingsPayment,
    SavingsScheme,
)


def enabled():
    return os.getenv("BILLING_DOCUMENTS_ENABLED", "0") == "1"


def issuer():
    try:
        data = json.loads(os.getenv("BILLING_ISSUER_JSON", ""))
        if not isinstance(data, dict) or set(data) != set(BillingIssuer.__dataclass_fields__):
            raise ValueError
        return asdict(BillingIssuer(**data))
    except (ValueError, TypeError):
        raise HTTPException(409, "Approved billing profile is not configured") from None


def canonical(snapshot):
    return json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def iso(value):
    return (
        value.replace(tzinfo=timezone.utc).isoformat()
        if value.tzinfo is None
        else value.isoformat()
    )


def require(condition):
    if not condition:
        raise HTTPException(409, "Saved payment accounting requires billing review")


def saved_receipt(db, receipt):
    require(digest(receipt.snapshot_json) == receipt.snapshot_sha256)
    audit = db.query(BillingReceiptAudit).filter_by(receipt_id=receipt.id).one_or_none()
    require(
        audit is not None
        and audit.snapshot_sha256 == receipt.snapshot_sha256
        and audit.action == "issued"
    )
    try:
        snapshot = json.loads(receipt.snapshot_json)
    except (ValueError, TypeError):
        raise HTTPException(409, "Saved billing document requires review") from None
    require(isinstance(snapshot, dict) and snapshot.get("document_id") == receipt.id)
    # A current hold is deliberately separate from the original immutable document.
    from savings import active_holds

    payment = db.get(SavingsPayment, receipt.source_id)
    require(payment is not None and payment.user_id == receipt.user_id)
    return dict(
        document=snapshot, review_required=active_holds(db, payment.scheme_id).first() is not None
    )


def savings_snapshot(db, payment, owner, document_id, now):
    scheme = db.get(SavingsScheme, payment.scheme_id)
    require(scheme is not None and scheme.user_id == owner.id)
    entry = db.query(SavingsEntry).filter_by(payment_id=payment.id, kind="credit").one_or_none()
    audits = (
        db.query(SavingsAudit)
        .filter_by(scheme_id=scheme.id, event_key=f"post:{payment.id}", action="payment_posted")
        .all()
    )
    require(payment.state == "posted" and payment.posted_at is not None)
    require(entry is not None and entry.scheme_id == scheme.id)
    require(type(payment.amount_paise) is int and 0 < payment.amount_paise <= MAX_PAISE)
    require(entry.principal_paise == payment.amount_paise and len(audits) == 1)
    require(
        bool(payment.verified_reference)
        and entry.evidence_reference == payment.verified_reference
        and audits[0].evidence_reference == payment.verified_reference
    )
    require(payment.mode in {"manual", "razorpay"} and entry.created_at == payment.posted_at)
    require(audits[0].created_at == payment.posted_at and audits[0].actor_id == entry.actor_id)
    require(payment.mode != "manual" or entry.actor_id is not None)
    require(payment.mode != "razorpay" or bool(payment.provider_order_id))
    detail = dict(
        plan_id=scheme.id,
        plan_kind=scheme.kind,
        installment=payment.installment,
        plan_started_at=iso(scheme.started_at),
        plan_matures_at=iso(scheme.matures_at),
        principal_paise=entry.principal_paise,
        gold_micrograms=entry.gold_micrograms,
        bonus_micrograms=entry.bonus_micrograms,
        bonus_basis_points=entry.bonus_bps,
    )
    if scheme.kind == "digigold":
        rate = db.get(SavingsGoldRate, entry.rate_id) if entry.rate_id else None
        require(rate is not None and rate.paise_per_gram > 0 and entry.gold_micrograms > 0)
        require(
            rate.effective_at <= payment.posted_at < rate.valid_until and rate.purity == "24K 995"
        )
        require(entry.gold_micrograms == entry.principal_paise * 1000000 // rate.paise_per_gram)
        require(entry.bonus_micrograms == entry.gold_micrograms * entry.bonus_bps // 10000)
        detail.update(
            rate_paise_per_gram=rate.paise_per_gram,
            purity=rate.purity,
            rate_source=rate.source,
            rate_reference_id=rate.id,
            rate_effective_at=iso(rate.effective_at),
            rate_valid_until=iso(rate.valid_until),
        )
    else:
        require(
            scheme.kind == "monthly"
            and type(payment.installment) is int
            and 1 <= payment.installment <= 11
            and entry.rate_id is None
            and scheme.monthly_paise == payment.amount_paise
        )
        require(entry.gold_micrograms == 0 and entry.bonus_micrograms == 0 and entry.bonus_bps == 0)
    return dict(
        schema_version=1,
        document_id=document_id,
        document_type="payment_acknowledgement",
        is_test_document=config.APP_ENV != "production",
        title="Savings payment acknowledgement",
        issued_at=iso(now),
        issuer=issuer(),
        customer=dict(account_name=owner.username, email=owner.email),
        payment=dict(
            id=payment.id,
            status_at_issue="posted",
            amount_paise=payment.amount_paise,
            currency="INR",
            mode=payment.mode,
            reference=payment.verified_reference,
            posted_at=iso(payment.posted_at),
        ),
        savings=detail,
        notice="This acknowledges a recorded scheme payment. It is not a sales tax invoice.",
    )


def issue_savings(db, user_id, payment_id):
    # Lock the existing source first so concurrent PostgreSQL requests serialize.
    payment = (
        db.query(SavingsPayment)
        .filter_by(id=payment_id, user_id=user_id)
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if payment is None:
        raise HTTPException(404, "Payment not found")
    existing = (
        db.query(BillingReceipt)
        .filter_by(source="savings", source_id=payment_id, user_id=user_id)
        .one_or_none()
    )
    if existing is not None:
        return saved_receipt(db, existing)
    owner = db.get(User, user_id)
    require(owner is not None)
    now = utc_now()
    identity = uuid4().hex
    snapshot = canonical(savings_snapshot(db, payment, owner, identity, now))
    fingerprint = digest(snapshot)
    receipt = BillingReceipt(
        id=identity,
        user_id=user_id,
        source="savings",
        source_id=payment_id,
        snapshot_json=snapshot,
        snapshot_sha256=fingerprint,
        created_at=now,
    )
    try:
        with db.begin_nested():
            db.add(receipt)
            db.flush()
            db.add(
                BillingReceiptAudit(
                    receipt_id=identity,
                    actor_id=user_id,
                    action="issued",
                    snapshot_sha256=fingerprint,
                    created_at=now,
                )
            )
            db.flush()
    except IntegrityError:
        # Never retry payment posting or any provider operation on an issuance race.
        existing = (
            db.query(BillingReceipt)
            .filter_by(source="savings", source_id=payment_id, user_id=user_id)
            .one_or_none()
        )
        if existing is None:
            raise HTTPException(409, "Concurrent billing change; reload before retrying") from None
        return saved_receipt(db, existing)
    return saved_receipt(db, receipt)
