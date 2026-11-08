"""Server PDF, private verification QR and accountant reconciliation exports."""

import csv
import hashlib
import hmac
import io
import os
import re
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, localcontext
from html import escape
from urllib.parse import urlsplit

from fastapi import HTTPException

from billing_profile import IST
from billing_receipts import digest, require

STATES = {
    "01": "Jammu and Kashmir",
    "02": "Himachal Pradesh",
    "03": "Punjab",
    "04": "Chandigarh",
    "05": "Uttarakhand",
    "06": "Haryana",
    "07": "Delhi",
    "08": "Rajasthan",
    "09": "Uttar Pradesh",
    "10": "Bihar",
    "11": "Sikkim",
    "12": "Arunachal Pradesh",
    "13": "Nagaland",
    "14": "Manipur",
    "15": "Mizoram",
    "16": "Tripura",
    "17": "Meghalaya",
    "18": "Assam",
    "19": "West Bengal",
    "20": "Jharkhand",
    "21": "Odisha",
    "22": "Chhattisgarh",
    "23": "Madhya Pradesh",
    "24": "Gujarat",
    "26": "Dadra and Nagar Haveli and Daman and Diu",
    "27": "Maharashtra",
    "29": "Karnataka",
    "30": "Goa",
    "31": "Lakshadweep",
    "32": "Kerala",
    "33": "Tamil Nadu",
    "34": "Puducherry",
    "35": "Andaman and Nicobar Islands",
    "36": "Telangana",
    "37": "Andhra Pradesh",
    "38": "Ladakh",
}


def money(paise):
    with localcontext() as context:
        context.prec = 80
        return f"{Decimal(paise) / Decimal(100):,.2f}"


def state(code):
    return f"{STATES.get(code, 'State requires review')} ({code})"


def verification_token(row):
    key = os.getenv("BILLING_VERIFICATION_KEY", "")
    if len(key.encode()) < 32:
        raise HTTPException(503, "Invoice verification is not configured")
    return hmac.new(
        key.encode(),
        ("sona-private-invoice-v1:" + row.id + ":" + row.snapshot_sha256).encode(),
        hashlib.sha256,
    ).hexdigest()


def verification_url(row):
    if os.getenv("BILLING_VERIFICATION_ENABLED", "0") != "1":
        return None
    base = os.getenv("BILLING_VERIFICATION_BASE_URL", "").rstrip("/")
    parsed = urlsplit(base)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise HTTPException(503, "HTTPS verification base URL is required")
    return f"{base}/billing/verify/{row.id}/{verification_token(row)}"


def verify(db, row, token):
    if row is None or not hmac.compare_digest(verification_token(row), token):
        raise HTTPException(404, "Verification record not found")
    require(digest(row.snapshot_json) == row.snapshot_sha256)
    from billing_adjustments import history

    entries = history(db, row.id)
    status = (
        "cancelled"
        if any(e["kind"] == "cancellation" for e in entries)
        else "credited"
        if any(e["kind"] == "credit_note" for e in entries)
        else "issued"
    )
    # Deliberate allowlist: no names, addresses, email, amounts, payment IDs,
    # invoice number, line items, approval documents or gateway responses.
    return dict(
        valid=True,
        status=status,
        verification_type="private_store_verification",
        government_irn=False,
    )


