"""Actual profile migration and concurrency on the disposable CI PostgreSQL only."""

import importlib.util
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlalchemy.orm import Session

import customer_profile as service
from models import User
from tests.test_purchase_identity_postgres import pg  # noqa: F401


@pytest.fixture
def profiles(pg, monkeypatch):  # noqa: F811 - imported disposable pytest fixture
    monkeypatch.setenv("CUSTOMER_PROFILE_ENABLED", "1")
    monkeypatch.setenv("SECRET_KEY", "synthetic-contact-postgres-only")
    path = Path(__file__).parents[1] / "alembic/versions/0041_customer_profiles.py"
    spec = importlib.util.spec_from_file_location("contact_pg_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pg.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
    return pg


def test_concurrent_name_edits_require_fresh_version(profiles):
    barrier = Barrier(2)

    def edit(label):
        with Session(profiles) as db:
            user = db.get(User, 1)
            barrier.wait(timeout=20)
            try:
                result = service.save_name(db, user, dict(version=0, display_name=label))
                db.commit()
                return result["version"]
            except HTTPException as error:
                db.rollback()
                assert error.status_code == 409
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(edit, ["Synthetic First", "Synthetic Second"]))
    assert result.count(1) == 1 and result.count("conflict") == 1


def test_concurrent_same_email_request_reserves_one_send(profiles):
    barrier = Barrier(2)

    def reserve(_):
        with Session(profiles) as db:
            user = db.get(User, 1)
            barrier.wait(timeout=20)
            row, code = service.request_email(
                db, user, dict(email="synthetic@example.com", request_key="a" * 32)
            )
            rid = row.id
            db.commit()
            return rid, code is not None

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(reserve, [1, 2]))
    assert result[0][0] == result[1][0] and sum(code for _, code in result) == 1
