"""Offline 0034 launcher checks; all database operations are mocked reads."""

from unittest.mock import MagicMock

import pytest
from test_financial_staging_guard import settings
from test_staging_feature_revisions import URL, database

from scripts import local_staging as local
from scripts import render_staging as render


@pytest.mark.parametrize(
    "missing",
    [
        None,
        *sorted(local.REFERRAL_TABLES),
        "savings_schemes",
        "custom_design_quotes",
        "reward_accounts",
        "catalogue_campaigns",
        "inbox_broadcasts",
        "custom_design_balance_entries",
        "delivery_drafts",
        "delivery_draft_audits",
    ],
)
def test_referral_revision_requires_every_predecessor_and_referral_table(monkeypatch, missing):
    conn = database(monkeypatch, local.REFERRAL_REVISION, missing)
    if missing:
        with pytest.raises(ValueError):
            local.preflight(URL, expected_revision=local.REFERRAL_REVISION)
    else:
        assert local.preflight(URL, expected_revision=local.REFERRAL_REVISION) == "ready"
    statements = [call.args[0] for call in conn.execute.call_args_list]
    assert statements[0] == "SET TRANSACTION READ ONLY"
    assert all(s.startswith("SELECT ") or s == "SET TRANSACTION READ ONLY" for s in statements)


def test_earlier_schema_cannot_satisfy_referrals(monkeypatch):
    database(monkeypatch, local.DELIVERY_DRAFT_REVISION)
    with pytest.raises(ValueError):
        local.preflight(URL, expected_revision=local.REFERRAL_REVISION, allow_descendants=True)


@pytest.mark.parametrize("flag", ["true", "on", "yes", "2", ""])
def test_referral_flag_rejects_ambiguous_values(flag):
    with pytest.raises(ValueError):
        render.render_environment({**settings(), "REFERRALS_ENABLED": flag})


def test_default_off_and_explicit_test_mode_enablement_do_not_enable_sending():
    assert render.render_environment(settings())["REFERRALS_ENABLED"] == "0"
    env = render.render_environment({**settings(), "REFERRALS_ENABLED": "1"})
    assert env["REFERRALS_ENABLED"] == "1"
    assert env["PUSH_DELIVERY_ENABLED"] == "0" and env["PUSH_OUTBOX_ENABLED"] == "0"
    assert env["RUN_MIGRATIONS_ON_STARTUP"] == "0" and env["RUN_MIGRATIONS_BEFORE_START"] == "0"


def test_referrals_require_financial_feature():
    with pytest.raises(ValueError):
        render.render_environment(
            {**settings(), "REFERRALS_ENABLED": "1", "REWARDS_VOUCHERS_ENABLED": "0"}
        )


def test_referral_feature_requires_current_review_schema_without_migration(monkeypatch):
    monkeypatch.setenv("REFERRALS_ENABLED", "1")
    monkeypatch.setenv("DELIVERY_DRAFTS_ENABLED", "1")
    check = MagicMock(return_value="empty")
    monkeypatch.setattr(render, "preflight", check)
    with pytest.raises(ValueError):
        render.verify_database(URL)
    check.assert_called_once_with(
        URL, expected_revision=local.REVIEW_PURCHASE_REVISION, allow_descendants=True
    )
