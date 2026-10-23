"""Opt-in disposable loopback PostgreSQL tests; no staging or live data."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func
from test_catalogue_bulk_import import REASON, bundle, row
from test_payment_concurrency_postgres import local_postgres

import catalogue_bulk_api as api
from catalogue_visibility import public_product_condition
from models import AppConfigEntry, CatalogueContentAudit, Product, User

__all__ = ["local_postgres"]


@pytest.mark.parametrize("same_key,case_variant", [(True, False), (False, False), (False, True)])
def test_parallel_import_posts_one_batch_without_duplicate_sku(
    local_postgres, monkeypatch, same_key, case_variant
):
    factory = local_postgres
    monkeypatch.setenv("CATALOGUE_BULK_IMPORT_ENABLED", "1")
    with factory() as db:
        user = User(
            username=f"bulk-{same_key}-{case_variant}",
            email=f"bulk-{same_key}-{case_variant}@example.test",
            hashed_password="synthetic-only",
            is_admin=True,
            is_verified=True,
        )
        db.add(user)
        db.commit()
        uid = user.id
    app = FastAPI()

    def database():
        with factory() as db:
            yield db

    def admin():
        return User(id=uid, username="synthetic", hashed_password="unused", is_admin=True)

    api.install(app, database, lambda permission: admin)
    sku = f"PARALLEL-{same_key}-{case_variant}"
    first = bundle(row(sku))
    values = [first, first if same_key else bundle(row(sku.lower() if case_variant else sku))]
    inputs = []
    with TestClient(app) as client:
        for value in values:
            preview = client.post("/admin/catalogue/imports/preview", json=value)
            assert preview.status_code == 200
            inputs.append(
                {
                    **value,
                    "preview_token": preview.json()["preview_token"],
                    "uploaded_images": {},
                    "reason": REASON,
                    "confirmed": True,
                }
            )
    barrier = Barrier(2)

    def confirm(value):
        barrier.wait(timeout=30)
        with TestClient(app) as client:
            response = client.post("/admin/catalogue/imports/confirm", json=value)
            return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(confirm, value) for value in inputs]
        results = [future.result(timeout=90) for future in futures]
    assert sorted(status for status, _ in results) == ([200, 200] if same_key else [200, 409])
    if same_key:
        assert results[0][1]["product_ids"] == results[1][1]["product_ids"]
    with factory() as db:
        products = db.query(Product).filter(func.lower(Product.sku) == sku.lower()).all()
        assert len(products) == 1 and products[0].in_stock is False
        assert (
            db.query(CatalogueContentAudit).filter_by(actor_id=uid, resource="bulk_product").count()
            == 1
        )
        markers = (
            db.query(AppConfigEntry)
            .filter(
                AppConfigEntry.key.in_(
                    [api.ledger_key(uid, value["request_key"]) for value in values]
                )
            )
            .all()
        )
        assert len(markers) == 1
        assert (
            db.query(Product)
            .filter(Product.id == products[0].id, public_product_condition())
            .count()
            == 0
        )
