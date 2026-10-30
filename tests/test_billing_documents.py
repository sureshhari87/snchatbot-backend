"""Real posted sources, immutable documents and invoice issuance gates."""

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from test_billing_receipts import ISSUER
from test_custom_design_balances import complete, manual, manufacture, request
from test_custom_design_quotes import create, decide, quote
from test_financial_api import confirm, jewellery, jewellery_verify, purchase
from test_financial_api import db as db
from test_financial_api import provider as provider

import custom_design_api
import custom_design_balance
from billing_document_models import BillingDocument, BillingDocumentAudit, BillingSeries
from billing_sales import SaleLine, calculate
from models import OrderSnapshot, OrderSnapshotItem, utc_now
from rewards_vouchers_models import GiftVoucherEntry, RewardVoucherHold


@pytest.fixture(autouse=True)
def billing(monkeypatch):
    monkeypatch.setenv("BILLING_DOCUMENTS_ENABLED", "1")
    monkeypatch.setenv("BILLING_ISSUER_JSON", json.dumps(ISSUER))
    monkeypatch.setenv("BILLING_SALES_ENABLED", "1")
    monkeypatch.setenv("BILLING_SALES_SERIES_START_CONFIRMED", "1")
    from billing_profile import financial_year

    start, end = financial_year(datetime.now(timezone.utc))
    monkeypatch.setenv("BILLING_SALES_SERIES_CONFIRMED_FY", f"{start}-{end}")
    monkeypatch.setenv(
        "BILLING_TAX_RULES_REVIEW_REFERENCE", "Synthetic accountant-reviewed tax rules"
    )
    monkeypatch.setenv("BILLING_SIGNATORY_NAME", "Synthetic authorized signatory")
    monkeypatch.setenv("BILLING_EINVOICE_APPLICABILITY", "unconfirmed")
    monkeypatch.setattr(custom_design_api, "ENABLED", True)
    monkeypatch.setattr(custom_design_balance, "ENABLED", True)


def receipt(client, headers, source, identity):
    return client.post(f"/billing/payments/{source}/{identity}/receipt", headers=headers, json={})


def sale_payload(item_id=0, name="Synthetic gift", amount=100000):
    return dict(
        buyer=dict(
            kind="retail",
            name="Synthetic buyer",
            address="Synthetic address",
            state_code="33",
            postal_code="625001",
        ),
        place_of_supply_state="33",
        lines=[
            dict(
                source_item_id=item_id,
                description=name,
                hsn="7117",
                quantity=1,
                uqc="NOS",
                material="Gifts",
                fixed_unit_paise=amount,
                gst_basis_points=0,
            )
        ],
        supply_date=datetime.now(timezone.utc).date().isoformat(),
        supply_reference="Synthetic dispatch record",
        audit_reason="Verified final synthetic billing and supply facts",
        final_facts_verified=True,
    )


def test_voucher_receipt_immutable_owned_and_no_second_post(
    client, db, auth_headers, admin_headers, provider, monkeypatch
):
    row = purchase(client, auth_headers)
    assert receipt(client, auth_headers, "voucher", row["id"]).status_code == 409
    assert confirm(client, admin_headers, row["id"]).status_code == 200
    calls = len(provider.calls)
    first = receipt(client, auth_headers, "voucher", row["id"])
    assert first.status_code == 200, first.text
    doc = first.json()["document"]
    assert doc["payment"]["amount_paise"] == 50000
    assert all(
        secret not in json.dumps(doc)
        for secret in ["code_hash", "code_ciphertext", "provider_secret"]
    )
    assert receipt(client, admin_headers, "voucher", row["id"]).status_code == 404
    monkeypatch.setenv("BILLING_ISSUER_JSON", "invalid")
    assert receipt(client, auth_headers, "voucher", row["id"]).json() == first.json()
    assert len(provider.calls) == calls
    assert db.query(GiftVoucherEntry).count() == 1
    assert db.query(BillingDocument).count() == 1 and db.query(BillingDocumentAudit).count() == 1
    path = "/billing/documents/" + doc["document_id"]
    assert client.get(path, headers=admin_headers).status_code == 404
    assert client.get(path).status_code == 401
    assert client.get("/admin" + path, headers=auth_headers).status_code == 403
    assert client.get("/admin" + path, headers=admin_headers).status_code == 200
    assert "no-store" in first.headers["cache-control"]
    assert len(client.get("/billing/documents", headers=auth_headers).json()) == 1


