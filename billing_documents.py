"""Immutable server billing documents; no posting, refunds, email or provider calls."""

import json
import os
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

import billing_sources as sources
import config
from billing_document_models import BillingDocument, BillingDocumentAudit, BillingSeries
from billing_profile import IST, clean_text, financial_year, invoice_number
from billing_receipts import canonical, digest, iso, issuer, require
from billing_sales import calculate, calculate_charges, einvoice_gate
from customer_profile import receipt_customer
from models import OrderSnapshotItem, User, utc_now

PERMISSIONS = {
    "voucher": "vouchers:manage",
    "custom_advance": "leads:manage",
    "custom_balance": "leads:manage",
    "custom_design": "leads:manage",
    "merchandise": "catalog:manage",
}


def owned(db, identity, user_id=None):
    query = db.query(BillingDocument).filter_by(id=identity)
    if user_id is not None:
        query = query.filter_by(user_id=user_id)
    row = query.one_or_none()
    if row is None:
        raise HTTPException(404, "Billing document not found")
    return row


def read(db, row):
    require(digest(row.snapshot_json) == row.snapshot_sha256)
    audit = db.query(BillingDocumentAudit).filter_by(document_id=row.id).one_or_none()
    require(audit is not None and audit.snapshot_sha256 == row.snapshot_sha256)
    try:
        snapshot = json.loads(row.snapshot_json)
    except (ValueError, TypeError):
        raise HTTPException(409, "Saved document requires billing review") from None
    require(
        isinstance(snapshot, dict)
        and snapshot.get("document_id") == row.id
        and snapshot.get("source") == row.source
        and snapshot.get("source_id") == row.source_id
    )
    parent = sources.root(db, row.source, row.source_id, row.user_id)
    return dict(document=snapshot, review_required=sources.review(db, row.source, parent))


def existing(db, source, identity, kind):
    return (
        db.query(BillingDocument)
        .filter_by(source=source, source_id=identity, kind=kind)
        .one_or_none()
    )


def store(db, parent, source, kind, snapshot, actor_id, reason, request_hash, number=None):
    text = canonical(snapshot)
    fingerprint = digest(text)
    row = BillingDocument(
        id=snapshot["document_id"],
        user_id=sources.owner(parent, source),
        source=source,
        source_id=parent.id,
        kind=kind,
        number=number,
        request_sha256=request_hash,
        snapshot_json=text,
        snapshot_sha256=fingerprint,
        created_at=datetime.fromisoformat(snapshot["issued_at"])
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
        if kind == "sales_invoice"
        else utc_now(),
    )
    db.add(row)
    db.flush()
    db.add(
        BillingDocumentAudit(
            document_id=row.id,
            actor_id=actor_id,
            reason=reason,
            snapshot_sha256=fingerprint,
            created_at=row.created_at,
        )
    )
    db.flush()
    return read(db, row)


def header(db, parent, source, title, identity):
    customer = db.get(User, sources.owner(parent, source))
    require(customer is not None)
    return dict(
        schema_version=1,
        currency="INR",
        document_id=identity,
        source=source,
        source_id=parent.id,
        title=title,
        is_test_document=config.APP_ENV != "production",
        issued_at=iso(utc_now()),
        issuer=issuer(),
        customer=receipt_customer(db, customer),
    )


def issue_receipt(db, source, identity, user_id):
    if source not in {"voucher", "custom_advance", "custom_balance", "merchandise"}:
        raise HTTPException(404, "Payment source not found")
    parent = sources.root(db, source, identity, user_id)
    previous = existing(db, source, identity, "receipt")
    if previous is not None:
        return read(db, previous)
    facts = sources.evidence(db, source, parent)
    names = {
        "voucher": "Gift voucher funding acknowledgement",
        "custom_advance": "Custom-design advance acknowledgement",
        "custom_balance": "Custom-design balance acknowledgement",
        "merchandise": "Merchandise payment acknowledgement",
    }
    snapshot = header(db, parent, source, names[source], uuid4().hex)
    snapshot.update(
        document_type="payment_acknowledgement",
        payment=facts["payment"],
        detail=facts["detail"],
        tender=facts["tender"],
        notice="This acknowledges an earlier verified payment. It is not a sales tax invoice. Review, redemption and supply terms remain separate.",
    )
    return store(
        db,
        parent,
        source,
        "receipt",
        snapshot,
        user_id,
        "Owner requested posted-payment acknowledgement",
        digest(canonical([source, identity])),
    )


