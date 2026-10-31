"""Disposable CI PostgreSQL only: serial submissions, decisions and immutable evidence."""

import importlib.util
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

import purchase_identity as identity
from models import User
from purchase_identity_models import PurchaseIdentity, PurchaseIdentityReview


@pytest.fixture
def pg(monkeypatch):
    value = os.getenv("PURCHASE_IDENTITY_TEST_POSTGRES_URL")
    if not value:
        pytest.skip("Dedicated disposable buyer identity PostgreSQL service not supplied")
    url = make_url(value)
    if (
        url.host != "127.0.0.1"
        or url.database != "sona_identity_ci"
        or url.username != "identity_ci"
        or url.query
        or url.drivername not in {"postgresql", "postgresql+psycopg"}
    ):
        pytest.fail("Refusing a non-disposable or non-loopback PostgreSQL target", pytrace=False)
    url = url.set(drivername="postgresql+psycopg")
    monkeypatch.setenv("PURCHASE_IDENTITY_ENABLED", "1")
    monkeypatch.setenv("PURCHASE_IDENTITY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    schema = "identity_test_" + uuid4().hex
    admin_engine = create_engine(url)
    with admin_engine.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = create_engine(url, connect_args={"options": "-c search_path=" + schema})
    try:
        with engine.begin() as conn:
            User.__table__.create(conn)
            conn.exec_driver_sql(
                "CREATE FUNCTION sona_billing_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Immutable billing evidence'; END $$"
            )
            path = Path(__file__).parents[1] / "alembic/versions/0040_purchase_identity.py"
            spec = importlib.util.spec_from_file_location("identity_pg_migration", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            with Operations.context(MigrationContext.configure(conn)):
                module.upgrade()
            for uid in [1, 2, 3]:
                conn.execute(
                    User.__table__.insert().values(
                        id=uid,
                        username=f"synthetic-{uid}",
                        email=f"synthetic-{uid}@example.test",
                        hashed_password="unused",
                        is_admin=uid != 1,
                    )
                )
        yield engine
    finally:
        engine.dispose()
        with admin_engine.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin_engine.dispose()


def submission(key):
    return dict(
        request_key=key,
        pan="ABCDE1234F",
        legal_name="Synthetic Buyer",
        buyer_kind="retail",
        declaration_confirmed=True,
    )


def test_concurrent_submissions_have_serial_versions(pg):
    barrier = threading.Barrier(2)

    def submit(key):
        with Session(pg) as db:
            barrier.wait(timeout=20)
            row = identity.submit(db, 1, submission(key))
            version = row.version
            db.commit()
            return version

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(submit, ["synthetic-a", "synthetic-b"]))
    assert sorted(result) == [1, 2]
    with Session(pg) as db:
        assert db.query(PurchaseIdentity).count() == 2


def test_concurrent_identical_submission_posts_once(pg):
    barrier = threading.Barrier(2)

    def submit(_):
        with Session(pg) as db:
            barrier.wait(timeout=20)
            row = identity.submit(db, 1, submission("synthetic-same"))
            result = row.id
            db.commit()
            return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(submit, [1, 2]))
    assert result[0] == result[1]
    with Session(pg) as db:
        assert db.query(PurchaseIdentity).count() == 1


def test_concurrent_admin_decisions_preserve_single_evidence(pg):
    with Session(pg) as db:
        row = identity.submit(db, 1, submission("synthetic-reviewed"))
        rid = row.id
        db.commit()
    barrier = threading.Barrier(2)

    def review(uid):
        with Session(pg) as db:
            admin = db.get(User, uid)
            barrier.wait(timeout=20)
            try:
                identity.review(
                    db,
                    rid,
                    admin,
                    dict(
                        decision="approved",
                        verified_pan="ABCDE1234F",
                        verified_legal_name="Synthetic Buyer",
                        evidence_reference=f"synthetic-{uid}",
                        reason="Synthetic original checked",
                    ),
                )
                db.commit()
                return "approved"
            except HTTPException as error:
                db.rollback()
                assert error.status_code == 409
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(review, [2, 3]))
    assert sorted(result) == ["approved", "conflict"]
    with Session(pg) as db:
        assert db.query(PurchaseIdentityReview).count() == 1


def test_postgres_history_and_identity_bindings_are_immutable(pg):
    with Session(pg) as db:
        row = identity.submit(db, 1, submission("synthetic-immutable"))
        identity.review(
            db,
            row.id,
            db.get(User, 2),
            dict(
                decision="approved",
                verified_pan="ABCDE1234F",
                verified_legal_name="Synthetic Buyer",
                evidence_reference="synthetic-original",
                reason="Synthetic check",
            ),
        )
        identity.bind(
            db, row, resource_kind="merchandise", resource_key="order:1", amount_paise=20_000_001
        )
        db.commit()
    for table in ["purchase_identities", "purchase_identity_reviews", "purchase_identity_uses"]:
        for statement in [f"UPDATE {table} SET id=id", f"DELETE FROM {table}"]:
            with pytest.raises(DBAPIError):
                with pg.begin() as conn:
                    conn.execute(text(statement))
        with pg.connect() as conn:
            assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 1