def test_current_voucher_hold_does_not_rewrite_original(client, db, auth_headers, admin_headers):
    row = purchase(client, auth_headers)
    confirm(client, admin_headers, row["id"])
    first = receipt(client, auth_headers, "voucher", row["id"]).json()
    db.add(
        RewardVoucherHold(
            voucher_id=row["id"],
            kind="admin_review",
            reference="synthetic",
            reason="Review",
            actor_id=None,
            created_at=utc_now(),
        )
    )
    db.commit()
    read = client.get(
        "/billing/documents/" + first["document"]["document_id"], headers=auth_headers
    )
    assert read.status_code == 200 and read.json()["review_required"] is True
    assert read.json()["document"] == first["document"]


def test_custom_balance_receipt_uses_verified_quote_and_remains_separate_from_sales(
    client, db, auth_headers, admin_headers
):
    row = create(client, auth_headers).json()
    q = quote(client, admin_headers, row, advance_paise=0).json()
    assert decide(client, auth_headers, row, q).status_code == 200
    assert receipt(client, auth_headers, "custom_balance", row["id"]).status_code == 409
    requested = request(client, auth_headers, row, q).json()
    assert manual(client, admin_headers, row, requested["balance_request"]["id"]).status_code == 200
    first = receipt(client, auth_headers, "custom_balance", row["id"])
    assert first.status_code == 200, first.text
    detail = first.json()["document"]["detail"]
    assert (
        detail["quote_total_paise"] == q["total_paise"]
        and detail["remaining_at_payment_paise"] == 0
    )
    assert manufacture(client, admin_headers, row).status_code == 200
    assert complete(client, admin_headers, row).status_code == 200
    payload = sale_payload(amount=q["total_paise"])
    endpoint = f"/admin/billing/sales/custom_design/{row['id']}/issue"
    issued = client.post(endpoint, headers=admin_headers, json=payload)
    assert issued.status_code == 200, issued.text
    invoice = issued.json()["document"]
    assert (
        invoice["invoice_number"].endswith("-1")
        and invoice["totals"]["total_paise"] == q["total_paise"]
    )
    assert client.post(endpoint, headers=admin_headers, json=payload).json() == issued.json()
    payload["audit_reason"] = "Changed request"
    assert client.post(endpoint, headers=admin_headers, json=payload).status_code == 409
    assert (
        db.query(BillingDocument).count() == 2 and db.query(BillingSeries).one().next_sequence == 2
    )


def posted_order(client, db, headers, provider):
    checkout, _, _ = jewellery(client, headers, db)
    response, _ = jewellery_verify(client, headers, provider, checkout)
    assert response.status_code == 200, response.text
    order = db.query(OrderSnapshot).filter_by(order_reference=checkout["order_id"]).one()
    item = db.query(OrderSnapshotItem).filter_by(order_id=order.id).one()
    order.status = "shipped"
    db.commit()
    return order, item


def test_merchandise_receipt_and_invoice_reconcile_with_frozen_payment(
    client, db, auth_headers, admin_headers, provider
):
    order, item = posted_order(client, db, auth_headers, provider)
    calls = len(provider.calls)
    ack = receipt(client, auth_headers, "merchandise", order.id)
    assert ack.status_code == 200, ack.text
    payload = sale_payload(item.id, item.name)
    endpoint = f"/admin/billing/sales/merchandise/{order.id}"
    preview = client.post(endpoint + "/preview", headers=admin_headers, json=payload)
    assert preview.status_code == 200, preview.text
    assert db.query(BillingSeries).count() == 0
    assert preview.json()["invoice_number_allocated"] is False
    assert client.post(endpoint + "/issue", headers=auth_headers, json=payload).status_code == 403
    issue = client.post(endpoint + "/issue", headers=admin_headers, json=payload)
    assert issue.status_code == 200, issue.text
    assert issue.json()["document"]["tender"]["cash_paise"] == 100000
    assert len(provider.calls) == calls
    assert (
        client.post(endpoint + "/issue", headers=admin_headers, json=payload).json() == issue.json()
    )
    assert db.query(BillingDocument).count() == 2