def pdf(document, qr_url=None):
    from pathlib import Path

    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    stream = io.BytesIO()
    styles = getSampleStyleSheet()
    font = Path(__file__).parent / "assets" / "NotoSans.ttf"
    if not font.is_file():
        raise HTTPException(503, "Invoice font asset is missing")
    if "SonaInvoice" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("SonaInvoice", str(font)))
    tamil_font = font.with_name("NotoSansTamil.ttf")
    if not tamil_font.is_file():
        raise HTTPException(503, "Invoice Tamil font asset is missing")
    if "SonaTamil" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("SonaTamil", str(tamil_font)))
    for style in styles.byName.values():
        style.fontName = "SonaInvoice"
        style.fontSize = 9
        style.leading = 13
        style.shaping = True
    body = []

    def text(value, style="Normal"):
        escaped = re.sub(
            r"[\u0b80-\u0bff]+",
            lambda match: '<font name="SonaTamil">' + match.group() + "</font>",
            escape(str(value), quote=False),
        )
        return Paragraph(escaped, styles[style])

    def add(value):
        body.append(text(value))

    shop, buyer = document["issuer"], document["buyer"]
    body.append(text(document.get("title", "GST tax invoice"), "Title"))
    if document.get("is_test_document"):
        add("SYNTHETIC SAMPLE - NOT VALID FOR TAX USE")
    add(shop["legal_name"])
    add(shop["billing_address"])
    add(f"GSTIN: {shop['gstin']} | State: {state(shop['state_code'])} | PIN: {shop['postal_code']}")
    add(
        f"Number: {document['invoice_number']} | Issued: {datetime.fromisoformat(document['issued_at']).astimezone(IST).strftime('%d-%m-%Y %H:%M IST')}"
    )
    if document.get("original_invoice_number"):
        add(
            f"Against invoice: {document['original_invoice_number']} dated {document['original_invoice_date']}"
        )
        add("Reason: " + document["reason"])
    add(f"Recipient: {buyer['name']} (unregistered domestic B2C)")
    add(
        f"Billing address: {buyer['address']} | {state(buyer['state_code'])} | PIN {buyer['postal_code']}"
    )
    add(
        f"Delivery address: {buyer.get('delivery_address') or buyer['address']} | {state(document['place_of_supply_state'])}"
    )
    add(f"Place of supply: {state(document['place_of_supply_state'])}")
    add("Reverse charge: No | Currency: INR")
    if document.get("supply_date"):
        add(
            f"Supply/dispatch date: {document['supply_date']} | Reference: {document['supply_reference']}"
        )
    body.append(Spacer(1, 4 * mm))
    data = [
        [
            text(x)
            for x in [
                "Description / HSN",
                "Qty / UQC",
                "Taxable INR",
                "GST rate",
                "CGST",
                "SGST",
                "IGST",
                "Total INR",
            ]
        ]
    ]
    for line in document["lines"] + document.get("additional_charges", []):
        label = f"{line['description']} / {line['hsn']}"
        intra = shop["state_code"] == document["place_of_supply_state"]
        half_rate = Decimal(line["gst_basis_points"]) / 200 if intra else Decimal(0)
        full_rate = Decimal(line["gst_basis_points"]) / 100 if not intra else Decimal(0)
        data.append(
            [
                text(label),
                text(f"{line.get('quantity', '-')} {line.get('uqc', '')}"),
                text(money(line["taxable_paise"])),
                text(f"{Decimal(line['gst_basis_points']) / 100}%"),
                text(f"{half_rate}% / {money(line['cgst_paise'])}"),
                text(f"{half_rate}% / {money(line['sgst_paise'])}"),
                text(f"{full_rate}% / {money(line['igst_paise'])}"),
                text(money(line["total_paise"])),
            ]
        )
    table = Table(
        data,
        colWidths=[45 * mm, 16 * mm, 24 * mm, 15 * mm, 19 * mm, 19 * mm, 19 * mm, 25 * mm],
        repeatRows=1,
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    body.append(table)
    body.append(Spacer(1, 3 * mm))
    for line in document["lines"]:
        add(
            f"{line['description']}: value before discount INR {money(line['taxable_paise'] + line.get('discount_paise', 0))}; discount INR {money(line.get('discount_paise', 0))}"
        )
        if "metal_value_paise" not in line:
            continue
        add(
            f"{line['description']}: metal {money(line['metal_value_paise'])}; stones {money(line.get('stone_paise', 0) + line.get('diamond_paise', 0))}; making {money(line.get('making_paise', 0))}; wastage {money(line.get('wastage_paise', 0))}; HUID/hallmark {money(line.get('huid_charge_paise', 0))}; other {money(line.get('other_paise', 0))}; taxable discount {money(line.get('discount_paise', 0))}"
        )
        add(
            f"Purity: {line.get('purity') or '-'} | HUID: {', '.join(line.get('huids', [])) or '-'} | Net grams each: {Decimal(line.get('net_weight_micrograms_each', 0)) / 1000000}"
        )
        if line.get("diamond_details"):
            add("Diamond/stone specifications: " + line["diamond_details"])
        add(
            f"Rates: CGST {Decimal(line['gst_basis_points']) / 200 if line['cgst_paise'] or document['issuer']['state_code'] == document['place_of_supply_state'] else 0}%; SGST same; IGST {Decimal(line['gst_basis_points']) / 100 if document['issuer']['state_code'] != document['place_of_supply_state'] else 0}%"
        )
    totals = document["totals"]
    for key, label in [
        ("taxable_paise", "Taxable value"),
        ("cgst_paise", "CGST"),
        ("sgst_paise", "SGST"),
        ("igst_paise", "IGST"),
        ("total_paise", "Invoice total"),
    ]:
        add(f"{label}: INR {money(totals[key])}")
    if "tender" in document:
        tender = document["tender"]
        add(
            f"Payment allocation: cash/provider INR {money(tender['cash_paise'])}; voucher INR {money(tender['voucher_paise'])}; rewards payment credit INR {money(tender['rewards_paise'])}"
        )
    add(f"Authorized issuer/signatory: {document['authorized_by']}")
    add(
        "Issuer signature status: " + document.get("signature", {}).get("status", "requires_review")
    )
    add("Customer electronic signature is not required.")
    if qr_url:
        import qrcode

        qr = io.BytesIO()
        qrcode.make(qr_url).save(qr, format="PNG")
        qr.seek(0)
        body.append(Image(qr, width=32 * mm, height=32 * mm))
        add("Private store verification QR. This is not a government IRN or government-signed QR.")
    if document.get("notice"):
        add(document["notice"])

    def footer(canvas, doc):
        canvas.setFont("SonaInvoice", 8)
        canvas.drawString(10 * mm, 10 * mm, f"{document['invoice_number']} | Page {doc.page}")

    SimpleDocTemplate(
        stream,
        pagesize=(210 * mm, 297 * mm),
        leftMargin=10 * mm,
        rightMargin=8 * mm,
        topMargin=10 * mm,
        bottomMargin=18 * mm,
        title=document.get("title", "GST invoice"),
        author=shop["legal_name"],
    ).build(body, onFirstPage=footer, onLaterPages=footer)
    return stream.getvalue()


def reporting_category(document):
    inter = document["issuer"]["state_code"] != document["place_of_supply_state"]
    issued = datetime.fromisoformat(document["issued_at"]).astimezone(IST).date()
    threshold = 10000000 if issued.isoformat() >= "2024-08-01" else 25000000
    return inter, "B2CL" if inter and document["totals"]["total_paise"] > threshold else "B2CS"


def reconciliation(db, start, end):
    """Detail export for accountant review, not a GST portal upload payload.

    Current B2CL threshold: interstate invoice value > INR 100000 from Aug 2024.
    Historical pre-Aug-2024 threshold: INR 250000. Credits classified by original.
    """
    from billing_adjustments import saved
    from billing_b2c_models import InvoiceAdjustment
    from billing_document_models import BillingDocument
    from billing_documents import read

    require(start <= end and (end - start).days <= 366)
    records = []
    originals = {}
    lower = datetime.combine(start, time.min, IST).astimezone(timezone.utc).replace(tzinfo=None)
    upper = (
        datetime.combine(end + timedelta(days=1), time.min, IST)
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
    )
    for row in (
        db.query(BillingDocument)
        .filter_by(kind="sales_invoice")
        .filter(BillingDocument.created_at >= lower, BillingDocument.created_at < upper)
        .order_by(BillingDocument.created_at)
        .all()
    ):
        d = read(db, row)["document"]
        originals[row.id] = d
        if start <= datetime.fromisoformat(d["issued_at"]).astimezone(IST).date() <= end:
            records.append((d, d, 1, "invoice"))
    for row in (
        db.query(InvoiceAdjustment)
        .filter(InvoiceAdjustment.kind != "refund")
        .filter(InvoiceAdjustment.created_at >= lower, InvoiceAdjustment.created_at < upper)
        .order_by(InvoiceAdjustment.created_at)
        .all()
    ):
        d = saved(row)
        if start <= datetime.fromisoformat(d["issued_at"]).astimezone(IST).date() <= end:
            if row.invoice_id not in originals:
                originals[row.invoice_id] = read(db, db.get(BillingDocument, row.invoice_id))[
                    "document"
                ]
            records.append((d, originals[row.invoice_id], -1, "credit_note"))
    stream = io.StringIO(newline="")
    fields = [
        "document_type",
        "number",
        "date_ist",
        "original_invoice",
        "supply_type",
        "category",
        "reporting_table",
        "place_of_supply",
        "gst_rate_percent",
        "tax_classification",
        "taxable_inr",
        "cgst_inr",
        "sgst_inr",
        "igst_inr",
        "document_total_inr",
        "rate_group_total_inr",
        "is_test",
    ]
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for d, original, sign, kind in records:
        require(original["buyer"]["kind"] == "retail")
        inter, category = reporting_category(original)
        groups = {}
        for index, line in enumerate(d["lines"] + d.get("additional_charges", [])):
            rate = line["gst_basis_points"]
            mappings = original.get("tax_approvals", {}).get("mappings", [])
            original_index = line.get("line_index", index)
            classification = (
                mappings[original_index].get("tax_classification", "taxable")
                if original_index < len(mappings)
                else "taxable"
            )
            require(rate > 0 or classification in {"nil_rated", "exempt", "non_gst"})
            group = groups.setdefault(
                (rate, classification),
                {k: 0 for k in ("taxable_paise", "cgst_paise", "sgst_paise", "igst_paise")},
            )
            for k in group:
                group[k] += line[k]
        for group_index, ((rate, classification), amounts) in enumerate(groups.items()):
            table = (
                "8"
                if classification != "taxable"
                else ("9B-CDNUR" if kind == "credit_note" else "5")
                if category == "B2CL"
                else "7-net"
            )
            if classification != "taxable":
                table = "8"
            writer.writerow(
                dict(
                    document_type=kind,
                    number=d["invoice_number"],
                    date_ist=datetime.fromisoformat(d["issued_at"]).astimezone(IST).date(),
                    original_invoice=original["invoice_number"],
                    supply_type="interstate" if inter else "intrastate",
                    category=category,
                    reporting_table=table,
                    place_of_supply=original["place_of_supply_state"],
                    gst_rate_percent=str(Decimal(rate) / 100),
                    tax_classification=classification,
                    taxable_inr=money(sign * amounts["taxable_paise"]).replace(",", ""),
                    cgst_inr=money(sign * amounts["cgst_paise"]).replace(",", ""),
                    sgst_inr=money(sign * amounts["sgst_paise"]).replace(",", ""),
                    igst_inr=money(sign * amounts["igst_paise"]).replace(",", ""),
                    document_total_inr=money(sign * d["totals"]["total_paise"]).replace(",", "")
                    if group_index == 0
                    else "",
                    rate_group_total_inr=money(sign * sum(amounts.values())).replace(",", ""),
                    is_test=d["is_test_document"],
                )
            )
    return stream.getvalue()
