"""Synthetic B2C invoice acceptance. No live gateway or database access."""

import csv
import io
from datetime import datetime, timezone
from decimal import localcontext
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError
from test_billing_documents import billing as billing
from test_billing_documents import posted_order, sale_payload
from test_financial_api import db as db
from test_financial_api import provider as provider

import billing_b2c_policy as policy
import billing_documents
from billing_b2c_models import BillingSupplyEvent, InvoiceAdjustment, TaxApproval
from billing_document_models import BillingDocument, BillingSeries
from billing_profile import IST, financial_year, invoice_number
from billing_sales import SaleLine, calculate, round_ratio
from billing_supply import SupplyInput
from billing_supply import record as record_supply
from models import Product


@pytest.fixture(autouse=True)
def controls(monkeypatch):
    monkeypatch.setenv("BILLING_CREDIT_SERIES_START_CONFIRMED", "1")
    start, end = financial_year(datetime.now(timezone.utc))
    monkeypatch.setenv("BILLING_CREDIT_SERIES_CONFIRMED_FY", f"{start}-{end}")
    monkeypatch.setenv("BILLING_B2C_CONTROLS_ENABLED", "1")
    monkeypatch.setenv("BILLING_VERIFICATION_ENABLED", "0")
    monkeypatch.setenv("BILLING_IRN_REQUIRED", "0")


def approve(db, actor, scope, treatment, **extra):
    if extra.get("gst_basis_points") == 0:
        extra["tax_classification"] = "nil_rated"
    old = policy.latest(db, scope)
    payload = policy.ApprovalInput(
        scope=scope,
        expected_version=old.version if old else 0,
        status="approved",
        classification="SYNTHETIC TEST ONLY",
        treatment=treatment,
        accountant_name="Synthetic accountant",
        written_approval_reference="SYNTHETIC - NOT A BUSINESS APPROVAL",
        reason="Synthetic test setup",
        **extra,
    )
    result = policy.record(db, payload, actor)
    db.commit()
    return result


def ready(client, db, auth_headers, admin_user, provider, *, amount=100000):
    order, item = posted_order(client, db, auth_headers, provider)
    order.status = "placed"
    db.commit()
    approve(db, admin_user, "rule:rounding", "line_component_half_up")
    approve(
        db,
        admin_user,
        f"product:{item.backend_product_id}",
        "fixed_goods",
        hsn_sac="7117",
        gst_basis_points=0,
    )
    payload = sale_payload(item.id, item.name, amount)
    payload["supply_date"] = datetime.now(timezone.utc).astimezone(IST).date().isoformat()
    record_supply(
        db,
        "merchandise",
        order.id,
        SupplyInput(event="ready_for_dispatch", reference=payload["supply_reference"]),
        admin_user,
    )
    db.commit()
    return order, item, payload


def issue(client, db, auth_headers, admin_headers, admin_user, provider):
    order, item, payload = ready(client, db, auth_headers, admin_user, provider)
    result = client.post(
        f"/admin/billing/sales/merchandise/{order.id}/issue", headers=admin_headers, json=payload
    )
    assert result.status_code == 200, result.text
    return order, item, payload, result.json()["document"]


def test_decimal_rounding_context_and_financial_year_boundaries():
    with localcontext() as context:
        context.prec = 3
        assert round_ratio(1000033 * 300, 20000) == 15000
        assert round_ratio(1, 2) == 1
        assert round_ratio(9223372036854775807 * 9999, 10000) == 9222449699651090329
    for value in (True, 1.5):
        with pytest.raises(ValueError):
            round_ratio(value, 1)
    before = datetime(2026, 3, 31, 18, 29, tzinfo=timezone.utc)
    after = datetime(2026, 3, 31, 18, 30, tzinfo=timezone.utc)
    assert financial_year(before) == (2025, 2026)
    assert financial_year(after) == (2026, 2027)
    assert invoice_number(prefix="SNJ", issued_at=after, sequence=999999) == "SNJ-26/27-999999"
    with pytest.raises(ValueError):
        invoice_number(prefix="SNJ", issued_at=after, sequence=10000000)


