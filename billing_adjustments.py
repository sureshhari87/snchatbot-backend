"""Append-only credit notes/cancellations and verified refund reconciliation.

No gateway mutation, balance restoration, stock adjustment or invoice rewrite.
"""

import json
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import HTTPException
from pydantic import Field, StrictInt

from billing_b2c_models import InvoiceAdjustment
from billing_b2c_policy import approved, production_gate
from billing_document_models import BillingDocument
from billing_documents import allocate, read
from billing_profile import IST, MAX_PAISE
from billing_receipts import canonical, digest, iso, require
from billing_sales import Facts, round_ratio
from financial_provider import get, identifier
from models import utc_now

FIELDS = ("taxable_paise", "cgst_paise", "sgst_paise", "igst_paise", "total_paise")


class CreditLine(Facts):
    line_index: Annotated[StrictInt, Field(ge=0, le=109)]
    taxable_paise: Annotated[StrictInt, Field(gt=0, le=MAX_PAISE)]


class AdjustmentInput(Facts):
    request_key: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")]
    kind: Literal["credit_note", "cancellation", "refund"]
    reason: Annotated[str, Field(min_length=1, max_length=200)]
    lines: Annotated[list[CreditLine], Field(max_length=110)] = []
    provider_reference: Annotated[str, Field(pattern=r"^rfnd_[A-Za-z0-9]{1,90}$")] | None = None


def saved(row):
    require(digest(row.snapshot_json) == row.snapshot_sha256)
    return json.loads(row.snapshot_json)


def history(db, invoice_id):
    return [
        saved(r)
        for r in db.query(InvoiceAdjustment)
        .filter_by(invoice_id=invoice_id)
        .order_by(InvoiceAdjustment.created_at, InvoiceAdjustment.id)
        .all()
    ]


