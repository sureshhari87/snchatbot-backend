"""Offline rewards/voucher staging launcher guards."""

from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from test_savings_staging_guard import source
from test_staging_push_revision import URL

from scripts.local_staging import (
    CUSTOM_DESIGNS_TABLES,
    DATABASE,
    FINANCIAL_REVISION,
    FINANCIAL_TABLES,
    ROLE,
    SAVINGS_TABLES,
    preflight,
)
from scripts.render_staging import render_environment


@pytest.mark.parametrize(
    "missing", [None, *sorted(FINANCIAL_TABLES | CUSTOM_DESIGNS_TABLES | SAVINGS_TABLES)]
)
def test_financial_schema_requires_every_table_read_only(missing):
    identity, version = MagicMock(), MagicMock()
    identity.fetchone.return_value = (DATABASE, ROLE)
    version.fetchall.return_value = [(FINANCIAL_REVISION,)]
    tables = (
        {"alembic_version", "products", "push_devices", "push_events", "push_attempts"}
        | FINANCIAL_TABLES
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
                preflight(URL, expected_revision=FINANCIAL_REVISION)
        else:
            assert preflight(URL, expected_revision=FINANCIAL_REVISION) == "ready"
        assert conn.execute.call_args_list[0].args == ("SET TRANSACTION READ ONLY",)


@pytest.mark.parametrize(
    "changes",
    [
        {"RAZORPAY_KEY_ID": ""},
        {"RAZORPAY_KEY_ID": "rzp_live_synthetic"},
        {"RAZORPAY_WEBHOOK_SECRET": ""},
        {"PUSH_OUTBOX_ENABLED": "1"},
        {"PUSH_DELIVERY_ENABLED": "1"},
        {"VOUCHER_CODE_ENCRYPTION_KEY": ""},
        {"VOUCHER_CODE_ENCRYPTION_KEY": "invalid"},
        {"REWARDS_VOUCHERS_ENABLED": "invalid"},
    ],
)
def test_financial_activation_rejects_unsafe_settings(changes):
    env = settings()
    env.update(changes)
    with pytest.raises(ValueError):
        render_environment(env)


def settings():
    return source(
        REWARDS_VOUCHERS_ENABLED="1",
        SAVINGS_ENABLED="0",
        CUSTOM_DESIGNS_ENABLED="0",
        RAZORPAY_KEY_ID="rzp_test_synthetic",
        RAZORPAY_KEY_SECRET="synthetic",
        RAZORPAY_WEBHOOK_SECRET="synthetic-only" * 4,
        VOUCHER_CODE_ENCRYPTION_KEY=Fernet.generate_key().decode(),
        PUSH_OUTBOX_ENABLED="0",
        PUSH_DELIVERY_ENABLED="0",
    )


def test_financial_activation_accepts_test_mode_without_sending_or_migration():
    checked = render_environment(settings())
    assert checked["REWARDS_VOUCHERS_ENABLED"] == "1"
    assert checked["PUSH_DELIVERY_ENABLED"] == "0"
    assert checked["RUN_MIGRATIONS_ON_STARTUP"] == "0"
