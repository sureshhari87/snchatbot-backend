"""Offline rewards/voucher staging launcher guards."""

from unittest.mock import MagicMock, patch

import pytest
from test_staging_push_revision import URL

from scripts.local_staging import (
    CATALOGUE_REVISION,
    CATALOGUE_TABLES,
    CUSTOM_DESIGNS_TABLES,
    DATABASE,
    FINANCIAL_TABLES,
    ROLE,
    SAVINGS_TABLES,
    preflight,
)


@pytest.mark.parametrize(
    "missing",
    [None, *sorted(CATALOGUE_TABLES | FINANCIAL_TABLES | CUSTOM_DESIGNS_TABLES | SAVINGS_TABLES)],
)
def test_catalogue_schema_requires_all_catalogue_and_financial_tables_read_only(missing):
    identity, version = MagicMock(), MagicMock()
    identity.fetchone.return_value = (DATABASE, ROLE)
    version.fetchall.return_value = [(CATALOGUE_REVISION,)]
    tables = (
        {"alembic_version", "products", "push_devices", "push_events", "push_attempts"}
        | CATALOGUE_TABLES
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
                preflight(URL, expected_revision=CATALOGUE_REVISION)
        else:
            assert preflight(URL, expected_revision=CATALOGUE_REVISION) == "ready"
        assert conn.execute.call_args_list[0].args == ("SET TRANSACTION READ ONLY",)


def test_catalogue_launcher_rejects_previous_schema_without_mutation():
    identity, version = MagicMock(), MagicMock()
    identity.fetchone.return_value = (DATABASE, ROLE)
    version.fetchall.return_value = [("0029_voucher_funding",)]
    tables = (
        {"alembic_version", "products", "push_devices", "push_events", "push_attempts"}
        | CATALOGUE_TABLES
        | FINANCIAL_TABLES
        | CUSTOM_DESIGNS_TABLES
        | SAVINGS_TABLES
    )
    with patch("psycopg.connect") as connect:
        conn = connect.return_value.__enter__.return_value
        conn.execute.side_effect = [None, identity, [(name,) for name in tables], version]
        with pytest.raises(ValueError, match="Unexpected schema revision"):
            preflight(URL, expected_revision=CATALOGUE_REVISION)
        assert conn.execute.call_args_list[0].args == ("SET TRANSACTION READ ONLY",)
        assert len(conn.execute.call_args_list) == 4
