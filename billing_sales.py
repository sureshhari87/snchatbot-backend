"""Exact invoice facts and optional e-invoice gate. No IRP network requests."""

import os
from datetime import date
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

from billing_profile import MAX_PAISE, clean_text, gstin_checksum_valid

Money = Annotated[StrictInt, Field(ge=0, le=MAX_PAISE)]
Positive = Annotated[StrictInt, Field(gt=0, le=MAX_PAISE)]
Text = Annotated[str, Field(min_length=1, max_length=200)]


class Facts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def plain_text(cls, value):
        if isinstance(value, str):
            clean_text(value, limit=2000)
        return value


class Buyer(Facts):
    kind: Literal["retail", "business"]
    name: Text
    address: Annotated[str, Field(min_length=1, max_length=1000)]
    state_code: Annotated[str, Field(pattern=r"^(0[1-9]|[12][0-9]|3[0-8])$")]
    postal_code: Annotated[str, Field(pattern=r"^[1-9][0-9]{5}$")]
    gstin: str | None = None

    @model_validator(mode="after")
    def tax_identity(self):
        if self.kind == "business":
            if not gstin_checksum_valid(self.gstin) or self.gstin[:2] != self.state_code:
                raise ValueError("Business GSTIN and billing state must be valid and consistent")
        elif self.gstin is not None:
            raise ValueError("Select business billing to include a GSTIN")
        return self


class SaleLine(Facts):
    source_item_id: Annotated[StrictInt, Field(ge=0, le=2147483647)]
    description: Text
    hsn: Annotated[str, Field(pattern=r"^(?:[0-9]{4}|[0-9]{6}|[0-9]{8})$")]
    quantity: Annotated[StrictInt, Field(gt=0, le=1000)]
    uqc: Literal["NOS", "PCS", "GMS"]
    material: Literal["Gold", "Diamond", "Silver", "Platinum", "Gifts"]
    purity: str | None = None
    colour: str | None = None
    size: str | None = None
    huids: Annotated[
        list[Annotated[str, Field(pattern=r"^[A-Z0-9]{6}$")]], Field(max_length=1000)
    ] = []
    gross_weight_micrograms_each: Money = 0
    net_weight_micrograms_each: Money = 0
    metal_rate_paise_per_gram: Money = 0
    fixed_unit_paise: Money = 0
    wastage_basis_points: Annotated[StrictInt, Field(ge=0, le=10000)] = 0
    making_paise: Money = 0
    huid_charge_paise: Money = 0
    stone_paise: Money = 0
    diamond_paise: Money = 0
    other_paise: Money = 0
    discount_paise: Money = 0
    gst_basis_points: Annotated[StrictInt, Field(ge=0, le=10000)]
    diamond_details: str | None = None

    @model_validator(mode="after")
    def jewellery_facts(self):
        if self.material == "Gifts":
            if not self.fixed_unit_paise or any(
                [
                    self.net_weight_micrograms_each,
                    self.gross_weight_micrograms_each,
                    self.metal_rate_paise_per_gram,
                    self.wastage_basis_points,
                    self.making_paise,
                    self.huid_charge_paise,
                    self.stone_paise,
                    self.diamond_paise,
                    self.other_paise,
                    self.huids,
                    self.purity,
                    self.diamond_details,
                ]
            ):
                raise ValueError("Fixed-price gifts must not invent metal or diamond calculations")
        else:
            if self.gst_basis_points != 300:
                raise ValueError("Finished jewellery GST is fixed at 3%")
            if not self.hsn.startswith("7113"):
                raise ValueError(
                    "Finished precious-metal ornaments require approved HSN 7113 classification"
                )
            allowed = {
                "Gold": {"22K", "18K"},
                "Diamond": {"22K", "18K"},
                "Silver": {"800", "925"},
                "Platinum": {"Pt950"},
            }
            if (
                self.purity not in allowed[self.material]
                or not (0 < self.net_weight_micrograms_each <= self.gross_weight_micrograms_each)
                or not self.metal_rate_paise_per_gram
                or self.fixed_unit_paise
            ):
                raise ValueError("Confirmed purity, billable weight and rate are required")
            if self.material in {"Gold", "Diamond"} and (
                len(self.huids) != self.quantity or len(set(self.huids)) != len(self.huids)
            ):
                raise ValueError("One unique final HUID per gold item is required")
            if self.material not in {"Gold", "Diamond"} and self.huids:
                raise ValueError("Gold HUIDs do not apply to this metal")
        if self.material == "Diamond" and not self.diamond_details:
            raise ValueError("Diamond specifications are required")
        if self.material != "Diamond" and (
            self.diamond_details or self.diamond_paise or self.stone_paise
        ):
            raise ValueError("Diamond/stone specifications belong only to diamond products")
        return self


