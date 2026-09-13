"""FastAPI-owned carts and configured delivery service levels."""

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from models import AppConfigEntry, CommerceCartItem, Product, utc_now
from schemas import ProductOut


class CartWrite(BaseModel):
    product_id: int = Field(gt=0)
    quantity: int = Field(default=1, ge=1, le=20)
    selected_size: str = Field(default="", max_length=80)
    selected_variant: str = Field(default="", max_length=80)
    gift_wrap: bool = False
    gift_message: str = Field(default="", max_length=500)


class CartQuantity(BaseModel):
    quantity: int = Field(ge=1, le=20)


class DeliveryRequest(BaseModel):
    product_id: int = Field(gt=0)
    destination_pin: str = Field(pattern=r"^[1-9][0-9]{5}$")
    quantity: int = Field(default=1, ge=1, le=20)
    selected_variant: str = Field(default="", max_length=80)
    delivery_method: str = Field(default="standard", pattern=r"^(standard|express)$")


def product_or_error(db, product_id):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(404, "Product not found")
    return product


def check_stock(product, quantity):
    if not product.in_stock or product.stock_quantity < quantity:
        raise HTTPException(409, "Requested quantity is no longer in stock")


def check_selection(product, size="", variant=""):
    data = product.source_data or {}
    choices = data.get("sizeOptions") or data.get("sizes") or []
    if not isinstance(choices, list) or any(isinstance(x, (dict, list)) for x in choices):
        raise HTTPException(409, "Product sizes need configuration")
    allowed = {str(x).strip() for x in choices if str(x).strip()}
    if variant and variant != size:
        raise HTTPException(422, "Select the exact product SKU; variant pricing is not configured")
    if (size and size not in allowed) or (allowed and not size):
        raise HTTPException(422, "Select an available product size")


def cart_out(db, item):
    product = db.get(Product, item.product_id)
    return {
        "id": item.id,
        "product_id": item.product_id,
        "quantity": item.quantity,
        "options": item.options,
        "updated_at": item.updated_at.isoformat(),
        "product": ProductOut.model_validate(product).model_dump() if product else None,
        "available": bool(product and product.in_stock and product.stock_quantity >= item.quantity),
    }


def owned_item(db, user_id, item_id):
    item = db.query(CommerceCartItem).filter_by(id=item_id, user_id=user_id).first()
    if item is None:
        raise HTTPException(404, "Cart item not found")
    return item


def delivery_quote(db, request, now=None):
    product = product_or_error(db, request.product_id)
    check_stock(product, request.quantity)
    entry = db.query(AppConfigEntry).filter_by(key="commerce.delivery_routes").first()
    try:
        routes = json.loads(entry.value) if entry else []
        if not isinstance(routes, list):
            raise ValueError("Expected routes list")
        matching = [
            r
            for r in routes
            if r.get("active", True)
            and request.destination_pin.startswith(str(r["pin_prefix"]))
            and r.get("method", "standard") == request.delivery_method
        ]
        matching.sort(key=lambda r: len(str(r["pin_prefix"])), reverse=True)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise HTTPException(503, "Delivery service configuration is invalid") from None
    if not routes:
        raise HTTPException(503, "Delivery service is not configured yet")
    if not matching:
        raise HTTPException(409, "Delivery is not available for this PIN code")
    route = matching[0]
    try:
        handling, minimum, maximum = (
            int(route[k]) for k in ("handling_days", "minimum_transit_days", "maximum_transit_days")
        )
        if not 0 <= handling <= 365 or not 0 <= minimum <= maximum <= 365:
            raise ValueError("Invalid transit days")
        weekdays = route.get("working_weekdays", [0, 1, 2, 3, 4, 5])
        if not weekdays or any(type(d) is not int or not 0 <= d <= 6 for d in weekdays):
            raise ValueError("Invalid working calendar")
        holidays = set(route.get("holidays", []))
        hour, minute = map(int, route.get("cutoff", "16:00").split(":"))
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("Invalid cutoff")
    except (ValueError, TypeError, KeyError):
        raise HTTPException(503, "Delivery service configuration is invalid") from None
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(timezone(timedelta(minutes=330)))
    start = local.date()

    def working(day):
        return day.weekday() in weekdays and day.isoformat() not in holidays

    if (local.hour, local.minute) >= (hour, minute):
        start += timedelta(days=1)
    while not working(start):
        start += timedelta(days=1)

    def advance(day, count):
        for _ in range(count):
            day += timedelta(days=1)
            while not working(day):
                day += timedelta(days=1)
        return day

    dispatch = advance(start, handling)
    return {
        "serviceable": True,
        "destinationPin": request.destination_pin,
        "fulfilmentGroup": str(route.get("fulfilment_group", "store")),
        "handlingBusinessDays": handling,
        "courierTransitBusinessDays": {"minimum": minimum, "maximum": maximum},
        "earliestDeliveryDate": advance(dispatch, minimum).isoformat(),
        "latestDeliveryDate": advance(dispatch, maximum).isoformat(),
        "estimatedDispatchDate": dispatch.isoformat(),
        "madeToOrder": False,
        "deliveryMethod": request.delivery_method,
        "confidence": "estimated",
        "estimateSource": "configured_sla",
        "explanation": "Estimated from configured delivery service levels; revalidated at checkout.",
        "quoteId": uuid4().hex,
        "calculatedTimestamp": now.isoformat(),
        "expiryTimestamp": (now + timedelta(minutes=15)).isoformat(),
    }


