"""Feature-gated, owner-scoped saved billing documents."""

from typing import Annotated

from fastapi import Depends, HTTPException, Path, Request, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

import billing_receipts
from billing_models import BillingReceipt
from models import User


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


def install(app, get_db, get_current_user, require_permission, log_admin_action):
    def available(response: Response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        if not billing_receipts.enabled():
            raise HTTPException(503, "Billing documents are not enabled")

    @app.post("/billing/savings/payments/{payment_id}/receipt", dependencies=[Depends(available)])
    def issue(
        payment_id: Annotated[int, Path(gt=0, le=2147483647)],
        payload: EmptyInput,
        response: Response,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        try:
            result = billing_receipts.issue_savings(db, user.id, payment_id)
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise

    @app.get("/billing/receipts/{receipt_id}", dependencies=[Depends(available)])
    def read(
        receipt_id: Annotated[str, Path(pattern=r"^[a-f0-9]{32}$")],
        response: Response,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        receipt = db.query(BillingReceipt).filter_by(id=receipt_id, user_id=user.id).one_or_none()
        if receipt is None:
            raise HTTPException(404, "Billing document not found")
        return billing_receipts.saved_receipt(db, receipt)

    def admin_document(db, receipt, request, admin):
        if receipt is None:
            raise HTTPException(404, "Saved billing document not found")
        result = billing_receipts.saved_receipt(db, receipt)
        log_admin_action(
            request, admin, "billing.receipt.read", "savings_payment", receipt.source_id
        )
        return result

    @app.get("/admin/billing/receipts/{receipt_id}", dependencies=[Depends(available)])
    def admin_read(
        receipt_id: Annotated[str, Path(pattern=r"^[a-f0-9]{32}$")],
        request: Request,
        admin: User = Depends(require_permission("savings:manage")),
        db: Session = Depends(get_db),
    ):
        receipt = db.query(BillingReceipt).filter_by(id=receipt_id, source="savings").one_or_none()
        return admin_document(db, receipt, request, admin)

    @app.get(
        "/admin/billing/savings/payments/{payment_id}/receipt",
        dependencies=[Depends(available)],
    )
    def admin_read_payment(
        payment_id: Annotated[int, Path(gt=0, le=2147483647)],
        request: Request,
        admin: User = Depends(require_permission("savings:manage")),
        db: Session = Depends(get_db),
    ):
        receipt = (
            db.query(BillingReceipt).filter_by(source="savings", source_id=payment_id).one_or_none()
        )
        return admin_document(db, receipt, request, admin)
