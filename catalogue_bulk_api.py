"""Admin-only preview and atomic, idempotent, create-only catalogue import."""

import json
import time
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import config
from catalogue_bulk_data import digest, https_image, plan
from catalogue_data import product_values, read_rates
from models import AppConfigEntry, CatalogueContentAudit, Product, User

AUDIENCE = "catalogue-bulk-import-v1"
MAX_IMPORT_REQUEST_BYTES = 30 * 1024 * 1024


def request_body_limit(request, default):
    # Only the authenticated import routes consume large bundles. The route's
    # products:manage dependency runs before its bounded body reader.
    if (
        request.method == "POST"
        and request.url.path
        in {
            "/admin/catalogue/imports/preview",
            "/admin/catalogue/imports/confirm",
        }
        and config.get_bool("CATALOGUE_BULK_IMPORT_ENABLED", False)
    ):
        return MAX_IMPORT_REQUEST_BYTES
    return default


class Bundle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: UUID
    csv_text: str = Field(min_length=1, max_length=524288)
    photos_base64: str = Field(default="", max_length=28000000)


class Confirm(Bundle):
    preview_token: str = Field(min_length=20, max_length=4096)
    uploaded_images: dict[str, str] = Field(default_factory=dict, max_length=200)
    reason: str = Field(min_length=5, max_length=500)
    confirmed: bool = Field(strict=True)


async def bounded_body(request, model):
    require_enabled()
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_IMPORT_REQUEST_BYTES:
            raise HTTPException(413, "Import request exceeds 30 MB")
    try:
        return model.model_validate_json(body)
    except ValidationError:
        raise HTTPException(422, "Invalid bulk import request fields") from None


async def bundle_body(request: Request):
    return await bounded_body(request, Bundle)


async def confirm_body(request: Request):
    return await bounded_body(request, Confirm)


def input_sha(payload):
    return digest(
        {
            "request_key": str(payload.request_key),
            "csv": payload.csv_text,
            "zip": digest(payload.photos_base64),
        }
    )


def ledger_key(actor, request_key):
    return "catalogue.bulk_import." + digest([actor, str(request_key)])


def saved(db, actor, request_key):
    row = db.query(AppConfigEntry).filter_by(key=ledger_key(actor, request_key)).first()
    return json.loads(row.value) if row else None


def lock_imports(db):
    # Serialize create-only batches even when no metal-rate row exists yet.
    # This also closes the case-variant SKU race without changing existing SKUs.
    dialect = db.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise HTTPException(409, "Bulk import requires a supported transactional database")
    statement = (postgres_insert if dialect == "postgresql" else sqlite_insert)(AppConfigEntry)
    db.execute(
        statement.values(
            key="catalogue.bulk_import_lock", value="{}", is_public=False
        ).on_conflict_do_nothing(index_elements=["key"])
    )
    db.query(AppConfigEntry).filter_by(key="catalogue.bulk_import_lock").with_for_update().one()


def require_enabled():
    if not config.get_bool("CATALOGUE_BULK_IMPORT_ENABLED", False):
        raise HTTPException(503, "Catalogue bulk import is disabled")


def validate(db, payload, rates):
    try:
        products, photos = plan(payload.csv_text, payload.photos_base64, rates)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    skus = [p["sku"].lower() for p in products]
    if db.query(Product.id).filter(func.lower(Product.sku).in_(skus)).first():
        raise HTTPException(409, "A SKU already exists; no products were imported")
    return products, photos


def response(record):
    return {
        "state": "completed",
        "request_key": record["request_key"],
        "product_ids": record["product_ids"],
        "created_count": len(record["product_ids"]),
        "active": False,
        "existing_products_modified": False,
    }


