"""Durable push foundation. No route, scheduler or real provider is installed."""

from datetime import timedelta, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint

from database import Base
from models import NotificationSettings, User, utc_now
from push_delivery_policy import ALLOWED_KINDS, evaluate_delivery
from push_devices import PushDevice, eligible_devices, lock_owner


class PushEvent(Base):
    __tablename__ = "push_events"
    id = Column(Integer, primary_key=True)
    event_key = Column(String(160), nullable=False, unique=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    kind = Column(String(30), nullable=False)
    created_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False)


class PushAttempt(Base):
    __tablename__ = "push_attempts"
    __table_args__ = (UniqueConstraint("event_id", "family_id", name="uq_push_event_family"),)
    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey("push_events.id"), nullable=False)
    family_id = Column(String(100), nullable=False)
    token_digest = Column(String(64), nullable=False)
    state = Column(String(20), nullable=False, default="pending")
    attempts = Column(Integer, nullable=False, default=0)
    due_at = Column(DateTime, nullable=False, index=True)
    reason = Column(String(40), nullable=True)


def enqueue(db, *, user_id, event_key, kind, enabled=False):
    """Trusted producer only; caller commits with the business event.

    Snapshot devices once. Duplicate events never add a later login.
    No history scanning and no raw tokens stored in the queue.
    """
    if not enabled:
        return None
    if (
        kind not in ALLOWED_KINDS
        or not isinstance(event_key, str)
        or not 1 <= len(event_key) <= 160
    ):
        raise ValueError("Invalid push event")
    if lock_owner(db, user_id) is None:
        raise ValueError("Unknown recipient")
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise ValueError("Unsupported queue database")
    now = utc_now()
    inserted = db.execute(
        insert(PushEvent)
        .values(
            event_key=event_key,
            user_id=user_id,
            kind=kind,
            created_at=now,
            expires_at=now + timedelta(hours=24),
        )
        .on_conflict_do_nothing(index_elements=["event_key"])
        .returning(PushEvent.id)
    ).scalar_one_or_none()
    if inserted is None:
        previous = db.query(PushEvent).filter_by(event_key=event_key).one()
        if previous.user_id != user_id or previous.kind != kind:
            raise ValueError("Push event conflict")
        return previous.id
    for device in eligible_devices(db, user_id):
        db.add(
            PushAttempt(
                event_id=inserted,
                family_id=device.family_id,
                token_digest=device.token_digest,
                due_at=now,
                state="pending",
                attempts=0,
            )
        )
    db.flush()
    return inserted


class RetryableDeliveryError(Exception):
    """Adapter must bound network timeouts; do not log tokens."""

    def __init__(self, message, *, retry_after=0):
        super().__init__(message)
        self.retry_after = max(0, retry_after)


class InvalidDeviceError(Exception):
    """Provider definitively reports this specific token is unregistered."""


class PermanentDeliveryError(Exception):
    """Non-retryable provider rejection."""


class DeliveryUnavailableError(Exception):
    """Disabled/misconfigured provider: retain queue, stop this batch."""


def deliver_one(factory, attempt_id, sender, *, enabled=False, allowed_user_ids=None):
    """Own transaction with an injected bounded sender.

    Lock owner BEFORE queue row, same order as logout/registration. Keep owner
    locked through bounded send so registration cannot replace the token. A
    crash after provider acceptance may redeliver: NOT exactly-once delivery.
    """
    if not enabled:
        return "disabled"
    with factory() as db, db.begin():
        recipient = (
            db.query(PushEvent.user_id)
            .join(PushAttempt)
            .filter(PushAttempt.id == attempt_id)
            .scalar()
        )
        if recipient is None:
            return "missing"
        if allowed_user_ids is not None and recipient not in allowed_user_ids:
            return "recipient_not_allowed"
        owner = (
            db.query(User)
            .filter_by(id=recipient)
            .with_for_update(key_share=True, skip_locked=True)
            .first()
        )
        if owner is None:
            return "busy"
        row = (
            db.query(PushAttempt).filter_by(id=attempt_id).with_for_update(skip_locked=True).first()
        )
        if row is None:
            return "busy"
        now = utc_now()
        if row.state != "pending" or row.due_at > now:
            return "not_due"
        event = db.get(PushEvent, row.event_id)
        if event.expires_at <= now or not owner.is_verified:
            row.state, row.reason = "cancelled", "expired_or_unverified"
            return row.state
        device = next(
            (
                d
                for d in eligible_devices(db, recipient)
                if d.family_id == row.family_id and d.token_digest == row.token_digest
            ),
            None,
        )
        if device is None:
            row.state, row.reason = "cancelled", "session_or_token_changed"
            return row.state
        preferences = db.query(NotificationSettings).filter_by(user_id=recipient).first()
        decision = evaluate_delivery(
            kind=event.kind,
            recipient_id=recipient,
            preferences=preferences,
            now=now.replace(tzinfo=timezone.utc),
            enabled=True,
        )
        if not decision.allowed:
            row.reason = decision.reason
            if decision.retry_at is not None:
                row.due_at = decision.retry_at.replace(tzinfo=None)
                return "deferred"
            row.state = "cancelled"
            return row.state
        row.attempts += 1
        # No order reference, amount, address or inbox text in OS payload.
        payload = {"user_id": str(recipient), "event_id": str(event.id), "type": event.kind}
        try:
            sender(device.push_token, payload)
        except DeliveryUnavailableError:
            row.attempts -= 1
            row.reason = "provider_unavailable"
            row.due_at = now + timedelta(minutes=5)
            return "provider_unavailable"
        except InvalidDeviceError:
            db.query(PushDevice).filter_by(
                id=device.id, user_id=recipient, token_digest=row.token_digest
            ).delete(synchronize_session=False)
            row.state, row.reason = "cancelled", "invalid_device"
        except PermanentDeliveryError:
            row.state, row.reason = "failed", "provider_rejected"
        except RetryableDeliveryError as exc:
            row.reason = "provider_retry"
            if row.attempts >= 5:
                row.state = "failed"
            else:
                delay = max(30 * 2 ** (row.attempts - 1), exc.retry_after)
                row.due_at = min(event.expires_at, now + timedelta(seconds=min(delay, 86400)))
        except Exception:
            # Persist no exception text; ambiguous transport failures retry.
            row.reason = "provider_retry"
            if row.attempts >= 5:
                row.state = "failed"
            else:
                row.due_at = now + timedelta(seconds=30 * 2 ** (row.attempts - 1))
        else:
            row.state, row.reason = "sent", "provider_accepted"
        return row.state


def run_batch(factory, sender, *, enabled=False, limit=25, allowed_user_ids=None):
    if not enabled:
        return []
    if not 1 <= limit <= 100:
        raise ValueError("Invalid batch limit")
    with factory() as db:
        query = db.query(PushAttempt.id).join(PushEvent)
        if allowed_user_ids is not None:
            query = query.filter(PushEvent.user_id.in_(allowed_user_ids))
        ids = [
            row.id
            for row in query
            .filter(PushAttempt.state == "pending", PushAttempt.due_at <= utc_now())
            .order_by(PushAttempt.due_at, PushAttempt.id)
            .limit(limit)
            .all()
        ]
    outcomes = []
    for key in ids:
        outcome = deliver_one(
            factory, key, sender, enabled=True, allowed_user_ids=allowed_user_ids
        )
        outcomes.append(outcome)
        if outcome == "provider_unavailable":
            break
    return outcomes
