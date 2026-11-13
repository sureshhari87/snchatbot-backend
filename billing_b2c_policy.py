"""Explicit written approvals; no automatic classification or historic repricing."""

import json
import os
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import HTTPException
from pydantic import Field, StrictInt, model_validator
from sqlalchemy import text

import config
from billing_b2c_models import TaxApproval
from billing_receipts import canonical, digest, require
from billing_sales import Facts
from models import OrderSnapshotItem, Product, utc_now


class ApprovalInput(Facts):
    scope: Annotated[
        str,
        Field(
            pattern=r"^(product:[1-9][0-9]*|custom:[1-9][0-9]*:[0-9]+|service:[a-z0-9_-]+|rule:[a-z0-9_-]+)$",
            max_length=120,
        ),
    ]
    expected_version: Annotated[StrictInt, Field(ge=0)]
    status: Literal["pending", "approved", "revoked"]
    classification: Annotated[str, Field(min_length=1, max_length=200)]
    tax_classification: Literal["taxable", "nil_rated", "exempt", "non_gst"] = "taxable"
    hsn_sac: Annotated[str, Field(pattern=r"^(?:[0-9]{4}|[0-9]{6}|[0-9]{8})$")] | None = None
    gst_basis_points: Annotated[StrictInt, Field(ge=0, le=10000)] | None = None
    treatment: Annotated[str, Field(min_length=1, max_length=100)]
    accountant_name: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    written_approval_reference: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    reason: Annotated[str, Field(min_length=1, max_length=200)]

    @model_validator(mode="after")
    def approved_facts(self):
        if self.status == "approved":
            if not self.accountant_name or not self.written_approval_reference:
                raise ValueError("Written GST-accountant approval is required")
            if not self.scope.startswith("rule:") and (
                self.hsn_sac is None or self.gst_basis_points is None
            ):
                raise ValueError("An approved product/service needs HSN/SAC and GST rate")
            if not self.scope.startswith("rule:"):
                if (self.tax_classification == "taxable") != (self.gst_basis_points > 0):
                    raise ValueError(
                        "GST rate and taxable/nil/exempt/non-GST classification must agree"
                    )
        return self


def enabled():
    # Production cannot bypass approval checks by turning off a feature flag.
    return config.APP_ENV == "production" or os.getenv("BILLING_B2C_CONTROLS_ENABLED", "0") == "1"


def latest(db, scope):
    return db.query(TaxApproval).filter_by(scope=scope).order_by(TaxApproval.version.desc()).first()


def scope_lock(db, scope, *, shared=False):
    if db.get_bind().dialect.name == "postgresql":
        key = int.from_bytes(bytes.fromhex(digest(scope))[:8], "big", signed=True)
        function = "pg_advisory_xact_lock_shared" if shared else "pg_advisory_xact_lock"
        db.execute(text(f"SELECT {function}(:key)"), {"key": key})


def decision(row):
    require(row is not None and digest(row.snapshot_json) == row.snapshot_sha256)
    return json.loads(row.snapshot_json)


def product_identity(product):
    return {
        key: getattr(product, key) for key in ("id", "sku", "category", "metal", "product_type")
    }


def record(db, payload, actor):
    # One explicit administrative operation per approval. The scope lock serializes
    # first versions across administrators; the unique constraint is a backstop.
    scope_lock(db, payload.scope)
    previous = latest(db, payload.scope)
    require((previous.version if previous else 0) == payload.expected_version)
    if payload.scope.startswith("product:"):
        product = db.get(Product, int(payload.scope.split(":")[1]))
        require(product is not None)
        from jewellery_tax_policy import finished_jewellery

        if (
            finished_jewellery(dict(category=product.category, productType=product.product_type))
            and payload.status == "approved"
        ):
            require(payload.gst_basis_points == 300 and payload.hsn_sac.startswith("7113"))
    identity = uuid4().hex
    snapshot = payload.model_dump(mode="json")
    if payload.status == "approved" and payload.scope.startswith("product:"):
        snapshot["product_identity"] = product_identity(product)
    if payload.status == "approved" and payload.scope.startswith("custom:"):
        from custom_design_api import latest_quote

        quote = latest_quote(db, int(payload.scope.split(":")[1]))
        require(quote is not None)
        snapshot["custom_quote_id"] = quote.id
    snapshot.update(id=identity, version=payload.expected_version + 1)
    text = canonical(snapshot)
    row = TaxApproval(
        id=identity,
        scope=payload.scope,
        version=snapshot["version"],
        status=payload.status,
        snapshot_json=text,
        snapshot_sha256=digest(text),
        actor_id=actor.id,
        created_at=utc_now(),
    )
    db.add(row)
    db.flush()
    return snapshot