def install(app, get_db, require_permission):
    access = require_permission("products:manage")

    @app.post("/admin/catalogue/imports/preview")
    def preview(
        admin: User = Depends(access),
        payload: Bundle = Depends(bundle_body),
        db: Session = Depends(get_db),
    ):
        require_enabled()
        previous = saved(db, admin.id, payload.request_key)
        sha = input_sha(payload)
        if previous:
            if previous["input_sha"] != sha:
                raise HTTPException(409, "Import key already used for different files")
            return response(previous)
        rates = read_rates(db)
        products, photos = validate(db, payload, rates)
        expires = int(time.time()) + 900
        token = jwt.encode(
            {
                "aud": AUDIENCE,
                "actor_id": admin.id,
                "exp": expires,
                "input_sha": sha,
                "rates_sha": digest(rates),
                "environment": config.APP_ENV,
            },
            config.SECRET_KEY,
            algorithm=config.ALGORITHM,
        )
        return {
            "state": "preview",
            "request_key": str(payload.request_key),
            "preview_token": token,
            "expires_at": expires,
            "product_count": len(products),
            "photo_count": len(photos),
            "active": False,
            "existing_products_modified": False,
            "photos": photos,
            "products": [
                {k: p[k] for k in ("row", "sku", "price", "stock", "images")}
                | {
                    "name": p["data"]["name"],
                    "category": p["data"]["category"],
                    "purity": p["data"].get("purity"),
                    "weight": p["data"].get("metalWeight"),
                    "readyToShip": p["data"]["readyToShip"],
                    "handlingDays": p["data"]["handlingDays"],
                    "manufacturingDays": p["data"]["manufacturingDays"],
                    "sizeAdjustmentDays": p["data"]["sizeAdjustmentDays"],
                    "dispatchNotBefore": p["data"].get("dispatchNotBefore"),
                }
                for p in products
            ],
        }

    @app.get("/admin/catalogue/imports/{request_key}")
    def status(request_key: UUID, admin: User = Depends(access), db: Session = Depends(get_db)):
        record = saved(db, admin.id, request_key)
        if record is None:
            raise HTTPException(404, "No committed import exists for this account and key")
        return response(record)

    @app.post("/admin/catalogue/imports/confirm")
    def confirm(
        admin: User = Depends(access),
        payload: Confirm = Depends(confirm_body),
        db: Session = Depends(get_db),
    ):
        require_enabled()
        if not payload.confirmed or not payload.reason.strip():
            raise HTTPException(422, "Confirm inactive-draft import and provide an audit reason")
        sha = input_sha(payload)
        commit_sha = digest(
            {"input": sha, "images": payload.uploaded_images, "reason": payload.reason}
        )
        previous = saved(db, admin.id, payload.request_key)
        if previous:
            if previous["commit_sha"] != commit_sha:
                raise HTTPException(
                    409, "Import key already used for different files or confirmation"
                )
            return response(previous)
        try:
            claims = jwt.decode(
                payload.preview_token,
                config.SECRET_KEY,
                algorithms=[config.ALGORITHM],
                audience=AUDIENCE,
                options={"require": ["exp", "actor_id", "input_sha", "rates_sha", "environment"]},
            )
        except jwt.PyJWTError:
            raise HTTPException(
                409, "Preview expired or invalid; preview the unchanged files again"
            ) from None
        lock_imports(db)
        winner = saved(db, admin.id, payload.request_key)
        if winner:
            if winner["commit_sha"] != commit_sha:
                raise HTTPException(
                    409, "Import key already used for different files or confirmation"
                )
            return response(winner)
        rates = read_rates(db, lock=True)
        if (
            claims["actor_id"] != admin.id
            or claims["input_sha"] != sha
            or claims["rates_sha"] != digest(rates)
            or claims["environment"] != config.APP_ENV
        ):
            raise HTTPException(409, "Files, account or rates changed; preview again")
        try:
            products, photos = validate(db, payload, rates)
        except HTTPException as error:
            # A parallel identical request may have committed while this request
            # waited for the rates lock or checked SKU uniqueness.
            winner = saved(db, admin.id, payload.request_key)
            if error.status_code == 409 and winner and winner["commit_sha"] == commit_sha:
                return response(winner)
            raise
        if set(payload.uploaded_images) != set(photos):
            raise HTTPException(422, "Uploaded image mapping must match the previewed ZIP photos")
        try:
            urls = {name: https_image(url) for name, url in payload.uploaded_images.items()}
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        record = {
            "request_key": str(payload.request_key),
            "input_sha": sha,
            "commit_sha": commit_sha,
            "product_ids": [],
        }
        marker = AppConfigEntry(
            key=ledger_key(admin.id, payload.request_key), is_public=False, value=json.dumps(record)
        )
        try:
            db.add(marker)
            db.flush()
            for item in products:
                data = dict(item["data"])
                images = [urls.get(value, value) for value in item["images"]]
                data.update(
                    image=images[0],
                    images=images,
                    sizeOptions=data["sizes"],
                    subCategory=data.get("productType"),
                )
                product = Product(
                    source_id="bulk_" + digest([admin.id, str(payload.request_key), item["sku"]]),
                    **product_values(data, rates),
                )
                db.add(product)
                db.flush()
                record["product_ids"].append(product.id)
                db.add(
                    CatalogueContentAudit(
                        actor_id=admin.id,
                        resource="bulk_product",
                        resource_id=product.id,
                        version=1,
                        reason=payload.reason.strip(),
                        before=None,
                        after={
                            "request_key": str(payload.request_key),
                            "input_sha": sha,
                            "sku": item["sku"],
                            "price": item["price"],
                            "stock": item["stock"],
                            "active": False,
                        },
                    )
                )
            marker.value = json.dumps(record)
            db.commit()
        except IntegrityError:
            db.rollback()
            winner = saved(db, admin.id, payload.request_key)
            if winner and winner["commit_sha"] == commit_sha:
                return response(winner)
            raise HTTPException(
                409, "Import conflicted; read saved status before retrying"
            ) from None
        return response(record)
