"""Explicit provider-verified release; never reverse principal, gold or bonuses."""

import hashlib
import json
import re

from savings import (
    SavingsConflict,
    audit,
    latest_hold_decision,
    locked_scheme,
    text,
    utc,
)
from savings_checkout import CheckoutUnavailable
from savings_models import SavingsHold, SavingsHoldDecision, SavingsPayment
from savings_reconciliation import report


def provider_get(provider, path):
    try:
        status, data = provider.call_razorpay("GET", path)
    except Exception:
        raise CheckoutUnavailable("Provider review verification unavailable") from None
    if status != 200 or not isinstance(data, dict):
        raise CheckoutUnavailable("Provider review verification unavailable")
    return data


def identifier(value, prefix):
    if not isinstance(value, str) or not re.fullmatch(prefix + r"[A-Za-z0-9_]{1,90}", value):
        raise SavingsConflict("Invalid provider review identifier")
    return value


def collection(provider, path, prefix):
    result, seen = [], set()
    skip = 0
    for _ in range(20):
        page = provider_get(provider, path + f"?count=100&skip={skip}")
        items = page.get("items")
        if (
            page.get("entity") != "collection"
            or not isinstance(items, list)
            or type(page.get("count")) is not int
            or page["count"] != len(items)
            or len(items) > 100
        ):
            raise SavingsConflict("Incomplete provider review collection")
        for item in items:
            if not isinstance(item, dict):
                raise SavingsConflict("Invalid provider review collection")
            item_id = identifier(item.get("id"), prefix)
            if item_id in seen:
                raise SavingsConflict("Provider pagination did not advance")
            seen.add(item_id)
            result.append(item)
        if not items:
            return result
        skip += len(items)
        if skip > 1000:
            break
    raise SavingsConflict("Provider review exceeds interactive verification limit")


def matched_entity(data, kind, reference, payment):
    if (
        data.get("entity") != kind
        or data.get("id") != reference
        or data.get("payment_id") != payment.verified_reference
        or data.get("currency") != "INR"
        or type(data.get("amount")) is not int
        or not 0 < data["amount"] <= payment.amount_paise
    ):
        raise SavingsConflict("Provider review does not match posted savings payment")


def fresh_proof(db, hold, provider):
    payment = db.get(SavingsPayment, hold.payment_id) if hold.payment_id else None
    if (
        hold.kind not in ("refund", "dispute")
        or payment is None
        or payment.scheme_id != hold.scheme_id
        or payment.mode != "razorpay"
        or payment.state != "posted"
        or not payment.provider_order_id
        or not payment.verified_reference
    ):
        raise SavingsConflict(
            "This review needs an approved manual or uncertain-payment evidence policy"
        )
    provider_id = identifier(payment.verified_reference, "pay_")
    order_id = identifier(payment.provider_order_id, "order_")
    current = provider_get(provider, "payments/" + provider_id)
    if (
        current.get("id") != provider_id
        or current.get("entity") != "payment"
        or current.get("order_id") != order_id
        or current.get("status") != "captured"
        or current.get("captured") is not True
        or current.get("currency") != "INR"
        or type(current.get("amount")) is not int
        or current["amount"] != payment.amount_paise
        or type(current.get("amount_refunded")) is not int
        or current["amount_refunded"] != 0
        or current.get("refund_status") is not None
    ):
        raise SavingsConflict("Refunded or uncertain payment cannot be released")
    summaries = [("payment", provider_id, current["amount"], 0)]
    refunds = collection(provider, f"payments/{provider_id}/refunds", "rfnd_")
    refund_ids = set()
    for item in refunds:
        reference = item["id"]
        fresh = provider_get(provider, "refunds/" + reference)
        matched_entity(fresh, "refund", reference, payment)
        if fresh.get("status") != "failed":
            raise SavingsConflict("Pending or completed refund prevents release")
        refund_ids.add(reference)
        summaries.append(("refund", reference, "failed", fresh["amount"]))
    # Scan the bounded merchant dispute collection, not merely known webhook IDs.
    disputes = collection(provider, "disputes", "disp_")
    dispute_ids = set()
    for item in disputes:
        identifier(item.get("payment_id"), "pay_")
        if item["payment_id"] != provider_id:
            continue
        reference = item["id"]
        fresh = provider_get(provider, "disputes/" + reference)
        matched_entity(fresh, "dispute", reference, payment)
        if (
            fresh.get("status") != "won"
            or type(fresh.get("amount_deducted")) is not int
            or fresh["amount_deducted"] != 0
        ):
            raise SavingsConflict("Active, lost or uncertain dispute prevents release")
        dispute_ids.add(reference)
        summaries.append(("dispute", reference, "won", fresh["amount"], 0))
    prefix = "rfnd_" if hold.kind == "refund" else "disp_"
    reference = identifier(hold.reference, prefix)
    known = refund_ids if hold.kind == "refund" else dispute_ids
    if reference not in known:
        raise SavingsConflict("Hold reference is missing from fresh provider collection")
    # Recheck payment after all collection/entity fetches; never trust a UI assertion.
    final = provider_get(provider, "payments/" + provider_id)
    keys = (
        "id",
        "entity",
        "order_id",
        "status",
        "captured",
        "currency",
        "amount",
        "amount_refunded",
        "refund_status",
    )
    if any(
        (type(final.get(key)), final.get(key)) != (type(current.get(key)), current.get(key))
        for key in keys
    ):
        raise SavingsConflict("Provider payment changed during review")
    digest = hashlib.sha256(
        json.dumps(sorted(summaries), separators=(",", ":")).encode()
    ).hexdigest()
    return digest


def release(db, hold_id, admin, reason, request_key, provider, now):
    if not admin.is_admin:
        raise PermissionError("Admin review required")
    reason, key = text(reason), text(request_key, 80)
    hold = db.get(SavingsHold, hold_id)
    if hold is None:
        raise SavingsConflict("Review hold unavailable")
    scheme = locked_scheme(db, hold.scheme_id, allow_held=True)
    latest = latest_hold_decision(db, hold.id)
    event_key = "release:" + key
    existing = db.query(SavingsHoldDecision).filter_by(event_key=event_key).first()
    if existing:
        if (
            existing.hold_id != hold.id
            or existing.reason != reason
            or existing.actor_id != admin.id
            or existing.action != "released"
            or latest is None
            or latest.id != existing.id
        ):
            raise SavingsConflict("Release key reused or case reopened; review again")
        return existing
    if latest and latest.action == "released":
        raise SavingsConflict("Hold already released under a different review")
    if not report(db, scheme.id)["consistent"]:
        raise SavingsConflict("Reconcile ledger inconsistencies before release")
    digest = fresh_proof(db, hold, provider)
    observed_at = utc(now() if callable(now) else now)
    if observed_at < hold.created_at or (latest and observed_at < latest.created_at):
        raise SavingsConflict("Review precedes existing evidence")
    row = SavingsHoldDecision(
        hold_id=hold.id,
        action="released",
        event_key=event_key,
        reason=reason,
        actor_id=admin.id,
        evidence_sha256=digest,
        created_at=observed_at,
    )
    db.add(row)
    audit(db, scheme, event_key, "review_hold_released", admin.id, hold.reference, observed_at)
    db.flush()
    return row
