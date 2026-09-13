"""Authenticated admin catalogue adapter for the Flutter administration app."""

import json

from fastapi import Body, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from catalogue_data import RATES_KEY, dynamic, number, price, product_values, read_rates
from models import AppConfigEntry, Product


def document(product):
    data = dict(product.source_data or {})
    data.update(
        name=product.name,
        description=product.description,
        image=product.image,
        stock=product.stock_quantity,
        productId=str(product.id),
    )
    if not product.source_data:
        data.update(
            price=product.price,
            category=product.category,
            useDynamicPricing=False,
            metalType=product.metal,
            purity=product.purity,
        )
    return {"id": str(product.id), "data": data}


def install(app, get_db, require_permission):
    permission = require_permission("products:manage")

    @app.get("/catalogue/metal-rates")
    def public_rates(db: Session = Depends(get_db)):
        saved = read_rates(db)
        return {
            key: saved[key] for key in ("gold18k", "gold22k", "gold24k", "silver") if key in saved
        }

    @app.get("/admin/catalogue/products")
    def products(db: Session = Depends(get_db), admin=Depends(permission)):
        return [document(p) for p in db.query(Product).order_by(Product.id.desc()).all()]

    @app.post("/admin/catalogue/products")
    def create(data: dict = Body(...), db: Session = Depends(get_db), admin=Depends(permission)):
        try:
            product = Product(**product_values(data, read_rates(db, lock=True)))
            db.add(product)
            db.commit()
            db.refresh(product)
            return document(product)
        except (ValueError, IntegrityError) as error:
            db.rollback()
            detail = str(error) if isinstance(error, ValueError) else "SKU already exists"
            raise HTTPException(422, detail) from None

    @app.patch("/admin/catalogue/products/{product_id}")
    def update(
        product_id: int,
        data: dict = Body(...),
        db: Session = Depends(get_db),
        admin=Depends(permission),
    ):
        rates = read_rates(db, lock=True)
        product = db.query(Product).filter_by(id=product_id).with_for_update().first()
        if product is None:
            raise HTTPException(404, "Product not found")
        try:
            merged = {**document(product)["data"], **data}
            for key, value in product_values(merged, rates).items():
                setattr(product, key, value)
            db.commit()
            db.refresh(product)
            return document(product)
        except (ValueError, IntegrityError) as error:
            db.rollback()
            detail = str(error) if isinstance(error, ValueError) else "SKU already exists"
            raise HTTPException(422, detail) from None

    @app.get("/admin/catalogue/rates")
    def rates(db: Session = Depends(get_db), admin=Depends(permission)):
        return read_rates(db)

    @app.put("/admin/catalogue/rates")
    def set_rates(data: dict = Body(...), db: Session = Depends(get_db), admin=Depends(permission)):
        try:
            normalized = {
                key: float(number(data.get(key), key))
                for key in ("gold18k", "gold22k", "gold24k", "silver")
            }
            if any(value <= 0 for value in normalized.values()):
                raise ValueError("All metal rates must be positive")
            entry = db.query(AppConfigEntry).filter_by(key=RATES_KEY).with_for_update().first()
            if entry is None:
                entry = AppConfigEntry(key=RATES_KEY, is_public=False)
                db.add(entry)
            entry.value = json.dumps(normalized)
            # Price changes and the rate record commit together. Never alter stock here.
            for product in db.query(Product).order_by(Product.id).with_for_update().all():
                if product.source_data and dynamic(product.source_data):
                    product.price = price(product.source_data, normalized)
            db.commit()
            return normalized
        except (ValueError, IntegrityError):
            db.rollback()
            raise HTTPException(422, "Invalid rates or product pricing; no rates changed") from None
