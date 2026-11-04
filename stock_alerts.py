"""Transactional inbox restock alerts. No email, SMS or push is sent here."""

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from catalogue_visibility import is_public_product
from models import BackInStockSubscription, CustomerNotification, utc_now


def available(product):
    return is_public_product(product) and product.in_stock and product.stock_quantity > 0


def notify_subscribers(db, product):
    """Caller holds the product lock and commits inventory, inbox and state together.

    Product-level stock does not certify a requested size or variant. A fresh
    subscription is needed for a later restock cycle after this one is notified.
    """
    if not available(product):
        return 0
    db.flush()
    rows = (
        db.query(BackInStockSubscription)
        .filter_by(product_id=product.id, status="active")
        .order_by(BackInStockSubscription.id)
        .with_for_update()
        .populate_existing()
        .all()
    )
    dialect = db.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise ValueError("Unsupported notification database")
    insert = pg_insert if dialect == "postgresql" else sqlite_insert
    for row in rows:
        now = utc_now()
        requested = ", ".join(str(value)[:80] for value in (row.size, row.variant) if value)
        detail = f" Check your requested option ({requested}) before ordering." if requested else ""
        db.execute(
            insert(CustomerNotification)
            .values(
                user_id=row.user_id,
                created_by=None,
                deduplication_key=f"stock-alert:{row.id}",
                title="Back in stock",
                body=f"{product.name[:200]} is available again.{detail} "
                "Open Stock alerts to view it. Stock and price may change; "
                "this alert does not reserve an item.",
                target="/stock-alerts",
                created_at=now,
            )
            .on_conflict_do_nothing(index_elements=["user_id", "deduplication_key"])
        )
        row.status = "notified"
        row.notified_at = now
        row.updated_at = now
    return len(rows)
