"""Actual Alembic + dump/restore rehearsal on disposable loopback databases only."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session
from test_billing_receipts import ISSUER, payment
from test_payment_concurrency_postgres import local_postgres as local_postgres

import billing_documents
import billing_receipts
from models import User
from voucher_funding import create, fund_manual


def fingerprint(engine, tables, sequences):
    result = {}
    with engine.connect() as connection:
        for table in tables:
            identifier = connection.dialect.identifier_preparer.quote(table)
            rows = connection.execute(text(f"SELECT row_to_json(t)::text FROM {identifier} t"))
            encoded = json.dumps(sorted(row[0] for row in rows), separators=(",", ":"))
            result[table] = hashlib.sha256(encoded.encode()).hexdigest()
        result["sequences"] = connection.execute(
            text(
                "SELECT sequencename, last_value FROM pg_sequences WHERE schemaname='public' AND sequencename = ANY(:names) ORDER BY sequencename"
            ),
            {"names": sequences},
        ).all()
    return result


def test_real_upgrade_restore_receipts_and_preserved_history(local_postgres, tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    tools = root.parent / "postgres-tools" / "pgsql" / "bin"
    base = local_postgres.kw["bind"].url
    assert base.host == "127.0.0.1" and base.database == "postgres" and base.password is None
    names = ["billing_chain_" + uuid4().hex for _ in range(2)]
    engines = []
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    env.update(
        APP_ENV="test",
        TESTING="1",
        RUN_MIGRATIONS_ON_STARTUP="0",
        PUSH_OUTBOX_ENABLED="0",
        PUSH_DELIVERY_ENABLED="0",
        BILLING_DOCUMENTS_ENABLED="0",
        BILLING_SALES_ENABLED="0",
    )
    monkeypatch.setenv("BILLING_ISSUER_JSON", json.dumps(ISSUER))
    monkeypatch.setenv("VOUCHER_CODE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("BILLING_SALES_ENABLED", "0")

    def run(args, label, database):
        settings = dict(
            env, DATABASE_URL=base.set(database=database).render_as_string(hide_password=False)
        )
        with (tmp_path / (label + ".private.log")).open("wb") as output:
            result = subprocess.run(
                args,
                cwd=root,
                env=settings,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=600,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        assert result.returncode == 0, (
            f"Synthetic local {label} failed; private diagnostics retained"
        )

    def migrate(database, revision, label, direction="upgrade", expected=0):
        settings = dict(
            env, DATABASE_URL=base.set(database=database).render_as_string(hide_password=False)
        )
        with (tmp_path / (label + ".private.log")).open("wb") as output:
            result = subprocess.run(
                [sys.executable, "-m", "alembic", direction, revision],
                cwd=root,
                env=settings,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=600,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        assert (result.returncode == 0) == (expected == 0), (
            f"Synthetic local {label} outcome differs; private diagnostics retained"
        )

    control = create_engine(base, isolation_level="AUTOCOMMIT")
    try:
        with control.connect() as connection:
            for name in names:
                assert name.startswith("billing_chain_") and name.replace("_", "").isalnum()
                connection.execute(text('CREATE DATABASE "' + name + '"'))
                engines.append(create_engine(base.set(database=name)))
        source, restored = engines
        migrate(names[0], "0036_review_photos", "baseline_0036")
        with Session(source) as db:
            owner = User(
                username="synthetic-billing-chain",
                email="chain@example.test",
                hashed_password="unused",
                is_verified=True,
                is_admin=True,
            )
            db.add(owner)
            db.flush()
            savings_payment, _ = payment(db, owner, owner)
            voucher, _ = create(
                db,
                purchaser_id=owner.id,
                request_key="chain-voucher",
                amount_paise=50000,
                payment_mode="manual",
            )
            fund_manual(
                db,
                voucher_id=voucher.id,
                actor_id=owner.id,
                confirmed_paise=50000,
                reference="synthetic-receipt",
                reason="Synthetic migration preservation",
            )
            db.commit()
            owner_id, savings_id, voucher_id = owner.id, savings_payment.id, voucher.id
        tables = [name for name in inspect(source).get_table_names() if name != "alembic_version"]
        with source.connect() as connection:
            sequences = (
                connection.execute(
                    text(
                        "SELECT sequencename FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename"
                    )
                )
                .scalars()
                .all()
            )
        baseline = fingerprint(source, tables, sequences)
        archive = tmp_path / "synthetic-0036.dump"
        flags = ["-h", "127.0.0.1", "-p", str(base.port), "-U", "postgres"]
        run(
            [
                str(tools / "pg_dump.exe"),
                *flags,
                "-Fc",
                "--file",
                str(archive),
                "--dbname",
                names[0],
            ],
            "dump",
            names[0],
        )
        assert archive.is_file() and archive.stat().st_size > 0
        run([str(tools / "pg_restore.exe"), "--list", str(archive)], "archive_read", names[0])
        run(
            [
                str(tools / "pg_restore.exe"),
                *flags,
                "--exit-on-error",
                "--no-owner",
                "--no-privileges",
                "--dbname",
                names[1],
                str(archive),
            ],
            "restore",
            names[1],
        )
        assert fingerprint(restored, tables, sequences) == baseline
        migrate(names[1], "0038_billing_documents", "upgrade_0037_0038")
        assert fingerprint(restored, tables, sequences) == baseline
        with restored.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "0038_billing_documents"
            )
            assert connection.execute(text("SELECT count(*) FROM billing_series")).scalar_one() == 0
        # Empty rollback/re-upgrade exercises the shared immutable-trigger function lifecycle.
        migrate(names[1], "0036_review_photos", "empty_rollback", direction="downgrade")
        assert fingerprint(restored, tables, sequences) == baseline
        migrate(names[1], "0038_billing_documents", "repeat_upgrade")
        with Session(restored) as db:
            first = billing_receipts.issue_savings(db, owner_id, savings_id)
            second = billing_receipts.issue_savings(db, owner_id, savings_id)
            assert first == second
            doc = billing_documents.issue_receipt(db, "voucher", voucher_id, owner_id)
            assert doc == billing_documents.issue_receipt(db, "voucher", voucher_id, owner_id)
            db.commit()
        assert fingerprint(restored, tables, sequences) == baseline
        migrate(
            names[1],
            "0036_review_photos",
            "history_rollback_rejected",
            direction="downgrade",
            expected=1,
        )
        with restored.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "0038_billing_documents"
            )
            assert (
                connection.execute(text("SELECT count(*) FROM billing_receipts")).scalar_one() == 1
            )
            assert (
                connection.execute(text("SELECT count(*) FROM billing_receipt_audit")).scalar_one()
                == 1
            )
            assert (
                connection.execute(text("SELECT count(*) FROM billing_documents")).scalar_one() == 1
            )
            assert (
                connection.execute(text("SELECT count(*) FROM billing_document_audit")).scalar_one()
                == 1
            )
            assert connection.execute(text("SELECT count(*) FROM billing_series")).scalar_one() == 0
        assert fingerprint(source, tables, sequences) == baseline
        assert fingerprint(restored, tables, sequences) == baseline
    finally:
        for engine in engines:
            engine.dispose()
        with control.connect() as connection:
            for name in names:
                connection.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:name AND pid <> pg_backend_pid()"
                    ),
                    {"name": name},
                )
                connection.execute(text('DROP DATABASE IF EXISTS "' + name + '"'))
        control.dispose()