def test_mixed_rate_and_bundled_component_arithmetic():
    gold = SaleLine(
        source_item_id=1,
        description="Synthetic gold",
        hsn="7113",
        quantity=1,
        uqc="NOS",
        material="Gold",
        purity="22K",
        huids=["ABC123"],
        gross_weight_micrograms_each=1000000,
        net_weight_micrograms_each=1000000,
        metal_rate_paise_per_gram=10000,
        making_paise=1000,
        huid_charge_paise=100,
        wastage_basis_points=1000,
        discount_paise=100,
        gst_basis_points=300,
    )
    gift = SaleLine(
        source_item_id=2,
        description="Synthetic gift",
        hsn="7117",
        quantity=1,
        uqc="NOS",
        material="Gifts",
        fixed_unit_paise=10000,
        gst_basis_points=1800,
    )
    rows, totals = calculate([gold, gift], "33", "33")
    assert rows[0]["taxable_paise"] == 12000
    assert rows[0]["cgst_paise"] == rows[0]["sgst_paise"] == 180
    assert totals["total_paise"] == 24160
    _, interstate = calculate([gold, gift], "33", "29")
    assert interstate["igst_paise"] == 2160 and interstate["cgst_paise"] == 0
    invalid = gift.model_dump()
    invalid["discount_paise"] = 10001
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        calculate([SaleLine(**invalid)], "33", "33")


@pytest.mark.parametrize(
    "change",
    [
        "pending",
        "sku_changed",
        "revoked",
        "mapping",
        "rounding",
        "unpaid",
        "late",
        "backdate",
        "coupon",
        "business",
        "irn",
    ],
)
def test_b2c_gates_do_not_allocate_number(
    client, db, auth_headers, admin_headers, admin_user, provider, monkeypatch, change
):
    order, item, payload = ready(client, db, auth_headers, admin_user, provider)
    scope = f"product:{item.backend_product_id}"
    if change in {"pending", "revoked"}:
        policy.record(
            db,
            policy.ApprovalInput(
                scope=scope,
                expected_version=1,
                status=change,
                classification="Needs review",
                treatment="fixed_goods",
                reason="Synthetic review",
            ),
            admin_user,
        )
    elif change == "sku_changed":
        db.get(Product, item.backend_product_id).sku = "SYNTHETIC-CHANGED-SKU"
    elif change == "mapping":
        payload["lines"][0]["hsn"] = "9999"
    elif change == "rounding":
        approve(db, admin_user, "rule:rounding", "unsupported_rounding_method")
    elif change == "unpaid":
        order.payment_status = "pending"
    elif change == "late":
        order.status = "shipped"
    elif change == "backdate":
        payload["supply_date"] = "2025-01-01"
    elif change == "coupon":
        payload["lines"][0]["discount_paise"] = 100
        payload["lines"][0]["fixed_unit_paise"] += 100
    elif change == "business":
        from test_billing_receipts import ISSUER

        payload["buyer"].update(kind="business", gstin=ISSUER["gstin"])
    elif change == "irn":
        monkeypatch.setenv("BILLING_IRN_REQUIRED", "1")
    db.commit()
    result = client.post(
        f"/admin/billing/sales/merchandise/{order.id}/issue", headers=admin_headers, json=payload
    )
    assert result.status_code == 409, result.text
    assert db.query(BillingDocument).count() == db.query(BillingSeries).count() == 0


def test_approval_requires_written_reference_and_preserves_jewellery(
    client, db, admin_user, auth_headers
):
    with pytest.raises(ValidationError):
        policy.ApprovalInput(
            scope="rule:rounding",
            expected_version=0,
            status="approved",
            classification="Rounding",
            treatment="line_component_half_up",
            reason="Missing written approval",
        )
    product = db.query(Product).first()
    product.category = "Gold"
    product.product_type = "Ring"
    db.commit()
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        approve(
            db,
            admin_user,
            f"product:{product.id}",
            "finished_jewellery_bundled",
            hsn_sac="7113",
            gst_basis_points=500,
        )
    db.rollback()
    result = client.post("/admin/billing/tax-approvals", headers=auth_headers, json={})
    assert result.status_code == 403


