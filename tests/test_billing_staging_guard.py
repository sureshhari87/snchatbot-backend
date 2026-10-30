"""Staging preflight recognises billing descendants with all prior schema guards."""

from unittest.mock import MagicMock, patch

import pytest
from test_staging_push_revision import URL

from scripts import local_staging as local
from scripts.local_staging import (
    BILLING_DOCUMENT_TABLES,
    BILLING_DOCUMENTS_REVISION,
    BILLING_RECEIPTS_REVISION,
    DATABASE,
    REVISION,
    ROLE,
    preflight,
)


def complete_tables():
    return (
        local.SAVINGS_TABLES
        | local.CUSTOM_DESIGNS_TABLES
        | local.FINANCIAL_TABLES
        | local.CATALOGUE_TABLES
        | local.REFERRAL_TABLES
        | local.BILLING_RECEIPT_TABLES
        | local.BILLING_DOCUMENT_TABLES
        | {
            "alembic_version",
            "products",
            "push_devices",
            "push_events",
            "push_attempts",
            "inbox_broadcasts",
            "delivery_drafts",
            "delivery_draft_audits",
            "custom_design_balance_requests",
            "custom_design_balance_checkouts",
            "custom_design_balance_entries",
            "review_photos",
        }
    )


@pytest.mark.parametrize("revision", [BILLING_RECEIPTS_REVISION, BILLING_DOCUMENTS_REVISION])
@pytest.mark.parametrize(
    "missing",
    [
        None,
        "billing_receipts",
        "billing_receipt_audit",
        "review_photos",
        "savings_entries",
        "verified_order_id",
    ],
)
def test_billing_guard_keeps_all_predecessor_checks_read_only(revision, missing):
    tables = complete_tables()
    if missing and missing != "verified_order_id":
        tables.remove(missing)
    identity, version, columns = MagicMock(), MagicMock(), MagicMock()
    identity.fetchone.return_value = (DATABASE, ROLE)
    version.fetchall.return_value = [(revision,)]
    columns.fetchall.return_value = (
        [] if missing == "verified_order_id" else [("verified_order_id",)]
    )
    with patch("psycopg.connect") as connect:
        conn = connect.return_value.__enter__.return_value
        conn.execute.side_effect = [None, identity, [(name,) for name in tables], version, columns]
        if missing:
            message = {
                "billing_receipts": "immutable savings receipt",
                "billing_receipt_audit": "immutable savings receipt",
                "review_photos": "private review photo",
                "savings_entries": "savings schema",
                "verified_order_id": "review purchase attribution",
            }[missing]
            with pytest.raises(ValueError, match=message):
                preflight(URL, expected_revision=revision)
        else:
            assert preflight(URL, expected_revision=REVISION, allow_descendants=True) == "ready"
        assert conn.execute.call_args_list[0].args == ("SET TRANSACTION READ ONLY",)
        assert all(
            call.args[0].startswith(("SET TRANSACTION READ ONLY", "SELECT"))
            for call in conn.execute.call_args_list
        )


@pytest.mark.parametrize("missing", sorted(BILLING_DOCUMENT_TABLES))
def test_billing_document_revision_requires_its_complete_schema(missing):
    tables = complete_tables()
    tables.remove(missing)
    identity, version, columns = MagicMock(), MagicMock(), MagicMock()
    identity.fetchone.return_value = (DATABASE, ROLE)
    version.fetchall.return_value = [(BILLING_DOCUMENTS_REVISION,)]
    columns.fetchall.return_value = [("verified_order_id",)]
    with patch("psycopg.connect") as connect:
        conn = connect.return_value.__enter__.return_value
        conn.execute.side_effect = [None, identity, [(name,) for name in tables], version, columns]
        with pytest.raises(ValueError, match="Required immutable billing document schema"):
            preflight(URL, expected_revision=BILLING_DOCUMENTS_REVISION)


def test_billing_revision_refuses_old_schema_and_does_not_stamp_or_migrate():
    identity, version = MagicMock(), MagicMock()
    identity.fetchone.return_value = (DATABASE, ROLE)
    version.fetchall.return_value = [("0036_review_photos",)]
    tables = complete_tables()
    with patch("psycopg.connect") as connect:
        conn = connect.return_value.__enter__.return_value
        conn.execute.side_effect = [None, identity, [(name,) for name in tables], version]
        with pytest.raises(ValueError, match="Unexpected schema revision"):
            preflight(URL, expected_revision=BILLING_DOCUMENTS_REVISION)
        assert len(conn.execute.call_args_list) == 4


@pytest.mark.parametrize("flag", ["BILLING_DOCUMENTS_ENABLED", "BILLING_SALES_ENABLED"])
@pytest.mark.parametrize("value", ["true", "yes", "on", "2", ""])
def test_billing_launcher_requires_explicit_binary_flags(flag, value):
    from test_financial_staging_guard import settings

    from scripts.render_staging import render_environment

    with pytest.raises(ValueError):
        render_environment(settings() | {flag: value})


def test_billing_flags_default_off_and_sales_cannot_bypass_documents():
    import json

    from test_billing_receipts import ISSUER
    from test_financial_staging_guard import settings

    from scripts.render_staging import render_environment

    env = render_environment(settings())
    assert env["BILLING_DOCUMENTS_ENABLED"] == env["BILLING_SALES_ENABLED"] == "0"
    with pytest.raises(ValueError, match="requires billing documents"):
        render_environment(settings() | {"BILLING_SALES_ENABLED": "1"})
    with pytest.raises(ValueError, match="issuer"):
        render_environment(settings() | {"BILLING_DOCUMENTS_ENABLED": "1"})
    enabled = render_environment(
        settings() | {"BILLING_DOCUMENTS_ENABLED": "1", "BILLING_ISSUER_JSON": json.dumps(ISSUER)}
    )
    assert enabled["BILLING_DOCUMENTS_ENABLED"] == "1" and enabled["BILLING_SALES_ENABLED"] == "0"
    assert enabled["PUSH_OUTBOX_ENABLED"] == enabled["PUSH_DELIVERY_ENABLED"] == "0"
    assert enabled["RUN_MIGRATIONS_ON_STARTUP"] == enabled["RUN_MIGRATIONS_BEFORE_START"] == "0"


def test_enabled_billing_checks_0038_and_only_reads_product_count():
    from scripts import render_staging as render

    with (
        patch.object(render, "preflight", return_value="ready") as guard,
        patch("psycopg.connect") as connect,
    ):
        connection = connect.return_value.__enter__.return_value
        connection.execute.return_value.fetchone.return_value = (1,)
        render.verify_database(URL, billing_documents_enabled=True)
        guard.assert_called_once_with(
            URL, expected_revision=BILLING_DOCUMENTS_REVISION, allow_descendants=True
        )
        assert connection.execute.call_args_list[0].args == ("SET TRANSACTION READ ONLY",)
        assert all(
            call.args[0].startswith(("SET TRANSACTION READ ONLY", "SELECT"))
            for call in connection.execute.call_args_list
        )
