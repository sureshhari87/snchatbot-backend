"""Inactive drafts cannot affect checkout, bypass preview or overwrite another version."""

import json
import time
from types import SimpleNamespace

import jwt
import pytest
from fastapi import HTTPException

import config
import delivery_draft_api as api
from delivery_draft_models import DeliveryDraft, DeliveryDraftAudit
from models import AppConfigEntry


@pytest.fixture(autouse=True)
def enable(monkeypatch):
    monkeypatch.setattr(api, "ENABLED", True)
    monkeypatch.setattr(config, "APP_ENV", "staging")


def payload(**changes):
    return {
        "request_key": "synthetic-draft-key",
        "draft_id": None,
        "expected_version": 0,
        "name": "Synthetic inactive route",
        "reason": "Synthetic staging draft review",
        "route": {
            "active": False,
            "pin_prefix": "625009",
            "method": "standard",
            "handling_days": 1,
            "minimum_transit_days": 2,
            "maximum_transit_days": 3,
            "working_weekdays": [0, 1, 2, 3, 4],
            "holidays": [],
            "cutoff": "16:00",
            "fulfilment_group": "store",
        },
        **changes,
    }


def preview(client, headers, value):
    r = client.post("/admin/delivery-drafts/preview", headers=headers, json=value)
    assert r.status_code == 200
    return {**value, "preview_token": r.json()["preview_token"]}


def test_permission_default_off_and_production(client, admin_headers, auth_headers, monkeypatch):
    assert client.get("/admin/delivery-drafts").status_code == 401
    assert client.get("/admin/delivery-drafts", headers=auth_headers).status_code == 403
    monkeypatch.setattr(api, "ENABLED", False)
    assert client.get("/admin/delivery-drafts", headers=admin_headers).status_code == 503
    monkeypatch.setattr(api, "ENABLED", True)
    monkeypatch.setattr(config, "APP_ENV", "production")
    assert client.get("/admin/delivery-drafts", headers=admin_headers).status_code == 403


@pytest.mark.parametrize(
    "change",
    [
        {"active": True},
        {"active": 0},
        {"active": "false"},
        {"pin_prefix": ""},
        {"pin_prefix": "000000"},
        {"handling_days": True},
        {"working_weekdays": []},
        {"working_weekdays": [0, 0]},
        {"working_weekdays": [7]},
        {"cutoff": "24:00"},
        {"holidays": ["2026-02-30"]},
        {"holidays": ["20261014"]},
        {"maximum_transit_days": 1},
        {"minimum_transit_days": 1.5},
        {"unexpected": "not accepted"},
    ],
)
def test_strict_inactive_calendar(client, admin_headers, change):
    value = payload()
    value["route"].update(change)
    assert (
        client.post("/admin/delivery-drafts/preview", headers=admin_headers, json=value).status_code
        == 422
    )


def test_preview_and_replay_leave_live_and_private_settings_unchanged(client, db, admin_headers):
    raw = json.dumps([{"active": False, "private_marker": "synthetic preserved"}])
    db.add_all(
        [
            AppConfigEntry(key="commerce.delivery_routes", value=raw),
            AppConfigEntry(key="private.synthetic", value="preserve"),
        ]
    )
    db.commit()
    value = preview(client, admin_headers, payload())
    assert db.query(DeliveryDraft).count() == db.query(DeliveryDraftAudit).count() == 0
    first = client.post("/admin/delivery-drafts/save", headers=admin_headers, json=value)
    assert first.status_code == 200
    result = first.json()
    assert result["affects_checkout"] is False and result["activation_allowed"] is False
    assert result["saved"]["route"]["active"] is False
    again = client.post("/admin/delivery-drafts/save", headers=admin_headers, json=value)
    assert again.json() == result
    assert db.query(DeliveryDraft).count() == db.query(DeliveryDraftAudit).count() == 1
    audit = db.query(DeliveryDraftAudit).one()
    assert audit.before is None and audit.after == result["saved"]
    assert audit.reason == payload()["reason"]
    assert db.query(AppConfigEntry).filter_by(key="commerce.delivery_routes").one().value == raw
    assert db.query(AppConfigEntry).filter_by(key="private.synthetic").one().value == "preserve"
    read = client.get("/admin/delivery-drafts/key/synthetic-draft-key", headers=admin_headers)
    assert read.json() == result
    changed = {**value, "name": "changed"}
    assert (
        client.post("/admin/delivery-drafts/save", headers=admin_headers, json=changed).status_code
        == 409
    )