def test_idempotent_issue_pdf_privacy_qr_and_export(
    client, db, auth_headers, admin_headers, admin_user, provider, monkeypatch
):
    order, item, payload, doc = issue(client, db, auth_headers, admin_headers, admin_user, provider)
    path = f"/admin/billing/sales/merchandise/{order.id}/issue"
    for _ in range(2):
        assert client.post(path, headers=admin_headers, json=payload).json()["document"] == doc
    assert db.query(BillingDocument).count() == 1
    assert db.query(BillingSeries).one().next_sequence == 2
    monkeypatch.setenv("BILLING_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("BILLING_VERIFICATION_KEY", "synthetic-verification-key-32-characters")
    monkeypatch.setenv("BILLING_VERIFICATION_BASE_URL", "https://store.example.invalid/sona")
    from billing_b2c_output import verification_token

    row = db.get(BillingDocument, doc["document_id"])
    url = f"/billing/verify/{row.id}/{verification_token(row)}"
    result = client.get(url)
    assert result.json() == dict(
        valid=True,
        status="issued",
        verification_type="private_store_verification",
        government_irn=False,
    )
    assert "no-store" in result.headers["cache-control"]
    assert client.get(url[:-1] + ("0" if url[-1] != "0" else "1")).status_code == 404
    pdf_path = f"/billing/documents/{row.id}/pdf"
    assert client.get(pdf_path).status_code == 401
    assert client.get(pdf_path, headers=admin_headers).status_code == 404
    result = client.get(pdf_path, headers=auth_headers)
    assert result.status_code == 200, result.text
    assert result.content.startswith(b"%PDF-")
    from pypdf import PdfReader

    contents = "\n".join(
        page.extract_text() for page in PdfReader(io.BytesIO(result.content)).pages
    )
    for fact in [
        doc["invoice_number"],
        "GSTIN",
        "CGST",
        "SGST",
        "IGST",
        "Place of supply",
        "not a government IRN",
        "Customer electronic signature is not required",
    ]:
        assert fact in contents
    assert client.get("/admin" + pdf_path, headers=auth_headers).status_code == 403
    assert client.get("/admin" + pdf_path, headers=admin_headers).status_code == 200
    today = datetime.fromisoformat(doc["issued_at"]).astimezone(IST).date()
    export = client.get(
        f"/admin/billing/gstr1-b2c.csv?start={today}&end={today}", headers=admin_headers
    )
    assert export.status_code == 200
    records = list(csv.DictReader(io.StringIO(export.text)))
    assert records[0]["category"] == "B2CS" and records[0]["supply_type"] == "intrastate"
    assert doc["buyer"]["name"] not in export.text


def test_partial_credit_cancel_and_verified_refund(
    client, db, auth_headers, admin_headers, admin_user, provider, monkeypatch
):
    order, _, _, doc = issue(client, db, auth_headers, admin_headers, admin_user, provider)
    approve(db, admin_user, "rule:credit_note", "original_component_reversal")
    path = f"/admin/billing/documents/{doc['document_id']}/adjustments"
    credit = dict(
        request_key="partial",
        kind="credit_note",
        reason="Synthetic partial return",
        lines=[dict(line_index=0, taxable_paise=30000)],
    )
    result = client.post(path, headers=admin_headers, json=credit)
    assert result.status_code == 200, result.text
    assert result.json()["totals"]["total_paise"] == 30000
    assert client.post(path, headers=admin_headers, json=credit).json() == result.json()
    original_hash = db.get(BillingDocument, doc["document_id"]).snapshot_sha256
    over = dict(credit, request_key="over", lines=[dict(line_index=0, taxable_paise=70001)])
    assert client.post(path, headers=admin_headers, json=over).status_code == 409
    refund = dict(
        id="rfnd_Synthetic",
        entity="refund",
        payment_id=order.payment_reference,
        amount=30000,
        currency="INR",
        status="processed",
    )
    provider.refunds[refund["id"]] = refund
    body = dict(
        request_key="refund",
        kind="refund",
        reason="Verified synthetic provider refund",
        provider_reference=refund["id"],
    )
    refunded = client.post(path, headers=admin_headers, json=body)
    assert refunded.status_code == 200, refunded.text
    assert refunded.json()["refund_paise"] == 30000
    assert client.post(path, headers=admin_headers, json=body).json() == refunded.json()
    cancel = client.post(
        path,
        headers=admin_headers,
        json=dict(request_key="cancel", kind="cancellation", reason="Synthetic cancellation"),
    )
    assert cancel.status_code == 200, cancel.text
    assert cancel.json()["totals"]["total_paise"] == 70000
    assert db.get(BillingDocument, doc["document_id"]).snapshot_sha256 == original_hash
    assert db.query(InvoiceAdjustment).count() == 3
    credit_id = result.json()["document_id"]
    assert (
        client.get(f"/billing/adjustments/{credit_id}/pdf", headers=auth_headers).status_code == 200
    )
    assert (
        client.get(f"/billing/adjustments/{credit_id}/pdf", headers=admin_headers).status_code
        == 404
    )


def test_refund_pending_or_wrong_payment_is_rejected(
    client, db, auth_headers, admin_headers, admin_user, provider
):
    order, _, _, doc = issue(client, db, auth_headers, admin_headers, admin_user, provider)
    approve(db, admin_user, "rule:credit_note", "original_component_reversal")
    path = f"/admin/billing/documents/{doc['document_id']}/adjustments"
    client.post(
        path,
        headers=admin_headers,
        json=dict(request_key="cancel", kind="cancellation", reason="Synthetic return"),
    )
    for status, payment in [("pending", order.payment_reference), ("processed", "pay_Other")]:
        provider.refunds["rfnd_Bad"] = dict(
            id="rfnd_Bad",
            entity="refund",
            payment_id=payment,
            amount=100,
            currency="INR",
            status=status,
        )
        response = client.post(
            path,
            headers=admin_headers,
            json=dict(
                request_key="bad",
                kind="refund",
                reason="Synthetic test",
                provider_reference="rfnd_Bad",
            ),
        )
        assert response.status_code in {409, 503}
    assert db.query(InvoiceAdjustment).count() == 1


def test_live_issue_cannot_bypass_approval_gate(
    client, db, auth_headers, admin_headers, admin_user, provider, monkeypatch
):
    order, item, payload = ready(client, db, auth_headers, admin_user, provider)
    monkeypatch.setattr(billing_documents.config, "APP_ENV", "production")
    monkeypatch.setenv("BILLING_B2C_CONTROLS_ENABLED", "0")
    path = f"/admin/billing/sales/merchandise/{order.id}/issue"
    assert client.post(path, headers=admin_headers, json=payload).status_code == 409
    assert db.query(BillingSeries).count() == 0


def test_migration_immutability_and_nonempty_downgrade(db, admin_user, monkeypatch):
    import importlib.util

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = Path(__file__).parents[1] / "alembic/versions/0042_b2c_gst.py"
    spec = importlib.util.spec_from_file_location("b2c_migration_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    db.commit()
    connection = db.connection()
    for table in [InvoiceAdjustment.__table__, BillingSupplyEvent.__table__, TaxApproval.__table__]:
        table.drop(connection)
    monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(connection)))
    module.upgrade()
    module.downgrade()
    module.upgrade()
    approve(db, admin_user, "rule:rounding", "line_component_half_up")
    for sql in [
        "UPDATE billing_tax_approvals SET status='revoked'",
        "DELETE FROM billing_tax_approvals",
    ]:
        with pytest.raises(DatabaseError):
            db.execute(text(sql))
        db.rollback()
    monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(db.connection())))
    with pytest.raises(RuntimeError, match="preservation"):
        module.downgrade()