def approved(db, scope, treatment=None):
    scope_lock(db, scope, shared=True)
    row = latest(db, scope)
    require(row is not None and row.status == "approved")
    saved = decision(row)
    require(saved["status"] == "approved" and bool(saved.get("written_approval_reference")))
    if treatment is not None:
        require(saved["treatment"] == treatment)
    return saved


def validate(db, source, parent, payload, facts):
    if not enabled():
        return {}
    require(payload.buyer.kind == "retail")
    from billing_b2c_output import STATES

    require(payload.buyer.state_code in STATES and payload.place_of_supply_state in STATES)
    if config.APP_ENV == "production":
        require(payload.buyer.delivery_address is not None)
        require(
            any(line.gst_basis_points > 0 for line in payload.lines)
            or any(charge.gst_basis_points > 0 for charge in payload.additional_charges)
        )
    rules = {"rounding": approved(db, "rule:rounding", "line_component_half_up")}
    mappings = []
    items = (
        {i.id: i for i in db.query(OrderSnapshotItem).filter_by(order_id=parent.id).all()}
        if source == "merchandise"
        else {}
    )
    for index, line in enumerate(payload.lines):
        if source == "merchandise":
            item = items.get(line.source_item_id)
            require(item is not None and item.backend_product_id is not None)
            scope = f"product:{item.backend_product_id}"
        else:
            scope = f"custom:{parent.id}:{index}"
        mapping = approved(db, scope)
        if source == "merchandise":
            product = db.get(Product, item.backend_product_id)
            require(
                product is not None and mapping.get("product_identity") == product_identity(product)
            )
        else:
            require(mapping.get("custom_quote_id") == facts["detail"]["quote_id"])
        require(
            mapping["hsn_sac"] == line.hsn and mapping["gst_basis_points"] == line.gst_basis_points
        )
        if line.material != "Gifts":
            require(mapping["treatment"] == "finished_jewellery_bundled")
        else:
            require(mapping["treatment"] != "finished_jewellery_bundled")
        mappings.append(mapping)
    for charge in payload.additional_charges:
        require(charge.tax_policy_key is not None)
        mapping = approved(db, charge.tax_policy_key)
        require(
            charge.tax_policy_key.startswith("service:")
            and mapping["hsn_sac"] == charge.hsn
            and mapping["gst_basis_points"] == charge.gst_basis_points
        )
        require(
            mapping["treatment"]
            in {"incidental_delivery", "independent_courier", "approved_charge"}
        )
        mappings.append(mapping)
    detail = facts["detail"]
    discount = sum(line.discount_paise for line in payload.lines)
    if discount or detail.get("coupon_discount_paise", 0):
        rules["coupon"] = approved(db, "rule:coupon", "eligible_taxable_discount")
        require(discount == detail.get("coupon_discount_paise", 0))
        # Eligibility is evidenced by the existing saved checkout quote, never
        # inferred from a fresh client-supplied discount or coupon code.
    for name in ("voucher", "rewards"):
        if facts["tender"][name + "_paise"]:
            rules[name] = approved(db, "rule:" + name, "stored_value_tender")
    if source == "custom_design":
        rules["custom_advance"] = approved(
            db, "rule:custom_advance", "supply_invoice_after_verified_payment"
        )
    return dict(mappings=mappings, rules=rules, rounding_policy="line_component_half_up")


def production_gate(db):
    if config.APP_ENV != "production":
        return
    approved(db, "rule:live_release", "domestic_b2c_reviewed")
    if os.getenv("BILLING_IRN_REQUIRED", "0") == "1":
        raise HTTPException(409, "IRN integration is required but not implemented")
    # Unsigned production documents need an explicit written reviewed exemption;
    # owner authorization alone never fabricates that accountant decision.
    approved(db, "rule:issuer_signature", "electronic_document_exemption_reviewed")
