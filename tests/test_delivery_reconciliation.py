"""Offline legacy delivery reconciliation must never create live coverage."""

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from delivery_reconciliation import audit, digest

ROOT = Path(__file__).resolve().parents[1]
READY = {
    "readyToShip": True,
    "madeToOrder": False,
    "handlingDays": 3,
    "manufacturingDays": 0,
    "sizeAdjustmentDays": 1,
    "sizeOptions": ["12", "14"],
    "sizeAdjustmentSizes": ["14"],
}


@pytest.fixture
def snapshots():
    legacy = {
        "schema_version": 1,
        "inventory_locations": [
            {
                "id": "origin-a",
                "data": {
                    "safeDisplayName": "Synthetic location",
                    "originPin": "625009",
                    "active": True,
                    "priority": 0,
                    "dispatchCutoffTime": "16:00",
                    "workingWeekdays": [1, 2, 3, 4, 5],
                    "defaultHandlingDays": 2,
                    "holidays": ["2026-10-25"],
                },
            }
        ],
        "delivery_sla_rules": [
            {
                "id": "sla-a",
                "data": {
                    "destinationPinPrefix": "625",
                    "minimumTransitBusinessDays": 1,
                    "maximumTransitBusinessDays": 3,
                    "deliveryMethod": "standard",
                    "active": True,
                    "priority": 0,
                },
            }
        ],
        "products": [{"id": "source-product", "data": copy.deepcopy(READY)}],
    }
    backend = {
        "schema_version": 1,
        "routes": [],
        "products": [
            {"id": 25, "source_id": "source-product", "source_data": copy.deepcopy(READY)}
        ],
    }
    bindings = {
        "schema_version": 1,
        "bindings": [
            {
                "sla_id": "sla-a",
                "location_id": "origin-a",
                "fulfilment_group": "synthetic",
                "reason": "Explicit synthetic association",
            }
        ],
    }
    return legacy, backend, bindings


def codes(report):
    return {issue["code"] for issue in report["issues"]}


def test_no_join_is_inferred_and_inputs_are_unchanged(snapshots):
    legacy, backend, _ = snapshots
    before = copy.deepcopy((legacy, backend))
    report = audit(legacy, backend)
    assert report["proposals"] == []
    assert "explicit_location_binding_required" in codes(report)
    assert "unbound_inventory_location" in codes(report)
    assert (legacy, backend) == before
    assert report["identities"]["products"][0]["sha256"] == digest(READY)


def test_explicit_binding_is_inactive_and_valid_for_strict_draft_api(snapshots):
    from delivery_draft_api import DraftRoute

    before = copy.deepcopy(snapshots)
    report = audit(*snapshots)
    proposed = report["proposals"][0]
    assert proposed["route"]["working_weekdays"] == [0, 1, 2, 3, 4]
    assert DraftRoute(**proposed["route"]).active is False
    assert proposed["source_sla_sha256"] == digest(snapshots[0]["delivery_sla_rules"][0]["data"])
    assert proposed["source_was_active"] == {"location": True, "sla": True}
    assert proposed["review_required"] is True
    assert "origin_and_priority_require_review" in codes(report)
    assert all(
        report[k] is False for k in ["activation_allowed", "import_authorized", "writes_performed"]
    )
    assert report["summary"]["ready_for_import"] is False
    assert snapshots == before
    assert audit(*snapshots) == report


@pytest.mark.parametrize(
    "collection,field,value",
    [
        ("inventory_locations", "workingWeekdays", [0, 1]),
        ("inventory_locations", "workingWeekdays", [1, True]),
        ("inventory_locations", "workingWeekdays", [1, 1]),
        ("inventory_locations", "workingWeekdays", []),
        ("inventory_locations", "holidays", ["2026-02-30"]),
        ("inventory_locations", "holidays", ["20261025"]),
        ("inventory_locations", "holidays", ["2026-10-25", "2026-10-25"]),
        ("inventory_locations", "dispatchCutoffTime", "25:00"),
        ("inventory_locations", "originPin", 625009),
        ("inventory_locations", "active", "true"),
        ("inventory_locations", "defaultHandlingDays", True),
        ("inventory_locations", "defaultHandlingDays", "2"),
        ("inventory_locations", "defaultHandlingDays", 366),
        ("delivery_sla_rules", "destinationPinPrefix", "062"),
        ("delivery_sla_rules", "destinationPinPrefix", 625),
        ("delivery_sla_rules", "minimumTransitBusinessDays", 4),
        ("delivery_sla_rules", "deliveryMethod", "standard\n"),
        ("delivery_sla_rules", "priority", -1),
    ],
)
def test_invalid_records_never_produce_proposals(snapshots, collection, field, value):
    snapshots[0][collection][0]["data"][field] = value
    report = audit(*snapshots)
    assert "invalid_legacy_record" in codes(report)
    assert report["proposals"] == []


