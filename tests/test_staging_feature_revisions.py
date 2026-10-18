"""Offline schema/flag checks; never connect to a provider or database."""

from unittest.mock import MagicMock

import pytest

from scripts import local_staging as local
from scripts import render_staging as render

URL = "postgresql://staging_owner:dummy@ep-example.neon.tech/snchatbot_staging?sslmode=require"


def database(monkeypatch, revision, missing=None):
    tables = set(local.SAVINGS_TABLES) | set(local.CUSTOM_DESIGNS_TABLES)
    tables |= local.FINANCIAL_TABLES | local.CATALOGUE_TABLES | local.REFERRAL_TABLES
    tables |= {
        "alembic_version",
        "push_devices",
        "push_events",
        "push_attempts",
        "inbox_broadcasts",
        "delivery_drafts",
        "delivery_draft_audits",
        "custom_design_balance_requests",
        "custom_design_balance_checkouts",
        "custom_design_balance_entries",
    }
    if missing:
        tables.remove(missing)
    conn = MagicMock()

    def execute(statement):
        result = MagicMock()
        if "current_database" in statement:
            result.fetchone.return_value = ("snchatbot_staging", "staging_owner")
        elif "pg_tables" in statement:
            result.__iter__.return_value = iter([(name,) for name in tables])
        elif "version_num" in statement:
            result.fetchall.return_value = [(revision,)]
        return result

    conn.execute.side_effect = execute
    connect = MagicMock()
    connect.return_value.__enter__.return_value = conn
    monkeypatch.setattr("psycopg.connect", connect)
    return conn


@pytest.mark.parametrize(
    "revision",
    [
        local.CATALOGUE_REVISION,
        local.INBOX_REVISION,
        local.BALANCE_REVISION,
        local.DELIVERY_DRAFT_REVISION,
        local.REFERRAL_REVISION,
    ],
)
def test_known_descendants_are_read_only_and_explicit(monkeypatch, revision):
    conn = database(monkeypatch, revision)
    assert (
        local.preflight(URL, expected_revision=local.CATALOGUE_REVISION, allow_descendants=True)
        == "ready"
    )
    assert conn.execute.call_args_list[0].args == ("SET TRANSACTION READ ONLY",)
    if revision != local.CATALOGUE_REVISION:
        with pytest.raises(ValueError):
            local.preflight(URL, expected_revision=local.CATALOGUE_REVISION)


@pytest.mark.parametrize("revision", ["unknown_revision", local.FINANCIAL_REVISION])
def test_unknown_or_older_schema_is_rejected(monkeypatch, revision):
    database(monkeypatch, revision)
    with pytest.raises(ValueError):
        local.preflight(URL, expected_revision=local.CATALOGUE_REVISION, allow_descendants=True)


@pytest.mark.parametrize(
    "missing",
    [
        "inbox_broadcasts",
        "custom_design_balance_entries",
        "custom_design_balance_checkouts",
        "custom_design_balance_requests",
        "custom_design_quotes",
    ],
)
def test_new_revision_requires_full_predecessor_and_feature_tables(monkeypatch, missing):
    database(monkeypatch, local.BALANCE_REVISION, missing)
    with pytest.raises(ValueError):
        local.preflight(URL, expected_revision=local.BALANCE_REVISION)


def source(**values):
    return {
        "APP_ENV": "staging",
        "DATABASE_URL": URL,
        "STAGING_NEON_HOST": "ep-example.neon.tech",
        "RENDER_EXTERNAL_HOSTNAME": "snchatbot-staging.onrender.com",
        "SECRET_KEY": "synthetic-local-only-" * 3,
        **values,
    }


def test_feature_flags_default_off_and_invalid_combinations_fail():
    env = render.render_environment(source())
    for flag in [
        "INBOX_BROADCAST_ENABLED",
        "DELIVERY_DRAFTS_ENABLED",
        "CUSTOM_DESIGN_BALANCE_ENABLED",
        "CUSTOM_DESIGN_BALANCE_RAZORPAY_ENABLED",
    ]:
        assert env[flag] == "0"
    for values in [
        {"INBOX_BROADCAST_ENABLED": "true"},
        {"DELIVERY_DRAFTS_ENABLED": "true"},
        {"INBOX_BROADCAST_ENABLED": "1", "PUSH_DELIVERY_ENABLED": "1"},
        {"CUSTOM_DESIGN_BALANCE_ENABLED": "1"},
        {"CUSTOM_DESIGN_BALANCE_RAZORPAY_ENABLED": "1"},
    ]:
        with pytest.raises(ValueError):
            render.render_environment(source(**values))


@pytest.mark.parametrize(
    "flag,revision",
    [
        ("INBOX_BROADCAST_ENABLED", local.INBOX_REVISION),
        ("DELIVERY_DRAFTS_ENABLED", local.DELIVERY_DRAFT_REVISION),
        ("CUSTOM_DESIGN_BALANCE_ENABLED", local.BALANCE_REVISION),
    ],
)
def test_enabled_feature_requires_its_revision(monkeypatch, flag, revision):
    monkeypatch.setenv(flag, "1")
    monkeypatch.setenv(
        "CUSTOM_DESIGN_BALANCE_ENABLED", "1" if flag == "CUSTOM_DESIGN_BALANCE_ENABLED" else "0"
    )
    check = MagicMock(return_value="empty")
    monkeypatch.setattr(render, "preflight", check)
    with pytest.raises(ValueError):
        render.verify_database(URL)
    check.assert_called_once_with(
        URL, expected_revision=local.REVIEW_PHOTOS_REVISION, allow_descendants=True
    )


@pytest.mark.parametrize(
    "missing",
    [
        "delivery_drafts",
        "delivery_draft_audits",
        "inbox_broadcasts",
        "custom_design_balance_entries",
        "custom_design_quotes",
    ],
)
def test_draft_revision_requires_draft_and_predecessor_tables(monkeypatch, missing):
    database(monkeypatch, local.DELIVERY_DRAFT_REVISION, missing)
    with pytest.raises(ValueError):
        local.preflight(URL, expected_revision=local.DELIVERY_DRAFT_REVISION)


def test_balance_revision_cannot_enable_draft_feature(monkeypatch):
    database(monkeypatch, local.BALANCE_REVISION)
    with pytest.raises(ValueError):
        local.preflight(
            URL, expected_revision=local.DELIVERY_DRAFT_REVISION, allow_descendants=True
        )
