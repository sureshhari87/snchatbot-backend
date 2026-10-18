"""Read-only purchase eligibility. Never trusts client claims or calls providers."""

from sqlalchemy import or_

from models import OrderSnapshot, OrderSnapshotItem
from rewards_vouchers_models import RewardVoucherHold
from rewards_vouchers_reservation_models import FinancialReservation, FinancialReservationEvent


def eligible_orders(db, user_id, product_id):
    # Account/voucher holds are conservative: no badge while evidence is under review.
    query = (
        db.query(OrderSnapshot.id)
        .join(OrderSnapshotItem, OrderSnapshotItem.order_id == OrderSnapshot.id)
        .join(FinancialReservation, FinancialReservation.order_id == OrderSnapshot.id)
        .join(
            FinancialReservationEvent,
            FinancialReservationEvent.reservation_id == FinancialReservation.id,
        )
        .filter(
            OrderSnapshot.user_id == user_id,
            OrderSnapshot.status.in_(["delivered", "completed"]),
            OrderSnapshot.payment_status == "verified",
            OrderSnapshot.source == "razorpay_checkout",
            OrderSnapshotItem.backend_product_id == product_id,
            OrderSnapshotItem.qty > 0,
            FinancialReservation.user_id == user_id,
            FinancialReservation.state == "posted",
            FinancialReservation.provider_payment_id == OrderSnapshot.payment_reference,
            FinancialReservationEvent.action == "payment_posted",
            ~db.query(RewardVoucherHold.id)
            .filter(
                or_(
                    RewardVoucherHold.reward_user_id == FinancialReservation.user_id,
                    RewardVoucherHold.voucher_id == FinancialReservation.voucher_id,
                )
            )
            .exists(),
        )
    )
    return query


def eligible_order(db, user_id, product_id, order_id=None):
    query = eligible_orders(db, user_id, product_id)
    if order_id is not None:
        query = query.filter(OrderSnapshot.id == order_id)
    row = query.order_by(OrderSnapshot.id).first()
    return row[0] if row else None


def verified(db, review):
    return (
        review.verified_order_id is not None
        and eligible_order(db, review.user_id, review.product_id, review.verified_order_id)
        is not None
    )


def verified_expression(db, review_model):
    """Correlated predicate applied before sorting and pagination."""
    return (
        eligible_orders(db, review_model.user_id, review_model.product_id)
        .filter(OrderSnapshot.id == review_model.verified_order_id)
        .correlate(review_model)
        .exists()
    )


def verified_ids(db, reviews):
    """Bounded batch check for public/admin pages; no query per review."""
    from models import ProductReview

    ids = [review.id for review in reviews if review.verified_order_id is not None]
    if not ids:
        return set()
    return {
        row[0]
        for row in db.query(ProductReview.id)
        .filter(ProductReview.id.in_(ids), verified_expression(db, ProductReview))
        .all()
    }
