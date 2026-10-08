"""Read-only reconciliation. Never repair, release, bind or post from this report."""

from savings import SavingsConflict, active_holds, bonus_basis_points, gold_credit, locked_scheme
from savings_models import (
    SavingsCheckoutAttempt,
    SavingsEntry,
    SavingsGoldRate,
    SavingsPayment,
    SavingsPaymentResolution,
)

MAX_RECORDS = 1000


def report(db, scheme_id):
    scheme = locked_scheme(db, scheme_id, allow_held=True)
    payments = (
        db.query(SavingsPayment)
        .filter_by(scheme_id=scheme.id)
        .order_by(SavingsPayment.id)
        .limit(MAX_RECORDS + 1)
        .all()
    )
    entries = (
        db.query(SavingsEntry)
        .filter_by(scheme_id=scheme.id)
        .order_by(SavingsEntry.id)
        .limit(MAX_RECORDS + 1)
        .all()
    )
    holds = active_holds(db, scheme.id).count()
    if len(payments) > MAX_RECORDS or len(entries) > MAX_RECORDS:
        raise SavingsConflict("Plan exceeds interactive reconciliation limit")
    issues = []

    def issue(code, payment_id=None):
        issues.append({"code": code, "payment_id": payment_id})

    by_payment = {entry.payment_id: entry for entry in entries if entry.kind == "credit"}
    local_ids = {payment.id for payment in payments}
    for entry in entries:
        if entry.kind == "credit" and entry.payment_id not in local_ids:
            issue("credit_without_plan_payment", entry.payment_id)
    for payment in payments:
        entry = by_payment.get(payment.id)
        if payment.user_id != scheme.user_id:
            issue("payment_owner_mismatch", payment.id)
        if payment.state == "posted":
            if entry is None:
                issue("posted_payment_without_credit", payment.id)
                continue
            if (
                entry.principal_paise != payment.amount_paise
                or entry.evidence_reference != payment.verified_reference
                or entry.created_at != payment.posted_at
                or not payment.verified_reference
            ):
                issue("posting_evidence_mismatch", payment.id)
            if scheme.kind == "monthly":
                if (
                    payment.amount_paise != scheme.monthly_paise
                    or payment.installment not in range(1, 12)
                    or entry.gold_micrograms != 0
                    or entry.bonus_micrograms != 0
                    or entry.rate_id is not None
                    or entry.bonus_bps != 0
                ):
                    issue("monthly_credit_mismatch", payment.id)
            else:
                rate = db.get(SavingsGoldRate, entry.rate_id) if entry.rate_id else None
                if (
                    rate is None
                    or rate.purity != "24K 995"
                    or not rate.effective_at <= entry.created_at < rate.valid_until
                ):
                    issue("gold_rate_snapshot_invalid", payment.id)
                else:
                    try:
                        bps = bonus_basis_points(scheme.started_at, entry.created_at)
                        gold, bonus = gold_credit(payment.amount_paise, rate.paise_per_gram, bps)
                        if (entry.gold_micrograms, entry.bonus_micrograms, entry.bonus_bps) != (
                            gold,
                            bonus,
                            bps,
                        ):
                            issue("gold_credit_mismatch", payment.id)
                    except ValueError:
                        issue("gold_credit_mismatch", payment.id)
        elif entry is not None or payment.verified_reference or payment.posted_at:
            issue("unposted_payment_has_credit_evidence", payment.id)
        if payment.state in ("cancelled", "rejected"):
            resolution = db.get(SavingsPaymentResolution, payment.id)
            if (
                resolution is None
                or resolution.kind != payment.state
                or payment.installment is not None
                or payment.provider_order_id
                or db.get(SavingsCheckoutAttempt, payment.id)
            ):
                issue("closed_request_evidence_mismatch", payment.id)
        if payment.mode == "razorpay":
            attempt = db.get(SavingsCheckoutAttempt, payment.id)
            if payment.provider_order_id and (attempt is None or attempt.state != "ready"):
                issue("checkout_binding_mismatch", payment.id)
            if attempt and attempt.state == "ready" and not payment.provider_order_id:
                issue("checkout_binding_mismatch", payment.id)
            if attempt and attempt.state in ("creating", "unknown"):
                issue("checkout_requires_recovery", payment.id)
        elif payment.provider_order_id or db.get(SavingsCheckoutAttempt, payment.id):
            issue("manual_request_has_checkout", payment.id)
    totals = {
        key: sum(getattr(entry, key) for entry in entries)
        for key in ("principal_paise", "gold_micrograms", "bonus_micrograms")
    }
    redemptions = [entry for entry in entries if entry.kind == "redemption"]
    if scheme.state == "redeemed":
        if len(redemptions) != 1 or any(totals.values()):
            issue("redemption_balance_mismatch")
        elif redemptions[0].created_at < scheme.matures_at or redemptions[0].store_bonus_paise != (
            scheme.monthly_paise if scheme.kind == "monthly" else 0
        ):
            issue("redemption_terms_mismatch")
        if scheme.kind == "monthly" and sum(p.state == "posted" for p in payments) != 11:
            issue("redemption_installments_mismatch")
    elif redemptions or any(value < 0 for value in totals.values()):
        issue("active_plan_balance_mismatch")
    return {
        "scheme_id": scheme.id,
        "scheme_state": scheme.state,
        "review_required": holds > 0,
        "hold_count": holds,
        "payment_count": len(payments),
        "entry_count": len(entries),
        "balances": totals,
        "issues": issues,
        "consistent": not issues,
        "provider_verified": False,
        "release_authorized": False,
    }
