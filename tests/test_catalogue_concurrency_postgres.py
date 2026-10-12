"""Opt-in loopback PostgreSQL checks; never contacts staging or reads DB credentials."""

import importlib.util
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier

import pytest
import sqlalchemy as sa
import test_payment_concurrency_postgres as postgres_tests
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient

from catalogue_extras import install
from database import Base
from models import CatalogueContentAudit, Product, RecentlyViewedProduct, User, utc_now

local_postgres = postgres_tests.local_postgres


@pytest.fixture(scope="module")
def catalogue_postgres(local_postgres):
    factory = local_postgres
    engine = factory.kw["bind"]
    tables = (
        "recently_viewed_products",
        "catalogue_home_layout",
        "catalogue_campaigns",
        "catalogue_campaign_recipients",
        "catalogue_content_audit",
    )
    path = Path("alembic/versions/0030_catalogue_extras.py")
    spec = importlib.util.spec_from_file_location("catalogue_pg_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as conn:
        for name in tables:
            assert conn.execute(sa.text(f"SELECT count(*) FROM {name}")).scalar_one() == 0
        for name in reversed(tables):
            Base.metadata.tables[name].drop(conn)
        module.op = Operations(MigrationContext.configure(conn))
        module.upgrade()
    return factory


def app_for(factory, user_id):
    app = FastAPI()

    def db():
        with factory() as session:
            yield session

    def user():
        return User(
            id=user_id,
            username="catalogue-test",
            email="catalogue@example.test",
            hashed_password="test-unused",
            is_admin=True,
        )

    install(app, db, user, lambda permission: user)
    return app


def seed(factory, suffix):
    with factory() as db:
        row = User(
            username=f"catalogue-{suffix}",
            email=f"{suffix}@example.test",
            hashed_password="test-unused",
        )
        db.add(row)
        products = [
            Product(name=f"Concurrent {suffix} {i}", price=100, stock_quantity=1) for i in range(12)
        ]
        db.add_all(products)
        db.commit()
        return row.id, [p.id for p in products]


def test_parallel_history_keeps_one_row_and_bound(catalogue_postgres):
    factory = catalogue_postgres
    user_id, ids = seed(factory, "history")
    app = app_for(factory, user_id)
    barrier = Barrier(2)

    def post(pid, synchronize=False):
        if synchronize:
            barrier.wait(timeout=30)
        with TestClient(app) as client:
            return client.post("/catalogue/recently-viewed", json={"product_id": pid}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(post, ids[0], True) for _ in range(2)]
        assert [f.result(timeout=60) for f in futures] == [200, 200]
    with factory() as db:
        assert db.query(RecentlyViewedProduct).filter_by(user_id=user_id).count() == 1
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(post, pid) for pid in ids]
        assert all(f.result(timeout=60) == 200 for f in futures)
    with factory() as db:
        assert db.query(RecentlyViewedProduct).filter_by(user_id=user_id).count() == 10


def test_parallel_campaign_edit_has_one_winner_and_one_audit(catalogue_postgres):
    factory = catalogue_postgres
    user_id, _ = seed(factory, "campaign")
    app = app_for(factory, user_id)
    body = {
        "title": "Concurrent campaign",
        "cta_text": "Browse",
        "target": "Gold",
        "starts_at": utc_now().isoformat() + "Z",
        "ends_at": (utc_now() + timedelta(days=1)).isoformat() + "Z",
        "reason": "Synthetic concurrent edit",
    }
    with TestClient(app) as client:
        row = client.post("/admin/catalogue/campaigns", json=body).json()
    barrier = Barrier(2)

    def edit(title):
        barrier.wait(timeout=30)
        with TestClient(app) as client:
            return client.put(
                f"/admin/catalogue/campaigns/{row['id']}",
                json={**body, "title": title, "expected_version": 1},
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(edit, title) for title in ("First edit", "Second edit")]
        assert sorted(f.result(timeout=60) for f in futures) == [200, 409]
    with factory() as db:
        assert (
            db.query(CatalogueContentAudit)
            .filter_by(resource="campaign", resource_id=int(row["id"]))
            .count()
            == 2
        )


def test_postgres_catalogue_audit_cannot_be_rewritten(catalogue_postgres):
    factory = catalogue_postgres
    user_id, _ = seed(factory, "audit")
    with factory() as db:
        db.add(
            CatalogueContentAudit(
                actor_id=user_id,
                resource="campaign",
                resource_id=1,
                version=1,
                reason="Synthetic immutable audit",
                before=None,
                after={},
            )
        )
        db.commit()
        for statement in (
            "UPDATE catalogue_content_audit SET reason='test-rewrite'",
            "DELETE FROM catalogue_content_audit",
        ):
            with pytest.raises(sa.exc.DBAPIError):
                with db.begin_nested():
                    db.execute(sa.text(statement))
        assert db.query(CatalogueContentAudit).filter_by(actor_id=user_id).count() == 1
