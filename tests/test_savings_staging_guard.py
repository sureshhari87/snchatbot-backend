"""Offline savings staging guard checks; no provider or database connections."""

from unittest.mock import MagicMock, patch

import pytest

from scripts.local_staging import DATABASE, ROLE, SAVINGS_REVISION, SAVINGS_TABLES, preflight
from scripts.render_staging import render_environment, verify_database
from test_staging_push_revision import URL


def source(**updates):
    return dict(
        APP_ENV="staging",
        DATABASE_URL=URL,
        STAGING_NEON_HOST="ep-test.neon.tech",
        RENDER_EXTERNAL_HOSTNAME="snchatbot-staging.onrender.com",
        SECRET_KEY="test-key-" * 5,
        **updates,
    )


@pytest.mark.parametrize("missing", [None, *sorted(SAVINGS_TABLES)])
def test_savings_schema_requires_every_table_read_only(missing):
    identity, version = MagicMock(), MagicMock()
    identity.fetchone.return_value = (DATABASE, ROLE)
    version.fetchall.return_value = [(SAVINGS_REVISION,)]
    tables = {
        "alembic_version",
        "products",
        "push_devices",
        "push_events",
        "push_attempts",
    } | SAVINGS_TABLES
    if missing:
        tables.remove(missing)
    with patch("psycopg.connect") as connect:
        conn = connect.return_value.__enter__.return_value
        conn.execute.side_effect = [None, identity, [(name,) for name in tables], version]
        if missing:
            with pytest.raises(ValueError, match="savings schema tables are missing"):
                preflight(URL, expected_revision=SAVINGS_REVISION)
        else:
            assert preflight(URL, expected_revision=SAVINGS_REVISION) == "ready"
        assert conn.execute.call_args_list[0].args == ("SET TRANSACTION READ ONLY",)


def test_render_requires_savings_revision_even_while_feature_disabled():
    with patch("scripts.render_staging.preflight", return_value="empty") as check:
        with pytest.raises(ValueError):
            verify_database(URL)
        check.assert_called_once_with(URL, expected_revision=SAVINGS_REVISION)


def test_unknown_schema_requirement_never_connects():
    with patch("psycopg.connect") as connect:
        with pytest.raises(ValueError, match="Unsupported"):
            preflight(URL, expected_revision="head")
        connect.assert_not_called()


@pytest.mark.parametrize(
    "update",
    [
        {},
        {"PUSH_OUTBOX_ENABLED": "1"},
        {"PUSH_DELIVERY_ENABLED": "1"},
    ],
)
def test_enabled_savings_requires_test_providers_and_disabled_sending(update):
    environment = source(SAVINGS_ENABLED="1")
    if update:
        environment.update(
            RAZORPAY_KEY_ID="rzp_test_synthetic",
            RAZORPAY_KEY_SECRET="test-only",
            RAZORPAY_WEBHOOK_SECRET="test-only" * 5,
            **update,
        )
    with pytest.raises(ValueError):
        render_environment(environment)


def test_enabled_test_savings_accepts_zero_sending_flags():
    environment = source(
        SAVINGS_ENABLED="1",
        RAZORPAY_KEY_ID="rzp_test_synthetic",
        RAZORPAY_KEY_SECRET="test-only",
        RAZORPAY_WEBHOOK_SECRET="test-only" * 5,
        PUSH_OUTBOX_ENABLED="0",
        PUSH_DELIVERY_ENABLED="0",
    )
    assert render_environment(environment)["SAVINGS_ENABLED"] == "1"