def test_missing_calendar_is_not_defaulted(snapshots):
    del snapshots[0]["inventory_locations"][0]["data"]["workingWeekdays"]
    assert audit(*snapshots)["proposals"] == []


def test_duplicate_identity_fails_closed(snapshots):
    snapshots[0]["inventory_locations"] *= 2
    with pytest.raises(ValueError, match="Invalid audit input"):
        audit(*snapshots)


def test_overlaps_duplicates_and_priority_conflicts_are_reported(snapshots):
    rule = snapshots[0]["delivery_sla_rules"][0]
    for identity, prefix, priority in [("same", "625", 2), ("nested", "625009", 0)]:
        snapshots[0]["delivery_sla_rules"].append(
            {
                "id": identity,
                "data": {**rule["data"], "destinationPinPrefix": prefix, "priority": priority},
            }
        )
    report = audit(*snapshots)
    assert {
        "duplicate_coverage",
        "overlapping_coverage",
        "priority_semantics_require_review",
    } <= codes(report)
    assert report["proposals"][0]["route"]["active"] is False


def test_methods_do_not_share_coverage(snapshots):
    row = copy.deepcopy(snapshots[0]["delivery_sla_rules"][0])
    row["id"] = "express"
    row["data"]["deliveryMethod"] = "express"
    snapshots[0]["delivery_sla_rules"].append(row)
    assert "duplicate_coverage" not in codes(audit(*snapshots))


def test_backend_routes_are_preserved_and_conflicts_reported(snapshots):
    route = audit(*snapshots)["proposals"][0]["route"]
    snapshots[1]["routes"] = [
        {**route, "active": True},
        {**route, "active": True},
        {"active": "true"},
    ]
    before = copy.deepcopy(snapshots[1])
    report = audit(*snapshots)
    assert {
        "backend_coverage_overlap",
        "proposal_overlaps_live_coverage",
        "invalid_backend_route",
    } <= codes(report)
    assert snapshots[1] == before
    assert report["identities"]["backend_routes"][0]["sha256"] == digest(before["routes"][0])


@pytest.mark.parametrize(
    "change",
    [
        {},
        {"readyToShip": True, "madeToOrder": True},
        {**READY, "sizeAdjustmentSizes": []},
        {**READY, "sizeOptions": ["12", "12"]},
        {**READY, "sizeAdjustmentSizes": ["99"]},
        {**READY, "dispatchNotBefore": "2026-02-30"},
    ],
)
def test_product_readiness_is_reviewed_without_copying_balances(snapshots, change):
    snapshots[0]["products"][0]["data"] = change
    snapshots[1]["products"][0]["source_data"] = change
    report = audit(*snapshots)
    assert sum(issue["code"] == "invalid_readiness" for issue in report["issues"]) == 2


def test_backend_identity_mapping_does_not_assume_matching_numeric_id(snapshots):
    snapshots[1]["products"] = [
        {"id": 25, "source_id": "other-source", "source_data": READY},
        {"id": 26, "source_id": "other-source", "source_data": READY},
    ]
    assert {
        "backend_source_missing_from_export",
        "duplicate_backend_source_identity",
        "unmapped_product_identity",
    } <= codes(audit(*snapshots))


def test_duplicate_or_unknown_bindings_do_not_guess_an_origin(snapshots):
    snapshots[2]["bindings"] *= 2
    with pytest.raises(ValueError):
        audit(*snapshots)
    snapshots[2]["bindings"] = [dict(snapshots[2]["bindings"][0], location_id="unknown")]
    report = audit(*snapshots)
    assert "unknown_explicit_binding" in codes(report)
    assert report["proposals"] == []