@pytest.mark.parametrize(
    "change",
    [
        "amount",
        "quantity",
        "item",
        "hold",
        "tax",
        "signatory",
        "series",
        "disabled",
        "business_unconfirmed",
        "business_required",
    ],
)
def test_invoice_review_failure_never_allocates_number(
    client, db, auth_headers, admin_headers, provider, monkeypatch, change
):
    order, item = posted_order(client, db, auth_headers, provider)
    payload = sale_payload(item.id, item.name)
    if change == "amount":
        payload["lines"][0]["fixed_unit_paise"] += 1
    if change == "quantity":
        payload["lines"][0]["quantity"] = 2
    if change == "item":
        payload["lines"][0]["source_item_id"] += 1
    if change == "hold":
        db.add(
            RewardVoucherHold(
                reward_user_id=order.user_id,
                kind="admin_review",
                reference="hold",
                reason="Review",
                created_at=utc_now(),
            )
        )
        db.commit()
    if change == "tax":
        monkeypatch.delenv("BILLING_TAX_RULES_REVIEW_REFERENCE")
    if change == "signatory":
        monkeypatch.delenv("BILLING_SIGNATORY_NAME")
    if change == "series":
        monkeypatch.setenv("BILLING_SALES_SERIES_START_CONFIRMED", "0")
    if change == "disabled":
        monkeypatch.setenv("BILLING_SALES_ENABLED", "0")
    if change.startswith("business"):
        payload["buyer"].update(kind="business", gstin=ISSUER["gstin"])
        if change == "business_required":
            monkeypatch.setenv("BILLING_EINVOICE_APPLICABILITY", "applicable")
    response = client.post(
        f"/admin/billing/sales/merchandise/{order.id}/issue", headers=admin_headers, json=payload
    )
    assert response.status_code in {409, 503}, response.text
    assert db.query(BillingSeries).count() == 0 and db.query(BillingDocument).count() == 0


def test_business_reviewed_not_applicable_can_issue_without_fake_irn(
    client, db, auth_headers, admin_headers, provider, monkeypatch
):
    order, item = posted_order(client, db, auth_headers, provider)
    payload = sale_payload(item.id, item.name)
    payload["buyer"].update(kind="business", gstin=ISSUER["gstin"])
    monkeypatch.setenv("BILLING_EINVOICE_APPLICABILITY", "not_applicable")
    monkeypatch.setenv("BILLING_EINVOICE_REVIEW_REFERENCE", "Synthetic accountant confirmation")
    r = client.post(
        f"/admin/billing/sales/merchandise/{order.id}/issue", headers=admin_headers, json=payload
    )
    assert r.status_code == 200, r.text
    assert r.json()["document"]["einvoice"]["irn"] is None
    assert r.json()["document"]["buyer"]["gstin"] == ISSUER["gstin"]


def test_minor_unit_tax_and_weight_arithmetic_and_diamond_scope():
    line = SaleLine(
        source_item_id=1,
        description="Gold bangle",
        hsn="7113",
        quantity=2,
        uqc="NOS",
        material="Gold",
        purity="22K",
        huids=["ABC123", "ABC124"],
        gross_weight_micrograms_each=12100000,
        net_weight_micrograms_each=12000000,
        metal_rate_paise_per_gram=1000000,
        wastage_basis_points=1500,
        making_paise=100000,
        gst_basis_points=300,
    )
    rows, totals = calculate([line], "33", "33")
    assert rows[0]["metal_value_paise"] == 24000000 and rows[0]["wastage_paise"] == 3600000
    assert (
        totals["cgst_paise"] == 415500
        and totals["sgst_paise"] == 415500
        and totals["igst_paise"] == 0
    )
    _, other = calculate([line], "33", "29")
    assert other["igst_paise"] == 831000 and other["cgst_paise"] == 0
    for bad in [
        dict(quantity=True),
        dict(metal_rate_paise_per_gram=1.5),
        dict(diamond_details="irrelevant"),
        dict(huids=["ABC123", "ABC123"]),
    ]:
        data = line.model_dump()
        data.update(bad)
        with pytest.raises(ValidationError):
            SaleLine(**data)


