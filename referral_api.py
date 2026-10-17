"""Authenticated own-customer referral API; financial and referral gates required."""

from fastapi import Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

import financial_checkout
import referrals
from financial_api import mutate, private
from models import User


class ApplicationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    code: str = Field(min_length=8, max_length=20, pattern=r"^[A-Za-z0-9]{8,20}$")


def install(app, get_db, get_current_user, require_permission):
    protected = [Depends(financial_checkout.enabled), Depends(referrals.enabled), Depends(private)]

    @app.get("/referrals", dependencies=protected)
    def programme(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
        return referrals.programme(db, user.id)

    @app.post("/referrals/code", dependencies=protected)
    def own_code(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
        def operation():
            referrals.code_for(db, user.id)
            return referrals.programme(db, user.id)

        return mutate(db, operation)

    @app.put("/referrals/application", dependencies=protected)
    def apply(
        payload: ApplicationInput,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        def operation():
            referrals.apply(db, user.id, payload.code)
            return referrals.programme(db, user.id)

        return mutate(db, operation)

    @app.get("/admin/referrals", dependencies=[Depends(private)])
    def review(
        after: int = Query(0, ge=0, le=2147483647),
        limit: int = Query(20, ge=1, le=50),
        actor: User = Depends(require_permission("rewards:manage")),
        db: Session = Depends(get_db),
    ):
        return referrals.audit(db, after, limit)
