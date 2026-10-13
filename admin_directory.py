"""Read-only customer directory and non-financial commerce counts."""

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func
from sqlalchemy.orm import Session

from models import OrderSnapshot, Product, User
from schemas import OrderSnapshotOut


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    email: str
    is_verified: bool


class CustomerPage(BaseModel):
    items: list[CustomerOut]
    next_after_id: int | None


class CommerceSummary(BaseModel):
    customers: int
    products: int
    orders: int
    order_status_counts: dict[str, int]


def install(app, get_db, require_permission, serialize_order):
    customer_access = require_permission("support:manage")
    summary_access = require_permission("metrics:read")

    def customer(db, customer_id):
        row = db.query(User).filter(User.id == customer_id, User.is_admin.is_(False)).first()
        if row is None:
            raise HTTPException(status_code=404, detail="Customer not found")
        return row

    @app.get("/admin/customers", response_model=CustomerPage)
    def list_customers(
        after_id: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
        admin: User = Depends(customer_access),
        db: Session = Depends(get_db),
    ):
        rows = (
            db.query(User)
            .filter(User.is_admin.is_(False), User.id > after_id)
            .order_by(User.id)
            .limit(limit + 1)
            .all()
        )
        return CustomerPage(
            items=[CustomerOut.model_validate(row) for row in rows[:limit]],
            next_after_id=rows[limit - 1].id if len(rows) > limit else None,
        )

    @app.get("/admin/customers/{customer_id}", response_model=CustomerOut)
    def customer_details(
        customer_id: int,
        admin: User = Depends(customer_access),
        db: Session = Depends(get_db),
    ):
        return customer(db, customer_id)

    @app.get("/admin/customers/{customer_id}/orders", response_model=list[OrderSnapshotOut])
    def customer_orders(
        customer_id: int,
        before_id: int | None = Query(default=None, ge=1),
        limit: int = Query(default=50, ge=1, le=100),
        admin: User = Depends(customer_access),
        db: Session = Depends(get_db),
    ):
        customer(db, customer_id)
        query = db.query(OrderSnapshot).filter(OrderSnapshot.user_id == customer_id)
        if before_id is not None:
            query = query.filter(OrderSnapshot.id < before_id)
        return [
            serialize_order(db, row)
            for row in query.order_by(OrderSnapshot.id.desc()).limit(limit).all()
        ]

    @app.get("/admin/commerce-summary", response_model=CommerceSummary)
    def commerce_summary(
        admin: User = Depends(summary_access),
        db: Session = Depends(get_db),
    ):
        return CommerceSummary(
            customers=db.query(User).filter(User.is_admin.is_(False)).count(),
            products=db.query(Product).count(),
            orders=db.query(OrderSnapshot).count(),
            order_status_counts={
                status: count
                for status, count in db.query(OrderSnapshot.status, func.count(OrderSnapshot.id))
                .group_by(OrderSnapshot.status)
                .all()
            },
        )