def test_document_rejects_tampering_and_extra_customer_totals(
    client, db, auth_headers, admin_headers
):
    row = purchase(client, auth_headers)
    confirm(client, admin_headers, row["id"])
    path = f"/billing/payments/voucher/{row['id']}/receipt"
    assert client.post(path, headers=auth_headers, json={"amount_paise": 1}).status_code == 422
    doc = receipt(client, auth_headers, "voucher", row["id"]).json()["document"]
    db.query(BillingDocument).one().snapshot_json = "{}"
    db.commit()
    assert (
        client.get("/billing/documents/" + doc["document_id"], headers=auth_headers).status_code
        == 409
    )


def test_custom_advance_receipt_freezes_payment_time_remaining_balance(
    client, db, auth_headers, admin_headers, monkeypatch
):
    from custom_balance_fakes import Provider

    import main

    fake = Provider()
    monkeypatch.setattr(main, "call_razorpay", fake.call_razorpay)
    monkeypatch.setattr(main, "RAZORPAY_KEY_ID", fake.RAZORPAY_KEY_ID)
    monkeypatch.setattr(main, "create_customer_action_lead", fake.create_customer_action_lead)
    row = create(client, auth_headers).json()
    q = quote(client, admin_headers, row, advance_paise=10000).json()
    assert decide(client, auth_headers, row, q).status_code == 200
    opened = client.post(f"/custom-designs/{row['id']}/checkout", headers=auth_headers)
    assert opened.status_code == 200, opened.text
    oid = opened.json()["order_id"]
    pid = fake.payment(oid)
    verified = client.post(
        f"/custom-designs/{row['id']}/verify",
        headers=auth_headers,
        json=dict(razorpay_order_id=oid, razorpay_payment_id=pid, razorpay_signature="0" * 64),
    )
    assert verified.status_code == 200, verified.text
    doc = receipt(client, auth_headers, "custom_advance", row["id"])
    assert doc.status_code == 200, doc.text
    assert (
        doc.json()["document"]["detail"]["remaining_at_payment_paise"] == q["total_paise"] - 10000
    )
    requested = request(client, auth_headers, row, q).json()
    assert manual(client, admin_headers, row, requested["balance_request"]["id"]).status_code == 200
    assert receipt(client, auth_headers, "custom_advance", row["id"]).json() == doc.json()
    assert db.query(BillingDocument).count() == 1


def test_merchandise_tenders_are_not_extra_receipt_money(
    client, db, auth_headers, admin_headers, provider
):
    from rewards_vouchers_models import RewardAccount

    voucher = purchase(client, auth_headers)
    assert confirm(client, admin_headers, voucher["id"]).status_code == 200
    code = client.post(f"/vouchers/{voucher['id']}/code", headers=auth_headers).json()["code"]
    # Synthetic opening points are test-only and never part of production billing.
    account = db.query(RewardAccount).first()
    if account:
        account.balance_points = 10
    else:
        from models import User

        user = db.query(User).filter_by(is_admin=False).first()
        account = RewardAccount(
            user_id=user.id, balance_points=10, reserved_points=0, created_at=utc_now()
        )
        db.add(account)
    db.commit()
    checkout, _, _ = jewellery(
        client, auth_headers, db, gift_voucher_code=code, reward_points_requested=10
    )
    verified, _ = jewellery_verify(client, auth_headers, provider, checkout)
    assert verified.status_code == 200, verified.text
    order = db.query(OrderSnapshot).filter_by(order_reference=checkout["order_id"]).one()
    result = receipt(client, auth_headers, "merchandise", order.id)
    assert result.status_code == 200, result.text
    doc = result.json()["document"]
    assert doc["payment"]["amount_paise"] == 49000
    assert doc["tender"] == dict(cash_paise=49000, voucher_paise=50000, rewards_paise=1000)