@pytest.mark.parametrize(
    "state,amount,issued,expected",
    [
        ("33", 10000001, "2026-11-08", "B2CS"),
        ("29", 10000000, "2026-11-08", "B2CS"),
        ("29", 10000001, "2026-11-08", "B2CL"),
        ("29", 25000000, "2024-07-31", "B2CS"),
        ("29", 25000001, "2024-07-31", "B2CL"),
    ],
)
def test_gstr1_category_boundaries(state, amount, issued, expected):
    from billing_b2c_output import reporting_category

    doc = dict(
        issuer=dict(state_code="33"),
        place_of_supply_state=state,
        totals=dict(total_paise=amount),
        issued_at=issued + "T12:00:00+05:30",
    )
    assert reporting_category(doc) == (state != "33", expected)


def test_taxed_partial_credits_consume_exact_frozen_residual(
    client, db, auth_headers, admin_headers, admin_user, provider
):
    order, item, payload = ready(client, db, auth_headers, admin_user, provider)
    approve(
        db,
        admin_user,
        f"product:{item.backend_product_id}",
        "finished_jewellery_bundled",
        hsn_sac="7113",
        gst_basis_points=300,
    )
    payload["lines"][0].update(
        material="Gold",
        hsn="7113",
        purity="22K",
        huids=["ABC123"],
        fixed_unit_paise=0,
        gross_weight_micrograms_each=1000000,
        net_weight_micrograms_each=1000000,
        metal_rate_paise_per_gram=97088,
        gst_basis_points=300,
    )
    issued = client.post(
        f"/admin/billing/sales/merchandise/{order.id}/issue", headers=admin_headers, json=payload
    )
    assert issued.status_code == 200, issued.text
    doc = issued.json()["document"]
    assert doc["totals"] == dict(
        taxable_paise=97088, cgst_paise=1456, sgst_paise=1456, igst_paise=0, total_paise=100000
    )
    approve(db, admin_user, "rule:credit_note", "original_component_reversal")
    path = f"/admin/billing/documents/{doc['document_id']}/adjustments"
    notes = []
    for i, amount in enumerate([33333, 33333, 30422]):
        result = client.post(
            path,
            headers=admin_headers,
            json=dict(
                request_key=f"partial{i}",
                kind="credit_note",
                reason="Synthetic partial return",
                lines=[dict(line_index=0, taxable_paise=amount)],
            ),
        )
        assert result.status_code == 200, result.text
        notes.append(result.json())
    for key in ("taxable_paise", "cgst_paise", "sgst_paise", "igst_paise", "total_paise"):
        assert sum(note["totals"][key] for note in notes) == doc["totals"][key]