def validate_checkout(db, payload, user):
    if not payload.items:
        raise HTTPException(422, "Checkout cart is empty")
    if payload.coupon_code or payload.gift_voucher_code or payload.reward_points_requested:
        raise HTTPException(
            409, "Discounts and rewards are not configured for the FastAPI catalogue"
        )
    address = payload.delivery_address or {}
    pin = str(address.get("postal_code") or address.get("pincode") or "")
    if len(pin) != 6 or not pin.isdigit() or pin.startswith("0"):
        raise HTTPException(422, "Enter a valid 6-digit delivery PIN code")
    totals, versions, quotes, options = {}, {}, [], {}
    for item in payload.items:
        if not item.backend_product_id or str(item.backend_product_id) != item.product_id:
            raise HTTPException(422, "Use the same numeric product ID throughout checkout")
        product_id = item.backend_product_id
        check_selection(product_or_error(db, product_id), item.selected_size, item.selected_variant)
        totals[product_id] = totals.get(product_id, 0) + item.qty
        if item.cart_item_id:
            try:
                cart_id = int(item.cart_item_id)
            except ValueError:
                raise HTTPException(422, "Invalid cart item ID") from None
            row = owned_item(db, user.id, cart_id)
            if row.product_id != product_id or row.quantity != item.qty:
                raise HTTPException(409, "Your cart changed. Refresh before paying")
            if (row.options or {}).get("selected_size", "") != (item.selected_size or ""):
                raise HTTPException(409, "Your selected size changed. Refresh before paying")
            versions[str(cart_id)] = row.updated_at.isoformat()
            options[str(cart_id)] = row.options
    for product_id, quantity in totals.items():
        if quantity > 20:
            raise HTTPException(422, "Maximum quantity per product is 20")
        quote = delivery_quote(
            db, DeliveryRequest(product_id=product_id, quantity=quantity, destination_pin=pin)
        )
        quotes.append(
            {
                "quoteId": quote["quoteId"],
                "productId": str(product_id),
                "fulfilmentGroup": quote["fulfilmentGroup"],
                "calculatedAt": quote["calculatedTimestamp"],
                "expiresAt": quote["expiryTimestamp"],
                "dispatchEstimate": quote["estimatedDispatchDate"],
                "earliestDate": quote["earliestDeliveryDate"],
                "latestDate": quote["latestDeliveryDate"],
                "confidence": quote["confidence"],
                "source": quote["estimateSource"],
                "deliveryMethod": quote["deliveryMethod"],
            }
        )
    return {"cart_versions": versions, "cart_options": options, "delivery_promises": quotes}


def clean_paid_cart(db, order):
    raw = json.loads(order.raw_payload or "{}")
    metadata = raw.get("metadata") or {}
    if metadata.get("commerce_source") != "fastapi":
        return {}
    versions = metadata.get("fastapi_checkout", {}).get("cart_versions", {})
    removed = []
    for item_id, version in versions.items():
        row = db.query(CommerceCartItem).filter_by(id=int(item_id), user_id=order.user_id).first()
        if row and row.updated_at.isoformat() == version:
            db.delete(row)
            removed.append(item_id)
    return {"cart_cleanup": {"status": "complete", "cart_item_ids": removed}}


def install(app, get_db, get_current_user):
    @app.get("/cart")
    def cart(user=Depends(get_current_user), db: Session = Depends(get_db)):
        return [
            cart_out(db, item)
            for item in db.query(CommerceCartItem)
            .filter_by(user_id=user.id)
            .order_by(CommerceCartItem.id)
            .all()
        ]

    @app.post("/cart")
    def add(payload: CartWrite, user=Depends(get_current_user), db: Session = Depends(get_db)):
        product = product_or_error(db, payload.product_id)
        check_selection(product, payload.selected_size, payload.selected_variant)
        check_stock(product, payload.quantity)
        item = CommerceCartItem(
            user_id=user.id,
            product_id=product.id,
            quantity=payload.quantity,
            options=payload.model_dump(exclude={"product_id", "quantity"}),
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return cart_out(db, item)

    @app.patch("/cart/{item_id}")
    def update(
        item_id: int,
        payload: CartQuantity,
        user=Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        item = owned_item(db, user.id, item_id)
        check_stock(product_or_error(db, item.product_id), payload.quantity)
        item.quantity = payload.quantity
        item.updated_at = utc_now()
        db.commit()
        return cart_out(db, item)

    @app.delete("/cart/{item_id}")
    def delete(item_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
        db.delete(owned_item(db, user.id, item_id))
        db.commit()
        return {"message": "Cart item removed"}

    @app.post("/delivery/quote")
    def quote(
        payload: DeliveryRequest, user=Depends(get_current_user), db: Session = Depends(get_db)
    ):
        return delivery_quote(db, payload)
