"""Opt-in concurrent writes to disposable loopback PostgreSQL, never Neon."""

import importlib.util
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import jwt
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError
from test_delivery_drafts import payload
from test_payment_concurrency_postgres import local_postgres as _pg

import config
import delivery_draft_api as api
from delivery_draft_models import DeliveryDraft, DeliveryDraftAudit
from models import User

local_postgres = _pg


def signed(value, actor_id):
    sha = api.digest(api.canonical(api.DraftInput(**value)))
    token = jwt.encode(
        {
            "aud": api.AUDIENCE,
            "exp": int(time.time()) + 600,
            "actor_id": actor_id,
            "payload_sha256": sha,
            "environment": "staging",
        },
        config.SECRET_KEY,
        algorithm=config.ALGORITHM,
    )
    return api.SaveInput(**value, preview_token=token)


@pytest.mark.parametrize("mode", ["create-same", "edit-same", "edit-different"])
def test_true_parallel_create_and_version_updates(local_postgres, monkeypatch, mode):
    factory = local_postgres
    monkeypatch.setattr(config, "APP_ENV", "staging")
    with factory() as db:
        admin = User(
            username=mode,
            email=mode + "@example.test",
            hashed_password="synthetic-only",
            is_verified=True,
            is_admin=True,
        )
        db.add(admin)
        db.commit()
        uid = admin.id
        draft_id = None
        if mode.startswith("edit"):
            value = payload(request_key="seed-" + mode)
            first = api.save(db, admin, signed(value, uid))
            draft_id = first["draft_id"]
    values = []
    for i in range(2):
        value = payload(
            request_key="race-" + mode, draft_id=draft_id, expected_version=1 if draft_id else 0
        )
        if mode == "edit-different":
            value.update(request_key="race-" + mode + str(i), name="Competing edit " + str(i))
        values.append(signed(value, uid))
    barrier = Barrier(2)
    original = api.existing

    def synchronized(db, value):
        row = original(db, value)
        barrier.wait(timeout=30)
        return row

    monkeypatch.setattr(api, "existing", synchronized)

    def worker(i):
        with factory() as db:
            try:
                return api.save(db, db.get(User, uid), values[i])
            except HTTPException as error:
                assert error.status_code == 409
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, range(2)))
    successful = [r for r in results if r is not None]
    if mode == "edit-different":
        assert len(successful) == 1
    else:
        assert len(successful) == 2 and successful[0] == successful[1]
    did = successful[0]["draft_id"]
    with factory() as db:
        row = db.get(DeliveryDraft, did)
        assert row.version == (2 if draft_id else 1)
        assert db.query(DeliveryDraftAudit).filter_by(draft_id=did).count() == row.version
        assert row.route["active"] is False


def test_postgres_draft_audit_guards(local_postgres):
    factory = local_postgres
    with factory() as db:
        spec = importlib.util.spec_from_file_location(
            "draft_guards", Path("alembic/versions/0033_delivery_drafts.py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.op = Operations(MigrationContext.configure(db.connection()))
        module.immutable_guards()
        db.commit()
    for statement in [
        "UPDATE delivery_draft_audits SET reason=reason",
        "DELETE FROM delivery_draft_audits",
    ]:
        with factory() as db:
            with pytest.raises(DatabaseError, match="immutable"):
                db.execute(text(statement))
            db.rollback()
