"""Authenticated, default-off negotiated cancellation endpoints."""

from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Path, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError

import order_cancellations as service


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Reason(Input):
    reason: str = Field(min_length=1, max_length=1000)


class Version(Reason):
    version: int = Field(strict=True, gt=0)


class Deduction(Input):
    amount_paise: int = Field(strict=True, gt=0, le=999999999999)
    reason: str = Field(min_length=1, max_length=300)


class Proposal(Version):
    refund_paise: int = Field(strict=True, ge=0, le=999999999999)
    deductions: list[Deduction] = Field(default_factory=list, max_length=30)


class Decision(Version):
    action: Literal["accept", "dispute"]


def install(app, get_db, get_current_user, require_permission, provider):
    def available(response: Response):
        response.headers["Cache-Control"] = "no-store"
        if not service.enabled():
            raise HTTPException(503, "Cancellation review is not enabled")

    def row_for(db, order):
        row = service.current(db, order)
        service.require(row is not None, "Cancellation review not found", 404)
        return row

    def finish(db, operation):
        try:
            row = operation()
            db.commit()
            return service.saved(db, row)
        except IntegrityError:
            db.rollback()
            raise HTTPException(
                409, "Review changed; reload saved history before retrying"
            ) from None
        except Exception:
            db.rollback()
            raise

    identity = Annotated[int, Path(gt=0, le=2147483647)]

    @app.get("/orders/{order_id}/cancellation-review", dependencies=[Depends(available)])
    def read(order_id: identity, user=Depends(get_current_user), db=Depends(get_db)):
        order = service.order_for(db, order_id, user.id)
        return service.saved(db, service.current(db, order))

    @app.post("/orders/{order_id}/cancellation-review", dependencies=[Depends(available)])
    def request(
        order_id: identity, payload: Reason, user=Depends(get_current_user), db=Depends(get_db)
    ):
        order = service.order_for(db, order_id, user.id)
        return finish(db, lambda: service.request(db, order, user.id, payload.reason))

    @app.post("/orders/{order_id}/cancellation-review/decision", dependencies=[Depends(available)])
    def decide(
        order_id: identity, payload: Decision, user=Depends(get_current_user), db=Depends(get_db)
    ):
        order = service.order_for(db, order_id, user.id)
        return finish(db, lambda: service.decide(db, order, row_for(db, order), user.id, payload))

    @app.get("/admin/orders/{order_id}/cancellation-review", dependencies=[Depends(available)])
    def admin_read(
        order_id: identity, admin=Depends(require_permission("support:manage")), db=Depends(get_db)
    ):
        order = service.order_for(db, order_id)
        return service.saved(db, service.current(db, order))

    @app.post(
        "/admin/orders/{order_id}/cancellation-review/proposal", dependencies=[Depends(available)]
    )
    def propose(
        order_id: identity,
        payload: Proposal,
        admin=Depends(require_permission("support:manage")),
        db=Depends(get_db),
    ):
        service.require(admin.is_admin, "Administrator required for refund proposals", 403)
        order = service.order_for(db, order_id)
        return finish(db, lambda: service.propose(db, order, row_for(db, order), admin.id, payload))

    @app.post(
        "/admin/orders/{order_id}/cancellation-review/refund", dependencies=[Depends(available)]
    )
    def refund(
        order_id: identity,
        payload: Version,
        admin=Depends(require_permission("support:manage")),
        db=Depends(get_db),
    ):
        service.require(admin.is_admin, "Administrator required for financial execution", 403)
        order = service.order_for(db, order_id)
        return finish(
            db, lambda: service.execute(db, order, row_for(db, order), admin.id, payload, provider)
        )

    @app.post(
        "/admin/orders/{order_id}/cancellation-review/reconcile", dependencies=[Depends(available)]
    )
    def reconcile(
        order_id: identity, admin=Depends(require_permission("support:manage")), db=Depends(get_db)
    ):
        service.require(admin.is_admin, "Administrator required for financial reconciliation", 403)
        order = service.order_for(db, order_id)
        return finish(
            db, lambda: service.reconcile(db, order, row_for(db, order), admin.id, provider)
        )
