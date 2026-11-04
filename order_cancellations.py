"""Negotiated cancellations. Provider refund dispatch is separately default-off."""

import json
import os
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import update

import billing_sources
import financial_provider as reads
from models import OrderSnapshot
from order_cancellation_models import OrderCancellation, OrderCancellationEvent
from rewards_vouchers_reservation_models import FinancialReservation
from rewards_vouchers_transactions import place_hold


def enabled():
    return os.getenv("ORDER_CANCELLATIONS_ENABLED") == "1"


def require(ok, message="Cancellation changed; reload before retrying", status=409):
    if not ok:
        raise HTTPException(status, message)


def text(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def dispatched(order):
    status = order.status.strip().lower()
    return bool(order.tracking_number) or any(
        word in status for word in ("ship", "dispatch", "deliver", "return", "completed")
    )


def order_for(db, order_id, user_id=None):
    query = db.query(OrderSnapshot).filter_by(id=order_id)
    if user_id is not None:
        query = query.filter_by(user_id=user_id)
    row = query.populate_existing().with_for_update().one_or_none()
    require(row is not None, "Order not found", 404)
    return row


def current(db, order):
    row = db.query(OrderCancellation).filter_by(order_id=order.id).populate_existing().one_or_none()
    require(row is None or row.user_id == order.user_id, "Cancellation ownership requires review")
    return row


def saved(db, row):
    if row is None:
        return None
    events = (
        db.query(OrderCancellationEvent)
        .filter_by(case_id=row.id)
        .order_by(OrderCancellationEvent.version)
        .all()
    )
    return dict(
        id=row.id,
        order_id=row.order_id,
        state=row.state,
        version=row.version,
        reason=row.reason,
        cash_paise=row.cash_paise,
        refund_paise=row.refund_paise,
        proposal=json.loads(row.proposal_json) if row.proposal_json else None,
        history=[
            dict(
                version=e.version,
                action=e.action,
                detail=json.loads(e.detail_json),
                created_at=e.created_at.isoformat(),
            )
            for e in events
        ],
    )


def event(db, row, actor, action, detail):
    db.add(
        OrderCancellationEvent(
            case_id=row.id,
            version=row.version,
            actor_id=actor,
            action=action,
            detail_json=text(detail),
        )
    )
    db.flush()


def change(db, row, version, state):
    require(row.version == version)
    result = db.execute(
        update(OrderCancellation)
        .where(OrderCancellation.id == row.id, OrderCancellation.version == version)
        .values(state=state, version=version + 1),
        execution_options={"synchronize_session": False},
    )
    require(result.rowcount == 1)
    db.refresh(row)


def request(db, order, actor, reason):
    old = current(db, order)
    if old is not None:
        return old
    require(order.status not in {"cancelled", "refunded"}, "Order is already closed")
    row = OrderCancellation(
        id=uuid4().hex,
        order_id=order.id,
        user_id=order.user_id,
        state="return_review" if dispatched(order) else "requested",
        version=1,
        reason=reason,
        cash_paise=0,
        refund_paise=0,
    )
    db.add(row)
    db.flush()
    event(db, row, actor, row.state, dict(reason=reason, order_status=order.status))
    return row


def propose(db, order, row, actor, payload):
    require(
        not dispatched(order) and row.state in {"requested", "proposed", "disputed"},
        "Separate return/recall review required, or proposal already agreed",
    )
    evidence = billing_sources.evidence(db, "merchandise", order)
    cash = evidence["tender"]["cash_paise"]
    deductions = sum(item.amount_paise for item in payload.deductions)
    require(
        payload.refund_paise + deductions == cash,
        "Refund plus itemized deductions must equal verified cash paid",
        422,
    )
    change(db, row, payload.version, "proposed")
    row.cash_paise = cash
    row.refund_paise = payload.refund_paise
    row.payment_id = order.payment_reference
    row.proposal_json = text(
        dict(
            refund_paise=payload.refund_paise,
            cash_paise=cash,
            deductions=[item.model_dump() for item in payload.deductions],
            reason=payload.reason,
            original_payment_only=True,
            voucher_and_rewards_adjusted=False,
        )
    )
    db.flush()
    event(db, row, actor, "proposal", json.loads(row.proposal_json))
    return row


def decide(db, order, row, actor, payload):
    require(
        not dispatched(order) and row.state == "proposed",
        "Current proposal is unavailable for acceptance",
    )
    change(db, row, payload.version, "accepted" if payload.action == "accept" else "disputed")
    event(
        db,
        row,
        actor,
        payload.action,
        dict(reason=payload.reason, proposal=json.loads(row.proposal_json)),
    )
    return row


def live_capture(provider, order, row):
    require(order.payment_reference == row.payment_id, "Original payment binding changed")
    upstream = reads.get(provider, "orders/" + order.order_reference)
    require(
        upstream.get("id") == order.order_reference
        and upstream.get("currency") == "INR"
        and type(upstream.get("amount")) is int
        and upstream["amount"] == row.cash_paise,
        "Original provider order differs",
    )
    _, adverse = reads.captured(
        provider,
        order=upstream,
        order_id=order.order_reference,
        payment_id=row.payment_id,
        amount_paise=row.cash_paise,
    )
    require(not adverse, "Refund/dispute evidence requires separate audited review")


def prepare_refund(db, order, row, actor, payload, provider):
    require(
        os.getenv("ORDER_REFUND_EXECUTION_ENABLED") == "1", "Refund execution is not enabled", 503
    )
    require(
        row.state == "accepted" and not dispatched(order),
        "Accepted undispatched cancellation required",
    )
    require(
        not billing_sources.review(db, "merchandise", order),
        "Existing financial hold requires review",
    )
    evidence = billing_sources.evidence(db, "merchandise", order)
    require(evidence["tender"]["cash_paise"] == row.cash_paise)
    require(row.version == payload.version)
    acceptance = (
        db.query(OrderCancellationEvent)
        .filter_by(case_id=row.id, version=row.version, action="accept", actor_id=row.user_id)
        .one_or_none()
    )
    require(acceptance is not None, "Saved customer agreement requires review")
    proposal = json.loads(row.proposal_json)
    require(
        json.loads(acceptance.detail_json).get("proposal") == proposal
        and proposal["refund_paise"] == row.refund_paise
        and proposal["cash_paise"] == row.cash_paise,
        "Accepted financial terms differ",
    )
    from billing_document_models import BillingDocument

    require(
        db.query(BillingDocument.id)
        .filter_by(source="merchandise", source_id=order.id, kind="sales_invoice")
        .first()
        is None,
        "Issued sales invoice requires separate credit-note review",
    )
    live_capture(provider, order, row)
    change(db, row, payload.version, "refund_pending" if row.refund_paise else "cancelled")
    place_hold(
        db,
        kind="admin_review",
        reference="cancellation:" + row.id,
        reason="Accepted cancellation requires audited financial review",
        reward_user_id=row.user_id,
        actor_id=actor,
    )
    reservation = db.query(FinancialReservation).filter_by(order_id=order.id).one()
    import referrals
    from referral_models import ReferralCredit

    if referrals.schema_available(db):
        beneficiaries = sorted(
            {credit.user_id for credit in db.query(ReferralCredit).filter_by(order_id=order.id)}
            - {row.user_id}
        )
        for beneficiary in beneficiaries:
            place_hold(
                db,
                kind="admin_review",
                reference="cancellation:" + row.id,
                reason="Accepted cancellation requires audited financial review",
                reward_user_id=beneficiary,
                actor_id=actor,
            )
    if reservation.voucher_id:
        place_hold(
            db,
            kind="admin_review",
            reference="cancellation:" + row.id,
            reason="Accepted cancellation requires audited financial review",
            voucher_id=reservation.voucher_id,
            actor_id=actor,
        )
    event(
        db,
        row,
        actor,
        "refund_prepared",
        dict(reason=payload.reason, amount_paise=row.refund_paise, original_payment_only=True),
    )
    if not row.refund_paise:
        order.status = "cancelled"
    db.commit()  # Durable intent BEFORE the provider write; never resend blindly.
    return row


def bind_refund(db, order, row, actor, evidence):
    require(
        isinstance(evidence, dict)
        and evidence.get("entity") == "refund"
        and reads.identifier(evidence.get("id"), "rfnd")
        and evidence.get("payment_id") == row.payment_id
        and type(evidence.get("amount")) is int
        and evidence["amount"] == row.refund_paise
        and evidence.get("currency") == "INR"
        and evidence.get("receipt") == "sc_" + row.id
        and evidence.get("status") in {"pending", "processed", "failed"},
        "Refund outcome requires reconciliation",
    )
    require(row.refund_id is None or row.refund_id == evidence["id"], "Refund identity changed")
    state = {"pending": "refund_pending", "processed": "refunded", "failed": "refund_review"}[
        evidence["status"]
    ]
    if row.refund_id == evidence["id"] and row.state == state:
        return row
    require(row.state != "refunded", "Processed refund cannot be downgraded")
    change(db, row, row.version, state)
    row.refund_id = evidence["id"]
    if state == "refunded":
        order.status = "cancelled"
        order.payment_status = (
            "refunded" if row.refund_paise == row.cash_paise else "partially_refunded"
        )
    event(db, row, actor, "refund_" + evidence["status"], dict(amount_paise=row.refund_paise))
    return row


def execute(db, order, row, actor, payload, provider):
    if row.state != "accepted":
        require(
            row.state in {"refund_pending", "refunded", "cancelled", "refund_review"},
            "Customer agreement required",
        )
        return row  # A prior durable intent is reconciled; no second POST.
    prepare_refund(db, order, row, actor, payload, provider)
    if row.state == "cancelled":
        return row
    try:
        evidence = provider.create_cancellation_refund(
            row.payment_id, row.refund_paise, "sc_" + row.id
        )
        order = order_for(db, order.id)
        row = current(db, order)
        return bind_refund(db, order, row, actor, evidence)
    except Exception:
        db.rollback()
        raise HTTPException(
            503, "Refund outcome unknown; reload and reconcile before retrying"
        ) from None


def reconcile(db, order, row, actor, provider):
    require(
        row.state in {"refund_pending", "refund_review", "refunded"},
        "No refund attempt to reconcile",
    )
    require(order.payment_reference == row.payment_id)
    refunds = reads.collection(provider, "payments/" + row.payment_id + "/refunds")
    matches = [item for item in refunds if item.get("receipt") == "sc_" + row.id]
    require(len(matches) == 1, "Refund evidence uncertain; do not create another refund")
    return bind_refund(db, order, row, actor, matches[0])