def test_concurrent_previews_allow_only_one_version(client, db, admin_headers):
    value = preview(client, admin_headers, payload())
    first = client.post("/admin/delivery-drafts/save", headers=admin_headers, json=value).json()
    edit = payload(
        draft_id=first["draft_id"],
        expected_version=1,
        request_key="synthetic-edit-one",
        name="First edit",
    )
    second = {**edit, "request_key": "synthetic-edit-two", "name": "Second edit"}
    a = preview(client, admin_headers, edit)
    b = preview(client, admin_headers, second)
    saved = client.post("/admin/delivery-drafts/save", headers=admin_headers, json=a)
    assert saved.status_code == 200 and saved.json()["version"] == 2
    assert (
        client.post("/admin/delivery-drafts/save", headers=admin_headers, json=b).status_code == 409
    )
    assert db.query(DeliveryDraft).one().name == "First edit"
    assert db.query(DeliveryDraftAudit).count() == 2
    replayed = client.post("/admin/delivery-drafts/save", headers=admin_headers, json=value)
    assert replayed.json() == first  # historical receipt remains stable after later edits


@pytest.mark.parametrize(
    "claim_change", [{"exp": 1}, {"actor_id": 999999}, {"environment": "production"}]
)
def test_preview_binds_actor_expiry_environment(
    client, db, admin_headers, admin_user, claim_change
):
    value = payload()
    claims = {
        "aud": api.AUDIENCE,
        "exp": int(time.time()) + 600,
        "actor_id": admin_user.id,
        "payload_sha256": api.digest(api.canonical(api.DraftInput(**value))),
        "environment": "staging",
        **claim_change,
    }
    token = jwt.encode(claims, config.SECRET_KEY, algorithm=config.ALGORITHM)
    value["preview_token"] = token
    assert (
        client.post("/admin/delivery-drafts/save", headers=admin_headers, json=value).status_code
        == 409
    )
    assert db.query(DeliveryDraft).count() == 0


def test_preview_edit_binding_and_actor_owned_recovery(client, db, admin_headers, admin_user):
    value = preview(client, admin_headers, payload())
    changed = {**value, "name": "Edited without fresh preview"}
    assert (
        client.post("/admin/delivery-drafts/save", headers=admin_headers, json=changed).status_code
        == 409
    )
    assert (
        client.post("/admin/delivery-drafts/save", headers=admin_headers, json=value).status_code
        == 200
    )
    with pytest.raises(HTTPException) as error:
        api.save(db, SimpleNamespace(id=admin_user.id + 100), api.SaveInput(**value))
    assert error.value.status_code == 409
    assert db.query(DeliveryDraftAudit).count() == 1


def test_bounded_listing_and_no_activation_route(client, admin_headers):
    for i in range(3):
        value = preview(client, admin_headers, payload(request_key=f"synthetic-page-{i}"))
        assert (
            client.post(
                "/admin/delivery-drafts/save", headers=admin_headers, json=value
            ).status_code
            == 200
        )
    page = client.get("/admin/delivery-drafts?limit=2", headers=admin_headers).json()
    assert len(page["items"]) == 2 and page["next_after"] == page["items"][-1]["id"]
    tail = client.get(
        f"/admin/delivery-drafts?after={page['next_after']}", headers=admin_headers
    ).json()
    assert len(tail["items"]) == 1 and tail["next_after"] is None
    assert (
        client.post("/admin/delivery-drafts/1/activate", headers=admin_headers, json={}).status_code
        == 404
    )