def test_unknown_private_fields_are_hashed_not_disclosed(snapshots):
    marker = "private-synthetic-value-not-for-reports"
    snapshots[0]["inventory_locations"][0]["data"]["unrelated_private_field"] = marker
    report = audit(*snapshots)
    assert marker not in json.dumps(report)
    assert report["identities"]["inventory_locations"][0]["sha256"] == digest(
        snapshots[0]["inventory_locations"][0]["data"]
    )


def run_cli(tmp_path, snapshots, extra=()):
    paths = []
    for name, data in zip(["legacy", "backend", "bindings"], snapshots, strict=True):
        path = tmp_path / (name + ".json")
        if not path.exists():
            path.write_text(json.dumps(data))
        paths.append(path)
    output = tmp_path / "report.json"
    env = {**os.environ, "DATABASE_URL": "invalid-private-marker", "APP_ENV": "production"}
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/audit_delivery_exports.py"),
            "--legacy",
            str(paths[0]),
            "--backend",
            str(paths[1]),
            "--bindings",
            str(paths[2]),
            "--report",
            str(output),
            *extra,
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result, output, paths


def test_cli_is_offline_preserves_inputs_and_refuses_report_overwrite(tmp_path, snapshots):
    result, output, paths = run_cli(tmp_path, snapshots)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["live_changes"] is False
    assert "invalid-private-marker" not in result.stdout + result.stderr
    before = output.read_bytes()
    input_bytes = [p.read_bytes() for p in paths]
    result, _, _ = run_cli(tmp_path, snapshots)
    assert result.returncode == 2 and output.read_bytes() == before
    assert [p.read_bytes() for p in paths] == input_bytes


@pytest.mark.parametrize(
    "contents", ['{"schema_version":1,"schema_version":1}', "{invalid-private-marker"]
)
def test_cli_rejects_ambiguous_or_invalid_json_without_echo(tmp_path, snapshots, contents):
    (tmp_path / "legacy.json").write_text(contents)
    result, output, _ = run_cli(tmp_path, snapshots)
    assert result.returncode == 2 and not output.exists()
    assert "invalid-private-marker" not in result.stdout + result.stderr


def test_cli_refuses_input_replacement_and_hides_invalid_arguments(tmp_path, snapshots):
    result, _, paths = run_cli(tmp_path, snapshots, ["--report", str(tmp_path / "legacy.json")])
    assert result.returncode == 2
    assert json.loads(paths[0].read_text()) == snapshots[0]
    result, _, _ = run_cli(tmp_path, snapshots, ["--invalid-private-marker"])
    assert result.returncode == 2
    assert "invalid-private-marker" not in result.stdout + result.stderr


def test_nonfinite_source_values_fail_closed(snapshots):
    snapshots[0]["products"][0]["data"]["unknown"] = float("nan")
    with pytest.raises(ValueError):
        audit(*snapshots)


@pytest.mark.parametrize(
    "field,value",
    [
        ("handling_days", "2"),
        ("minimum_transit_days", 1.0),
        ("working_weekdays", [0, 0, 1]),
        ("holidays", ["2026-10-25", "2026-10-25"]),
        ("cutoff", "6:00"),
        ("pin_prefix", 625),
        ("active", None),
    ],
)
def test_live_snapshot_coercions_are_reviewed_not_silently_normalized(snapshots, field, value):
    route = audit(*snapshots)["proposals"][0]["route"]
    snapshots[1]["routes"] = [{**route, "active": True, field: value}]
    assert "invalid_backend_route" in codes(audit(*snapshots))


def test_source_payload_drift_and_backend_created_products_are_distinct(snapshots):
    snapshots[1]["products"][0]["source_data"] = {**READY, "handlingDays": 4}
    snapshots[1]["products"].append({"id": 26, "source_id": None, "source_data": READY})
    report = audit(*snapshots)
    assert "source_payload_differs" in codes(report)
    assert "unmapped_product_identity" not in codes(report)
    assert "backend_source_missing_from_export" not in codes(report)