class AdditionalCharge(Facts):
    description: Text
    hsn: Annotated[str, Field(pattern=r"^(?:[0-9]{4}|[0-9]{6}|[0-9]{8})$")]
    taxable_paise: Money
    gst_basis_points: Annotated[StrictInt, Field(ge=0, le=10000)]


class SalesInput(Facts):
    buyer: Buyer
    place_of_supply_state: Annotated[str, Field(pattern=r"^(0[1-9]|[12][0-9]|3[0-8])$")]
    lines: Annotated[list[SaleLine], Field(min_length=1, max_length=100)]
    additional_charges: Annotated[list[AdditionalCharge], Field(max_length=10)] = []
    supply_date: date
    supply_reference: Text
    audit_reason: Text
    final_facts_verified: StrictBool
    reverse_charge: Literal[False] = False

    @model_validator(mode="after")
    def verified(self):
        if not self.final_facts_verified:
            raise ValueError("Final billing and supply facts must be verified")
        return self


def round_ratio(numerator, denominator):
    return (numerator * 2 + denominator) // (2 * denominator)


def calculate(lines, issuer_state, supply_state):
    same_state = issuer_state == supply_state
    results = []
    for line in lines:
        row = line.model_dump(mode="json")
        metal = round_ratio(
            line.net_weight_micrograms_each * line.quantity * line.metal_rate_paise_per_gram,
            1000000,
        )
        wastage = round_ratio(metal * line.wastage_basis_points, 10000)
        before = (
            line.fixed_unit_paise * line.quantity
            if line.material == "Gifts"
            else metal
            + wastage
            + line.making_paise
            + line.huid_charge_paise
            + line.stone_paise
            + line.diamond_paise
            + line.other_paise
        )
        if line.discount_paise > before:
            raise HTTPException(422, "Line discount exceeds its value")
        taxable = before - line.discount_paise
        cgst = round_ratio(taxable * line.gst_basis_points, 20000) if same_state else 0
        sgst = cgst
        igst = 0 if same_state else round_ratio(taxable * line.gst_basis_points, 10000)
        total = taxable + cgst + sgst + igst
        if total > MAX_PAISE:
            raise HTTPException(422, "Invoice arithmetic exceeds supported range")
        row.update(
            metal_value_paise=metal,
            wastage_paise=wastage,
            taxable_paise=taxable,
            cgst_paise=cgst,
            sgst_paise=sgst,
            igst_paise=igst,
            total_paise=total,
        )
        results.append(row)
    totals = {
        k: sum(row[k] for row in results)
        for k in ["taxable_paise", "cgst_paise", "sgst_paise", "igst_paise", "total_paise"]
    }
    if not 0 < totals["total_paise"] <= MAX_PAISE:
        raise HTTPException(422, "Invoice total is invalid")
    return results, totals


def einvoice_gate(buyer):
    if buyer.kind == "retail":
        return dict(status="not_applicable_domestic_b2c", irn=None, signed_qr=None)
    mode = os.getenv("BILLING_EINVOICE_APPLICABILITY", "unconfirmed")
    reference = os.getenv("BILLING_EINVOICE_REVIEW_REFERENCE", "")
    if mode == "not_applicable" and reference.strip():
        return dict(
            status="not_applicable_reviewed",
            review_reference=clean_text(reference, limit=200),
            irn=None,
            signed_qr=None,
        )
    if mode == "applicable":
        raise HTTPException(
            409, "IRP registration and verified IRN/signed QR are required before issuance"
        )
    raise HTTPException(409, "Business e-invoice applicability requires accountant review")


def calculate_charges(charges, issuer_state, supply_state):
    rows = []
    for charge in charges:
        cgst = (
            round_ratio(charge.taxable_paise * charge.gst_basis_points, 20000)
            if issuer_state == supply_state
            else 0
        )
        igst = (
            round_ratio(charge.taxable_paise * charge.gst_basis_points, 10000)
            if issuer_state != supply_state
            else 0
        )
        row = charge.model_dump(mode="json")
        row.update(
            cgst_paise=cgst,
            sgst_paise=cgst,
            igst_paise=igst,
            total_paise=charge.taxable_paise + 2 * cgst + igst,
        )
        if row["total_paise"] > MAX_PAISE:
            raise HTTPException(422, "Charge arithmetic exceeds supported range")
        rows.append(row)
    return rows
