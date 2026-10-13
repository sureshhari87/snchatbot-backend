"""Immutable audit/snapshot of an explicitly confirmed inbox broadcast."""

from sqlalchemy import JSON, CheckConstraint, Column, DateTime, ForeignKey, Integer, String, Text

from database import Base


class InboxBroadcast(Base):
    __tablename__ = "inbox_broadcasts"
    __table_args__ = (
        CheckConstraint(
            "recipient_count >= 1 AND recipient_count <= 100", name="ck_broadcast_recipients"
        ),
        CheckConstraint(
            "json_array_length(recipient_ids) = recipient_count AND json_array_length(notification_ids) = recipient_count",
            name="ck_broadcast_snapshot_count",
        ),
        CheckConstraint("length(payload_sha256) = 64", name="ck_broadcast_digest"),
        CheckConstraint("channel = 'inbox'", name="ck_broadcast_inbox_only"),
    )
    id = Column(Integer, primary_key=True)
    request_key = Column(String(100), nullable=False, unique=True)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    payload_sha256 = Column(String(64), nullable=False)
    channel = Column(String(10), nullable=False)
    recipient_count = Column(Integer, nullable=False)
    recipient_ids = Column(JSON, nullable=False)
    notification_ids = Column(JSON, nullable=False)
    title = Column(String(160), nullable=False)
    body = Column(Text, nullable=False)
    target = Column(String(50), nullable=False)
    created_at = Column(DateTime, nullable=False)