def allocate(db, issued_at, prefix="SNJ"):
    setting = "BILLING_CREDIT_SERIES" if prefix == "SCN" else "BILLING_SALES_SERIES"
    require(os.getenv(setting + "_START_CONFIRMED", "0") == "1")
    start, end = financial_year(issued_at)
    key = f"{prefix}-{start}-{end}"
    require(os.getenv(setting + "_CONFIRMED_FY", "") == f"{start}-{end}")
    row = (
        db.query(BillingSeries)
        .filter_by(key=key)
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if row is None:
        try:
            with db.begin_nested():
                db.add(BillingSeries(key=key, next_sequence=1))
                db.flush()
        except IntegrityError:
            pass
        row = db.query(BillingSeries).filter_by(key=key).populate_existing().with_for_update().one()
    try:
        value = invoice_number(prefix=prefix, issued_at=issued_at, sequence=row.next_sequence)
    except ValueError:
        raise HTTPException(
            409, "Document series is exhausted; an approved series review is required"
        ) from None
    row.next_sequence += 1
    db.flush()
    return value


def sales_preview(db, source, identity, payload):
    if source not in {"merchandise", "custom_design"}:
        raise HTTPException(404, "Sales source not found")
    parent = sources.root(db, source, identity)
    require(not sources.review(db, source, parent))
    facts = sources.evidence(db, source, parent)
    from billing_b2c_policy import enabled as b2c_enabled

    if b2c_enabled():
        from billing_supply import validate as validate_supply

        supply_state = validate_supply(db, source, parent, payload)
    elif source == "merchandise":
        require(parent.status in {"shipped", "delivered", "completed"})
        supply_state = parent.status
    else:
        from custom_design_models import CustomDesignAudit

        completed = (
            db.query(CustomDesignAudit)
            .filter_by(design_id=parent.id, action="completed")
            .one_or_none()
        )
        require(completed is not None and completed.quote_id == facts["detail"]["quote_id"])
        supply_state = "completed"
    shop = issuer()
    now = utc_now().replace(tzinfo=timezone.utc)
    if payload.supply_date > now.astimezone(IST).date():
        raise HTTPException(422, "Supply date cannot be in the future")
    # Admin supplies final item facts, not a new total or payment status.
    if source == "merchandise":
        items = db.query(OrderSnapshotItem).filter_by(order_id=parent.id).all()
        expected = {item.id: (item.qty, item.name) for item in items}
        actual = {line.source_item_id: (line.quantity, line.description) for line in payload.lines}
        require(len(actual) == len(payload.lines) and actual == expected and bool(expected))
    else:
        require(all(line.source_item_id == 0 for line in payload.lines))
    all_huids = [huid for line in payload.lines for huid in line.huids]
    if len(set(all_huids)) != len(all_huids):
        raise HTTPException(422, "A final HUID cannot appear on multiple invoice items")
    from billing_b2c_policy import validate

    approvals = validate(db, source, parent, payload, facts)
    lines, totals = calculate(payload.lines, shop["state_code"], payload.place_of_supply_state)
    charges = calculate_charges(
        payload.additional_charges, shop["state_code"], payload.place_of_supply_state
    )
    for field in totals:
        totals[field] += sum(charge[field] for charge in charges)
    require(totals["total_paise"] == facts["gross_paise"])
    import purchase_identity

    if (
        purchase_identity.enabled() or purchase_identity.strict_runtime()
    ) and purchase_identity.amount_requires_identity(facts["gross_paise"]):
        if source == "merchandise":
            from rewards_vouchers_reservation_models import FinancialReservation

            reservation = db.query(FinancialReservation).filter_by(order_id=parent.id).first()
            identity_kind = "merchandise"
            identity_key = (
                "reservation:" + str(reservation.id) if reservation else "order:" + str(parent.id)
            )
        else:
            identity_kind = "custom_quote"
            identity_key = str(facts["detail"]["quote_id"])
        purchase_identity.require_bound(
            db,
            parent.user_id,
            facts["gross_paise"],
            resource_kind=identity_kind,
            resource_key=identity_key,
            legal_name=payload.buyer.name,
            buyer_kind=payload.buyer.kind,
            gstin=payload.buyer.gstin,
        )
    if (
        facts["tender"]["rewards_paise"]
        and os.getenv("BILLING_REWARD_TAX_TREATMENT", "unconfirmed") != "tender"
    ):
        raise HTTPException(409, "Reward redemption tax treatment requires accountant review")
    e_invoice = einvoice_gate(payload.buyer)
    reference = os.getenv("BILLING_TAX_RULES_REVIEW_REFERENCE", "")
    if not reference.strip():
        raise HTTPException(409, "Approved transaction tax rules require billing review")
    signatory = os.getenv("BILLING_SIGNATORY_NAME", "")
    if not signatory.strip():
        raise HTTPException(409, "Authorized invoice signatory is not configured")
    return parent, dict(
        issuer=shop,
        buyer=payload.buyer.model_dump(mode="json"),
        place_of_supply_state=payload.place_of_supply_state,
        lines=lines,
        additional_charges=charges,
        totals=totals,
        tender=facts["tender"],
        source_detail=facts["detail"],
        source_supply_state=supply_state,
        supply_date=payload.supply_date.isoformat(),
        supply_reference=payload.supply_reference,
        reverse_charge=False,
        tax_approvals=approvals,
        tax_review_reference=clean_text(reference, limit=200),
        authorized_by=clean_text(signatory, limit=200),
        signature=dict(
            status="electronic_signing_not_configured",
            electronically_signed=False,
            production_issuance_available=False,
        ),
        einvoice=e_invoice,
        payment_status="fully_paid",
    )


def issue_sales(db, source, identity, payload, admin):
    if os.getenv("BILLING_SALES_ENABLED", "0") != "1":
        raise HTTPException(503, "Sales invoice issuance is not enabled")
    parent = sources.root(db, source, identity)
    request = payload.model_dump(mode="json")
    # Preserve request hashes of already-issued pre-extension invoices when
    # newly optional fields are absent. Existing frozen snapshots never change.
    if request["buyer"].get("delivery_address") is None:
        request["buyer"].pop("delivery_address", None)
    for charge in request["additional_charges"]:
        if charge.get("tax_policy_key") is None:
            charge.pop("tax_policy_key", None)
    fingerprint = digest(canonical(request))
    previous = existing(db, source, identity, "sales_invoice")
    if previous is not None:
        require(previous.request_sha256 == fingerprint)
        return read(db, previous)
    # The owner no longer requests certificate-based signing. Unsigned production
    # documents still require the explicit written electronic-document review.
    # Existing immutable documents above remain readable/idempotent.
    from billing_b2c_policy import production_gate

    production_gate(db)
    parent, details = sales_preview(db, source, identity, payload)
    now = utc_now().replace(tzinfo=timezone.utc)
    from billing_b2c_policy import enabled as b2c_enabled

    if b2c_enabled():
        require(payload.supply_date == now.astimezone(IST).date())
    number = allocate(db, now)
    snapshot = header(db, parent, source, "Sales tax invoice", uuid4().hex)
    snapshot["issued_at"] = iso(now)
    snapshot.update(
        **details,
        document_type="sales_tax_invoice",
        invoice_number=number,
        notice="TEST ONLY: not electronically signed and not valid for tax use. Issued against synthetic final billing and supply facts. Payment acknowledgements are separate and do not add to this sale.",
    )
    if config.APP_ENV == "production":
        snapshot["signature"] = dict(
            status="reviewed_electronic_document_exemption",
            electronically_signed=False,
            production_issuance_available=True,
        )
        snapshot["notice"] = (
            "Domestic B2C tax invoice. No government IRN is issued. Customer signature is not required."
        )
    return store(
        db,
        parent,
        source,
        "sales_invoice",
        snapshot,
        admin.id,
        payload.audit_reason,
        fingerprint,
        number,
    )