def create(db, invoice_id, payload, actor, provider):
    row = (
        db.query(BillingDocument)
        .filter_by(id=invoice_id, kind="sales_invoice")
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(404, "Sales invoice not found")
    fingerprint = digest(canonical(payload.model_dump(mode="json")))
    old = (
        db.query(InvoiceAdjustment)
        .filter_by(invoice_id=row.id, request_key=payload.request_key)
        .one_or_none()
    )
    if old is not None:
        require(old.request_sha256 == fingerprint)
        return saved(old)
    production_gate(db)
    rule = approved(db, "rule:credit_note", "original_component_reversal")
    original = read(db, row)["document"]
    entries = history(db, row.id)
    lines = original["lines"] + original.get("additional_charges", [])
    credited = [{k: 0 for k in FIELDS} for _ in lines]
    for entry in entries:
        if entry["kind"] != "refund":
            for line in entry["lines"]:
                for k in FIELDS:
                    credited[line["line_index"]][k] += line[k]
    now = utc_now().replace(tzinfo=timezone.utc)
    snapshot = dict(
        schema_version=1,
        document_id=uuid4().hex,
        document_type=payload.kind,
        kind=payload.kind,
        invoice_id=row.id,
        original_invoice_number=row.number,
        original_invoice_date=datetime.fromisoformat(original["issued_at"])
        .astimezone(IST)
        .date()
        .isoformat(),
        issued_at=iso(now),
        currency="INR",
        issuer=original["issuer"],
        buyer=original["buyer"],
        place_of_supply_state=original["place_of_supply_state"],
        is_test_document=original["is_test_document"],
        reason=payload.reason,
        authorized_by=original["authorized_by"],
        signature=original["signature"],
        approval=rule,
        lines=[],
        totals={k: 0 for k in FIELDS},
    )
    if payload.kind == "refund":
        require(not payload.lines and payload.provider_reference is not None)
        require(
            db.query(InvoiceAdjustment)
            .filter_by(provider_reference=payload.provider_reference)
            .first()
            is None
        )
        refund = get(provider, "refunds/" + payload.provider_reference)
        payment_id = refund.get("payment_id")
        require(identifier(payment_id, "pay"))
        # Read trusted payment linkage from the authoritative source. No client
        # amount or client payment reference is accepted as refund evidence.
        if row.source == "merchandise":
            from models import OrderSnapshot

            parent = db.get(OrderSnapshot, row.source_id)
            require(parent is not None and payment_id == parent.payment_reference)
            provider_order_id, paid_amount = (
                parent.order_reference,
                original["tender"]["cash_paise"],
            )
        else:
            from custom_design_balance import accounting
            from custom_design_models import CustomDesign

            parent = db.get(CustomDesign, row.source_id)
            require(parent is not None)
            _, advance, _, balance, _, _ = accounting(db, parent)
            matches = [
                entry
                for entry in (advance, balance)
                if entry is not None and getattr(entry, "provider_payment_id", None) == payment_id
            ]
            require(len(matches) == 1)
            provider_order_id, paid_amount = matches[0].provider_order_id, matches[0].amount_paise
        payment = get(provider, "payments/" + payment_id)
        amount = refund.get("amount")
        require(
            refund.get("id") == payload.provider_reference
            and refund.get("entity") == "refund"
            and refund.get("status") == "processed"
            and refund.get("currency") == "INR"
            and type(amount) is int
            and amount > 0
            and payment.get("id") == payment_id
            and payment.get("entity") == "payment"
            and payment.get("order_id") == provider_order_id
            and payment.get("currency") == "INR"
            and type(payment.get("amount")) is int
            and payment.get("amount") == paid_amount
        )
        credit_total = sum(e["totals"]["total_paise"] for e in entries if e["kind"] != "refund")
        refunded = sum(e["refund_paise"] for e in entries if e["kind"] == "refund")
        require(refunded + amount <= min(credit_total, original["tender"]["cash_paise"]))
        payment_refunded = sum(
            e["refund_paise"]
            for e in entries
            if e["kind"] == "refund" and e.get("payment_reference") == payment_id
        )
        require(payment_refunded + amount <= paid_amount)
        snapshot.update(
            refund_paise=amount,
            provider_reference=payload.provider_reference,
            payment_reference=payment_id,
            evidence_sha256=digest(canonical(refund)),
        )
        number = None
    else:
        require(payload.provider_reference is None)
        if any(e["kind"] == "cancellation" for e in entries):
            raise HTTPException(409, "Invoice already cancelled through a full credit note")
        requested = payload.lines
        if payload.kind == "cancellation":
            require(not requested)
            requested = [
                CreditLine(
                    line_index=i, taxable_paise=line["taxable_paise"] - credited[i]["taxable_paise"]
                )
                for i, line in enumerate(lines)
                if line["taxable_paise"] > credited[i]["taxable_paise"]
            ]
        require(bool(requested) and len({r.line_index for r in requested}) == len(requested))
        for request in requested:
            require(request.line_index < len(lines))
            line, used = lines[request.line_index], credited[request.line_index]
            taxable = line["taxable_paise"]
            remaining = taxable - used["taxable_paise"]
            require(0 < request.taxable_paise <= remaining)
            reduced = dict(
                line_index=request.line_index,
                description=line["description"],
                hsn=line["hsn"],
                gst_basis_points=line["gst_basis_points"],
                taxable_paise=request.taxable_paise,
            )
            for key in ("cgst_paise", "sgst_paise", "igst_paise"):
                # Cumulative proportional reversal avoids repeated half-paise
                # over-credit. The last return consumes the exact frozen residual.
                cumulative = round_ratio(
                    line[key] * (used["taxable_paise"] + request.taxable_paise), taxable
                )
                reduced[key] = cumulative - used[key]
            reduced["total_paise"] = sum(reduced[k] for k in FIELDS[:-1])
            snapshot["lines"].append(reduced)
            for key in FIELDS:
                snapshot["totals"][key] += reduced[key]
        number = allocate(db, now, prefix="SCN")
        snapshot["invoice_number"] = number
        snapshot["title"] = (
            "GST credit note" if payload.kind == "credit_note" else "GST credit note - cancellation"
        )
    text = canonical(snapshot)
    result = InvoiceAdjustment(
        id=snapshot["document_id"],
        invoice_id=row.id,
        request_key=payload.request_key,
        kind=payload.kind,
        number=number,
        provider_reference=payload.provider_reference,
        request_sha256=fingerprint,
        snapshot_json=text,
        snapshot_sha256=digest(text),
        actor_id=actor.id,
        created_at=now.replace(tzinfo=None),
    )
    db.add(result)
    db.flush()
    return saved(result)
