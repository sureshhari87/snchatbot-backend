import pytest
from fastapi import HTTPException

import commerce
import main
from models import CustomerNotification, Product
from schemas import OrderSyncRequest


@pytest.mark.parametrize("failure", ["stock", "cleanup"])
def test_finalization_failure_never_commits_partial_stock(db, verified_user, monkeypatch, failure):
    products = db.query(Product).order_by(Product.id).limit(2).all()
    if failure == "stock":
        products[1].stock_quantity = 0
        products[1].in_stock = False
    db.commit()
    before = {p.id: p.stock_quantity for p in products}
    order = main.upsert_local_order_snapshot(db, verified_user, OrderSyncRequest(
        order_reference="order_atomic", status="payment_pending", total=200,
        currency="INR", payment_status="pending", source="razorpay_checkout",
        items=[{"product_id": f"snchatbot_{p.id}", "backend_product_id": p.id,
                "name": p.name, "qty": 1, "price": 100} for p in products],
    ))
    db.commit()
    monkeypatch.setattr(main, "fetch_razorpay_payment", lambda *a, **kw: (200, {
        "id": "pay_atomic", "status": "captured", "amount": 20000, "currency": "INR",
    }))

    def fail_cleanup(*args):
        raise HTTPException(409, "Synthetic cleanup failure")

    if failure == "cleanup":
        monkeypatch.setattr(commerce, "clean_paid_cart", fail_cleanup)
    with pytest.raises(HTTPException):
        main.finalize_razorpay_payment(db, order, "pay_atomic", "verify")
    # The HTTP caller commits the failure audit; inventory must remain unchanged.
    db.commit()
    db.expire_all()
    assert {p.id: p.stock_quantity for p in db.query(Product) if p.id in before} == before
    assert db.query(CustomerNotification).count() == 0
