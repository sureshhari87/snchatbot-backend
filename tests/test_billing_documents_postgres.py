"""Opt-in real invoice/receipt races and immutable migration enforcement."""

import importlib.util
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError
from test_billing_documents import sale_payload
from test_billing_receipts import ISSUER
from test_payment_concurrency_postgres import local_postgres as local_postgres
from test_savings_concurrency_postgres import race

import billing_documents
import billing_sales
from billing_document_models import BillingDocument, BillingDocumentAudit, BillingSeries
from billing_profile import financial_year
from models import OrderSnapshot, OrderSnapshotItem, User, utc_now
from rewards_vouchers_models import GiftVoucherEntry
from rewards_vouchers_reservation_models import FinancialReservation, FinancialReservationEvent
from rewards_vouchers_rules import calculate_checkout
from rewards_vouchers_transactions import digest
from voucher_funding import create, fund_manual


@pytest.fixture(autouse=True)
def billing(monkeypatch):
    monkeypatch.setenv("BILLING_ISSUER_JSON", json.dumps(ISSUER))
    monkeypatch.setenv("BILLING_SALES_ENABLED", "1")
    monkeypatch.setenv("BILLING_SALES_SERIES_START_CONFIRMED", "1")
    start, end = financial_year(datetime.now(timezone.utc))
    monkeypatch.setenv("BILLING_SALES_SERIES_CONFIRMED_FY", f"{start}-{end}")
    monkeypatch.setenv("BILLING_TAX_RULES_REVIEW_REFERENCE", "Synthetic reviewed rules")
    monkeypatch.setenv("BILLING_SIGNATORY_NAME", "Synthetic signatory")
    from cryptography.fernet import Fernet

    monkeypatch.setenv("VOUCHER_CODE_ENCRYPTION_KEY", Fernet.generate_key().decode())


def module(number):
    path = next((Path(__file__).resolve().parents[1] / "alembic/versions").glob(number + "_*.py"))
    spec = importlib.util.spec_from_file_location("billing_migration_" + number, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_document_migration_preserves_data_and_rejects_mutation(local_postgres, monkeypatch):
    engine = local_postgres.kw["bind"]
    with engine.begin() as connection:
        for table in [
            BillingDocumentAudit.__table__,
            BillingDocument.__table__,
            BillingSeries.__table__,
        ]:
            table.drop(connection)
        before = connection.execute(text("SELECT count(*) FROM users")).scalar_one()
        # 0037's trigger function already exists only on a migrated database.
        connection.execute(
            text(
                "CREATE OR REPLACE FUNCTION sona_billing_immutable() RETURNS trigger AS $$ BEGIN RAISE EXCEPTION 'Billing documents are immutable'; END; $$ LANGUAGE plpgsql"
            )
        )
        migration = module("0038")
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        migration.downgrade()
        migration.upgrade()
        assert connection.execute(text("SELECT count(*) FROM users")).scalar_one() == before
    with local_postgres() as db:
        owner = User(
            username="billing-races",
            email="billing-races@example.test",
            hashed_password="unused",
            is_verified=True,
            is_admin=True,
        )
        db.add(owner)
        db.flush()
        voucher, _ = create(
            db,
            purchaser_id=owner.id,
            request_key="race-voucher",
            amount_paise=50000,
            payment_mode="manual",
        )
        fund_manual(
            db,
            voucher_id=voucher.id,
            actor_id=owner.id,
            confirmed_paise=50000,
            reference="synthetic-race",
            reason="Verified synthetic receipt",
        )
        db.commit()
        owner_id, voucher_id = owner.id, voucher.id

    def work(index):
        with local_postgres() as db:
            doc = billing_documents.issue_receipt(db, "voucher", voucher_id, owner_id)
            db.commit()
            return doc["document"]["document_id"]

    assert len(set(race(work))) == 1
    with local_postgres() as db:
        assert (
            db.query(BillingDocument).count() == 1
            and db.query(BillingDocumentAudit).count() == 1
            and db.query(GiftVoucherEntry).count() == 1
        )
    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        for table in ["billing_documents", "billing_document_audit"]:
            with pytest.raises(DatabaseError), connection.begin_nested():
                connection.execute(text("DELETE FROM " + table))
        with pytest.raises(DatabaseError), connection.begin_nested():
            connection.execute(text("UPDATE billing_documents SET snapshot_json='{}'"))
        with pytest.raises(RuntimeError, match="preservation"):
            migration.downgrade()


def test_different_sources_get_unique_consecutive_invoice_numbers(local_postgres):
    with local_postgres() as db:
        owner = db.query(User).filter_by(email="invoice-races@example.test").one_or_none()
        if owner is None:
            owner = User(
                username="invoice-races",
                email="invoice-races@example.test",
                hashed_password="unused",
                is_verified=True,
                is_admin=True,
            )
            db.add(owner)
            db.flush()
        ids = []
        for index in range(2):
            row = OrderSnapshot(
                user_id=owner.id,
                order_reference=f"order_billing_race{index}",
                status="shipped",
                total=1000,
                currency="INR",
                payment_status="verified",
                payment_reference=f"pay_billing_race{index}",
                source="razorpay_checkout",
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            db.add(row)
            db.flush()
            item = OrderSnapshotItem(order_id=row.id, name="Synthetic gift", qty=1, unit_price=1000)
            db.add(item)
            db.flush()
            quote = asdict(calculate_checkout(merchandise_paise=100000))
            reservation = FinancialReservation(
                user_id=owner.id,
                request_key=f"race{index}",
                request_sha256="0" * 64,
                receipt=f"race{index}",
                quote_json=json.dumps(quote),
                payable_paise=100000,
                reward_points=0,
                voucher_paise=0,
                state="posted",
                order_id=row.id,
                provider_order_id=row.order_reference,
                provider_payment_id=row.payment_reference,
                created_at=utc_now(),
            )
            db.add(reservation)
            db.flush()
            evidence = digest(
                dict(
                    reservation_id=reservation.id,
                    order_id=row.id,
                    payment_id=row.payment_reference,
                    quote=quote,
                )
            )
            db.add(
                FinancialReservationEvent(
                    reservation_id=reservation.id,
                    event_key=f"event{index}",
                    action="payment_posted",
                    evidence_sha256=digest(
                        dict(payment_id=row.payment_reference, evidence_sha256=evidence)
                    ),
                    created_at=utc_now(),
                )
            )
            ids.append((row.id, item.id, owner.id))
        db.commit()

    def work(index):
        oid, iid, uid = ids[index]
        with local_postgres() as db:
            value = billing_documents.issue_sales(
                db,
                "merchandise",
                oid,
                billing_sales.SalesInput(**sale_payload(iid)),
                db.get(User, uid),
            )
            db.commit()
            return value["document"]["invoice_number"]

    numbers = race(work)
    assert len(set(numbers)) == 2 and {int(n.rsplit("-", 1)[1]) for n in numbers} == {1, 2}
    with local_postgres() as db:
        assert db.query(BillingSeries).one().next_sequence == 3
