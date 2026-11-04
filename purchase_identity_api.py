"""Sensitive input is parsed explicitly: validation errors never echo request values."""

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

import purchase_identity as service


def enabled(response: Response):
    response.headers["Cache-Control"] = "no-store"
    service.require(service.enabled(), "Buyer verification is not enabled", 503)
    service.cipher()


async def body(request):
    try:
        raw = await request.body()
        service.require(len(raw) <= 4096, "Buyer verification request is too large", 413)
        value = await request.json()
        service.require(isinstance(value, dict), "Invalid buyer verification request", 422)
        return value
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, "Invalid buyer verification request") from None


def mutate(db, operation):
    try:
        result = operation()
        db.commit()
        return result
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Verification changed; reload before retrying") from None
    except Exception:
        db.rollback()
        raise


def install(app, get_db, get_user):
    protected = [Depends(enabled)]

    def admin(user=Depends(get_user)):
        service.require(
            getattr(user, "is_admin", False) is True, "Administrator access required", 403
        )
        return user

    @app.get("/purchase-identity", dependencies=protected)
    def read_own(user=Depends(get_user), db=Depends(get_db)):
        service.ensure_key_compatible(db)
        return service.document(db, service.latest(db, user.id), include_name=True)

    @app.post("/purchase-identity", dependencies=protected)
    async def submit_own(request: Request, user=Depends(get_user), db=Depends(get_db)):
        value = await body(request)
        return await run_in_threadpool(
            mutate,
            db,
            lambda: service.document(db, service.submit(db, user.id, value), include_name=True),
        )

    @app.get("/admin/purchase-identities/{identity_id}", dependencies=protected)
    def read_admin(identity_id: str, _admin=Depends(admin), db=Depends(get_db)):
        row = db.get(service.PurchaseIdentity, identity_id)
        service.require(row is not None, "Buyer verification unavailable", 404)
        return dict(service.document(db, row, include_name=True), user_id=row.user_id)

    @app.post("/admin/purchase-identities/{identity_id}/review", dependencies=protected)
    async def review_admin(
        identity_id: str, request: Request, actor=Depends(admin), db=Depends(get_db)
    ):
        value = await body(request)

        def operation():
            service.review(db, identity_id, actor, value)
            return service.document(
                db, db.get(service.PurchaseIdentity, identity_id), include_name=True
            )

        return await run_in_threadpool(mutate, db, operation)
