"""Private billing APIs. Reads never issue documents; issuance has explicit routes."""

from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Path, Query, Request, Response
from sqlalchemy.orm import Session

import billing_documents as documents
import billing_receipts
from billing_api import EmptyInput
from billing_document_models import BillingDocument
from billing_sales import SalesInput
from models import User

Source = Literal["voucher", "custom_advance", "custom_balance", "merchandise", "custom_design"]
Identity = Annotated[int, Path(gt=0, le=2147483647)]
DocumentId = Annotated[str, Path(pattern=r"^[a-f0-9]{32}$")]


def install(app, get_db, get_current_user, require_permission, log_admin_action):
    def available(response: Response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        if not billing_receipts.enabled():
            raise HTTPException(503, "Billing documents are not enabled")

    def permission(admin, source):
        require_permission(documents.PERMISSIONS[source])(admin)

    def transaction(db, work):
        try:
            result = work()
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise

    @app.post("/billing/payments/{source}/{source_id}/receipt", dependencies=[Depends(available)])
    def issue_receipt(
        source: Source,
        source_id: Identity,
        payload: EmptyInput,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        return transaction(db, lambda: documents.issue_receipt(db, source, source_id, user.id))

    @app.get("/billing/documents/{document_id}", dependencies=[Depends(available)])
    def read(
        document_id: DocumentId,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        return documents.read(db, documents.owned(db, document_id, user.id))

    @app.get("/billing/documents", dependencies=[Depends(available)])
    def saved(
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        offset: Annotated[int, Query(ge=0, le=10000)] = 0,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        rows = (
            db.query(BillingDocument)
            .filter_by(user_id=user.id)
            .order_by(BillingDocument.created_at.desc(), BillingDocument.id)
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [
            dict(
                id=r.id,
                source=r.source,
                source_id=r.source_id,
                kind=r.kind,
                number=r.number,
                created_at=r.created_at,
            )
            for r in rows
        ]

    @app.get("/admin/billing/documents/{document_id}", dependencies=[Depends(available)])
    def admin_read(
        document_id: DocumentId,
        request: Request,
        admin: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        # Require some billing authority before looking up another account's ID.
        require_permission("catalog:manage")(admin)
        row = documents.owned(db, document_id)
        permission(admin, row.source)
        result = documents.read(db, row)
        log_admin_action(request, admin, "billing.document.read", row.source, row.source_id)
        return result

    @app.get("/admin/billing/readiness", dependencies=[Depends(available)])
    def readiness(admin: User = Depends(require_permission("catalog:manage"))):
        import os

        mode = os.getenv("BILLING_EINVOICE_APPLICABILITY", "unconfirmed")
        return dict(
            sales_enabled=os.getenv("BILLING_SALES_ENABLED", "0") == "1",
            series_confirmed=os.getenv("BILLING_SALES_SERIES_START_CONFIRMED", "0") == "1",
            tax_rules_reviewed=bool(os.getenv("BILLING_TAX_RULES_REVIEW_REFERENCE", "").strip()),
            signatory_configured=bool(os.getenv("BILLING_SIGNATORY_NAME", "").strip()),
            einvoice_applicability=mode
            if mode in {"unconfirmed", "not_applicable", "applicable"}
            else "unconfirmed",
            irp_registration_available=False,
            electronic_signing_available=False,
            production_sales_issuance_available=False,
        )

    @app.get("/billing/sales/{source}/{source_id}", dependencies=[Depends(available)])
    def owner_invoice(
        source: Source,
        source_id: Identity,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        documents.sources.root(db, source, source_id, user.id)
        row = documents.existing(db, source, source_id, "sales_invoice")
        if row is None:
            raise HTTPException(404, "Sales invoice has not been issued")
        return documents.read(db, row)

    @app.get("/admin/billing/sales/{source}/{source_id}", dependencies=[Depends(available)])
    def admin_invoice(
        source: Source,
        source_id: Identity,
        request: Request,
        admin: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        permission(admin, source)
        row = documents.existing(db, source, source_id, "sales_invoice")
        if row is None:
            raise HTTPException(404, "Sales invoice has not been issued")
        result = documents.read(db, row)
        log_admin_action(request, admin, "billing.document.read", source, source_id)
        return result

    @app.get("/admin/billing/sales/{source}/{source_id}/facts", dependencies=[Depends(available)])
    def final_facts(
        source: Source,
        source_id: Identity,
        admin: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        permission(admin, source)
        if source not in {"merchandise", "custom_design"}:
            raise HTTPException(404, "Sales source not found")
        parent = documents.sources.root(db, source, source_id)
        facts = documents.sources.evidence(db, source, parent)
        items = []
        if source == "merchandise":
            from models import OrderSnapshotItem

            items = [
                dict(source_item_id=i.id, description=i.name, quantity=i.qty)
                for i in db.query(OrderSnapshotItem)
                .filter_by(order_id=source_id)
                .order_by(OrderSnapshotItem.id)
                .all()
            ]
        return dict(
            source=source,
            source_id=source_id,
            gross_paise=facts["gross_paise"],
            tender=facts["tender"],
            source_detail=facts["detail"],
            items=items,
            review_required=documents.sources.review(db, source, parent),
            final_tax_and_item_facts_verified=False,
        )

    @app.post(
        "/admin/billing/sales/{source}/{source_id}/preview", dependencies=[Depends(available)]
    )
    def preview(
        source: Source,
        source_id: Identity,
        payload: SalesInput,
        admin: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        permission(admin, source)
        _, result = documents.sales_preview(db, source, source_id, payload)
        return dict(preview=result, invoice_number_allocated=False)

    @app.post("/admin/billing/sales/{source}/{source_id}/issue", dependencies=[Depends(available)])
    def issue_sale(
        source: Source,
        source_id: Identity,
        payload: SalesInput,
        request: Request,
        admin: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        permission(admin, source)
        result = transaction(
            db, lambda: documents.issue_sales(db, source, source_id, payload, admin)
        )
        log_admin_action(request, admin, "billing.sales.issue", source, source_id)
        return result
