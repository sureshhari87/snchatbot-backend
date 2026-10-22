"""Server-owned inbox expiry; audit records and deduplication keys remain."""

from datetime import timedelta

from sqlalchemy import and_, or_

from config import get_bool
from models import CustomerNotification, utc_now

READ_DAYS = 45
UNREAD_DAYS = 90


def enabled():
    return get_bool("NOTIFICATION_RETENTION_ENABLED", False)


def expired_condition(now):
    return or_(
        and_(
            CustomerNotification.read_at.is_not(None),
            CustomerNotification.read_at <= now - timedelta(days=READ_DAYS),
        ),
        and_(
            CustomerNotification.read_at.is_(None),
            CustomerNotification.created_at <= now - timedelta(days=UNREAD_DAYS),
        ),
    )


def visible_condition(now=None):
    return ~expired_condition(now if now is not None else utc_now())


def preview(db, now=None):
    now = now if now is not None else utc_now()
    candidates = db.query(CustomerNotification).filter(expired_condition(now))
    read = candidates.filter(CustomerNotification.read_at.is_not(None)).count()
    unread = candidates.filter(CustomerNotification.read_at.is_(None)).count()
    return {
        "enabled": enabled(),
        "read_days": READ_DAYS,
        "unread_days": UNREAD_DAYS,
        "read_clock": "first_read_at",
        "unread_clock": "created_at",
        "checked_at": now.isoformat() + "+00:00",
        "expired_read_count": read,
        "expired_unread_count": unread,
        "expired_total_count": read + unread,
        "records_deleted": False,
        "audit_and_transaction_records_preserved": True,
    }
