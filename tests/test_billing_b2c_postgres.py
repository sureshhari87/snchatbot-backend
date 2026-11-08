"""Disposable local PostgreSQL races; never uses configured DATABASE_URL."""

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError
from test_payment_concurrency_postgres import local_postgres as local_postgres
from test_savings_concurrency_postgres import race

from billing_b2c_models import BillingSupplyEvent, InvoiceAdjustment, TaxApproval
from billing_b2c_policy import ApprovalInput, record
from models import User


def test_approval_race_and_immutable_migration(local_postgres, monkeypatch):
    engine = local_postgres.kw["bind"]
    path = Path(__file__).parents[1] / "alembic/versions/0042_b2c_gst.py"
    spec = importlib.util.spec_from_file_location("b2c_pg_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as connection:
        for table in [
            InvoiceAdjustment.__table__,
            BillingSupplyEvent.__table__,
            TaxApproval.__table__,
        ]:
            table.drop(connection)
        connection.execute(
            text(
                "CREATE OR REPLACE FUNCTION sona_billing_immutable() RETURNS trigger AS $$ BEGIN RAISE EXCEPTION 'Billing records are immutable'; END; $$ LANGUAGE plpgsql"
            )
        )
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        migration.downgrade()
        migration.upgrade()
    with local_postgres() as db:
        user = User(
            username="b2c-pg",
            email="b2c-pg@example.invalid",
            hashed_password="unused",
            is_admin=True,
        )
        db.add(user)
        db.commit()
        identity = user.id
    payload = ApprovalInput(
        scope="rule:rounding",
        expected_version=0,
        status="approved",
        classification="SYNTHETIC",
        treatment="line_component_half_up",
        accountant_name="Synthetic accountant",
        written_approval_reference="Synthetic reference",
        reason="Local test",
    )
    from fastapi import HTTPException

    def work(index):
        with local_postgres() as db:
            try:
                result = record(db, payload, db.get(User, identity))
                db.commit()
                return result["version"]
            except HTTPException:
                db.rollback()
                return "version_conflict"

    assert sorted(race(work), key=str) == [1, "version_conflict"]
    with engine.begin() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM billing_tax_approvals")).scalar_one() == 1
        )
        for statement in [
            "DELETE FROM billing_tax_approvals",
            "UPDATE billing_tax_approvals SET status='revoked'",
        ]:
            with pytest.raises(DatabaseError), connection.begin_nested():
                connection.execute(text(statement))
        for table in migration.TABLES:
            count = connection.execute(
                text("SELECT count(*) FROM pg_trigger WHERE tgname=:name"),
                {"name": table + "_immutable"},
            ).scalar_one()
            assert count == 1
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        with pytest.raises(RuntimeError, match="preservation"):
            migration.downgrade()


def test_real_0041_to_0042_upgrade_and_empty_rollback(local_postgres, tmp_path):
    import os
    import subprocess
    import sys
    from uuid import uuid4

    from sqlalchemy import create_engine, inspect
    from sqlalchemy.orm import Session

    root = Path(__file__).parents[1]
    base = local_postgres.kw["bind"].url
    assert base.host == "127.0.0.1" and base.database == "postgres" and base.password is None
    name = "b2c_chain_" + uuid4().hex
    control = create_engine(base, isolation_level="AUTOCOMMIT")
    engine = None
    with control.connect() as connection:
        connection.execute(text('CREATE DATABASE "' + name + '"'))
    try:
        url = base.set(database=name)
        engine = create_engine(url)
        env = dict(
            os.environ,
            APP_ENV="test",
            TESTING="1",
            DATABASE_URL=url.render_as_string(hide_password=False),
            RUN_MIGRATIONS_ON_STARTUP="0",
            BILLING_SALES_ENABLED="0",
            BILLING_DOCUMENTS_ENABLED="0",
        )

        def migrate(direction, revision):
            with (tmp_path / (direction + "_" + revision + ".log")).open("wb") as output:
                result = subprocess.run(
                    [sys.executable, "-m", "alembic", direction, revision],
                    cwd=root,
                    env=env,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    timeout=180,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            assert result.returncode == 0, "Local migration failed; inspect synthetic test logs"

        migrate("upgrade", "0041_customer_profiles")
        with Session(engine) as db:
            user = User(
                username="b2c-preservation",
                email="preserved@example.invalid",
                hashed_password="unused",
                is_admin=True,
            )
            db.add(user)
            db.commit()
        with engine.connect() as connection:
            tables = inspect(connection).get_table_names()
            before = {
                table: connection.execute(
                    text(
                        'SELECT row_to_json(t)::text FROM "'
                        + table
                        + '" t ORDER BY row_to_json(t)::text'
                    )
                )
                .scalars()
                .all()
                for table in tables
                if table != "alembic_version"
            }
        migrate("upgrade", "0042_b2c_gst")
        for direction, revision in [
            ("downgrade", "0041_customer_profiles"),
            ("upgrade", "0042_b2c_gst"),
        ]:
            migrate(direction, revision)
        with engine.connect() as connection:
            for table, rows in before.items():
                assert (
                    connection.execute(
                        text(
                            'SELECT row_to_json(t)::text FROM "'
                            + table
                            + '" t ORDER BY row_to_json(t)::text'
                        )
                    )
                    .scalars()
                    .all()
                    == rows
                )
            for table in (
                "billing_tax_approvals",
                "billing_invoice_adjustments",
                "billing_supply_events",
            ):
                assert (
                    connection.execute(text('SELECT count(*) FROM "' + table + '"')).scalar_one()
                    == 0
                )
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "0042_b2c_gst"
            )
    finally:
        if engine:
            engine.dispose()
        with control.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:name AND pid<>pg_backend_pid()"
                ),
                {"name": name},
            )
            connection.execute(text('DROP DATABASE "' + name + '"'))
        control.dispose()
