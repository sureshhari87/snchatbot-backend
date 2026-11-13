"""B2C extensions using the existing authentication and service permissions."""

import base64
import os
from datetime import date
from typing import Annotated

from fastapi import Depends, HTTPException, Path, Query, Request, Response
from sqlalchemy.orm import Session

import billing_documents as documents
import billing_receipts
from billing_adjustments import AdjustmentInput, create, history, saved
from billing_b2c_models import InvoiceAdjustment
from billing_b2c_output import pdf, reconciliation, verification_url, verify
from billing_b2c_policy import ApprovalInput, decision, latest, record
from billing_document_models import BillingDocument

Identity = Annotated[str, Path(pattern=r"^[a-f0-9]{32}$")]


def install(app, get_db, get_current_user, require_permission, log_admin_action, provider, limiter):
    def available(response: Response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        if not billing_receipts.enabled():
            raise HTTPException(503, "Billing documents are not enabled")

    def permission(admin, row):
        require_permission("catalog:manage")(admin)
        require_permission(documents.PERMISSIONS[row.source])(admin)

    def download(content, filename):
        return Response(
            content,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}.pdf"',
                "Cache-Control": "no-store",
                "Pragma": "no-cache",
                "X-Content-Type-Options": "nosniff",
            },
        )

    from billing_supply import SupplyInput
    from billing_supply import record as record_supply

    @app.post("/admin/billing/supply/{source}/{source_id}", dependencies=[Depends(available)])
    def supply_event(
        source: Annotated[str, Path(pattern=r"^(merchandise|custom_design)$")],
        source_id: Annotated[int, Path(gt=0)],
        payload: SupplyInput,
        request: Request,
        admin=Depends(require_permission("catalog:manage")),
        db: Session = Depends(get_db),
    ):
        require_permission(documents.PERMISSIONS[source])(admin)
        try:
            result = record_supply(db, source, source_id, payload, admin)
            db.commit()
        except Exception:
            db.rollback()
            raise
        log_admin_action(request, admin, "billing.supply.ready", source, source_id)
        return result

    @app.get("/billing/documents/{document_id}/pdf", dependencies=[Depends(available)])
    def owner_pdf(
        document_id: Identity, user=Depends(get_current_user), db: Session = Depends(get_db)
    ):
        row = documents.owned(db, document_id, user.id)
        if row.kind != "sales_invoice":
            raise HTTPException(409, "Use the payment acknowledgement viewer")
        doc = documents.read(db, row)["document"]
        return download(pdf(doc, verification_url(row)), "invoice-" + row.id)

    @app.get("/billing/documents/{document_id}/pdf-data", dependencies=[Depends(available)])
    def owner_pdf_data(
        document_id: Identity, user=Depends(get_current_user), db: Session = Depends(get_db)
    ):
        response = owner_pdf(document_id, user, db)
        return dict(
            document_id=document_id,
            media_type="application/pdf",
            data_base64=base64.b64encode(response.body).decode("ascii"),
        )

    @app.get("/admin/billing/documents/{document_id}/pdf", dependencies=[Depends(available)])
    def admin_pdf(
        document_id: Identity,
        request: Request,
        admin=Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        require_permission("catalog:manage")(admin)
        row = documents.owned(db, document_id)
        permission(admin, row)
        if row.kind != "sales_invoice":
            raise HTTPException(409, "Use the payment acknowledgement viewer")
        doc = documents.read(db, row)["document"]
        output = download(pdf(doc, verification_url(row)), "invoice-" + row.id)
        log_admin_action(request, admin, "billing.pdf.download", row.source, row.source_id)
        return output

    @app.get("/admin/billing/documents/{document_id}/pdf-data", dependencies=[Depends(available)])
    def admin_pdf_data(
        document_id: Identity,
        request: Request,
        admin=Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        response = admin_pdf(document_id, request, admin, db)
        return dict(
            document_id=document_id,
            media_type="application/pdf",
            data_base64=base64.b64encode(response.body).decode("ascii"),
        )

    @app.get("/billing/documents/{document_id}/adjustments", dependencies=[Depends(available)])
    def owner_adjustments(
        document_id: Identity, user=Depends(get_current_user), db: Session = Depends(get_db)
    ):
        documents.owned(db, document_id, user.id)
        return history(db, document_id)

    @app.get(
        "/admin/billing/documents/{document_id}/adjustments", dependencies=[Depends(available)]
    )
    def admin_adjustments(
        document_id: Identity, admin=Depends(get_current_user), db: Session = Depends(get_db)
    ):
        require_permission("catalog:manage")(admin)
        permission(admin, documents.owned(db, document_id))
        return history(db, document_id)

    @app.post(
        "/admin/billing/documents/{document_id}/adjustments", dependencies=[Depends(available)]
    )
    def adjustment(
        document_id: Identity,
        payload: AdjustmentInput,
        request: Request,
        admin=Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        require_permission("catalog:manage")(admin)
        permission(admin, documents.owned(db, document_id))
        try:
            result = create(db, document_id, payload, admin, provider)
            db.commit()
        except Exception:
            db.rollback()
            raise
        log_admin_action(
            request, admin, "billing.adjustment.create", "billing_document", document_id
        )
        return result

    def adjustment_pdf(db, adjustment_id, user, admin=False):
        row = db.get(InvoiceAdjustment, adjustment_id)
        if row is None:
            raise HTTPException(404, "Credit note not found")
        parent = documents.owned(db, row.invoice_id, None if admin else user.id)
        if admin:
            permission(user, parent)
        doc = saved(row)
        if row.kind == "refund":
            raise HTTPException(409, "Refund evidence is not a tax credit note")
        return download(pdf(doc), "credit-note-" + row.id)

    @app.get("/billing/adjustments/{adjustment_id}/pdf", dependencies=[Depends(available)])
    def owner_credit_pdf(
        adjustment_id: Identity, user=Depends(get_current_user), db: Session = Depends(get_db)
    ):
        return adjustment_pdf(db, adjustment_id, user)

    @app.get("/admin/billing/adjustments/{adjustment_id}/pdf", dependencies=[Depends(available)])
    def admin_credit_pdf(
        adjustment_id: Identity,
        admin=Depends(require_permission("catalog:manage")),
        db: Session = Depends(get_db),
    ):
        return adjustment_pdf(db, adjustment_id, admin, True)

    @app.post("/admin/billing/tax-approvals", dependencies=[Depends(available)])
    def approval(
        payload: ApprovalInput,
        request: Request,
        admin=Depends(require_permission("catalog:manage")),
        db: Session = Depends(get_db),
    ):
        try:
            result = record(db, payload, admin)
            db.commit()
        except Exception:
            db.rollback()
            raise
        log_admin_action(request, admin, "billing.tax.approval", "tax_scope", payload.scope)
        return result

    @app.get("/admin/billing/tax-approvals", dependencies=[Depends(available)])
    def approval_read(
        scope: Annotated[str, Query(max_length=120)],
        admin=Depends(require_permission("catalog:manage")),
        db: Session = Depends(get_db),
    ):
        row = latest(db, scope)
        if row is None:
            raise HTTPException(404, "Tax decision not recorded")
        return decision(row)

    @app.get("/admin/billing/gstr1-b2c.csv", dependencies=[Depends(available)])
    def export(
        start: date,
        end: date,
        request: Request,
        admin=Depends(require_permission("catalog:manage")),
        db: Session = Depends(get_db),
    ):
        result = reconciliation(db, start, end)
        log_admin_action(request, admin, "billing.gstr1.export", "billing", start.isoformat())
        return Response(
            result,
            media_type="text/csv",
            headers={
                "Content-Disposition": 'attachment; filename="gstr1-b2c-review.csv"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/billing/verify/{document_id}/{token}", dependencies=[Depends(available)])
    @limiter.limit("30/minute")
    def public_verify(
        request: Request,
        document_id: Identity,
        token: Annotated[str, Path(pattern=r"^[a-f0-9]{64}$")],
        db: Session = Depends(get_db),
    ):
        if os.getenv("BILLING_VERIFICATION_ENABLED", "0") != "1":
            raise HTTPException(503, "Invoice verification is not enabled")
        row = (
            db.query(BillingDocument).filter_by(id=document_id, kind="sales_invoice").one_or_none()
        )
        return verify(db, row, token)
