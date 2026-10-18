"""Disposable loopback PostgreSQL only; no provider calls or existing DB URLs."""

import importlib.util
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import text
from test_payment_concurrency_postgres import local_postgres as local_postgres
from test_review_photos import payload
from test_savings_concurrency_postgres import race

import review_photos as photos
from models import Product, ProductReview, User
from review_photo_models import ReviewPhoto


def setup(factory, label):
    with factory() as db:
        user = User(username=label, email=label + "@example.test", hashed_password="unused")
        product = Product(name=label, price=100)
        db.add_all([user, product])
        db.commit()
        return user.id, product.id


def test_parallel_same_key_creates_one_photo(local_postgres, monkeypatch):
    monkeypatch.setattr(photos, "ENABLED", True)
    owner, product = setup(local_postgres, "photo-duplicate")
    body = payload()

    def work(index):
        with local_postgres() as db:
            row = photos.upload(
                db, owner, product, body["upload_key"], body["data_base64"], body["content_type"]
            )
            db.commit()
            return row.id

    assert len(set(race(work))) == 1
    with local_postgres() as db:
        assert db.query(ReviewPhoto).filter_by(owner_id=owner).count() == 1


def test_parallel_uploads_cannot_exceed_daily_quota(local_postgres, monkeypatch):
    monkeypatch.setattr(photos, "ENABLED", True)
    monkeypatch.setattr(photos, "MAX_DAILY_UPLOADS", 1)
    owner, product = setup(local_postgres, "photo-quota")
    body = payload()

    def work(index):
        with local_postgres() as db:
            try:
                photos.upload(
                    db, owner, product, f"quota-{index}", body["data_base64"], body["content_type"]
                )
                db.commit()
                return "created"
            except HTTPException as error:
                db.rollback()
                assert error.status_code == 429
                return "limited"

    assert sorted(race(work)) == ["created", "limited"]


def test_0035_0036_roundtrip_preserves_legacy_reviews(local_postgres, monkeypatch):
    owner, product = setup(local_postgres, "photo-migrations")
    with local_postgres() as db:
        review = ProductReview(
            user_id=owner,
            product_id=product,
            rating=5,
            title="Legacy review",
            body="Preserve this review",
            status="approved",
        )
        db.add(review)
        db.commit()
        review_id = review.id

    def load(name):
        path = Path(__file__).resolve().parents[1] / "alembic/versions" / name
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    old = load("0035_review_purchase.py")
    new = load("0036_review_photos.py")
    engine = local_postgres.kw["bind"]
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(old, "op", operations)
        monkeypatch.setattr(new, "op", operations)
        users = connection.execute(text("SELECT count(*) FROM users")).scalar_one()
        new.downgrade()
        old.downgrade()
        old.upgrade()
        new.upgrade()
        row = connection.execute(
            text("SELECT title, body, verified_order_id FROM product_reviews WHERE id=:id"),
            {"id": review_id},
        ).one()
        assert tuple(row) == ("Legacy review", "Preserve this review", None)
        assert connection.execute(text("SELECT count(*) FROM users")).scalar_one() == users
        assert connection.execute(text("SELECT count(*) FROM review_photos")).scalar_one() == 0
