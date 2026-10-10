"""Offline custom-design rollout guards; no external connections."""

from unittest.mock import MagicMock, patch

import pytest
from test_savings_staging_guard import source
from test_staging_push_revision import URL

from scripts.local_staging import (
    CUSTOM_DESIGNS_REVISION,
    CUSTOM_DESIGNS_TABLES,
    DATABASE,
    ROLE,
    SAVINGS_TABLES,
    preflight,
)
from scripts.render_staging import render_environment


@pytest.mark.parametrize("missing", [None, *sorted(CUSTOM_DESIGNS_TABLES | SAVINGS_TABLES)])
def test_complete_financial_schema_is_required_read_only(missing):
    identity, version = MagicMock(), MagicMock()
    identity.fetchone.return_value = (DATABASE, ROLE)
    version.fetchall.return_value = [(CUSTOM_DESIGNS_REVISION,)]
    tables = (
        {"alembic_version", "products", "push_devices", "push_events", "push_attempts"}
        | CUSTOM_DESIGNS_TABLES
        | SAVINGS_TABLES
    )
    if missing:
        tables.remove(missing)
    with patch("psycopg.connect") as connect:
        conn = connect.return_value.__enter__.return_value
        conn.execute.side_effect = [None, identity, [(name,) for name in tables], version]
        if missing:
            with pytest.raises(ValueError, match="schema tables are missing"):
                preflight(URL, expected_revision=CUSTOM_DESIGNS_REVISION)
        else:
            assert preflight(URL, expected_revision=CUSTOM_DESIGNS_REVISION) == "ready"
        assert conn.execute.call_args_list[0].args == ("SET TRANSACTION READ ONLY",)


@pytest.mark.parametrize(
    "changes",
    [
        {"RAZORPAY_KEY_ID": ""},
        {"RAZORPAY_WEBHOOK_SECRET": ""},
        {"PUSH_OUTBOX_ENABLED": "1"},
        {"PUSH_DELIVERY_ENABLED": "1"},
        {"RAZORPAY_KEY_ID": "rzp_live_synthetic"},
    ],
)
def test_custom_only_activation_rejects_unsafe_provider_or_sending(changes):
    env = source(
        CUSTOM_DESIGNS_ENABLED="1",
        SAVINGS_ENABLED="0",
        RAZORPAY_KEY_ID="rzp_test_synthetic",
        RAZORPAY_KEY_SECRET="synthetic",
        RAZORPAY_WEBHOOK_SECRET="synthetic-only" * 4,
        PUSH_OUTBOX_ENABLED="0",
        PUSH_DELIVERY_ENABLED="0",
    )
    env.update(changes)
    with pytest.raises(ValueError):
        render_environment(env)


def test_custom_only_activation_accepts_test_mode_without_enabling_push():
    env = source(
        CUSTOM_DESIGNS_ENABLED="1",
        SAVINGS_ENABLED="0",
        RAZORPAY_KEY_ID="rzp_test_synthetic",
        RAZORPAY_KEY_SECRET="synthetic",
        RAZORPAY_WEBHOOK_SECRET="synthetic-only" * 4,
        PUSH_OUTBOX_ENABLED="0",
        PUSH_DELIVERY_ENABLED="0",
    )
    checked = render_environment(env)
    assert checked["CUSTOM_DESIGNS_ENABLED"] == "1"
    assert checked["PUSH_DELIVERY_ENABLED"] == "0"
    assert checked["RUN_MIGRATIONS_ON_STARTUP"] == "0"
