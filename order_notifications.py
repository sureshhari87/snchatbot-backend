"""Transactional admin order-status inbox messages; no payment assertions."""

from models import CustomerNotification
from push_controls import outbox_enabled
from push_devices import lock_owner
from push_outbox import enqueue


def enqueue_verified_payment(db, order):
    """Called only after trusted payment/inventory finalization, in its transaction.

    One confirmation per server order, even across different webhook IDs.
    Database conflict handling makes notification insertion race-safe.
    """
    if order.payment_status != "verified" or not order.payment_reference:
        raise ValueError("Payment notification requires verified payment")
    push_enabled = outbox_enabled()
    if push_enabled:
        lock_owner(db, order.user_id)
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise ValueError("Unsupported notification database")
    statement = (
        insert(CustomerNotification)
        .values(
            user_id=order.user_id,
            created_by=None,
            deduplication_key=f"payment-verified:{order.id}",
            title="Payment confirmed",
            body=f"Payment for order {order.order_reference[:120]} has been verified. "
            "Check your orders for details.",
            target="",
        )
        .on_conflict_do_nothing(index_elements=["user_id", "deduplication_key"])
        .returning(CustomerNotification.id)
    )
    inserted = db.execute(statement).scalar_one_or_none()
    if inserted is not None:
        enqueue(
            db,
            user_id=order.user_id,
            event_key=f"payment-verified:{order.id}",
            kind="verified_payment",
            enabled=push_enabled,
        )


STATUS_LABELS = {
    "processing": "being processed",
    "shipped": "shipped",
    "delivered": "delivered",
    "cancelled": "cancelled",
}


def enqueue_admin_order_status(db, order, previous_status, admin_id):
    """Caller must lock the order row and commit this with its status update.

    Send at most once per order/status, including repeated admin requests.
    Address/tracking edits and payment status changes do not generate messages.
    """
    if order.status == previous_status or order.status not in STATUS_LABELS:
        return
    push_enabled = outbox_enabled()
    if push_enabled:
        lock_owner(db, order.user_id)
    key = f"order-status:{order.id}:{order.status}"
    if (
        db.query(CustomerNotification.id)
        .filter_by(user_id=order.user_id, deduplication_key=key)
        .first()
    ):
        return
    db.add(
        CustomerNotification(
            user_id=order.user_id,
            created_by=admin_id,
            deduplication_key=key,
            title="Order status update",
            body=f"Order {order.order_reference[:120]} is now {STATUS_LABELS[order.status]}. "
            "Check your order details for more information.",
            target="",
        )
    )
    enqueue(db, user_id=order.user_id, event_key=key, kind="order_status", enabled=push_enabled)
