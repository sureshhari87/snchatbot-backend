"""Recorded ready-for-removal event, before physical dispatch/collection."""

import os
from datetime import timezone
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field

import billing_sources as sources
from billing_b2c_models import BillingSupplyEvent
from billing_b2c_policy import decision, enabled
from billing_profile import IST
from billing_receipts import canonical, digest, require
from billing_sales import Facts
from models import utc_now


class SupplyInput(Facts):
    event: Literal["ready_for_dispatch", "ready_for_collection"]
    reference: Annotated[str, Field(min_length=1, max_length=200)]


def record(db, source, identity, payload, actor):
    require(source in {"merchandise", "custom_design"})
    parent = sources.root(db, source, identity)
    require(not sources.review(db, source, parent))
    sources.evidence(db, source, parent)
    now = utc_now()
    today = now.replace(tzinfo=timezone.utc).astimezone(IST).date().isoformat()
    old = (
        db.query(BillingSupplyEvent)
        .filter_by(source=source, source_id=identity)
        .order_by(BillingSupplyEvent.created_at.desc(), BillingSupplyEvent.id.desc())
        .first()
    )
    if old is not None:
        saved = decision(old)
        if saved["date"] == today:
            require(saved["event"] == payload.event and saved["reference"] == payload.reference)
            return saved
    if source == "merchandise":
        require(parent.status in {"placed", "confirmed", "processing", "packed", "ready_to_ship"})
    else:
        from custom_design_models import CustomDesignAudit

        require(
            db.query(CustomDesignAudit)
            .filter_by(design_id=parent.id, action="completed")
            .one_or_none()
            is not None
        )
    now = utc_now()
    saved = dict(
        id=uuid4().hex,
        source=source,
        source_id=identity,
        event=payload.event,
        reference=payload.reference,
        date=now.replace(tzinfo=timezone.utc).astimezone(IST).date().isoformat(),
    )
    text = canonical(saved)
    db.add(
        BillingSupplyEvent(
            id=saved["id"],
            source=source,
            source_id=identity,
            snapshot_json=text,
            snapshot_sha256=digest(text),
            actor_id=actor.id,
            created_at=now,
        )
    )
    db.flush()
    return saved


def validate(db, source, parent, payload):
    saved = decision(
        db.query(BillingSupplyEvent)
        .filter_by(source=source, source_id=parent.id)
        .order_by(BillingSupplyEvent.created_at.desc(), BillingSupplyEvent.id.desc())
        .first()
    )
    today = utc_now().replace(tzinfo=timezone.utc).astimezone(IST).date().isoformat()
    require(saved["date"] == today == payload.supply_date.isoformat())
    require(saved["reference"] == payload.supply_reference)
    if source == "merchandise":
        require(parent.status in {"placed", "confirmed", "processing", "packed", "ready_to_ship"})
    return saved["event"]


def dispatch_gate(db, order, new_status):
    if (
        enabled()
        and os.getenv("BILLING_SALES_ENABLED", "0") == "1"
        and new_status in {"shipped", "delivered", "completed"}
    ):
        from billing_adjustments import history
        from billing_document_models import BillingDocument

        row = (
            db.query(BillingDocument)
            .filter_by(source="merchandise", source_id=order.id, kind="sales_invoice")
            .one_or_none()
        )
        require(row is not None)
        require(not any(e["kind"] == "cancellation" for e in history(db, row.id)))