def test_dispatch_requires_an_issued_invoice(
    client, db, auth_headers, admin_headers, admin_user, provider
):
    order, _, payload = ready(client, db, auth_headers, admin_user, provider)
    path = f"/admin/orders/{order.id}"
    assert client.patch(path, headers=admin_headers, json=dict(status="shipped")).status_code == 409
    assert (
        client.post(
            f"/admin/billing/sales/merchandise/{order.id}/issue",
            headers=admin_headers,
            json=payload,
        ).status_code
        == 200
    )
    assert client.patch(path, headers=admin_headers, json=dict(status="shipped")).status_code == 200


def test_coupon_and_payment_credit_approvals_are_separate(
    client, db, auth_headers, admin_user, provider
):
    from fastapi import HTTPException

    from billing_sales import SalesInput
    from billing_sources import evidence

    order, _, payload = ready(client, db, auth_headers, admin_user, provider)
    facts = evidence(db, "merchandise", order)
    facts["detail"]["coupon_discount_paise"] = 100
    facts["tender"].update(cash_paise=98000, voucher_paise=1000, rewards_paise=1000)
    payload["lines"][0].update(discount_paise=100, fixed_unit_paise=100100)
    for name, treatment in [
        ("coupon", "eligible_taxable_discount"),
        ("voucher", "stored_value_tender"),
        ("rewards", "stored_value_tender"),
    ]:
        with pytest.raises(HTTPException):
            policy.validate(db, "merchandise", order, SalesInput(**payload), facts)
        approve(db, admin_user, "rule:" + name, treatment)
    result = policy.validate(db, "merchandise", order, SalesInput(**payload), facts)
    assert set(result["rules"]) == {"rounding", "coupon", "voucher", "rewards"}
    # A new discount without matching saved quote evidence is rejected.
    facts["detail"]["coupon_discount_paise"] = 0
    with pytest.raises(HTTPException):
        policy.validate(db, "merchandise", order, SalesInput(**payload), facts)


@pytest.mark.parametrize(
    "source", ["custom_advance", "custom_balance", "voucher", "savings", "digigold"]
)
def test_funding_sources_do_not_automatically_issue_sales_tax_invoices(
    client, db, admin_headers, source
):
    response = client.post(
        f"/admin/billing/sales/{source}/1/issue", headers=admin_headers, json=sale_payload()
    )
    assert response.status_code in {404, 422}
    assert db.query(BillingSeries).count() == db.query(BillingDocument).count() == 0


def test_server_pdf_embeds_shaped_tamil_font():
    from pypdf import PdfReader
    from reportlab.pdfbase import pdfmetrics

    from billing_b2c_output import pdf
    from scripts.sample_b2c_invoice import sample

    document = PdfReader(io.BytesIO(pdf(sample())))
    fonts = {
        str(font.get_object()["/BaseFont"])
        for page in document.pages
        for font in page["/Resources"]["/Font"].get_object().values()
    }
    assert any("NotoSansTamil" in name for name in fonts)
    assert pdfmetrics.getFont("SonaTamil").shapable


def test_invoice_number_and_timestamp_share_the_same_financial_year(
    client, db, auth_headers, admin_headers, admin_user, provider, monkeypatch
):
    import billing_supply

    before = datetime(2027, 3, 31, 18, 29, 59)
    after = datetime(2027, 3, 31, 18, 30, 0)
    monkeypatch.setattr(billing_supply, "utc_now", lambda: before)
    order, _, payload = ready(client, db, auth_headers, admin_user, provider)
    payload["supply_date"] = "2027-03-31"
    monkeypatch.setenv("BILLING_SALES_SERIES_CONFIRMED_FY", "2026-2027")
    ticks = iter([before, before, after])
    monkeypatch.setattr(billing_documents, "utc_now", lambda: next(ticks))
    response = client.post(
        f"/admin/billing/sales/merchandise/{order.id}/issue", headers=admin_headers, json=payload
    )
    assert response.status_code == 200, response.text
    document = response.json()["document"]
    issued = datetime.fromisoformat(document["issued_at"])
    assert financial_year(issued) == (2026, 2027)
    assert document["invoice_number"].startswith("SNJ-26/27-")
    assert db.get(BillingDocument, document["document_id"]).created_at == before