def test_saved_invoice_read_is_owned_and_never_issues(
    client, db, auth_headers, admin_headers, provider
):
    order, item = posted_order(client, db, auth_headers, provider)
    path = f"/billing/sales/merchandise/{order.id}"
    assert client.get(path, headers=auth_headers).status_code == 404
    assert db.query(BillingDocument).count() == 0
    payload = sale_payload(item.id, item.name)
    issued = client.post(
        f"/admin/billing/sales/merchandise/{order.id}/issue", headers=admin_headers, json=payload
    )
    assert issued.status_code == 200, issued.text
    assert client.get(path, headers=auth_headers).json() == issued.json()
    assert client.get(path, headers=admin_headers).status_code == 404
    facts = client.get(f"/admin/billing/sales/merchandise/{order.id}/facts", headers=admin_headers)
    assert facts.status_code == 200 and facts.json()["final_tax_and_item_facts_verified"] is False
    assert (
        client.get(
            f"/admin/billing/sales/merchandise/{order.id}/facts", headers=auth_headers
        ).status_code
        == 403
    )


def test_additional_charge_arithmetic_is_explicit_and_exact():
    from billing_sales import AdditionalCharge, calculate_charges

    charge = AdditionalCharge(
        description="Synthetic delivery", hsn="996812", taxable_paise=10000, gst_basis_points=1800
    )
    local = calculate_charges([charge], "33", "33")[0]
    assert (
        local["cgst_paise"] == 900 and local["sgst_paise"] == 900 and local["total_paise"] == 11800
    )
    inter = calculate_charges([charge], "33", "29")[0]
    assert inter["igst_paise"] == 1800 and inter["cgst_paise"] == 0


def test_invoice_with_approved_charge_reconciles_without_adding_payment(
    client, db, auth_headers, admin_headers, provider
):
    order, item = posted_order(client, db, auth_headers, provider)
    payload = sale_payload(item.id, item.name, amount=99000)
    payload["additional_charges"] = [
        dict(
            description="Synthetic approved charge",
            hsn="996812",
            taxable_paise=1000,
            gst_basis_points=0,
        )
    ]
    issued = client.post(
        f"/admin/billing/sales/merchandise/{order.id}/issue", headers=admin_headers, json=payload
    )
    assert issued.status_code == 200, issued.text
    doc = issued.json()["document"]
    assert (
        doc["totals"]["total_paise"] == 100000
        and doc["additional_charges"][0]["taxable_paise"] == 1000
    )


def test_invoice_requires_recorded_supply_state(client, db, auth_headers, admin_headers, provider):
    order, item = posted_order(client, db, auth_headers, provider)
    order.status = "placed"
    db.commit()
    payload = sale_payload(item.id, item.name)
    response = client.post(
        f"/admin/billing/sales/merchandise/{order.id}/issue", headers=admin_headers, json=payload
    )
    assert response.status_code == 409
    assert db.query(BillingSeries).count() == 0 and db.query(BillingDocument).count() == 0


def test_production_issuance_requires_real_electronic_signing(
    client, db, auth_headers, admin_headers, provider, monkeypatch
):
    import billing_documents

    order, item = posted_order(client, db, auth_headers, provider)
    payload = sale_payload(item.id, item.name)
    calls = len(provider.calls)
    monkeypatch.setattr(billing_documents.config, "APP_ENV", "production")
    endpoint = f"/admin/billing/sales/merchandise/{order.id}"
    preview = client.post(endpoint + "/preview", headers=admin_headers, json=payload)
    assert preview.status_code == 200
    assert preview.json()["preview"]["signature"]["electronically_signed"] is False
    for _ in range(2):
        stopped = client.post(endpoint + "/issue", headers=admin_headers, json=payload)
        assert stopped.status_code == 409
        assert "electronic invoice signing" in stopped.json()["detail"]
    assert len(provider.calls) == calls
    assert db.query(BillingSeries).count() == 0
    assert db.query(BillingDocument).count() == 0
    assert db.query(BillingDocumentAudit).count() == 0
