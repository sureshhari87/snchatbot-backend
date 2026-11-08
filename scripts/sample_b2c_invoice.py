"""Generate clearly labelled synthetic review PDFs without a database/provider."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from billing_b2c_output import pdf  # noqa: E402
from billing_sales import SaleLine, calculate  # noqa: E402


def sample(state="33"):
    line = SaleLine(
        source_item_id=1,
        description="SYNTHETIC gold ring",
        hsn="7113",
        quantity=1,
        uqc="NOS",
        material="Gold",
        purity="22K",
        huids=["TST123"],
        gross_weight_micrograms_each=6000000,
        net_weight_micrograms_each=5500000,
        metal_rate_paise_per_gram=700000,
        wastage_basis_points=500,
        making_paise=250000,
        huid_charge_paise=4500,
        gst_basis_points=300,
    )
    gift = SaleLine(
        source_item_id=2,
        description="SYNTHETIC gift - test classification only",
        hsn="7117",
        quantity=1,
        uqc="NOS",
        material="Gifts",
        fixed_unit_paise=10000,
        gst_basis_points=1800,
    )
    lines, totals = calculate([line, gift], "33", state)
    return dict(
        schema_version=1,
        document_id=("1" if state == "33" else "2") * 32,
        source="merchandise",
        source_id=1,
        document_type="sales_tax_invoice",
        title="GST tax invoice",
        invoice_number="SNJ-26/27-TEST1" if state == "33" else "SNJ-26/27-TEST2",
        issued_at="2026-11-08T10:00:00+00:00",
        currency="INR",
        is_test_document=True,
        issuer=dict(
            legal_name="SONA JEWELLERS - SYNTHETIC SAMPLE",
            billing_address="NO 32, KANSA METTU STREET, MADURAI-625001",
            state_code="33",
            postal_code="625001",
            gstin="33AEYFS4414C1ZZ",
        ),
        buyer=dict(
            kind="retail",
            name="SYNTHETIC CUSTOMER / சோதனை",
            address="Example review address, India",
            state_code=state,
            postal_code="625001" if state == "33" else "560001",
        ),
        place_of_supply_state=state,
        lines=lines,
        additional_charges=[],
        totals=totals,
        tender=dict(
            cash_paise=totals["total_paise"] - 100000, voucher_paise=100000, rewards_paise=0
        ),
        authorized_by="SYNTHETIC SAMPLE - UNSIGNED",
        signature=dict(status="unsigned_test_only"),
        supply_date="2026-11-08",
        supply_reference="SYNTHETIC ready-for-dispatch event",
        tax_approvals=dict(
            notice="All sample classifications and rounding are illustrative test fixtures, not accountant approvals"
        ),
        notice="SYNTHETIC SAMPLE ONLY. No payment, supply, invoice allocation, accountant approval or government IRN exists. Not valid for tax use.",
    )


def main():
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("sample-b2c-output")
    output.mkdir(parents=True, exist_ok=True)
    for state, name in [("33", "intrastate"), ("29", "interstate")]:
        document = sample(state)
        filename = output / ("sample-b2c-" + name)
        filename.with_suffix(".json").write_text(json.dumps(document, indent=2), encoding="utf-8")
        # This non-live URL is visibly labelled private store verification.
        url = "https://example.invalid/billing/verify/" + document["document_id"] + "/" + "0" * 64
        filename.with_suffix(".pdf").write_bytes(pdf(document, url))
    print("Generated two synthetic PDFs and JSON snapshots; no database or provider was used.")


if __name__ == "__main__":
    main()
